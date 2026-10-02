"""Real Blender integration checks for the ragdoll pose writer.

Run with factory startup and the task's own Blender user profile.
"""

import json
from copy import deepcopy
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_armature
from yugo_ragdoll import service


PASSED = []


def check(name, callback):
    service.cleanup()
    callback()
    service.cleanup()
    PASSED.append(name)
    print(f"PASS {name}", flush=True)


def assert_rejected(callback, text):
    try:
        callback()
    except (ValueError, RuntimeError) as exc:
        assert text.casefold() in str(exc).casefold(), str(exc)
    else:
        raise AssertionError(f"Expected rejection containing {text}")


def values(matrix):
    return tuple(float(v) for row in matrix for v in row)


def close_matrix(a, b, tolerance=1e-5):
    return max(abs(x - y) for x, y in zip(values(a), values(b))) < tolerance


def endpoint_alignment(obj, snapshot):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for name, endpoints in snapshot["pose"].items():
        bone = evaluated.pose.bones[name]
        assert (obj.matrix_world @ bone.head - Vector(endpoints["head"])).length < 2e-4, (name, "head")
        assert (obj.matrix_world @ bone.tail - Vector(endpoints["tail"])).length < 2e-4, (name, "tail")


def physics_and_restore():
    obj = make_armature("PoseAlignment")
    obj.location = (1.3, -.2, .6)
    obj.rotation_euler.z = .41
    obj.scale = (1.3,) * 3
    obj.pose.bones["hips"].rotation_mode = "QUATERNION"
    obj.pose.bones["hips"].rotation_quaternion = (.98, .04, -.1, .12)
    bpy.context.view_layer.update()
    initial_world = {bone.name: (obj.matrix_world @ bone.matrix).copy() for bone in obj.pose.bones}
    original = {bone.name: bone.matrix_basis.copy() for bone in obj.pose.bones}
    object_matrix = obj.matrix_world.copy()
    sid = service.start(obj, settings={"ground_z": -10, "auto_recover": False})
    first = service.read(sid)
    assert service.read(sid) == first
    assert first["elapsed"] == 0
    assert len(first["pose"]) == 21
    service.hit(sid, "hips", (1.5, .2, .6))
    solver = service._sessions[sid].solver
    service.hit(sid, "left_hand", (-.1, .4, .2))
    assert service._sessions[sid].solver is solver
    for _ in range(20):
        snapshot = service.update(sid, 1 / 60)
        assert snapshot["status"] == "PHYSICS", snapshot
        endpoint_alignment(obj, snapshot)
    assert snapshot["elapsed"] > .3
    assert close_matrix(obj.matrix_world, object_matrix)
    for name, endpoints in snapshot["pose"].items():
        initial = initial_world[name]
        direction = Vector(endpoints["tail"]) - Vector(endpoints["head"])
        expected_x = initial.to_3x3().col[1].normalized().rotation_difference(direction.normalized()) @ initial.to_3x3().col[0]
        actual_x = (obj.matrix_world @ obj.pose.bones[name].matrix).to_3x3().col[0]
        assert expected_x.normalized().dot(actual_x.normalized()) > .99999, name
    stopped = service.stop(sid)
    archived = service.read(sid)
    assert archived["status"] == "CANCELLED"
    saved_head = archived["pose"]["hips"]["head"][0]
    stopped["pose"]["hips"]["head"][0] += 100
    archived["pose"]["hips"]["head"][0] += 100
    assert service.read(sid)["pose"]["hips"]["head"][0] == saved_head
    assert all(close_matrix(obj.pose.bones[name].matrix_basis, basis) for name, basis in original.items())


