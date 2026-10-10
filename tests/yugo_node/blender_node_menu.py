"""Verify frame-based Add menus and real Blender Mixer shortcut creation.

Run in an independent --background --factory-startup --disable-autoexec process.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import bpy

from _bootstrap import ROOT, load_node, output_directory

addon = load_node()
_package_directory = None


class Settings(list):
    def add(self):
        item = SimpleNamespace(name="", value="")
        self.append(item)
        return item


class MenuLayout:
    def __init__(self):
        self.operators = []
        self.menus = []

    def operator(self, identifier, **kwargs):
        assert identifier == "node.add_node"
        operator = SimpleNamespace(text=kwargs["text"], settings=Settings())
        self.operators.append(operator)
        return operator

    def menu(self, identifier):
        self.menus.append(identifier)

    def separator(self):
        pass


CATEGORIES = (
    "Inputs", "Custom Inputs", "Math / Mixer", "FlowControl", "Outputs",
    "Motion Operation", "Expression Operation", "Physics",
)
PRESETS = {
    "Math / Mixer": "FLOAT",
    "Motion Operation": "HUMANOID_MOTION",
    "Expression Operation": "EXPRESSIONS",
}

addon.register()
try:
    assert tuple(title for title, _entries in addon.nodes.NODE_MENU) == CATEGORIES
    assert tuple(menu.bl_label for menu in addon.ui.ADD_MENU_CLASSES) == CATEGORIES
    layout = MenuLayout()
    context = SimpleNamespace(space_data=SimpleNamespace(tree_type="YUGUS_NT_humanoid_motion"))
    addon.ui.draw_add_menu(SimpleNamespace(layout=layout), context)
    assert layout.menus == [menu.bl_idname for menu in addon.ui.ADD_MENU_CLASSES]
    unrelated = MenuLayout()
    addon.ui.draw_add_menu(SimpleNamespace(layout=unrelated),
                           SimpleNamespace(space_data=SimpleNamespace(tree_type="ShaderNodeTree")))
    assert not unrelated.menus

    tree = bpy.data.node_groups.new("MenuShortcuts", "YUGUS_NT_humanoid_motion")
    tree.use_fake_user = True
    area = bpy.context.screen.areas[0]
    area.type = "NODE_EDITOR"
    area.ui_type = "YUGUS_NT_humanoid_motion"
    area.spaces.active.pin = True
    area.spaces.active.node_tree = tree
    region = next(region for region in area.regions if region.type == "WINDOW")
    advertised = set()
    for menu_class, (_title, entries) in zip(addon.ui.ADD_MENU_CLASSES, addon.nodes.NODE_MENU):
        layout = MenuLayout()
        menu_class.draw(SimpleNamespace(layout=layout, bl_label=menu_class.bl_label), bpy.context)
        assert [(op.type, op.text) for op in layout.operators] == list(entries)
        for op in layout.operators:
            assert op.use_transform is True
            advertised.add(op.type)
            assert addon.availability.is_available(op.type), op.type
            if op.type != addon.mixer.NODE:
                assert not op.settings
                continue
            expected = PRESETS[menu_class.bl_label]
            assert [(s.name, s.value) for s in op.settings] == [("data_type", repr(expected))]
            # Execute Blender's actual operator with the exact settings drawn by
            # the menu, so the test covers RNA settings and socket updates too.
            settings = [{"name": s.name, "value": s.value} for s in op.settings]
            with bpy.context.temp_override(area=area, region=region):
                result = bpy.ops.node.add_node(type=op.type, use_transform=False, settings=settings)
            assert result == {"FINISHED"}, result
            node = tree.nodes.active
            node.name = menu_class.bl_label
            assert node.bl_idname == addon.mixer.NODE
            assert node.data_type == expected
            assert node.outputs[addon.mixer.output_name(node)].hide is False
            assert node.inputs[0].name == "Factor" and not node.inputs[0].hide
            node.data_type = "VECTOR"
            assert node.outputs["Vector Result"].hide is False
            node.data_type = expected

    assert not advertised & addon.availability.RETIRED_NODE_TYPES
    assert len(tree.nodes) == len(PRESETS)
    temporary_root = output_directory()
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yugo_node_menu_", dir=temporary_root) as directory:
        path = str(Path(directory) / "shortcuts.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        tree = bpy.data.node_groups["MenuShortcuts"]
        for category, expected in PRESETS.items():
            node = tree.nodes[category]
            assert node.bl_idname == addon.mixer.NODE and node.data_type == expected
            assert node.inputs[0].name == "Factor" and not node.inputs[0].hide
        bpy.data.node_groups.remove(tree)
    print("NODE_MENU_OK " + json.dumps({"blender": bpy.app.version_string,
                                       "addon": addon.bl_info["version"],
                                       "categories": CATEGORIES, "mixer_presets": PRESETS}))
finally:
    addon.unregister()
