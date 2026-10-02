"""Event routing declarations; execution lives in the flow dispatcher."""

import bpy

from .constants import SOCKET_TRIGGER
from .nodes import YUGUS_NodeBase

EVENT = "YUGUS_NS_update_event"


class _FlowControl:
    last_summary: bpy.props.StringProperty(
        default="Connect Enter", options={"SKIP_SAVE"}
    )

    def _init_flow(self, width=200):
        self._init_base(width)
        self.inputs.new(EVENT, "Enter")

    def _condition(self, name):
        socket = self.inputs.new(SOCKET_TRIGGER, name)
        socket.default_value = 1.0

    def draw_buttons(self, _context, layout):
        layout.label(text=self.last_summary)


class YUGUS_NODE_flow_if(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_if"
    bl_label = "If"
    bl_description = "Send Enter to True when Condition is above zero, otherwise False"
    bl_icon = "DECORATE_OVERRIDE"

    def init(self, _context):
        self._init_flow()
        self._condition("Condition")
        self.outputs.new(EVENT, "True")
        self.outputs.new(EVENT, "False")


class YUGUS_NODE_flow_gate(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_gate"
    bl_label = "Gate"
    bl_description = "Pass Enter to End while Open is above zero"
    bl_icon = "LOCKED"

    def init(self, _context):
        self._init_flow()
        self._condition("Open")
        self.outputs.new(EVENT, "End")


class YUGUS_NODE_flow_delay(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_delay"
    bl_label = "Delay"
    bl_description = "Wait before End; ignore further Enter while waiting; Reset cancels the wait"
    bl_icon = "TIME"
    seconds: bpy.props.FloatProperty(name="Seconds", default=0.5, min=0.0)

    def init(self, _context):
        self._init_flow()
        self.inputs.new(EVENT, "Reset")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        layout.prop(self, "seconds")
        _FlowControl.draw_buttons(self, context, layout)


class YUGUS_NODE_flow_wait_until(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_wait_until"
    bl_label = "Wait Until"
    bl_description = "After Enter, wait for Condition to become true; Reset cancels the wait"
    bl_icon = "TIME"

    def init(self, _context):
        self._init_flow()
        self._condition("Condition")
        self.inputs.new(EVENT, "Reset")
        self.outputs.new(EVENT, "End")


class YUGUS_NODE_flow_once(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_once"
    bl_label = "Once"
    bl_description = "Pass the first Enter to End; Reset allows it to run again"
    bl_icon = "CHECKMARK"

    def init(self, _context):
        self._init_flow()
        self.inputs.new(EVENT, "Reset")
        self.outputs.new(EVENT, "End")


class YUGUS_NODE_flow_sequence(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_sequence"
    bl_label = "Flow Sequence"
    bl_description = "Run Then 1, Then 2, and Then 3 in order for each Enter"
    bl_icon = "NODETREE"

    def init(self, _context):
        self._init_flow()
        for index in range(1, 4):
            self.outputs.new(EVENT, f"Then {index}")


class YUGUS_NODE_flow_every_n(_FlowControl, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_flow_every_n"
    bl_label = "Every N"
    bl_description = "Pass every Nth Enter to End; Reset restarts the count"
    bl_icon = "SORTTIME"
    every: bpy.props.IntProperty(name="Every N", default=2, min=1)

    def init(self, _context):
        self._init_flow()
        self.inputs.new(EVENT, "Reset")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        layout.prop(self, "every")
        _FlowControl.draw_buttons(self, context, layout)


CLASSES = (
    YUGUS_NODE_flow_if, YUGUS_NODE_flow_gate, YUGUS_NODE_flow_delay,
    YUGUS_NODE_flow_wait_until, YUGUS_NODE_flow_once, YUGUS_NODE_flow_sequence,
    YUGUS_NODE_flow_every_n,
)
MENU_ENTRIES = tuple((cls.bl_idname, cls.bl_label) for cls in CLASSES)
