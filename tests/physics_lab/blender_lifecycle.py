"""Native Blender lifecycle regression; run with an isolated task profile.

Manual timer calls verify callback behavior while the timeline stays stopped.
They do not claim wall-clock event-loop coverage. Undo/Redo emits an independent
result marker, since some Blender background contexts cannot execute Undo.
"""

from pathlib import Path
import os
import sys

import bpy


sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("YUGO_TEST_REPOSITORY", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(ROOT / "addons"))

import yugo_physics_lab as addon
from yugo_physics_lab import preview, service


assert Path(addon.__file__).resolve() == ROOT / "addons" / "yugo_physics_lab" / "__init__.py"
print(f"YUGO_TEST_SOURCE_OK: {Path(addon.__file__).resolve()}")


def make_fixture():
    """Create one unconstrained native mesh, outside all other runtime scopes."""
    scene = bpy.context.scene
    collection = bpy.data.collections.new("PhysicsLifecycleBodies")
    scene.collection.children.link(collection)
    mesh = bpy.data.meshes.new("PhysicsLifecycleMesh")
    mesh.from_pydata(
        [(-.2, -.2, -.2), (.2, -.2, -.2), (.2, .2, -.2), (-.2, .2, -.2),
         (-.2, -.2, .2), (.2, -.2, .2), (.2, .2, .2), (-.2, .2, .2)],
        [],
        [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
         (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    )
    body = bpy.data.objects.new("PhysicsLifecycleBody", mesh)
    collection.objects.link(body)
    body.location = (0, 0, 3)
    left = bpy.data.objects.new("PhysicsLifecycleLeftAnchor", None)
    right = bpy.data.objects.new("PhysicsLifecycleRightAnchor", None)
    scene.collection.objects.link(left)
    scene.collection.objects.link(right)
    left.location = (-.5, 0, 3)
    right.location = (.5, 0, 3)
    settings = scene.yugo_physics_lab
    settings.bodies = collection
    settings.target = body
    settings.left_anchor = left
    settings.right_anchor = right
    settings.use_floor = False
    settings.gravity = (0, 0, -9.81)
    scene.frame_set(37, subframe=.375)
    bpy.context.view_layer.update()
    return scene, settings, body


def frame_stamp():
    scene = bpy.context.scene
    return scene.frame_current, scene.frame_subframe


def assert_stopped(body=None, *, cleared=False):
    assert not service.active(), "Runtime survived a stop/replacement"
    assert not bpy.app.timers.is_registered(preview._timer), "Preview timer survived shutdown"
    assert preview._last_time is None, "Timer clock survived shutdown"
    if cleared:
        assert service._session is None, "Runtime snapshots survived lifecycle clear"


def assert_handlers(count):
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        assert handlers.count(preview._before_replace) == count, "Lifecycle handler duplicated/leaked"


def assert_no_retained_rna(value, seen=None):
    """Catch accidental RNA retention inside the complete service session."""
    assert not isinstance(value, bpy.types.bpy_struct), "Runtime retained Blender RNA"
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        children = list(value.keys()) + list(value.values())
    elif isinstance(value, (list, tuple, set)):
        children = value
    else:
        children = vars(value).values()
    for child in children:
        assert_no_retained_rna(child, seen)


def real_undo_redo():
    """Replace native RNA using Blender's operators, when supported by context."""
    scene = bpy.context.scene
    body = bpy.data.objects["PhysicsLifecycleBody"]
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Physics Lab Lifecycle Baseline")
    body.location.x = 2
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="Physics Lab Lifecycle Moved")
    preview.start_preview(scene, scene.yugo_physics_lab)
    if not bpy.ops.ed.undo.poll():
        preview.stop_preview(clear=True)
        print("YUGO_PHYSICS_LAB_UNDO_NOT_RUN: Blender context does not support Undo")
        if "--require-undo" in sys.argv:
            raise AssertionError("Real Undo coverage required but unavailable")
        return False
    assert bpy.ops.ed.undo() == {"FINISHED"}
    body = bpy.data.objects["PhysicsLifecycleBody"]
    assert_stopped(body, cleared=True)
    assert abs(body.location.x) < 1e-6, "Undo did not replace the edited state"
    scene = bpy.context.scene
    preview.start_preview(scene, scene.yugo_physics_lab)
    assert bpy.ops.ed.redo.poll(), "Redo unavailable after successful Undo"
    assert bpy.ops.ed.redo() == {"FINISHED"}
    body = bpy.data.objects["PhysicsLifecycleBody"]
    assert_stopped(body, cleared=True)
    assert abs(body.location.x - 2) < 1e-6, "Redo did not restore the edited state"
    print("YUGO_PHYSICS_LAB_UNDO_OK")
    return True


def main():
    addon.register()
    try:
        assert_stopped()
        assert_handlers(1)
        addon.register()
        assert_handlers(1)
        assert_stopped()
        scene, settings, body = make_fixture()
        stamp = frame_stamp()
        home_z = body.matrix_world.translation.z

        preview.start_preview(scene, settings)
        assert service.active()
        assert bpy.app.timers.is_registered(preview._timer)
        try:
            preview.start_preview(scene, settings)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Repeated Start must refuse duplicate target ownership")
        assert service.active(), "Rejected duplicate Start stopped the original session"
        assert_no_retained_rna(service._session)
        for _ in range(12):
            service.update(1 / 60)
            assert service.active(), f"Ungrabbed configured anchors stopped the preview: {service.status()}"
            assert frame_stamp() == stamp, "Physics advanced Blender's animation frame"
        assert body.matrix_world.translation.z < home_z - .01, "Stopped-frame physics did not move"
        preview._last_time -= 1 / 60
        assert preview._timer() is not None
        assert frame_stamp() == stamp, "Preview callback advanced Blender's animation frame"

        preview.stop_preview()
        assert_stopped()
        retained = service.body_state(body)
        assert retained is not None, "Ordinary Stop unexpectedly removed session homes"
        preview.stop_preview()
        assert_stopped()
        preview.start_preview(scene, settings)
        preview._before_replace(None)
        assert_stopped(body, cleared=True)
        assert_handlers(1)

        real_undo_redo()
        scene = bpy.context.scene
        settings = scene.yugo_physics_lab
        body = bpy.data.objects["PhysicsLifecycleBody"]
        stamp = frame_stamp()
        preview.start_preview(scene, settings)
        service.update(1 / 60)
        assert frame_stamp() == stamp
        saved = ROOT / "artifacts" / "physics-lifecycle.blend"
        saved.parent.mkdir(parents=True, exist_ok=True)
        assert bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False) == {"FINISHED"}
        assert service.active(), "Saving silently stopped the explicit preview"
        assert bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False) == {"FINISHED"}
        body = bpy.data.objects["PhysicsLifecycleBody"]
        assert_stopped(body, cleared=True)
        assert frame_stamp() == stamp
        assert_handlers(1)
        assert bpy.context.scene.yugo_physics_lab.bodies is not None, "Saved parameters were lost"

        # Explicit Start after reopening is the only way to resume the preview.
        scene = bpy.context.scene
        settings = scene.yugo_physics_lab
        preview.start_preview(scene, settings)
        assert service.active()
        preview.stop_preview(clear=True)
        assert_stopped(body, cleared=True)
        preview.start_preview(scene, settings)
        addon.unregister()
        assert_stopped()
        assert_handlers(0)
        assert not hasattr(bpy.types.Scene, "yugo_physics_lab")
        addon.unregister()
        assert_handlers(0)
        addon.register()
        assert_handlers(1)
        assert_stopped()
        print("YUGO_PHYSICS_LAB_LIFECYCLE_OK")
    finally:
        addon.unregister()
        assert_stopped()
        assert_handlers(0)


if __name__ == "__main__":
    main()
