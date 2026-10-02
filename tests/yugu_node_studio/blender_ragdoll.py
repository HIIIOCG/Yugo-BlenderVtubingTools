"""Native ragdoll actions, copied pose values, completed recovery and rig leases."""

from copy import deepcopy
from pathlib import Path
import math
import sys

import bpy
from mathutils import Quaternion, Vector
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio, output_directory
addon = load_node_studio()
from yugu_node_studio import extensions as api, flow, workflow, scheduler, workflow_ui
from yugu_node_studio.modules import ragdoll

with RestrictBlend():
    addon.register()
assert "ragdoll" in api.registered_modules()


def status(run_id):
    return workflow.status(run_id)["state"]


def matrix_values(matrix):
    return tuple(float(value) for row in matrix for value in row)


def aligned(rig, snapshot):
    bpy.context.view_layer.update()
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for name, pair in snapshot["pose"].items():
        bone = evaluated.pose.bones[name]
        assert (rig.matrix_world @ bone.head - Vector(pair["head"])).length < 2e-4, name
        assert (rig.matrix_world @ bone.tail - Vector(pair["tail"])).length < 2e-4, name


class Fixture:
    def __init__(self, full=False):
        ragdoll.cleanup("test fixture")
        flow.clear()
        self.example = ragdoll.create_example(bpy.context.scene)
        self.bind()
        if not full:
            self.source.bone_names = "hips"
        self.now = 20.
        self.frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
        self.original = {bone.name: matrix_values(bone.matrix_basis) for bone in self.rig.pose.bones}

    def bind(self):
        for name, value in self.example.items():
            setattr(self, name, value)

    def rebind(self, names):
        self.example = {key: bpy.data.node_groups[value] if key == "tree" else
                       bpy.data.objects[value] if key == "rig" else
                       bpy.data.node_groups[names["tree"]].nodes[value] for key, value in names.items()}
        self.bind()

    def fire(self, name, payload=None):
        return flow.emit_event(self.tree, self.controls, name, payload=payload, now=self.now)

    def tick(self, frames=1):
        for _ in range(frames):
            self.now += 1 / 60
            ragdoll.motion(bpy.context, 1 / 60)
            api.poll(now=self.now)
        assert (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe) == self.frame

    def start(self):
        rid = self.fire("Enter", {"origin": [1, 2, 3]})
        assert status(rid) == "running", workflow.status(rid)
        assert api.transform_owner(self.rig)
        assert ragdoll.get_status(self.apply)["status"] == "PHYSICS"
        return rid

    def close(self):
        ragdoll.cleanup("fixture teardown")
        flow.clear()
        try:
            bpy.data.node_groups.remove(self.tree)
        except ReferenceError:
            pass
        try:
            data = self.rig.data
            bpy.data.objects.remove(self.rig, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)
        except ReferenceError:
            pass
        assert not api.claimed_transforms()


def native_pose_and_repeated_hits():
    fixture = Fixture(full=True)
    try:
        assert len(fixture.rig.data.bones) == 21
        assert ragdoll.get_status(fixture.apply)["status"] == "STOPPED"
        fixture.motion.auto_recover = False
        fixture.motion.ground_z = -10
        rid = fixture.start()
        before = ragdoll.get_status(fixture.apply)
        for _ in range(20):
            values = flow.PullContext(fixture.tree, bpy.context.scene, fixture.now, 10).evaluate(fixture.apply)
            assert isinstance(values["Pose"], ragdoll.PoseValue)
            assert values["Status"]["elapsed"] == 0
            values["Status"]["pose"]["hips"]["head"][0] += 99
        assert ragdoll.get_status(fixture.apply) == before
        sid = before["session_id"]
        solver = ragdoll.service._sessions[sid].solver
        fixture.motion.impulse = (.25, .05, .2)
        assert status(fixture.fire("Hit")) == "end"
        first_velocity = deepcopy(solver.velocities)
        assert status(fixture.fire("Hit")) == "end"
        assert ragdoll.service._sessions[sid].solver is solver
        mass = next(bone.mass for bone in solver.bones if bone.name == "hips")
        for first, second in zip(first_velocity["hips"], solver.velocities["hips"]):
            assert math.dist(second, [first[i] + fixture.motion.impulse[i] / mass for i in range(3)]) < 1e-6
        assert ragdoll.get_status(fixture.apply)["pose"] == before["pose"]
        fixture.tick(5)
        snapshot = ragdoll.get_status(fixture.apply)
        aligned(fixture.rig, snapshot)
        assert snapshot["elapsed"] > 0 and snapshot["pose"] != before["pose"]
        assert status(rid) == "running"
        assert status(fixture.fire("Stop")) == "end"
        fixture.tick()
        assert status(rid) == "cancelled" and not api.transform_owner(fixture.rig)
        assert all(math.dist(matrix_values(fixture.rig.pose.bones[name].matrix_basis), value) < 1e-5
                   for name, value in fixture.original.items())
    finally:
        fixture.close()


