"""Independent Node Editor controls and add menus."""

from __future__ import annotations

import bpy

from .constants import TREE_ID
from .nodes import NODE_MENU, NODE_MENU_SETTINGS


def _menu_entries(title):
    return next((entries for label, entries in NODE_MENU if label == title), ())


def _active_tree(context):
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
    return tree if tree is not None and tree.bl_idname == TREE_ID else None


def _snapshot(context):
    from . import vrm_cache

    tree = _active_tree(context)
    if tree is None:
        return None
    data_node = next(
        (node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_vrm_data"),
        None,
    )
    return vrm_cache.get_snapshot(data_node) if data_node is not None else None


class YUGUS_MT_add_inputs(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_inputs"
    bl_label = "Inputs"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_inputs.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_inputs.bl_label, {}))


class YUGUS_MT_add_custom_inputs(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_custom_inputs"
    bl_label = "Custom Inputs"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_custom_inputs.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_custom_inputs.bl_label, {}))


class YUGUS_MT_add_math(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_math"
    bl_label = "Math / Mixer"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_math.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_math.bl_label, {}))


class YUGUS_MT_add_flow(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_flow"
    bl_label = "FlowControl"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_flow.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_flow.bl_label, {}))


class YUGUS_MT_add_output(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_output"
    bl_label = "Outputs"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_output.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_output.bl_label, {}))


class YUGUS_MT_add_motion(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_motion"
    bl_label = "Motion Operation"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_motion.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_motion.bl_label, {}))


class YUGUS_MT_add_expressions(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_expressions"
    bl_label = "Expression Operation"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_expressions.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_expressions.bl_label, {}))


class YUGUS_MT_add_physics(bpy.types.Menu):
    bl_idname = "YUGUS_MT_add_physics"
    bl_label = "Physics"

    def draw(self, _context):
        _draw_entries(self.layout, _menu_entries(YUGUS_MT_add_physics.bl_label),
                      NODE_MENU_SETTINGS.get(YUGUS_MT_add_physics.bl_label, {}))


def _draw_entries(layout, entries, settings=None):
    for node_type, label in entries:
        operator = layout.operator("node.add_node", text=label)
        operator.type = node_type
        operator.use_transform = True
        for name, value in (settings or {}).get(node_type, {}).items():
            setting = operator.settings.add()
            setting.name = name
            setting.value = repr(value)


ADD_MENU_CLASSES = (
    YUGUS_MT_add_inputs,
    YUGUS_MT_add_custom_inputs,
    YUGUS_MT_add_math,
    YUGUS_MT_add_flow,
    YUGUS_MT_add_output,
    YUGUS_MT_add_motion,
    YUGUS_MT_add_expressions,
    YUGUS_MT_add_physics,
)


def draw_add_menu(self, context):
    space = getattr(context, "space_data", None)
    if getattr(space, "tree_type", "") != TREE_ID:
        return
    layout = self.layout
    layout.separator()
    for menu in ADD_MENU_CLASSES:
        layout.menu(menu.bl_idname)


def draw_header(self, context):
    tree = _active_tree(context)
    if tree is None:
        return
    layout = self.layout
    layout.separator_spacer()
    layout.label(text=tree.last_status)


class YUGUS_PT_graph(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Graph"

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "space_data", None), "tree_type", "") == TREE_ID

    def draw(self, context):
        layout = self.layout
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Create or select a Yugu graph", icon="INFO")
            layout.operator("yugu_node_studio.new_graph", icon="ADD")
            return
        active_outputs = sum(
            1
            for node in tree.nodes
            if node.bl_idname == "YUGUS_NODE_hz_trigger"
            and not node.mute
        )
        layout.label(
            text=f"Hz Triggers: {active_outputs}",
            icon="OUTPUT",
        )
        layout.label(text="Hz Trigger → Apply → pull upstream data")
        layout.label(text=tree.last_status)
        if tree.last_error:
            error = layout.box()
            error.alert = True
            error.label(text=tree.last_error, icon="ERROR")