def recovery_goal():
    obj = make_armature("RecoveryGoal")
    sid = service.start(obj, settings={"auto_recover": False, "recovery_duration": .12,
                                      "min_hold": 0, "settle_time": 0, "settle_speed": 1000})
    initial = service.read(sid)
    target = {name: {key: [value[0] + .3, value[1] + .1, value[2] + .2]
                          for key, value in endpoints.items()}
              for name, endpoints in initial["pose"].items()}
    service.hit(sid, "hips", (1, 0, 1))
    service.update(sid, .05)
    solver = service._sessions[sid].solver
    service.set_target(sid, target)
    service.recover(sid)
    assert service._sessions[sid].solver is solver
    for _ in range(180):
        snapshot = service.update(sid, 1 / 60)
        assert snapshot["status"] != "FAILED", snapshot
        endpoint_alignment(obj, snapshot)
        if snapshot["status"] == "COMPLETED":
            break
    assert snapshot["status"] == "COMPLETED", snapshot
    for name, endpoints in target.items():
        for key in ("head", "tail"):
            assert (Vector(endpoints[key]) - Vector(snapshot["pose"][name][key])).length < 2e-4
    assert_rejected(lambda: service.start(obj), "already")
    service.stop(sid, restore=False)
    endpoint_alignment(obj, snapshot)


def selection_and_intermediate_parent():
    obj = make_armature("SelectedBones")
    sid = service.start(obj, ["hips", "left_lower_arm", "left_hand"],
                        {"ground_z": -10, "auto_recover": False})
    assert set(service.read(sid)["pose"]) == {"hips", "left_shoulder", "left_upper_arm", "left_lower_arm", "left_hand"}
    service.hit(sid, "hips", (1, 0, .5))
    service.hit(sid, "left_lower_arm", (.3, .2, .1))
    for _ in range(3):
        snapshot = service.update(sid, 1 / 60)
        assert snapshot["status"] == "PHYSICS", snapshot
        endpoint_alignment(obj, snapshot)
    service.stop(sid)
    assert_rejected(lambda: service.start(obj, ["missing"]), "exists")
    assert_rejected(lambda: service.start(obj, ["hips", "hips"]), "unique")


def natural_full_body_recovery():
    obj = make_armature("NaturalRecovery")
    sid = service.start(obj, settings={"min_hold": .8, "settle_speed": .18,
                                      "settle_time": .3, "timeout": 15})
    initial = service.read(sid)
    service.hit(sid, "hips", (1.5, 0, 1))
    saw_recovery, repeated = False, False
    solver = service._sessions[sid].solver
    for frame in range(2400):
        snapshot = service.update(sid, 1 / 60)
        assert snapshot["status"] != "FAILED", {key: value for key, value in snapshot.items() if key != "pose"}
        endpoint_alignment(obj, snapshot)
        if snapshot["status"] == "RECOVERING":
            saw_recovery = True
            if not repeated:
                service.hit(sid, "left_upper_arm", (.2, .1, .2))
                assert service.read(sid)["status"] == "PHYSICS"
                assert service._sessions[sid].solver is solver
                repeated = True
        if snapshot["status"] == "COMPLETED":
            break
    assert saw_recovery and repeated and snapshot["status"] == "COMPLETED", snapshot
    print(f"Natural recovery completed after {snapshot['elapsed']:.3f}s, repeated hit during recovery", flush=True)
    for name, endpoints in initial["pose"].items():
        for key in ("head", "tail"):
            assert (Vector(endpoints[key]) - Vector(snapshot["pose"][name][key])).length < 2e-4


def per_bone_configuration():
    obj = make_armature("BoneConfiguration")
    obj.data.bones["hips"]["ragdoll_mass"] = 2.3
    obj.data.bones["head"]["ragdoll_radius"] = .18
    obj.data.bones["left_lower_arm"]["ragdoll_limit_min"] = 2
    obj.data.bones["left_lower_arm"]["ragdoll_limit_max"] = 130
    sid = service.start(obj)
    specs = {bone.name: bone for bone in service._sessions[sid].solver.bones}
    assert math.isclose(specs["hips"].mass, 2.3)
    assert math.isclose(specs["head"].radius, .18)
    assert math.isclose(specs["left_lower_arm"].limit.max_angle, math.radians(130))
    json.dumps(service.read(sid))
    obj.data.bones["hips"]["ragdoll_mass"] = 2.4
    assert service.read(sid)["status"] == "FAILED"
    obj.data.bones["hips"]["ragdoll_mass"] = -1
    assert_rejected(lambda: service.start(obj), "mass")