def truthful_completion_and_completed_resume():
    fixture = Fixture()
    try:
        fixture.motion.min_hold = .1
        fixture.motion.settle_speed = 1000
        fixture.motion.settle_time = .05
        fixture.motion.recovery_duration = .15
        rid = fixture.start()
        sid = ragdoll.get_status(fixture.apply)["session_id"]
        transitions = set()
        for _ in range(180):
            fixture.tick()
            snapshot = ragdoll.get_status(fixture.apply)
            transitions.add(snapshot["status"])
            if snapshot["status"] != "COMPLETED":
                assert status(rid) == "running", "End preceded completed recovery"
            else:
                break
        assert transitions >= {"PHYSICS", "RECOVERING", "COMPLETED"}, transitions
        assert status(rid) == "end" and api.transform_owner(fixture.rig)
        assert workflow.status(rid)["payload"] == {"origin": [1, 2, 3]}
        aligned(fixture.rig, snapshot)
        assert snapshot["since_hit"] >= .1 and snapshot["settled_elapsed"] >= .05
        fixture.motion.impulse = (.1, 0, .1)
        assert status(fixture.fire("Hit")) == "end"
        current = ragdoll.get_status(fixture.apply)
        assert current["session_id"] == sid and current["status"] == "PHYSICS"
        assert current["pose"] == snapshot["pose"]
        fixture.fire("Stop")
        assert not api.transform_owner(fixture.rig)
    finally:
        fixture.close()


def recover_cancel_hit_and_restart():
    fixture = Fixture()
    try:
        fixture.motion.auto_recover = False
        fixture.motion.min_hold = 0
        fixture.motion.settle_time = 0
        fixture.motion.settle_speed = 1000
        fixture.motion.recovery_duration = .4
        entered = fixture.start()
        recovering = fixture.fire("Recover")
        assert status(recovering) == "running"
        for _ in range(120):
            fixture.tick()
            if ragdoll.get_status(fixture.apply)["status"] == "RECOVERING":
                break
        before = ragdoll.get_status(fixture.apply)
        assert before["status"] == "RECOVERING", before
        sid = before["session_id"]
        solver = ragdoll.service._sessions[sid].solver
        fixture.motion.impulse = (.05, 0, .05)
        fixture.fire("Hit")
        after = ragdoll.get_status(fixture.apply)
        assert after["session_id"] == sid and after["pose"] == before["pose"]
        assert after["status"] == "PHYSICS" and ragdoll.service._sessions[sid].solver is solver
        api.poll(now=fixture.now)
        assert status(recovering) == "cancelled" and status(entered) == "running"
        recovering = fixture.fire("Recover")
        velocities = deepcopy(solver.velocities)
        fixture.fire("Cancel", {"target_run_id": recovering})
        assert status(recovering) == "cancelled" and status(entered) == "running"
        assert solver.velocities == velocities
        assert api.transform_owner(fixture.rig)
        second_enter = fixture.fire("Enter")
        assert status(second_enter) == "failed" and status(entered) == "running"
        fixture.apply.busy_policy = "RESTART"
        api.poll(now=fixture.now)
        assert status(entered) == "cancelled" and not api.transform_owner(fixture.rig)
        first = fixture.start()
        first_session = ragdoll.get_status(fixture.apply)["session_id"]
        second = fixture.start()
        assert status(first) == "cancelled"
        assert ragdoll.get_status(fixture.apply)["session_id"] != first_session
        fixture.fire("Cancel", {"target_run_id": second})
        assert status(second) == "cancelled" and not api.transform_owner(fixture.rig)
    finally:
        fixture.close()


