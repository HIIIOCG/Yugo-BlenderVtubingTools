"""Active disable and repeated enable under Blender's real extension contract."""
from pathlib import Path
import sys

import bpy
from _bpy_restrict_state import RestrictBlend

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import yugo_ragdoll as addon
from yugo_ragdoll import preview, service
from fixtures import make_armature
from build_demo import make_graph

assert Path(addon.__file__).resolve().parent == (ROOT / "addons/yugo_ragdoll").resolve()


def capture(obj):
    return {bone.name: bone.matrix_basis.copy() for bone in obj.pose.bones}


def difference(saved, obj):
    return max(abs(old[row][column] - obj.pose.bones[name].matrix_basis[row][column])
               for name, old in saved.items() for row in range(4) for column in range(4))


def no_runtime():
    assert not service._sessions and not preview._sessions and not preview._playing
    assert not bpy.app.timers.is_registered(preview._timer)


def no_handlers():
    for label in ("undo_pre", "redo_pre", "load_pre", "save_pre"):
        assert preview._cleanup_handler not in getattr(bpy.app.handlers, label)
    assert preview._check_handler not in bpy.app.handlers.depsgraph_update_post


def socket_callbacks(tree, source, motion):
    # Registered RNA socket subclasses can remove their parent's callbacks.
    # Exercise the native instances after every complete class registration.
    class Labels:
        def __init__(self):
            self.text = []

        def label(self, *, text):
            self.text.append(text)

    trigger = next(node for node in tree.nodes if node.bl_idname == preview.TRIGGER_ID)
    for socket, node in ((source.outputs["Pose"], source),
                         (motion.outputs["Motion"], motion),
                         (motion.outputs["Status"], motion),
                         (trigger.outputs["Tick"], trigger)):
        assert callable(getattr(socket, "draw", None)), socket.bl_idname
        assert callable(getattr(socket, "draw_color", None)), socket.bl_idname
        layout = Labels()
        socket.draw(bpy.context, layout, node, socket.name)
        assert layout.text == [socket.name]
        color = socket.draw_color(bpy.context, node)
        assert len(color) == 4 and all(0 <= value <= 1 for value in color)


obj = make_armature("Restricted lifecycle fixture")
sid = service.start(obj, settings={"auto_recover": False})
service.hit(sid, "hips", (1, 0, .5))
service.update(sid, 1 / 60)
last_pose = capture(obj)
with RestrictBlend():
    cancelled = service.stop(sid)
assert cancelled["status"] == "CANCELLED" and not service._sessions
assert difference(last_pose, obj) < 1e-6, "Restricted stop touched inaccessible pose data"
sid = service.start(obj, settings={"auto_recover": False})
service.hit(sid, "hips", (1, 0, .5))
service.update(sid, 1 / 60)
with RestrictBlend():
    service.cleanup()
assert not service._sessions and not service._finished
print("RAGDOLL_RESTRICTED_SERVICE_PASS", flush=True)

for cycle in range(3):
    with RestrictBlend():
        addon.register()
        addon.register()
    for label in ("undo_pre", "redo_pre", "load_pre", "save_pre"):
        assert getattr(bpy.app.handlers, label).count(preview._cleanup_handler) == 1
    assert bpy.app.handlers.depsgraph_update_post.count(preview._check_handler) == 1
    tree, source, motion, apply = make_graph(obj)
    socket_callbacks(tree, source, motion)
    motion.auto_recover = False
    source.bone_names = "hips,spine,chest,neck,head"
    home = capture(obj)
    preview.play(tree, apply)
    preview.hit(tree, motion)
    preview.step(tree, apply, 1 / 60)
    assert difference(home, obj) > 1e-4
    assert preview.stop(tree, apply)["status"] == "CANCELLED"
    assert difference(home, obj) < 1e-6, "Ordinary Stop no longer restores the captured pose"
    no_runtime()
    preview.play(tree, apply)
    preview.hit(tree, motion)
    preview.step(tree, apply, 1 / 60)
    assert service._sessions and bpy.app.timers.is_registered(preview._timer)
    last_pose = capture(obj)
    with RestrictBlend():
        addon.unregister()
        addon.unregister()
    no_runtime()
    no_handlers()
    assert difference(last_pose, obj) < 1e-6
    print("RAGDOLL_RESTRICTED_EXTENSION_PASS", cycle, flush=True)

print("RAGDOLL_RESTRICTED_LIFECYCLE_PASS", bpy.app.version_string, flush=True)
