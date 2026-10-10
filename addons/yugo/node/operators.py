"""Graph, cache, source, and runtime operators."""

from __future__ import annotations

import bpy

from . import network, runtime, vrm_cache
from .constants import TREE_ID




def _tree(name):
    tree = bpy.data.node_groups.get(name)
    return tree if tree is not None and tree.bl_idname == TREE_ID else None


def _node(tree_name, node_name):
    tree = _tree(tree_name)
    return (tree, tree.nodes.get(node_name)) if tree is not None else (None, None)


def _tree_poll(_self, candidate):
    return candidate is not None and getattr(candidate, "bl_idname", "") == TREE_ID


class YUGUS_OT_new_graph(bpy.types.Operator):
    bl_idname = "yugu_node_studio.new_graph"
    bl_label = "New Yugu Graph"
    bl_description = "Create a Yugo Node node graph"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="Yugo Node")
    create_default_nodes: bpy.props.BoolProperty(name="Default Nodes", default=True)

    def execute(self, context):
        tree = bpy.data.node_groups.new(self.name, TREE_ID)
        context.scene.yugu_node_studio_tree = tree
        if self.create_default_nodes:
            data = tree.nodes.new("YUGUS_NODE_vrm_data")
            data.location = (-420.0, 80.0)
            action = tree.nodes.new("YUGUS_NODE_action_source")
            action.location = (-130.0, 170.0)
            convert = tree.nodes.new("YUGUS_NODE_motion_to_rotation")
            convert.location = (180, 170)
            output = tree.nodes.new("YUGUS_NODE_apply_rotation")
            output.location = (500, 170)
            clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
            clock.location = (180, -100)
            shapes = tree.nodes.new("YUGUS_NODE_expression_to_shapes")
            shapes.location = (180, -350)
            shape_output = tree.nodes.new("YUGUS_NODE_apply_shapes")
            shape_output.location = (500, -300)
            tree.links.new(data.outputs["Bone Map"], action.inputs["Source Bone Map"])
            tree.links.new(action.outputs["Humanoid Motion"], convert.inputs["Motion"])
            tree.links.new(data.outputs["Bone Map"], convert.inputs["Bone Map"])
            tree.links.new(convert.outputs["Bone Transform"], output.inputs["Bone Transform"])
            tree.links.new(data.outputs["Expression Map"], shapes.inputs["Expression Map"])
            tree.links.new(shapes.outputs["Shape Keys Values"], shape_output.inputs["Shape Keys Values"])
            tree.links.new(data.outputs["Armatures"], output.inputs["Armatures"])
            tree.links.new(data.outputs["Objects"], shape_output.inputs["Objects"])
            for sink in (output, shape_output):
                tree.links.new(clock.outputs["Trigger"], sink.inputs.get("Enter") or sink.inputs["Trigger"])
        self.report({"INFO"}, f'Created "{tree.name}"')
        return {"FINISHED"}


class YUGUS_OT_open_editor(bpy.types.Operator):
    bl_idname = "yugu_node_studio.open_editor"
    bl_label = "Open Yugo Node Editor"
    bl_description = "Use this area as an independent Yugo Node editor"

    @classmethod
    def poll(cls, context):
        return context.area is not None

    def execute(self, context):
        tree = getattr(context.scene, "yugu_node_studio_tree", None)
        if tree is None or tree.bl_idname != TREE_ID:
            tree = next(
                (item for item in bpy.data.node_groups if item.bl_idname == TREE_ID),
                None,
            )
        if tree is None:
            tree = bpy.data.node_groups.new("Yugo Node", TREE_ID)
            context.scene.yugu_node_studio_tree = tree
        context.area.type = "NODE_EDITOR"
        space = context.area.spaces.active
        space.tree_type = TREE_ID
        try:
            space.pin = True
            space.path.start(tree)
        except (AttributeError, RuntimeError, TypeError):
            pass
        return {"FINISHED"}


class YUGUS_OT_reload_vrm(bpy.types.Operator):
    bl_idname = "yugu_node_studio.reload_vrm"
    bl_label = "Reload VRM Data"
    bl_description = "Read VRM data once and rebuild the native runtime snapshot"
    bl_options = {"REGISTER"}

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_vrm_data":
            self.report({"ERROR"}, "VRM Data node was not found")
            return {"CANCELLED"}
        try:
            from . import flow
            snapshot = flow.reload_vrm(node)
        except Exception as error:
            node.cache_status = "ERROR"
            node.cache_message = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Loaded {len(snapshot.bone_map.by_humanoid)} bones and "
            f"{len(snapshot.expression_map.definitions)} expressions",
        )
        return {"FINISHED"}


