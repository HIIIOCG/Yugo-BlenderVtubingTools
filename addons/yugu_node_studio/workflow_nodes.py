"""Workflow action declarations; their execution uses flow's event dispatcher."""

import bpy

from .constants import SOCKET_TRIGGER, TREE_ID
from .nodes import YUGUS_NodeBase

EVENT = "YUGUS_NS_update_event"
BUSY_POLICIES = (
    ("PARALLEL", "Parallel", "Keep every incoming invocation independent"),
    ("IGNORE", "Ignore", "Ignore Enter while this action is running"),
    ("RESTART", "Restart", "Cancel the previous invocation before starting"),
)


def definition_poll(_self, tree):
    return tree.bl_idname == TREE_ID and bool(getattr(tree, "is_workflow_definition", tree.get("is_workflow_definition", False)))


def event_input(node, name):
    socket = node.inputs.new(EVENT, name)
    socket.link_limit = 4095
    return socket


class _Action:
    last_summary: bpy.props.StringProperty(default="Idle", options={"SKIP_SAVE"})
    last_run_id: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})
    busy_policy: bpy.props.EnumProperty(name="When Busy", items=BUSY_POLICIES, default="PARALLEL")

    def init_action(self):
        self._init_base(230)
        event_input(self, "Enter")
        event_input(self, "Cancel")
        for name in ("End", "Cancelled", "Failed"):
            self.outputs.new(EVENT, name)
        self.outputs.new(SOCKET_TRIGGER, "Running")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "busy_policy")
        layout.label(text=self.last_summary)


class YUGUS_NODE_action_flow(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_action_flow"
    bl_label = "Action Flow"
    bl_icon = "NODETREE"
    workflow_tree: bpy.props.PointerProperty(name="Workflow", type=bpy.types.NodeTree, poll=definition_poll)

    def init(self, _context):
        self.init_action()

    def draw_buttons(self, context, layout):
        layout.prop(self, "workflow_tree")
        if self.workflow_tree is not None:
            row = layout.row(align=True)
            for operator_id, title in (("yugu_node_studio.workflow_edit_cards", "Edit Cards"), ("yugu_node_studio.workflow_expand", "Expand Graph")):
                operator = row.operator(operator_id, text=title)
                operator.tree_name = self.workflow_tree.name
                operator.parent_tree_name = self.id_data.name
        else:
            operator = layout.operator("yugu_node_studio.workflow_new_definition", text="New Workflow", icon="ADD")
            operator.parent_tree_name = self.id_data.name
            operator.node_name = self.name
        _Action.draw_buttons(self, context, layout)


class YUGUS_NODE_flow_entry(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_entry"
    bl_label = "Flow Entry"
    bl_icon = "PLAY"

    def init(self, _context):
        self._init_base(160)
        self.outputs.new(EVENT, "Start")

    def draw_buttons(self, _context, layout):
        layout.label(text="Started by Action Flow")


class YUGUS_NODE_flow_end(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_end"
    bl_label = "Flow End"
    bl_icon = "CHECKMARK"

    def init(self, _context):
        self._init_base(160)
        event_input(self, "Enter")

    def draw_buttons(self, _context, layout):
        layout.label(text="Completes this invocation")


class YUGUS_NODE_wait_action(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_wait_action"
    bl_label = "Wait Action"
    bl_icon = "TIME"
    seconds: bpy.props.FloatProperty(name="Seconds", default=.5, min=0)

    def init(self, _context):
        self.init_action()

    def draw_buttons(self, context, layout):
        layout.prop(self, "seconds")
        _Action.draw_buttons(self, context, layout)


class YUGUS_NODE_test_action(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_test_action"
    bl_label = "Test Action"
    bl_icon = "EXPERIMENTAL"
    seconds: bpy.props.FloatProperty(name="Seconds", default=.1, min=0)
    fail: bpy.props.BoolProperty(name="Fail", default=False)
    label: bpy.props.StringProperty(name="Label", default="Test")
    payload_log: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})

    def init(self, _context):
        self.init_action()

    def draw_buttons(self, context, layout):
        layout.prop(self, "label")
        layout.prop(self, "seconds")
        layout.prop(self, "fail")
        _Action.draw_buttons(self, context, layout)


class YUGUS_NODE_flow_parallel(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_parallel"
    bl_label = "Parallel"
    bl_icon = "NODETREE"
    branches: bpy.props.IntProperty(name="Branches", default=2, min=2, max=3)

    def init(self, _context):
        self.init_action()
        for index in range(1, 4):
            self.outputs.new(EVENT, f"Branch {index}")

    def draw_buttons(self, context, layout):
        layout.prop(self, "branches")
        _Action.draw_buttons(self, context, layout)


class YUGUS_NODE_flow_wait_all(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_wait_all"
    bl_label = "Wait All"
    bl_icon = "CHECKMARK"

    def init(self, _context):
        self.init_action()
        self.inputs.remove(self.inputs["Enter"])
        for index in range(1, 4):
            event_input(self, f"Branch {index}")


class YUGUS_NODE_flow_repeat(_Action, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_repeat"
    bl_label = "Repeat"
    bl_icon = "FILE_REFRESH"
    count: bpy.props.IntProperty(name="Count", default=2, min=1, max=10000)

    def init(self, _context):
        self.init_action()
        self.outputs.new(EVENT, "Body")

    def draw_buttons(self, context, layout):
        layout.prop(self, "count")
        _Action.draw_buttons(self, context, layout)


CLASSES = (
    YUGUS_NODE_action_flow, YUGUS_NODE_flow_entry, YUGUS_NODE_flow_end,
    YUGUS_NODE_wait_action, YUGUS_NODE_test_action, YUGUS_NODE_flow_parallel,
    YUGUS_NODE_flow_wait_all, YUGUS_NODE_flow_repeat,
)
ACTION_TYPES = {cls.bl_idname for cls in CLASSES if cls is not YUGUS_NODE_flow_entry}
MENU_ENTRIES = tuple((cls.bl_idname, cls.bl_label) for cls in CLASSES)