def timeout_and_existing_apply_arbitration():
    fixture = Fixture()
    try:
        fixture.motion.ground_z = -100
        fixture.motion.timeout = .1
        entered = fixture.start()
        fixture.tick(10)
        assert status(entered) == "failed", workflow.status(entered)
        assert not api.transform_owner(fixture.rig)
        assert ragdoll.get_status(fixture.apply)["status"] == "FAILED"
        fixture.motion.auto_recover = False
        fixture.motion.timeout = 15
        rotations = flow.BoneRotations(fixture.rig.name, {"hips": Quaternion((1, 0, 0, 0))})
        ordinary = fixture.tree.nodes.new("YUGUS_NODE_apply_rotation")
        ordinary.armature = fixture.rig
        flow.apply(ordinary, rotations)
        denied = fixture.fire("Enter")
        assert status(denied) == "failed" and not api.transform_owner(fixture.rig)
        flow.release(str(ordinary.runtime_id))
        entered = fixture.start()
        original = matrix_values(fixture.rig.pose.bones["hips"].matrix_basis)
        try:
            flow.apply(ordinary, rotations)
        except ValueError:
            pass
        else:
            raise AssertionError("Ordinary Apply wrote a claimed ragdoll armature")
        assert matrix_values(fixture.rig.pose.bones["hips"].matrix_basis) == original
        fixture.fire("Stop")
        flow.apply(ordinary, rotations)
        flow.release(str(ordinary.runtime_id))
        api.claim_transforms("other module", [fixture.rig])
        assert status(fixture.fire("Enter")) == "failed"
        assert api.transform_owner(fixture.rig) == "other module"
        api.release_owner("other module")
    finally:
        fixture.close()


def command_inputs_respect_enter_policies():
    for policy in ("RESTART", "IGNORE"):
        fixture = Fixture()
        try:
            fixture.apply.busy_policy = policy
            fixture.motion.auto_recover = False
            fixture.motion.ground_z = -10
            entered = fixture.start()
            sid = ragdoll.get_status(fixture.apply)["session_id"]
            assert status(fixture.fire("Hit")) == "end"
            assert status(entered) == "running", "Hit applied the Enter busy policy"
            assert ragdoll.get_status(fixture.apply)["session_id"] == sid
            recovering = fixture.fire("Recover")
            assert status(recovering) == "running" and status(entered) == "running"
            fixture.fire("Hit")
            api.poll(now=fixture.now)
            assert status(recovering) == "cancelled" and status(entered) == "running"
            second_enter = fixture.fire("Enter")
            if policy == "RESTART":
                assert status(entered) == "cancelled" and status(second_enter) == "running"
                assert ragdoll.get_status(fixture.apply)["session_id"] != sid
                entered = second_enter
            else:
                assert status(second_enter) == "end" and status(entered) == "running"
                assert ragdoll.get_status(fixture.apply)["session_id"] == sid
            stopped = fixture.fire("Stop")
            assert status(stopped) == "end" and not api.transform_owner(fixture.rig)
            fixture.tick()
            assert status(entered) == "cancelled" and not ragdoll.service._sessions
        finally:
            fixture.close()


