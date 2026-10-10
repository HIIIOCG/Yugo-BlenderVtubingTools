"""Save live Apply channels in an isolated factory-startup Blender.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1.
Only temporary files inside the runner's task output directory are written.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True

import bpy
from mathutils import Quaternion, Vector

from _bootstrap import ROOT, load_node, output_directory

addon = load_node()
_package_directory = None


def same(actual, expected):
    if isinstance(expected, str):
        assert actual == expected, (actual, expected)
    elif isinstance(expected, (int, float)):
        assert abs(actual - expected) < 1e-6, (actual, expected)
    else:
        assert len(actual) == len(expected), (actual, expected)
        for first, second in zip(actual, expected):
            same(first, second)


def channels():
    return {target: addon.flow._snapshot_target(target) for target in TARGETS}


def assert_channels(expected):
    for target, value in channels().items():
        same(value, expected[target])


assert bpy.app.background, "Use a disposable background Blender"
addon.register()
try:
    armature = bpy.data.objects.new("SaveLifecycleRig", bpy.data.armatures.new("SaveLifecycleBones"))
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.data.edit_bones.new("Body")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.location = (1, 2, 3)
    armature.rotation_mode = "XYZ"
    armature.rotation_euler = (0.1, 0.2, 0.3)
    bone = armature.pose.bones["Body"]
    bone.location = (0.1, 0.2, 0.3)
    bone.rotation_mode = "AXIS_ANGLE"
    bone.rotation_axis_angle = (0.2, 0, 1, 0)
    mesh = bpy.data.meshes.new("SaveLifecycleMesh")
    mesh.from_pydata([(0, 0, 0)], [], [])
    obj = bpy.data.objects.new("SaveLifecycleFace", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Smile").value = 0.25
    TARGETS = (("bone", armature.name, "Body"), ("bone_location", armature.name, "Body"),
               ("object", armature.name, ""), ("object_location", armature.name, ""),
               ("shape", obj.name, "Smile"))
    authored = channels()
    tree = bpy.data.node_groups.new("SaveLifecycleTree", "YUGUS_NT_humanoid_motion")
    tree.use_fake_user = True
    body_apply = tree.nodes.new("YUGUS_NODE_apply_rotation")
    face_apply = tree.nodes.new("YUGUS_NODE_apply_shapes")
    body_uid, face_uid = str(body_apply.runtime_id), str(face_apply.runtime_id)
    packet = addon.flow.BoneTransforms(
        armature.name, {"Body": addon.data.HumanoidTransform(
            Quaternion((1, 0, 0), 0.8), Vector((0.4, 0.5, 0.6)))},
        root=addon.data.HumanoidTransform(Quaternion((0, 0, 1), 0.9), Vector((4, 5, 6))))
    addon.flow.apply(body_apply, packet)
    addon.flow.apply(face_apply, addon.flow.ShapeValues({(obj.name, "Smile"): 0.75}))
    preview = channels()
    button = tree.nodes.new("YUGUS_NODE_button_trigger")
    delay = tree.nodes.new("YUGUS_NODE_flow_delay")
    delay.seconds = 10
    tree.links.new(button.outputs["Trigger"], delay.inputs["Enter"])
    addon.flow.emit_event(tree, button, "Trigger", now=10)
    delay_uid = str(delay.runtime_id)
    pending = dict(addon.flow._events[delay_uid])
    trace_node = tree.nodes.new(addon.physics.SOLVER)
    trace_uid = str(trace_node.runtime_id)
    trace = {"uid": trace_uid, "tree": tree.name, "node": trace_node.name,
             "active": True, "lease": "save-test-trace", "frames": [{"sample": 1}]}
    addon.spring_trace._records[trace_uid] = trace

    seen = []
    save_obstacle = None

    def inspect_saved_channels(_unused):
        seen.append(channels())
        if save_obstacle is not None:
            # Fail the actual write after save_pre has exposed authored values.
            save_obstacle.mkdir()

    bpy.app.handlers.save_pre.append(inspect_saved_channels)
    temporary_root = output_directory()
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yugo_save_lifecycle_", dir=temporary_root) as directory:
        assert Path(directory).resolve().is_relative_to(temporary_root)
        path = Path(directory) / "authored.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        for target, value in seen[-1].items():
            same(value, authored[target])
        assert_channels(preview)
        assert body_uid in addon.flow._owners and face_uid in addon.flow._owners
        assert addon.flow._events[delay_uid] == pending
        assert addon.spring_trace._records[trace_uid] is trace
        assert not trace["active"] and trace["frames"] == [{"sample": 1}]
        assert not addon.scheduler.is_suspended() and addon._save_state is None

        # Handler exceptions cannot cancel Blender's save. A failed cleanup
        # must therefore leave the independent Apply restoration running.
        original_clear = addon.physics.clear

        def failed_cleanup():
            raise RuntimeError("Injected physics cleanup failure")

        addon.physics.clear = failed_cleanup
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / "cleanup-failed.blend"),
                                      check_existing=False)
        finally:
            addon.physics.clear = original_clear
        for target, value in seen[-1].items():
            same(value, authored[target])
        assert_channels(preview)
        assert not addon.scheduler.is_suspended() and addon._save_state is None

        # One inaccessible target must not re-expose other restored targets.
        original_restore = addon.flow._restore_target

        def failed_shape_restore(target, value):
            if target == TARGETS[-1] and value == authored[target]:
                raise RuntimeError("Injected shape restoration failure")
            original_restore(target, value)

        addon.flow._restore_target = failed_shape_restore
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / "one-target-failed.blend"),
                                      check_existing=False)
        finally:
            addon.flow._restore_target = original_restore
        for target, value in seen[-1].items():
            same(value, preview[target] if target == TARGETS[-1] else authored[target])
        assert_channels(preview)
        assert not addon.scheduler.is_suspended() and addon._save_state is None

        # A failed native save must run save_post_fail and restore the preview.
        failed = Path(directory) / "failed.blend"
        save_obstacle = Path(str(failed) + "@")
        before_failure = len(seen)
        try:
            result = bpy.ops.wm.save_as_mainfile(filepath=str(failed), check_existing=False)
            assert result == {"CANCELLED"}, result
        except RuntimeError:
            pass
        save_obstacle = None
        assert len(seen) == before_failure + 1
        for target, value in seen[-1].items():
            same(value, authored[target])
        assert_channels(preview)
        assert not addon.scheduler.is_suspended() and addon._save_state is None
        assert addon.flow._events[delay_uid] == pending
        assert trace_uid in addon.spring_trace._records

        # Saving during an intentional pause must leave that pause in place.
        addon.scheduler.suspend()
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        assert addon.scheduler.is_suspended()
        assert_channels(preview)
        addon.scheduler.resume()

        addon.flow.release(body_uid)
        addon.flow.release(face_uid)
        assert_channels(authored)
        bpy.ops.wm.open_mainfile(filepath=str(path))
        assert_channels(authored)
        assert not addon.flow._owners and not addon.scheduler.is_suspended()
    print("YUGO SAVE LIFECYCLE PASSED", bpy.app.version_string)
finally:
    if "inspect_saved_channels" in globals() and inspect_saved_channels in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(inspect_saved_channels)
    addon.unregister()
    assert addon._save_state is None
    assert addon._save_post not in bpy.app.handlers.save_post
    assert addon._save_post not in bpy.app.handlers.save_post_fail
