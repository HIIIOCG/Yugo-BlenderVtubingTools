"""Load an actual graph saved by the qualified 0.6.0 runtime and run it unchanged."""
import hashlib
import json
from pathlib import Path
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
addon = load_node_studio()
from yugu_node_studio import flow, input_runtime, workflow, workflow_ui

folder = Path(__file__).resolve().parent / 'fixtures'
expected = json.loads((folder / 'studio-0.6.0-graph.json').read_text(encoding='utf-8'))
fixture = folder / expected['fixture']
assert hashlib.sha256(fixture.read_bytes()).hexdigest() == expected['sha256']
addon.register()
try:
    bpy.ops.wm.open_mainfile(filepath=str(fixture))
    actual = {}
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'YUGUS_NT_humanoid_motion':
            continue
        actual[tree.name] = {
            'nodes': {node.name: {'type': node.bl_idname,
                'inputs': [[socket.name, socket.identifier, socket.bl_idname] for socket in node.inputs],
                'outputs': [[socket.name, socket.identifier, socket.bl_idname] for socket in node.outputs]}
                for node in tree.nodes},
            'links': sorted([[link.from_node.name, link.from_socket.identifier, link.to_node.name,
                              link.to_socket.identifier] for link in tree.links])}
    assert actual == expected['graphs'], 'Saved 0.6.0 node/socket identities or connections changed'
    tree = bpy.context.scene.yugu_node_studio_tree
    caller = next(node for node in tree.nodes if node.bl_idname == 'YUGUS_NODE_action_flow')
    assert workflow_ui.cards_from_graph(caller.workflow_tree).supported
    assert not input_runtime.global_keyboard.is_running()
    assert not workflow.history()
    start = next(node for node in tree.nodes if node.label == 'Start sample')
    run = flow.emit_event(tree, start, 'Trigger', {'source': 'real 0.6.0 saved graph'}, now=0)
    for tick in range(1, 51):
        flow.tick(tick * .1)
    assert workflow.status(run)['state'] == 'end', workflow.status(run)
    assert not tree.last_error, tree.last_error
    assert any(entry['payload'].get('source') == 'real 0.6.0 saved graph' for entry in workflow.logs)
    print('STUDIO_0_6_SAVED_GRAPH_COMPATIBILITY_OK')
finally:
    addon.unregister()
