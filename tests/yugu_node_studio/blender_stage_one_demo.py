"""Build the inspectable first-stage example delivered with the extension."""
from pathlib import Path
import sys

import bpy

project = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import example_path, load_node_studio, version
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import workflow_ui

addon.register()
try:
    root, definition = workflow_ui.build_demo()
    bpy.context.scene.yugu_node_studio_tree = root
    assert workflow_ui.cards_from_graph(definition).supported
    assert all(not node.listen for node in root.nodes if node.bl_idname == "YUGUS_NODE_keyboard_data")
    readme = bpy.data.texts.new("Keyboard and Action Flow - Read Me")
    readme.write(f"""Yugu Node Studio {version()} example

Install the extension before opening this file.
The Yugu Studio sidebar in the Node Editor has an Action Flow panel.
Start sample / Fire starts the sample. Cancel sample / Fire cancels it.
Select the Action Flow node and choose Edit Cards to edit the real graph.
Expand Graph shows the definition; Return to Parent returns to this graph.

For global keyboard input, enable Keyboard Listen explicitly.
F8 cycles Idle / Stage 1 / Stage 2.
Ctrl+R starts this workflow in Stage 1; Escape cancels it.
All keys still reach the application in front.
The example contains timed test actions, parallel branches, repeat and if.
""")
    window = bpy.context.window
    if window is not None:
        area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
        if area is not None:
            with bpy.context.temp_override(window=window, area=area):
                workflow_ui.open_tree(bpy.context, root)
                area.spaces.active.show_region_ui = True
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            if region is not None:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.node.view_all()
    destination = example_path()
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), compress=True)
    print("STAGE_ONE_DEMO_SAVED", destination)
finally:
    addon.unregister()
