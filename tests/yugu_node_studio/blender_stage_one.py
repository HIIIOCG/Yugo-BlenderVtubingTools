"""Cross-module workflow completion, mode changes and source payload isolation."""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, input_runtime, runtime, workflow, workflow_ui
from yugu_node_studio.constants import TREE_ID


def link(tree, source, output, target, socket="Enter"):
    return tree.links.new(source.outputs[output], target.inputs[socket])


def mode_value(mode, now):
    return flow.PullContext(mode.id_data, bpy.context.scene, now, 0).evaluate(mode)["Mode"]


addon.register()
try:
    root = bpy.data.node_groups.new("Stage One Integration", TREE_ID)
    definition = workflow_ui.new_definition("Reusable Completion")
    view = workflow_ui.cards_from_graph(definition)
    assert view.supported, view.error
    deferred = workflow_ui.add_card(definition, view.chain, "TEST")
    deferred.seconds = .25
    deferred.label = "Deferred original event"
    caller = root.nodes.new("YUGUS_NODE_action_flow")
    caller.workflow_tree = definition
    source = root.nodes.new("YUGUS_NODE_test_event")
    controller = root.nodes.new("YUGUS_NODE_control_mode")
    controller.modes = "Idle, Control"
    controller.initial_mode = "Idle"
    set_mode = root.nodes.new("YUGUS_NODE_set_mode")
    set_mode.mode_name = "Control"
    idle = root.nodes.new("YUGUS_NODE_mode_match")
    idle.mode_name = "Idle"
    link(root, controller, "Mode", set_mode, "Mode")
    link(root, controller, "Mode", idle, "Mode")
    link(root, source, "Trigger", caller)
    link(root, caller, "End", set_mode)

    # Definitions are templates: an update cannot start one on its own.
    flow.tick(10)
    assert not workflow.logs
    assert flow.PullContext(root, bpy.context.scene, 10, 0).evaluate(idle)["State"]

    original = {"message": "first", "nested": {"amount": 12}}
    first = flow.emit_event(root, source, "Trigger", original, now=10)
    original["message"] = "edited"
    original["nested"]["amount"] = 99
    flow.tick(10.1)
    assert flow.PullContext(root, bpy.context.scene, 10.1, 0).evaluate(idle)["State"], "Mode changed before action completion"
    second = flow.emit_event(root, source, "Trigger", {"message": "second"}, now=10.1)
    flow.tick(10.25)
    assert not flow.PullContext(root, bpy.context.scene, 10.25, 0).evaluate(idle)["State"], "Completed flow did not set mode"
    completed = [item for item in workflow.logs if item["state"] == "end"]
    assert len(completed) == 1, completed
    assert completed[0]["payload"] == {"message": "first", "nested": {"amount": 12}}, completed
    assert workflow.status(second)["state"] == "running", "A simultaneous invocation finished too soon"
    flow.tick(10.35)
    completed = [item for item in workflow.logs if item["state"] == "end"]
    assert [item["payload"]["message"] for item in completed] == ["first", "second"]
    assert workflow.status(first)["state"] == "end"
    assert workflow.status(second)["state"] == "end"

    # Mutating a reused definition stops both instances and cannot deliver stale End.
    input_runtime.set_mode(controller, "Idle", now=11)
    flow.emit_event(root, source, "Trigger", {"message": "cancel one"}, now=11)
    flow.emit_event(root, source, "Trigger", {"message": "cancel two"}, now=11.01)
    deferred.mute = True
    flow.tick(11.02)
    flow.tick(12)
    assert flow.PullContext(root, bpy.context.scene, 12, 0).evaluate(idle)["State"], "Cancelled definition delivered stale End"

    # Exercise the actual ordered keyboard -> workflow -> mode pipeline.
    deferred.mute = False
    keyboard = root.nodes.new("YUGUS_NODE_keyboard_data")
    keyboard.listen = True
    key = root.nodes.new("YUGUS_NODE_key_state")
    key.key, key.allowed_modes = "A", "Idle"
    events = root.nodes.new("YUGUS_NODE_state_events")
    release = root.nodes.new("YUGUS_NODE_test_action")
    release.label, release.seconds = "Keyboard release", 0
    link(root, keyboard, "Data", key, "Keyboard")
    link(root, controller, "Mode", key, "Mode")
    link(root, key, "State", events, "State")
    link(root, events, "On", caller)
    link(root, events, "Off", release)
    input_runtime.tick(bpy.context, 0, now=20, manage_backend=False)
    input_runtime.feed_event("A", True, now=20)
    input_runtime.feed_event("A", False, now=20.01)
    input_runtime.tick(bpy.context, .02, now=20.02, manage_backend=False)
    assert any(item["label"] == "Keyboard release" and item["state"] == "end" for item in workflow.logs)
    flow.tick(20.25)
    finished = [item for item in workflow.logs if item["label"] == deferred.label and item["state"] == "end"]
    assert finished[-1]["payload"]["key"] == "A" and finished[-1]["payload"]["down"], finished[-1]
    assert input_runtime.mode_state(controller).name == "Control"

    # Holding a disallowed key cannot become a new press when its mode is enabled.
    count_before = len([item for item in workflow.logs if item["label"] == deferred.label and item["state"] == "running"])
    input_runtime.feed_event("A", True, now=21)
    input_runtime.tick(bpy.context, .01, now=21, manage_backend=False)
    input_runtime.set_mode(controller, "Idle", now=21.01)
    input_runtime.tick(bpy.context, .01, now=21.02, manage_backend=False)
    count_after = len([item for item in workflow.logs if item["label"] == deferred.label and item["state"] == "running"])
    assert count_after == count_before, "Mode activation replayed a held press"
    input_runtime.feed_event("A", False, now=21.03)
    input_runtime.feed_event("A", True, now=21.04)
    input_runtime.tick(bpy.context, .02, now=21.05, manage_backend=False)
    count_after = len([item for item in workflow.logs if item["label"] == deferred.label and item["state"] == "running"])
    assert count_after == count_before + 1

    # There is one shared clock for input and action progress; no runtime timer.
    from yugu_node_studio import scheduler
    assert "flow" in scheduler._motion_callbacks
    assert not bpy.app.timers.is_registered(runtime._timer)
    assert not any(name == "Key Bind" for _, name in dict(addon.nodes.NODE_MENU)["Sources"])
    for section in ("Keyboard & State", "Actions"):
        for node_type, label in dict(addon.nodes.NODE_MENU)[section]:
            assert root.nodes.new(node_type).bl_label == label
    print("STAGE_ONE_INTEGRATION_OK")
finally:
    flow.clear()
    addon.unregister()