def invalid_recovery_target_is_atomic():
    obj = make_armature("InvalidRecoveryGoal")
    sid = service.start(obj, settings={"auto_recover": False, "ground_z": -10})
    initial = service.read(sid)
    valid = {name: {key: [value[0] + .2, value[1] + .1, value[2] + .3]
                         for key, value in endpoints.items()}
             for name, endpoints in initial["pose"].items()}
    service.set_target(sid, valid)
    service.hit(sid, "hips", (1, 0, .5))
    snapshot = service.update(sid, 1 / 60)
    assert snapshot["status"] == "PHYSICS"
    previous = deepcopy(service._sessions[sid].target_pose)
    solver_target = deepcopy(service._sessions[sid].solver._targets)
    connected = deepcopy(valid)
    for key in ("head", "tail"):
        connected["spine"][key][0] += .2
    for callback in (service.set_target, service.recover):
        assert_rejected(lambda: callback(sid, connected), "connected")
        assert service.read(sid) == snapshot
        assert service._sessions[sid].target_pose == previous
        assert service._sessions[sid].solver._targets == solver_target
    offset = deepcopy(valid)
    for name in ("left_upper_leg", "left_lower_leg", "left_foot", "left_toes"):
        for key in ("head", "tail"):
            offset[name][key][0] += .2
    for callback in (service.set_target, service.recover):
        assert_rejected(lambda: callback(sid, offset), "attachment")
        assert service.read(sid) == snapshot
        assert service._sessions[sid].target_pose == previous
        assert service._sessions[sid].solver._targets == solver_target
    # Failed goal validation leaves the same writer and physics state usable.
    snapshot = service.update(sid, 1 / 60)
    assert snapshot["status"] == "PHYSICS"
    endpoint_alignment(obj, snapshot)


def control_conflicts():
    obj = make_armature("WriterConflicts")
    sid = service.start(obj, settings={"auto_recover": False, "ground_z": -10})
    assert_rejected(lambda: service.start(obj), "already")
    service.hit(sid, "hips", (1, 0, .5))
    service.update(sid, 1 / 60)
    bone = obj.pose.bones["left_hand"]
    bone.matrix_basis = Matrix.Rotation(.7, 4, "Z") @ bone.matrix_basis
    external = bone.matrix_basis.copy()
    snapshot = service.read(sid)
    assert snapshot["status"] == "FAILED", snapshot
    assert "writer" in snapshot["error"]
    assert close_matrix(bone.matrix_basis, external)
    assert not service._sessions
    # This tests a writer running during the service's depsgraph refresh.
    obj = make_armature("RefreshConflict")
    sid = service.start(obj, settings={"auto_recover": False, "ground_z": -10})
    changed = []
    def writer(_scene, _depsgraph):
        if service.is_writing() and not changed:
            changed_bone = obj.pose.bones["left_hand"]
            changed_bone.matrix_basis = Matrix.Rotation(.2, 4, "Z") @ changed_bone.matrix_basis
            changed.append(obj.pose.bones["left_hand"].matrix_basis.copy())
    bpy.app.handlers.depsgraph_update_post.append(writer)
    try:
        snapshot = service.update(sid, 1 / 60)
        assert changed, "Test depsgraph writer did not execute"
        assert snapshot["status"] == "FAILED", snapshot
        assert close_matrix(obj.pose.bones["left_hand"].matrix_basis, changed[0])
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(writer)


def unsupported_targets():
    obj = make_armature("UnsafeTargets")
    obj.scale = (1, 2, 1)
    bpy.context.view_layer.update()
    assert_rejected(lambda: service.start(obj), "uniform")
    obj.scale = (-1, -1, -1)
    bpy.context.view_layer.update()
    assert_rejected(lambda: service.start(obj), "positive")
    obj.scale = (1, 1, 1)
    obj.pose.bones["hips"].scale = (1, 2, 1)
    bpy.context.view_layer.update()
    assert_rejected(lambda: service.start(obj), "bone pose scale")
    obj.pose.bones["hips"].scale = (1, 1, 1)
    obj.animation_data_create().action = bpy.data.actions.new("ConflictAction")
    assert_rejected(lambda: service.start(obj), "animated")
    obj.animation_data_clear()
    obj.driver_add("location", 0)
    assert_rejected(lambda: service.start(obj), "driven")
    obj.animation_data_clear()
    constraint = obj.pose.bones["hips"].constraints.new("COPY_ROTATION")
    assert_rejected(lambda: service.start(obj), "constrained")
    obj.pose.bones["hips"].constraints.remove(constraint)
    # A linked fixture is generated into local test output, never committed.
    path = ROOT / "artifacts" / "ragdoll-linked-fixture.blend"
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.data.libraries.write(str(path), {obj, obj.data})
    with bpy.data.libraries.load(str(path), link=True) as (source, destination):
        destination.objects = [obj.name]
    linked = destination.objects[0]
    bpy.context.scene.collection.objects.link(linked)
    assert_rejected(lambda: service.start(linked), "read-only")