def topology_config_and_competing_writer():
    fixture = Fixture()
    try:
        fixture.motion.auto_recover = False
        fixture.tree.nodes.new("NodeFrame")
        reroute = fixture.tree.nodes.new("NodeReroute")
        fixture.tree.links.remove(next(link for link in fixture.tree.links if link.to_node == fixture.motion))
        incoming = fixture.tree.links.new(fixture.source.outputs["Pose"], reroute.inputs[0])
        fixture.tree.links.new(reroute.outputs[0], fixture.motion.inputs["Pose"])
        entered = fixture.start()
        fixture.tick()
        assert status(entered) == "running"
        fixture.source.mute = True
        fixture.tick()
        assert status(entered) == "cancelled" and not api.transform_owner(fixture.rig)
        fixture.source.mute = False
        entered = fixture.start()
        fixture.motion.damping += .1
        fixture.tick()
        assert status(entered) == "cancelled" and not api.transform_owner(fixture.rig)
        entered = fixture.start()
        fixture.tree.links.remove(incoming)
        fixture.tick()
        assert status(entered) == "cancelled" and not api.transform_owner(fixture.rig)
        fixture.tree.links.new(fixture.source.outputs["Pose"], reroute.inputs[0])
        entered = fixture.start()
        fixture.rig.pose.bones["hips"].location.x = 7
        bpy.context.view_layer.update()
        fixture.tick()
        assert status(entered) == "failed" and not api.transform_owner(fixture.rig)
        assert abs(fixture.rig.pose.bones["hips"].location.x - 7) < 1e-6
        fixture.rig.pose.bones["hips"].location.x = 0
        bpy.context.view_layer.update()
        entered = fixture.start()
        fixture.tree.nodes.remove(fixture.apply)
        fixture.tick()
        assert status(entered) in {"cancelled", "failed"} and not api.transform_owner(fixture.rig)
    finally:
        fixture.close()


def bone_structure_guard_and_sampling_after_cancel():
    fixture = Fixture(full=True)
    try:
        fixture.motion.auto_recover = False
        entered = fixture.start()
        fixture.tick(3)
        fixture.rig.select_set(True)
        bpy.context.view_layer.objects.active = fixture.rig
        bpy.ops.object.mode_set(mode="EDIT")
        fixture.rig.data.edit_bones.remove(fixture.rig.data.edit_bones["left_hand"])
        bpy.ops.object.mode_set(mode="OBJECT")
        fixture.tick()
        assert status(entered) == "failed" and not api.transform_owner(fixture.rig)
        snapshot = ragdoll.get_status(fixture.apply)
        assert snapshot["status"] == "FAILED" and "left_hand" not in snapshot["pose"]
        assert not ragdoll.service._sessions
        entered = fixture.start()
        fixture.fire("Cancel", {"target_run_id": entered})
        values = flow.PullContext(fixture.tree, bpy.context.scene, fixture.now, 0).evaluate(fixture.apply)
        assert values["Status"]["status"] == "CANCELLED" and not values["Running"]
    finally:
        fixture.close()


def replacement_guards_and_default_full_rig_completion():
    fixture = Fixture(full=True)
    try:
        entered = fixture.start()
        for _ in range(1200):
            fixture.tick()
            snapshot = ragdoll.get_status(fixture.apply)
            if snapshot["status"] in {"COMPLETED", "FAILED"}:
                break
        assert snapshot["status"] == "COMPLETED", snapshot
        assert status(entered) == "end"
        aligned(fixture.rig, snapshot)
        fixture.fire("Stop")
        fixture.motion.auto_recover = False
        entered = fixture.start()
        fixture.tick(3)
        replacement_data = fixture.rig.data.copy()
        fixture.rig.data = replacement_data
        preserved = {bone.name: matrix_values(bone.matrix_basis) for bone in fixture.rig.pose.bones}
        fixture.tick()
        assert status(entered) in {"cancelled", "failed"} and not api.transform_owner(fixture.rig)
        assert not ragdoll.service._sessions
        assert all(matrix_values(fixture.rig.pose.bones[name].matrix_basis) == value
                   for name, value in preserved.items()), "Replacement armature data was restored by the old session"
        entered = fixture.start()
        fixture.tick(2)
        old_rig = fixture.rig
        replacement = old_rig.copy()
        replacement.data = old_rig.data.copy()
        old_name = old_rig.name
        bpy.context.scene.collection.objects.link(replacement)
        bpy.data.objects.remove(old_rig, do_unlink=True)
        replacement.name = old_name
        fixture.rig = replacement
        fixture.example["rig"] = replacement
        preserved = {bone.name: matrix_values(bone.matrix_basis) for bone in replacement.pose.bones}
        fixture.tick()
        assert status(entered) in {"cancelled", "failed"} and not api.claimed_transforms()
        assert not ragdoll.service._sessions
        assert all(matrix_values(replacement.pose.bones[name].matrix_basis) == value
                   for name, value in preserved.items()), "Same-name replacement was touched by the old session"
        fixture.source.armature = replacement
        fixture.start()
        fixture.tick()
        fixture.fire("Stop")
        old_owner = api.node_owner("ragdoll", fixture.apply, bpy.context.scene)
        old_sid = ragdoll.get_status(fixture.apply)["session_id"]
        fixture.tree.nodes.remove(fixture.apply)
        fixture.apply = fixture.tree.nodes.new(ragdoll.YUGUS_NODE_apply_ragdoll.bl_idname)
        fixture.example["apply"] = fixture.apply
        fixture.tree.links.new(fixture.motion.outputs["Motion"], fixture.apply.inputs["Motion"])
        for name in ("Enter", "Hit", "Recover", "Stop", "Cancel"):
            fixture.tree.links.new(fixture.controls.outputs[name], fixture.apply.inputs[name])
        fixture.start()
        new_sid = ragdoll.get_status(fixture.apply)["session_id"]
        assert new_sid != old_sid
        values = flow.PullContext(fixture.tree, bpy.context.scene, fixture.now, 0).evaluate(fixture.motion)
        assert values["Status"]["session_id"] == new_sid and values["Status"]["status"] == "PHYSICS"
        fixture.tick()
        assert old_owner not in ragdoll._records, "Deleted Apply history was retained"
        fixture.fire("Stop")
    finally:
        fixture.close()