class YUGUS_PT_data_inspector(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "VRM Snapshot"

    @classmethod
    def poll(cls, context):
        return YUGUS_PT_graph.poll(context)

    def draw(self, context):
        from . import vrm_cache

        layout = self.layout
        tree = _active_tree(context)
        data_node = next(
            (node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_vrm_data"),
            None,
        ) if tree else None
        if data_node is None:
            layout.label(text="No VRM Data node", icon="INFO")
            return
        snapshot = vrm_cache.get_snapshot(data_node)
        if snapshot is None:
            layout.label(text=data_node.cache_message, icon="ERROR")
            return
        layout.label(text=snapshot.meta.values.get("name", snapshot.armature_name), icon="ARMATURE_DATA")
        layout.label(text=f"VRM: {snapshot.vrm_version}")
        layout.label(text=f"Humanoid Bones: {len(snapshot.bone_map.by_humanoid)}")
        layout.label(text=f"Expressions: {len(snapshot.expression_map.definitions)}")
        layout.label(text=f"Springs: {len(snapshot.spring_data.springs)}")
        layout.label(text=f"Colliders: {len(snapshot.spring_data.colliders)}")


class YUGUS_PT_bone_mapping(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Humanoid Mapping"
    bl_parent_id = "YUGUS_PT_data_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _snapshot(context) is not None

    def draw(self, context):
        layout = self.layout
        snapshot = _snapshot(context)
        for humanoid, bone_name in snapshot.bone_map.by_humanoid.items():
            row = layout.row(align=True)
            row.label(text=humanoid)
            row.label(text=bone_name, icon="BONE_DATA")


class YUGUS_PT_expression_mapping(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Expression Targets"
    bl_parent_id = "YUGUS_PT_data_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _snapshot(context) is not None

    def draw(self, context):
        layout = self.layout
        snapshot = _snapshot(context)
        definitions = snapshot.expression_map.definitions.values()
        if not definitions:
            layout.label(text="No mapped expressions", icon="INFO")
            return
        for definition in definitions:
            box = layout.box()
            row = box.row(align=True)
            row.label(text=definition.name, icon="SHAPEKEY_DATA")
            if definition.is_binary:
                row.label(text="Binary")
            for target in definition.morph_targets:
                box.label(
                    text=f"{target.object_name} · {target.shape_key_name} · {target.weight:.3f}",
                    icon="MESH_DATA",
                )
            for target in definition.material_targets:
                label = target.bind_type
                if target.property_name:
                    label += f" · {target.property_name}"
                box.label(text=f"{target.material_name} · {label}", icon="MATERIAL")


class YUGUS_PT_spring_snapshot(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Spring Data"
    bl_parent_id = "YUGUS_PT_data_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _snapshot(context) is not None

    def draw(self, context):
        layout = self.layout
        snapshot = _snapshot(context)
        springs = snapshot.spring_data.springs
        if not springs:
            layout.label(text="No Spring Bone data", icon="INFO")
            return
        for index, spring in enumerate(springs):
            box = layout.box()
            box.label(text=spring.get("name") or f"Spring {index + 1}", icon="PHYSICS")
            if spring.get("center"):
                box.label(text=f"Center · {spring['center']}")
            for joint in spring.get("joints", ()):
                box.label(text=str(joint.get("bone", "")), icon="BONE_DATA")


class YUGUS_PT_meta_snapshot(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Meta"
    bl_parent_id = "YUGUS_PT_data_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _snapshot(context) is not None

    def draw(self, context):
        layout = self.layout
        snapshot = _snapshot(context)
        for key, value in snapshot.meta.values.items():
            if value in (None, "", (), []):
                continue
            row = layout.row()
            row.label(text=key.replace("_", " ").title())
            row.label(text=", ".join(value) if isinstance(value, tuple) else str(value))


class YUGUS_PT_view3d_launcher(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_label = "Yugo Node"

    def draw(self, context):
        layout = self.layout
        layout.template_ID(context.scene, "yugu_node_studio_tree", new="yugu_node_studio.new_graph")
        layout.operator("yugu_node_studio.open_editor", text="Open Node Editor", icon="NODETREE")


CLASSES = ADD_MENU_CLASSES + (
    YUGUS_PT_graph,
    YUGUS_PT_data_inspector,
    YUGUS_PT_bone_mapping,
    YUGUS_PT_expression_mapping,
    YUGUS_PT_spring_snapshot,
    YUGUS_PT_meta_snapshot,
    YUGUS_PT_view3d_launcher,
)


def register_hooks():
    bpy.types.NODE_MT_add.append(draw_add_menu)
    bpy.types.NODE_HT_header.append(draw_header)


def unregister_hooks():
    try:
        bpy.types.NODE_MT_add.remove(draw_add_menu)
    except ValueError:
        pass
    try:
        bpy.types.NODE_HT_header.remove(draw_header)
    except ValueError:
        pass
