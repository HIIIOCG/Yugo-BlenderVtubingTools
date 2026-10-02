"""Keyboard, state and mode node declarations; no input work in drawing."""
import bpy

from .constants import SOCKET_TRIGGER
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase

EVENT = "YUGUS_NS_update_event"
KEYBOARD = "YUGUS_NS_keyboard_data"
STATE = "YUGUS_NS_input_state"
MODE = "YUGUS_NS_control_mode"


class YUGUS_NS_keyboard_data(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = KEYBOARD
    bl_label = "Keyboard Data"

    def draw_color(self, _context, _node):
        return (0.22, 0.60, 0.84, 1.0)


class YUGUS_NS_input_state(bpy.types.NodeSocket):
    bl_idname = STATE
    bl_label = "State"
    default_value: bpy.props.BoolProperty(name="State", default=False)

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.48, 0.73, 0.25, 1.0)


class YUGUS_NS_control_mode(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = MODE
    bl_label = "Control Mode"

    def draw_color(self, _context, _node):
        return (0.75, 0.43, 0.91, 1.0)


def _listen_changed(node, _context):
    # Update runs on Blender's main thread. Starting is deferred to the scheduler.
    from . import input_runtime
    input_runtime.configuration_changed(node)


def _event_input(node, name):
    socket = node.inputs.new(EVENT, name)
    socket.link_limit = 4095
    return socket


class _InputNode:
    last_summary: bpy.props.StringProperty(default="Ready", options={"SKIP_SAVE"})

    def draw_buttons(self, _context, layout):
        layout.label(text=self.last_summary)


class YUGUS_NODE_keyboard_data(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_keyboard_data"
    bl_label = "Keyboard Data"
    bl_icon = "EVENT_K"
    bl_description = "Explicit global Windows keyboard listening; other apps still receive every key"
    listen: bpy.props.BoolProperty(name="Listen", default=False, options={"SKIP_SAVE"}, update=_listen_changed)

    def init(self, _context):
        self._init_base(220)
        self.outputs.new(KEYBOARD, "Data")

    def draw_buttons(self, context, layout):
        layout.prop(self, "listen", toggle=True)
        _InputNode.draw_buttons(self, context, layout)
        self._draw_monitor(layout)


class YUGUS_NODE_key_state(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_key_state"
    bl_label = "Key State"
    bl_icon = "EVENT_K"
    bl_description = "Raw Key ignores modifiers; Shortcut matches all keys with exact modifiers by default"
    match_mode: bpy.props.EnumProperty(name="Match", items=[("RAW", "Raw Key", "Read physical key, ignoring modifiers"), ("SHORTCUT", "Shortcut", "All shortcut keys held, in any order")], default="RAW")
    key: bpy.props.StringProperty(name="Key", default="A")
    shortcut: bpy.props.StringProperty(name="Shortcut", default="Ctrl+A")
    exact_modifiers: bpy.props.BoolProperty(name="Exact Modifiers", default=True)
    allowed_modes: bpy.props.StringProperty(name="Allowed Modes", default="", description="Comma-separated mode names; empty permits all modes")

    def init(self, _context):
        self._init_base(245)
        self.inputs.new(KEYBOARD, "Keyboard")
        self.inputs.new(MODE, "Mode")
        self.outputs.new(STATE, "State")

    def draw_buttons(self, context, layout):
        layout.prop(self, "match_mode", text="")
        layout.prop(self, "key" if self.match_mode == "RAW" else "shortcut")
        if self.match_mode == "SHORTCUT":
            layout.prop(self, "exact_modifiers")
        if self.inputs["Mode"].is_linked:
            layout.prop(self, "allowed_modes")
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_state_logic(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_state_logic"
    bl_label = "State Logic"
    bl_icon = "NODETREE"
    operation: bpy.props.EnumProperty(name="Operation", items=[(name, name, name) for name in ("AND", "OR", "NOT", "XOR")], default="AND")

    def init(self, _context):
        self._init_base(190)
        self.inputs.new(STATE, "A")
        self.inputs.new(STATE, "B")
        self.outputs.new(STATE, "State")

    def draw_buttons(self, context, layout):
        layout.prop(self, "operation", text="")
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_state_events(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_state_events"
    bl_label = "State Events"
    bl_icon = "SORTTIME"
    bl_description = "On and Off are ordered state edges, including quick press and release between timer ticks"

    def init(self, _context):
        self._init_base(190)
        self.inputs.new(STATE, "State")
        for name in ("On", "Off", "Changed"):
            self.outputs.new(EVENT, name)


class YUGUS_NODE_state_toggle(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_state_toggle"
    bl_label = "Toggle"
    bl_icon = "CHECKMARK"
    initial_state: bpy.props.BoolProperty(name="Initial State", default=False)

    def init(self, _context):
        self._init_base(190)
        for name in ("Toggle", "Set", "Reset"):
            _event_input(self, name)
        self.outputs.new(STATE, "State")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        layout.prop(self, "initial_state")
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_state_value(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_state_value"
    bl_label = "State Value"
    bl_icon = "IPO_EASE_IN_OUT"
    on_value: bpy.props.FloatProperty(name="On Value", default=1.0)
    off_value: bpy.props.FloatProperty(name="Off Value", default=0.0)
    attack: bpy.props.FloatProperty(name="Attack", default=0.1, min=0, subtype="TIME")
    release: bpy.props.FloatProperty(name="Release", default=0.15, min=0, subtype="TIME")

    def init(self, _context):
        self._init_base(205)
        self.inputs.new(STATE, "State")
        self.outputs.new(SOCKET_TRIGGER, "Value")

    def draw_buttons(self, context, layout):
        for name in ("on_value", "off_value", "attack", "release"):
            layout.prop(self, name)
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_control_mode(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_control_mode"
    bl_label = "Control Mode"
    bl_icon = "NODETREE"
    modes: bpy.props.StringProperty(name="Modes", default="Idle, Stage 1, Stage 2", description="Comma-separated mode names; each Control Mode is its own input scope")
    initial_mode: bpy.props.StringProperty(name="Initial Mode", default="Idle")

    def init(self, _context):
        self._init_base(240)
        for name in ("Cycle", "Lock", "Unlock"):
            _event_input(self, name)
        self.outputs.new(MODE, "Mode")
        self.outputs.new(STATE, "Unlocked")
        self.outputs.new(EVENT, "Changed")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        layout.prop(self, "modes")
        layout.prop(self, "initial_mode")
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_mode_match(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_mode_match"
    bl_label = "Mode Match"
    bl_icon = "DECORATE_OVERRIDE"
    mode_name: bpy.props.StringProperty(name="Mode", default="Idle")
    respect_lock: bpy.props.BoolProperty(name="Respect Lock", default=True)

    def init(self, _context):
        self._init_base(215)
        self.inputs.new(MODE, "Mode")
        self.outputs.new(STATE, "State")

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode_name")
        layout.prop(self, "respect_lock")
        _InputNode.draw_buttons(self, context, layout)


class YUGUS_NODE_set_mode(_InputNode, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_set_mode"
    bl_label = "Set Mode"
    bl_icon = "DECORATE_OVERRIDE"
    mode_name: bpy.props.StringProperty(name="Mode", default="Stage 1")

    def init(self, _context):
        self._init_base(215)
        _event_input(self, "Enter")
        self.inputs.new(MODE, "Mode")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode_name")
        _InputNode.draw_buttons(self, context, layout)


NODE_CLASSES = (
    YUGUS_NODE_keyboard_data, YUGUS_NODE_key_state, YUGUS_NODE_state_logic,
    YUGUS_NODE_state_events, YUGUS_NODE_state_toggle, YUGUS_NODE_state_value,
    YUGUS_NODE_control_mode, YUGUS_NODE_mode_match, YUGUS_NODE_set_mode,
)
CLASSES = (YUGUS_NS_keyboard_data, YUGUS_NS_input_state, YUGUS_NS_control_mode) + NODE_CLASSES
MENU_ENTRIES = tuple((node.bl_idname, node.bl_label) for node in NODE_CLASSES)