def saved_lifecycle_and_cards():
    fixture = Fixture()
    try:
        # The bundled service has the same restricted restoration fix as the
        # qualified prototype, even when used directly without adapter records.
        raw_session = ragdoll.service.start(fixture.rig, ["hips"])
        with RestrictBlend():
            raw_result = ragdoll.service.stop(raw_session)
        assert raw_result["status"] == "CANCELLED" and not ragdoll.service._sessions
        raw_session = ragdoll.service.start(fixture.rig, ["hips"])
        with RestrictBlend():
            ragdoll.service.cleanup()
        assert not ragdoll.service._sessions
        entered = fixture.start()
        addon._undo_pre(None)
        assert not api.claimed_transforms() and not workflow.history()
        assert scheduler.is_suspended()
        addon._undo_post(None)
        assert not scheduler.is_suspended() and ragdoll.get_status(fixture.apply)["status"] == "STOPPED"
        entered = fixture.start()
        fixture.tick(3)
        names = {key: value.name for key, value in fixture.example.items()}
        captured = {bone.name: matrix_values(bone.matrix_basis) for bone in fixture.rig.pose.bones}
        path = output_directory() / "studio-ragdoll-saved.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        fixture.rebind(names)
        assert not api.claimed_transforms() and not workflow.history()
        assert ragdoll.get_status(fixture.apply)["status"] == "STOPPED"
        assert all(math.dist(matrix_values(fixture.rig.pose.bones[name].matrix_basis), value) < 1e-5
                   for name, value in captured.items())
        fixture.start()
        before_errors = api.lifecycle_errors()
        with RestrictBlend():
            addon.unregister()
        assert api.lifecycle_errors() == before_errors
        assert not api.claimed_transforms() and not ragdoll._records and not ragdoll.service._sessions
        with RestrictBlend():
            addon.register()
        fixture.rebind(names)
        assert ragdoll.get_status(fixture.apply)["status"] == "STOPPED"
        definition = workflow_ui.new_definition("Ragdoll card declaration")
        view = workflow_ui.cards_from_graph(definition)
        card = workflow_ui.add_card(definition, view.chain, ragdoll.YUGUS_NODE_apply_ragdoll.bl_idname)
        assert workflow_ui.cards_from_graph(definition).supported
        assert api.card_spec(card.bl_idname).label == "Apply Ragdoll"
        bpy.data.node_groups.remove(definition)
    finally:
        fixture.close()


for test in (native_pose_and_repeated_hits, truthful_completion_and_completed_resume,
             recover_cancel_hit_and_restart, timeout_and_existing_apply_arbitration,
             command_inputs_respect_enter_policies,
             topology_config_and_competing_writer, bone_structure_guard_and_sampling_after_cancel,
             replacement_guards_and_default_full_rig_completion,
             saved_lifecycle_and_cards):
    test()
    print("YUGU_RAGDOLL_PASS:", test.__name__)
with RestrictBlend():
    addon.unregister()
assert not api.claimed_transforms()
print("YUGU_RAGDOLL_OK")