class YUGUS_OT_source_toggle(bpy.types.Operator):
    bl_idname = "yugu_node_studio.source_toggle"
    bl_label = "Connect Motion Source"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None:
            return {"CANCELLED"}
        action = "DISCONNECT" if network.is_running(str(node.runtime_id)) else "CONNECT"
        try:
            from . import flow
            flow.source_connection_action(node, action, retry=True)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_source_connection(bpy.types.Operator):
    bl_idname = "yugu_node_studio.source_connection"
    bl_label = "Motion Source Connection"
    bl_description = "Connect or disconnect the motion receiver"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    action: bpy.props.EnumProperty(
        items=(
            ("CONNECT", "Connect", "Start receiving motion packets"),
            ("DISCONNECT", "Disconnect", "Stop receiving motion packets"),
        ),
        options={"HIDDEN"},
    )

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname not in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}:
            return {"CANCELLED"}
        try:
            from . import flow
            flow.source_connection_action(node, self.action, retry=True)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_vmc_connection(bpy.types.Operator):
    bl_idname = "yugu_node_studio.vmc_connection"
    bl_label = "VMC Connection"
    bl_description = "Connect or disconnect the VMC Receiver"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    action: bpy.props.EnumProperty(
        items=(
            ("CONNECT", "Connect", "Start receiving VMC packets"),
            ("DISCONNECT", "Disconnect", "Stop receiving VMC packets"),
        ),
        options={"HIDDEN"},
    )

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_vmc_source":
            return {"CANCELLED"}
        try:
            from . import flow
            flow.vmc_connection_action(node, self.action)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_ifacial_calibrate(bpy.types.Operator):
    bl_idname = "yugu_node_studio.ifacial_calibrate"
    bl_label = "Calibrate iFacialMocap"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_ifacial_source":
            return {"CANCELLED"}
        try:
            from . import flow
            flow.calibrate_ifacial(node)
        except ValueError as error:
            node.last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def _map_collection(node, collection):
    if collection == "EXPRESSIONS" and hasattr(node, "expressions"):
        return node, node.expressions, "expression_index"
    if collection not in {"MORPH_TARGETS", "MATERIAL_TARGETS"}:
        return None
    expressions = getattr(node, "expressions", ())
    if len(expressions) == 0:
        return None
    index = min(max(int(node.expression_index), 0), len(expressions) - 1)
    expression = expressions[index]
    if collection == "MORPH_TARGETS":
        return expression, expression.morph_targets, "morph_target_index"
    return expression, expression.material_targets, "material_target_index"


class YUGUS_OT_map_item_add(bpy.types.Operator):
    bl_idname = "yugu_node_studio.map_item_add"
    bl_label = "Add Map Item"
    bl_options = {"REGISTER", "UNDO"}

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    collection: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        resolved = _map_collection(node, self.collection) if node is not None else None
        if resolved is None:
            return {"CANCELLED"}
        owner, items, index_property = resolved
        item = items.add()
        setattr(owner, index_property, len(items) - 1)
        return {"FINISHED"}


class YUGUS_OT_map_item_remove(bpy.types.Operator):
    bl_idname = "yugu_node_studio.map_item_remove"
    bl_label = "Remove Map Item"
    bl_options = {"REGISTER", "UNDO"}

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    collection: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        _graph, node = _node(self.tree_name, self.node_name)
        resolved = _map_collection(node, self.collection) if node is not None else None
        if resolved is None:
            return {"CANCELLED"}
        owner, items, index_property = resolved
        if len(items) == 0:
            return {"CANCELLED"}
        index = min(max(int(getattr(owner, index_property)), 0), len(items) - 1)
        items.remove(index)
        setattr(owner, index_property, min(index, max(len(items) - 1, 0)))
        return {"FINISHED"}


class YUGUS_OT_spring_toggle(bpy.types.Operator):
    bl_idname = "yugu_node_studio.spring_toggle"
    bl_label = "Start Spring Physics"
    bl_description = "Start or stop this standalone Spring Bone simulation"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_spring_simulation":
            return {"CANCELLED"}
        if node.spring_active:
            runtime.stop_spring(node, reset_pose=True)
            self.report({"INFO"}, "Spring Bone physics stopped at Reset Pose")
            return {"FINISHED"}
        try:
            runtime.start_spring(node, context)
        except Exception as error:
            node.spring_active = False
            node.spring_status = "Stopped"
            node.spring_last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Spring Bone physics started")
        return {"FINISHED"}


class YUGUS_OT_spring_reload(bpy.types.Operator):
    bl_idname = "yugu_node_studio.spring_reload"
    bl_label = "Reload Spring Physics"
    bl_description = "Return Spring joints to Reset Pose and rebuild the solver"

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_spring_simulation":
            return {"CANCELLED"}
        try:
            runtime.reload_spring(node, context)
        except Exception as error:
            node.spring_last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Spring Bone physics reloaded")
        return {"FINISHED"}


class YUGUS_OT_spring_debug_print(bpy.types.Operator):
    bl_idname = "yugu_node_studio.spring_debug_print"
    bl_label = "Print Spring Debug"
    bl_description = (
        "Print a detailed Spring diagnostic report and save it in Blender Text data"
    )

    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        _graph, node = _node(self.tree_name, self.node_name)
        if node is None or node.bl_idname != "YUGUS_NODE_spring_simulation":
            return {"CANCELLED"}
        try:
            report = runtime.spring_debug_report(node, context.scene)
        except Exception as error:
            self.report({"ERROR"}, f"Could not create Spring debug report: {error}")
            return {"CANCELLED"}

        print(report, flush=True)
        text = bpy.data.texts.get(runtime.SPRING_DEBUG_TEXT_NAME)
        if text is None:
            text = bpy.data.texts.new(runtime.SPRING_DEBUG_TEXT_NAME)
        else:
            text.clear()
        text.write(report)
        self.report(
            {"INFO"},
            f'Printed Spring debug and saved Text "{runtime.SPRING_DEBUG_TEXT_NAME}"',
        )
        return {"FINISHED"}


CLASSES = (
    YUGUS_OT_new_graph,
    YUGUS_OT_open_editor,
    YUGUS_OT_reload_vrm,
    YUGUS_OT_source_toggle,
    YUGUS_OT_source_connection,
    YUGUS_OT_vmc_connection,
    YUGUS_OT_ifacial_calibrate,
    YUGUS_OT_map_item_add,
    YUGUS_OT_map_item_remove,
)


def register_properties():
    bpy.types.Scene.yugu_node_studio_tree = bpy.props.PointerProperty(
        name="Yugu Graph", type=bpy.types.NodeTree, poll=_tree_poll
    )


def unregister_properties():
    if hasattr(bpy.types.Scene, "yugu_node_studio_tree"):
        del bpy.types.Scene.yugu_node_studio_tree
