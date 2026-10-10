"""String values, text/number conversions and inert saved-file declarations."""
import bpy

from .math_ops import finite_number
from .nodes import YUGUS_NodeBase

FLOAT = "YUGUS_NS_float"
TEXT = "YUGUS_NS_text"
NODE = "YUGUS_NODE_float_receiver"
EVENT = "YUGUS_NS_update_event"


class YUGUS_NS_float(bpy.types.NodeSocket):
    bl_idname = FLOAT
    bl_label = "Float"
    default_value: bpy.props.FloatProperty(name="Value", default=0.0)
    mixer_slot: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            from .nodes import NUMERIC_PARAMETERS, source_connection_property
            parameter = NUMERIC_PARAMETERS.get(_node.bl_idname)
            if parameter and parameter[0] == self.name:
                # Saved/animated RNA remains the source for an unlinked input.
                layout.prop(_node, parameter[1], text=text or self.name)
            else:
                property_name = source_connection_property(_node, self.name)
                if property_name:
                    layout.prop(_node, property_name, text=text or self.name)
                else:
                    layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NODE_string_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_string_value"
    bl_label = "String"
    bl_icon = "FONT_DATA"
    bl_description = "Enter text and output it through a text socket"
    value: bpy.props.StringProperty(name="Text")

    def init(self, _context):
        self._init_base(220)
        self.outputs.new(TEXT, "Text")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "value", text="")


class YUGUS_NODE_string_to_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_string_to_value"
    bl_label = "String to Value"
    bl_icon = "DRIVER"
    bl_description = "Convert numeric text to a Float; empty, invalid or nonfinite text returns zero"

    def init(self, _context):
        self._init_base(200)
        self.inputs.new(TEXT, "Text")
        self.outputs.new(FLOAT, "Value")


class YUGUS_NODE_value_to_string(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_value_to_string"
    bl_label = "Value to String"
    bl_icon = "FONT_DATA"
    bl_description = "Convert a finite Float to text, preserving its numeric precision"

    def init(self, _context):
        self._init_base(200)
        self.inputs.new(FLOAT, "Value")
        self.outputs.new(TEXT, "Text")


def evaluate(context, node):
    kind = node.bl_idname
    if kind not in {
        YUGUS_NODE_string_value.bl_idname,
        YUGUS_NODE_string_to_value.bl_idname,
        YUGUS_NODE_value_to_string.bl_idname,
    }:
        return None
    from .runtime import _output
    if kind == YUGUS_NODE_string_value.bl_idname:
        return _output(node, "Text", node.value if not node.mute else "")
    if kind == YUGUS_NODE_string_to_value.bl_idname:
        if node.mute:
            return _output(node, "Value", 0.0)
        text = context.input(node, "Text", "")
        if not isinstance(text, str):
            raise TypeError("String to Value Text: expected a Text input")
        try:
            value = finite_number(float(text))
        except (ValueError, OverflowError):
            value = 0.0
        return _output(node, "Value", value)
    if node.mute:
        return _output(node, "Text", "")
    value = finite_number(context.input(node, "Value", 0.0))
    return _output(node, "Text", str(value))


class YUGUS_NODE_float_receiver(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Float Receiver"
    bl_icon = "DRIVER"
    value: bpy.props.FloatProperty(
        name="Received Value", default=0.0, options={"ANIMATABLE"},
    )
    last_summary: bpy.props.StringProperty(default="Connect Value", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(220)
        self.inputs.new(EVENT, "Enter").link_limit = 4095
        self.inputs.new(FLOAT, "Value")
        self.outputs.new(EVENT, "End")
        self.outputs.new(FLOAT, "Value")

SOCKET_CLASSES = (YUGUS_NS_float,)
NODE_CLASSES = (
    YUGUS_NODE_string_value, YUGUS_NODE_string_to_value,
    YUGUS_NODE_value_to_string, YUGUS_NODE_float_receiver,
)
CLASSES = SOCKET_CLASSES + NODE_CLASSES
