"""Native graph, writer lifecycle, real Undo/Redo and saved inactive graphs."""
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yugo_ragdoll
from yugo_ragdoll import preview, service
from fixtures import make_armature


def graph():
    arm = make_armature()
    tree = bpy.data.node_groups.new("Preview Test", preview.TREE_ID)
    tree.use_fake_user = True
    source = tree.nodes.new(preview.SOURCE_ID)
    source.armature = arm
    motion = tree.nodes.new(preview.RAGDOLL_ID)
    motion.auto_recover = False
    apply = tree.nodes.new(preview.APPLY_ID)
    trigger = tree.nodes.new(preview.TRIGGER_ID)
    tree.links.new(source.outputs["Pose"], motion.inputs["Pose"])
    tree.links.new(motion.outputs["Motion"], apply.inputs["Motion"])
    tree.links.new(trigger.outputs["Tick"], apply.inputs["Tick"])
    return arm, tree, source, motion, apply, trigger


def main():
    yugo_ragdoll.register()
    scene = bpy.context.scene
    scene.frame_set(42, subframe=.25)
    arm, tree, source, motion, apply, trigger = graph()
    original = {b.name: tuple(x for row in b.matrix_basis for x in row) for b in arm.pose.bones}
    initial = preview.start(tree, apply)
    assert initial["status"] == "PHYSICS"
    assert preview.read(tree, apply) == initial
    preview.hit(tree, motion)
    for _ in range(20):
        result = preview.step(tree, apply, 1 / 60)
        assert result["status"] == "PHYSICS", result
    assert scene.frame_current == 42 and abs(scene.frame_subframe - .25) < 1e-6
    before = preview.read(tree, apply)
    for _ in range(10):
        assert preview.read(tree, apply) == before
    # Hit parameters change without rebuilding the body or revoking ownership.
    motion.impulse = (-1, 0, 1)
    added = preview.hit(tree, motion)
    assert added["session_id"] == before["session_id"]
    assert added["pose"] == before["pose"]
    preview.play(tree, apply)
    assert bpy.app.timers.is_registered(preview._timer)
    preview.pause(tree, apply)
    assert not bpy.app.timers.is_registered(preview._timer)
    preview.stop(tree, apply)
    assert not preview._sessions
    for b in arm.pose.bones:
        actual = tuple(x for row in b.matrix_basis for x in row)
        assert max(abs(a-b) for a,b in zip(actual, original[b.name])) < 1e-5
    # Data changes and link changes release a paused preview.
    preview.start(tree, apply)
    motion.ground_z = -.1
    assert not preview._sessions
    preview.start(tree, apply)
    tree.links.remove(apply.inputs["Motion"].links[0])
    tree.update()
    assert not preview._sessions
    tree.links.new(motion.outputs["Motion"], apply.inputs["Motion"])
    preview.start(tree, apply)
    tree.nodes.remove(motion)
    assert not preview._sessions
    # Socket types are semantic: status cannot be consumed as native motion.
    arm, tree, source, motion, apply, trigger = graph()
    tree.links.remove(apply.inputs["Motion"].links[0])
    tree.links.new(motion.outputs["Status"], apply.inputs["Motion"])
    try:
        preview.start(tree, apply)
        raise AssertionError("Status socket was accepted as Motion")
    except ValueError:
        pass
    assert not preview._sessions and not service._sessions
    # Independent graphs retain their own writers when an unused node is deleted.
    arm_a, tree_a, _, _, apply_a, _ = graph()
    arm_b, tree_b, _, motion_b, apply_b, _ = graph()
    preview.start(tree_a, apply_a)
    preview.start(tree_b, apply_b)
    unused = tree_b.nodes.new(preview.TRIGGER_ID)
    unused.hz = "8"
    tree_b.nodes.remove(unused)
    assert len(preview._sessions) == 2
    tree_b.nodes.remove(motion_b)
    assert len(preview._sessions) == 1
    assert preview.read(tree_a, apply_a)["status"] == "PHYSICS"
    preview.stop(tree_a, apply_a)
    assert not preview._sessions
    # Depsgraph validation also revokes a paused writer after external edits.
    arm, tree, source, motion, apply, trigger = graph()
    preview.start(tree, apply)
    arm.pose.bones["hips"].location.x += .25
    external = arm.pose.bones["hips"].location.x
    bpy.context.view_layer.update()
    assert not preview._sessions and apply.status == "FAILED", apply.message
    assert abs(arm.pose.bones["hips"].location.x - external) < 1e-6
    arm, tree, source, motion, apply, trigger = graph()
    preview.play(tree, apply)
    bpy.data.objects.remove(arm, do_unlink=True)
    bpy.context.view_layer.update()
    assert not preview._sessions and not preview._playing
    assert not bpy.app.timers.is_registered(preview._timer)
    # Native Undo/Redo clears runtime, preserving the saved graph.
    arm, tree, source, motion, apply, trigger = graph()
    tree_name, arm_name = tree.name, arm.name
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Ragdoll graph")
    preview.start(tree, apply)
    preview.step(tree, apply, .1)
    bpy.ops.ed.undo_push(message="Ragdoll simulated pose")
    assert bpy.ops.ed.undo.poll()
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not preview._sessions and not service._sessions
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert not preview._sessions and not service._sessions
    tree = bpy.data.node_groups[tree_name]
    apply = next(n for n in tree.nodes if n.bl_idname == preview.APPLY_ID)
    preview.start(tree, apply)
    preview.play(tree, apply)
    output = ROOT / "artifacts" / "preview-lifecycle.blend"
    output.parent.mkdir(parents=True, exist_ok=True)
    assert bpy.ops.wm.save_as_mainfile(filepath=str(output)) == {"FINISHED"}
    assert not preview._sessions and not service._sessions
    assert not bpy.app.timers.is_registered(preview._timer)
    assert bpy.ops.wm.open_mainfile(filepath=str(output)) == {"FINISHED"}
    assert not preview._sessions and not service._sessions
    assert not bpy.app.timers.is_registered(preview._timer)
    tree = bpy.data.node_groups[tree_name]
    apply = next(n for n in tree.nodes if n.bl_idname == preview.APPLY_ID)
    preview.start(tree, apply)
    yugo_ragdoll.unregister()
    assert not preview._sessions and not service._sessions
    assert not bpy.app.timers.is_registered(preview._timer)
    assert preview._cleanup_handler not in bpy.app.handlers.undo_pre
    assert not hasattr(bpy.types, "YUGORAG_NT_preview")
    print(json.dumps({"result": "PREVIEW_OK", "groups": 12, "frame": 42.25}))


if __name__ == "__main__":
    main()
