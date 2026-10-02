"""Open the delivered example and execute its saved graph after load reset."""
from pathlib import Path
import sys

import bpy

project = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import example_path, load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, input_runtime, workflow, workflow_ui

addon.register()
try:
    bpy.ops.wm.open_mainfile(filepath=str(example_path()))
    tree = bpy.context.scene.yugu_node_studio_tree
    assert tree is not None
    caller = next(node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_action_flow")
    assert workflow_ui.cards_from_graph(caller.workflow_tree).supported
    keyboard = next(node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_keyboard_data")
    assert not keyboard.listen, "Loading the example started global input"
    mode = next(node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_control_mode")
    assert flow.PullContext(tree, bpy.context.scene, 0, 0).evaluate(mode)["Mode"].name == "Idle"
    assert not workflow.history() and not workflow.logs
    source = next(node for node in tree.nodes if node.label == "Start sample")
    run_id = flow.emit_event(tree, source, "Trigger", {"message": "saved example"}, now=0)
    for index in range(1, 51):
        flow.tick(index * .1)
    assert workflow.status(run_id)["state"] == "end", workflow.status(run_id)
    assert not tree.last_error, tree.last_error
    assert any(entry["payload"].get("message") == "saved example" for entry in workflow.logs)
    flow.emit_event(tree, source, "Trigger", {"message": "clear on reload"}, now=6)
    assert workflow._tasks
    bpy.ops.wm.open_mainfile(filepath=str(example_path()))
    assert not workflow.history() and not workflow._tasks and not workflow._callbacks
    assert not input_runtime.global_keyboard.is_running()
    print("STAGE_ONE_SAVED_EXAMPLE_OK")
finally:
    addon.unregister()
