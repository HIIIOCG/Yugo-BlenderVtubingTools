"""Real GUI checks for finite modal preview, Stop and node-deletion cleanup.

Launch in a separate factory-startup Blender process, WITHOUT --background, with
task-specific BLENDER_USER_* and FLOATING_UI_ARTIFACTS directories. The runner
must hide that process's window and inspect gui_result.json, because Blender can
exit successfully even when this script reports a failed assertion.

These short-lived application timers belong only to this test harness. The
add-on's modal event timer must run through Blender's actual GUI event loop.
"""

import json
from pathlib import Path
import sys
import time
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture import artifacts_dir, make_template
from yugo_floating_ui import preview
from yugo_floating_ui.service import OWNER


_state = {"phase": "startup", "startup_time": time.monotonic()}


def _require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def _operator(name, **kwargs):
    with bpy.context.temp_override(
        window=_state["window"], area=_state["area"], region=_state["region"]
    ):
        return getattr(bpy.ops.yugofui, name)(**kwargs)


def _start_play(seconds):
    _require(_operator("play", seconds=seconds) == {"RUNNING_MODAL"},
             "Play must start an actual modal operator in the GUI")
    _require(len(preview._sessions) == 1, "Play must own exactly one preview session")
    session = next(iter(preview._sessions.values()))
    _require(session._timer is not None, "Play must create a GUI event timer")
    return session


def _assert_finished(session, label):
    _require(not preview._sessions, f"{label}: no preview sessions may remain")
    _require(session._finished, f"{label}: session must be marked finished")
    _require(session._timer is None, f"{label}: GUI event timer must be removed")


def _finish(status, detail):
    """Report first, then quit this script's own isolated Blender process."""
    try:
        preview.unregister()
    except Exception:
        status = "failed"
        detail += "\nCleanup failed:\n" + traceback.format_exc()
    result = {
        "status": status,
        "detail": detail,
        "blender_version": bpy.app.version_string,
    }
    path = artifacts_dir() / "gui_result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FLOATING_UI_GUI_RESULT " + json.dumps(result, ensure_ascii=True), flush=True)
    bpy.ops.wm.quit_blender()


def _setup():
    _require(not bpy.app.background, "GUI validation requires Blender without --background")
    temporary = artifacts_dir() / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(temporary)
    windows = tuple(bpy.context.window_manager.windows)
    if not windows:
        _require(time.monotonic() - _state["startup_time"] < 10.0,
                 "Blender did not create a GUI window within 10 seconds")
        return 0.1
    preview.register()
    window = windows[0]
    area = max(window.screen.areas, key=lambda candidate: candidate.width * candidate.height)
    area.type = "NODE_EDITOR"
    area.spaces.active.tree_type = preview.TREE_ID
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    _require(region is not None, "The GUI node editor must have a WINDOW region")
    _state.update(window=window, area=area, region=region)

    scene = window.scene
    template, _parts = make_template(name="GUI Test Template", transformed=True)
    anchor = bpy.data.objects.new("GUI Test Anchor", None)
    scene.collection.objects.link(anchor)
    anchor.location = (2.0, -1.0, 0.5)
    tree = bpy.data.node_groups.new("GUI Floating UI Preview", preview.TREE_ID)
    source = tree.nodes.new(preview.SOURCE_ID)
    display = tree.nodes.new(preview.DISPLAY_ID)
    source.location = (-360, 0)
    display.location = (0, 0)
    tree.links.new(source.outputs[0], display.inputs[0])
    display.template = template
    display.anchor = anchor
    display.spacing = 1.4
    display.max_messages = 3
    display.move_duration = 0.1
    area.spaces.active.node_tree = tree
    _state.update(tree=tree, source=source, display=display, scene=scene)

    source.text, source.author = "First GUI message", "Viewer One"
    _require(_operator("send", tree_name=tree.name, node_name=source.name) == {"FINISHED"},
             "First GUI Send must succeed")
    source.text, source.author = "Second GUI message", "Viewer Two"
    _require(_operator("send", tree_name=tree.name, node_name=source.name) == {"FINISHED"},
             "Second GUI Send must succeed")
    service = preview.get_service(display, scene)
    _require(len(service.instances) == 2, "Send must create two native message rows")
    oldest_id = service.model.messages[0].id
    _state.update(service=service, oldest_id=oldest_id,
                  initial_y=service.instances[oldest_id].root.location.y)
    _state["finite_session"] = _start_play(0.2)
    _state["phase"] = "finite_finished"
    return 0.5


def _check_finite():
    _assert_finished(_state["finite_session"], "Finite Play")
    service = _state["service"]
    current_y = service.instances[_state["oldest_id"]].root.location.y
    _require(current_y > _state["initial_y"] + 0.1,
             "The actual modal event loop must advance native row movement")
    _require(abs(current_y - 1.4) < 0.001,
             "Finite modal playback must finish the older row's smooth movement")

    _state["stopped_session"] = _start_play(10.0)
    _require(_operator("stop") == {"FINISHED"}, "Explicit Stop must succeed")
    _assert_finished(_state["stopped_session"], "Explicit Stop")
    _state["phase"] = "stopped"
    return 0.1


def _check_stop_and_delete():
    _assert_finished(_state["stopped_session"], "Stop after another GUI cycle")
    _require(len(_state["service"].instances) == 2,
             "Stopping playback must preserve already displayed messages")
    _state["deleted_session"] = _start_play(10.0)
    _state["tree"].nodes.remove(_state["display"])
    _state["display"] = None
    _state["phase"] = "deleted"
    return 0.1


def _check_deleted():
    _assert_finished(_state["deleted_session"], "Display-node deletion")
    _require(not preview._services, "Deleting the display node must release its service")
    _require(_state["service"].closed, "Deleting the display must close the service")
    for database in (bpy.data.objects, bpy.data.collections, bpy.data.curves,
                     bpy.data.meshes, bpy.data.materials, bpy.data.images):
        _require(not any(block.get(OWNER) for block in database),
                 "Display-node deletion must remove all owned generated outputs and data")
    _finish("passed", "Real GUI finite Play, native movement, Stop and display-deletion cleanup passed")
    return None


def _tick():
    try:
        phase = _state["phase"]
        if phase == "startup":
            return _setup()
        if phase == "finite_finished":
            return _check_finite()
        if phase == "stopped":
            return _check_stop_and_delete()
        if phase == "deleted":
            return _check_deleted()
        raise AssertionError("Unknown GUI-test phase: " + phase)
    except Exception:
        _finish("failed", traceback.format_exc())
        return None


if __name__ == "__main__":
    # Initial window/editor state is not yet ready when startup scripts execute.
    bpy.app.timers.register(_tick, first_interval=0.25)