def topology_and_lifecycle():
    obj = make_armature("DeletedTarget")
    sid = service.start(obj)
    name = obj.name
    bpy.data.objects.remove(obj, do_unlink=True)
    replacement = make_armature(name)
    assert service.read(sid)["status"] == "FAILED"
    assert all(bone.location.length == 0 for bone in replacement.pose.bones)
    obj = make_armature("ChangedRest")
    sid = service.start(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    obj.data.edit_bones["left_hand"].tail.x += .1
    bpy.ops.object.mode_set(mode="OBJECT")
    assert service.read(sid)["status"] == "FAILED"
    obj = make_armature("MovedObject")
    sid = service.start(obj)
    obj.location.x += .3
    bpy.context.view_layer.update()
    assert service.read(sid)["status"] == "FAILED"
    assert math.isclose(obj.location.x, .3, abs_tol=1e-6)
    original_scene = bpy.context.scene
    temporary = bpy.data.scenes.new("RagdollTemporaryScene")
    bpy.context.window.scene = temporary
    obj = make_armature("DeletedSceneTarget")
    sid = service.start(obj)
    bpy.context.window.scene = original_scene
    bpy.data.scenes.remove(temporary)
    assert service.read(sid)["status"] == "FAILED"
    obj = make_armature("CleanupTarget")
    original = {bone.name: bone.matrix_basis.copy() for bone in obj.pose.bones}
    sid = service.start(obj, settings={"auto_recover": False})
    service.hit(sid, "hips", (1, 0, 1))
    service.update(sid, .02)
    service.cleanup()
    assert not service._sessions and not service.is_writing()
    assert all(close_matrix(obj.pose.bones[name].matrix_basis, basis) for name, basis in original.items())
    obj = make_armature("RenameTarget")
    original = obj.pose.bones["hips"].matrix_basis.copy()
    sid = service.start(obj, settings={"auto_recover": False})
    service.hit(sid, "hips", (1, 0, 1))
    service.update(sid, .02)
    obj.name = "RenamedTarget"
    obj.data.bones["hips"].name = "RenamedHips"
    assert service.read(sid)["status"] == "FAILED"
    assert close_matrix(obj.pose.bones["RenamedHips"].matrix_basis, original)


def run():
    print(f"Blender {bpy.app.version_string}; source {service.__file__}", flush=True)
    assert bpy.app.version >= (5, 1, 0)
    check("world endpoints, twist, repeated hit, read purity, restore", physics_and_restore)
    check("recovery target, session reuse, terminal pose", recovery_goal)
    check("manual subset with intermediate unselected parent", selection_and_intermediate_parent)
    check("normal full-body landing, recovery, hit during recovery", natural_full_body_recovery)
    check("per-bone mass, radius, limits and config changes", per_bone_configuration)
    check("connected and offset recovery goals reject atomically", invalid_recovery_target_is_atomic)
    check("duplicate and external pose writer conflicts", control_conflicts)
    check("scale, action, driver, constraints, linked target rejection", unsupported_targets)
    check("deletion, topology, object transform, scene, cleanup", topology_and_lifecycle)
    report = {"blender": bpy.app.version_string, "source": service.__file__, "passed": PASSED}
    path = ROOT / "artifacts" / "blender-service-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RAGDOLL_SERVICE_PASS {len(PASSED)} checks", flush=True)


if __name__ == "__main__":
    run()
