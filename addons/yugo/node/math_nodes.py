"""Popular scalar/vector nodes. Pulls are pure and use the existing graph cache."""

import bpy

from . import math_ops
from .nodes import YUGUS_NodeBase
from .value_nodes import FLOAT

VECTOR = "YUGUS_NS_vector3"
SCALAR_OPERATIONS = (
    ("ADD", "Add", "A + B"),
    ("SUBTRACT", "Subtract", "A - B"),
    ("MULTIPLY", "Multiply", "A * B"),
    ("DIVIDE", "Divide", "A / B; zero divisor returns zero"),
    ("MULTIPLY_ADD", "Multiply Add", "A * B + C"),
    ("MINIMUM", "Minimum", "Smaller of A and B"),
    ("MAXIMUM", "Maximum", "Larger of A and B"),
    ("ABSOLUTE", "Absolute", "Absolute value of A"),
)
VECTOR_OPERATIONS = (
    ("ADD", "Add", "Vector A + Vector B"),
    ("SUBTRACT", "Subtract", "Vector A - Vector B"),
    ("SCALE", "Scale", "Multiply Vector A by Scale"),
    ("DOT_PRODUCT", "Dot Product", "Scalar dot product"),
    ("CROSS_PRODUCT", "Cross Product", "Vector cross product"),
    ("LENGTH", "Length", "Length of Vector A"),
    ("DISTANCE", "Distance", "Distance between the vectors"),
    ("NORMALIZE", "Normalize", "Unit vector; a zero vector returns zero"),
)
SCALAR_VECTOR_OPS = {"DOT_PRODUCT", "LENGTH", "DISTANCE"}


def _visibility(node, inputs, outputs):
    # Never delete/recreate sockets when switching operations: saved links and
    # identifiers remain valid. Linked inactive sockets remain inspectable.
    for collection, active in ((node.inputs, inputs), (node.outputs, outputs)):
        for socket in collection:
            hidden = socket.name not in active and not socket.is_linked
            if socket.hide != hidden:
                socket.hide = hidden


def _operation_changed(node, _context=None):
    if node.bl_idname == "YUGUS_NODE_math":
        inputs = {"A"}
        if node.operation != "ABSOLUTE":
            inputs.add("B")
        if node.operation == "MULTIPLY_ADD":
            inputs.add("C")
        _visibility(node, inputs, {"Value"})
    elif node.bl_idname == "YUGUS_NODE_vector_math":
        inputs = {"Vector A"}
        if node.operation in {"ADD", "SUBTRACT", "DOT_PRODUCT", "CROSS_PRODUCT", "DISTANCE"}:
            inputs.add("Vector B")
        if node.operation == "SCALE":
            inputs.add("Scale")
        _visibility(node, inputs, {"Value"} if node.operation in SCALAR_VECTOR_OPS else {"Vector"})


def _float_input(node, name, default=0.0):
    socket = node.inputs.new(FLOAT, name)
    socket.default_value = default
    return socket


def _vector_input(node, name, default=(0.0, 0.0, 0.0)):
    socket = node.inputs.new(VECTOR, name)
    socket.default_value = default
    return socket


class YUGUS_NS_vector3(bpy.types.NodeSocket):
    bl_idname = VECTOR
    bl_label = "Vector"
    default_value: bpy.props.FloatVectorProperty(name="Vector", size=3, default=(0.0, 0.0, 0.0))

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.35, 0.35, 0.8, 1.0)


class YUGUS_NODE_float_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_float_value"
    bl_label = "Float"
    bl_icon = "DRIVER"
    bl_description = "A numeric constant or animated value without a 0–1 limit"
    value: bpy.props.FloatProperty(name="Value", default=0.0)

    def init(self, _context):
        self._init_base(180)
        self.outputs.new(FLOAT, "Value")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "value")


class YUGUS_NODE_math(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_math"
    bl_label = "Math"
    bl_description = "Common float arithmetic, minimum, maximum and absolute value"
    operation: bpy.props.EnumProperty(name="Operation", items=SCALAR_OPERATIONS, default="ADD", update=_operation_changed)

    def init(self, _context):
        self._init_base(200)
        _float_input(self, "A")
        _float_input(self, "B", 1.0)
        _float_input(self, "C")
        self.outputs.new(FLOAT, "Value")
        _operation_changed(self)

    def update(self):
        _operation_changed(self)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "operation", text="")


class YUGUS_NODE_clamp(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_clamp"
    bl_label = "Clamp"
    bl_description = "Limit a numeric value between Min and Max"

    def init(self, _context):
        self._init_base(180)
        _float_input(self, "Value")
        _float_input(self, "Min")
        _float_input(self, "Max", 1.0)
        self.outputs.new(FLOAT, "Value")


class YUGUS_NODE_map_range(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_map_range"
    bl_label = "Map Range"
    bl_description = "Map a value from an input range to an output range"
    clamp_result: bpy.props.BoolProperty(name="Clamp", default=True, description="Clamp the mapped factor to 0–1")

    def init(self, _context):
        self._init_base(210)
        for name, value in (("Value", 0.0), ("From Min", 0.0), ("From Max", 1.0), ("To Min", 0.0), ("To Max", 1.0)):
            _float_input(self, name, value)
        self.outputs.new(FLOAT, "Value")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "clamp_result")


class YUGUS_NODE_float_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_float_mix"
    bl_label = "Mix Float"
    bl_description = "Interpolate numeric values A and B using Factor"
    clamp_factor: bpy.props.BoolProperty(name="Clamp Factor", default=True)

    def init(self, _context):
        self._init_base(200)
        _float_input(self, "Factor", 0.5)
        _float_input(self, "A")
        _float_input(self, "B", 1.0)
        self.outputs.new(FLOAT, "Value")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "clamp_factor")


class YUGUS_NODE_vector_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_vector_value"
    bl_label = "Vector"
    bl_description = "A three-component vector constant or animated value"
    value: bpy.props.FloatVectorProperty(name="Vector", size=3, default=(0.0, 0.0, 0.0))

    def init(self, _context):
        self._init_base(200)
        self.outputs.new(VECTOR, "Vector")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "value")


class YUGUS_NODE_combine_xyz(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_combine_xyz"
    bl_label = "Combine XYZ"
    bl_description = "Combine three numeric values into a vector"

    def init(self, _context):
        self._init_base(180)
        for name in ("X", "Y", "Z"):
            _float_input(self, name)
        self.outputs.new(VECTOR, "Vector")


class YUGUS_NODE_separate_xyz(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_separate_xyz"
    bl_label = "Separate XYZ"
    bl_description = "Separate a vector into three numeric values"

    def init(self, _context):
        self._init_base(180)
        _vector_input(self, "Vector")
        for name in ("X", "Y", "Z"):
            self.outputs.new(FLOAT, name)


class YUGUS_NODE_vector_math(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_vector_math"
    bl_label = "Vector Math"
    bl_description = "Common vector arithmetic and directional measurements"
    operation: bpy.props.EnumProperty(name="Operation", items=VECTOR_OPERATIONS, default="ADD", update=_operation_changed)

    def init(self, _context):
        self._init_base(220)
        _vector_input(self, "Vector A")
        _vector_input(self, "Vector B")
        _float_input(self, "Scale", 1.0)
        self.outputs.new(VECTOR, "Vector")
        self.outputs.new(FLOAT, "Value")
        _operation_changed(self)

    def update(self):
        _operation_changed(self)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "operation", text="")


class YUGUS_NODE_vector_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_vector_mix"
    bl_label = "Mix Vector"
    bl_description = "Interpolate vectors A and B using Factor"
    clamp_factor: bpy.props.BoolProperty(name="Clamp Factor", default=True)

    def init(self, _context):
        self._init_base(220)
        _float_input(self, "Factor", 0.5)
        _vector_input(self, "A")
        _vector_input(self, "B", (1.0, 1.0, 1.0))
        self.outputs.new(VECTOR, "Vector")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "clamp_factor")


NODE_CLASSES = (
    YUGUS_NODE_float_value, YUGUS_NODE_math, YUGUS_NODE_clamp,
    YUGUS_NODE_map_range, YUGUS_NODE_float_mix, YUGUS_NODE_vector_value,
    YUGUS_NODE_combine_xyz, YUGUS_NODE_separate_xyz, YUGUS_NODE_vector_math,
    YUGUS_NODE_vector_mix,
)
CLASSES = (YUGUS_NS_vector3,) + NODE_CLASSES
NODE_TYPES = frozenset(cls.bl_idname for cls in NODE_CLASSES)
VALUE_ENTRIES = tuple((cls.bl_idname, cls.bl_label) for cls in (YUGUS_NODE_float_value, YUGUS_NODE_vector_value))
SCALAR_ENTRIES = tuple((cls.bl_idname, cls.bl_label) for cls in (YUGUS_NODE_math, YUGUS_NODE_clamp, YUGUS_NODE_map_range))
VECTOR_ENTRIES = tuple((cls.bl_idname, cls.bl_label) for cls in (YUGUS_NODE_combine_xyz, YUGUS_NODE_separate_xyz, YUGUS_NODE_vector_math))


def _output(node, values):
    result = dict(values)
    for socket in node.outputs:
        if socket.name in values:
            result[socket.identifier] = values[socket.name]
    return result


def _number(context, node, name):
    socket = node.inputs[name]
    return math_ops.finite_number(context.input(node, name, socket.default_value))


def _vector(context, node, name):
    socket = node.inputs[name]
    return math_ops.finite_vector(context.input(node, name, socket.default_value))


def evaluate(context, node):
    kind = node.bl_idname
    if kind not in NODE_TYPES:
        return None
    if node.mute:
        result = {socket.name: (0.0, 0.0, 0.0) if socket.bl_idname == VECTOR else 0.0 for socket in node.outputs}
        passthroughs = {"YUGUS_NODE_math": ("A", "Value"), "YUGUS_NODE_clamp": ("Value", "Value"),
                        "YUGUS_NODE_map_range": ("Value", "Value"), "YUGUS_NODE_float_mix": ("A", "Value"),
                        "YUGUS_NODE_vector_math": ("Vector A", "Vector"), "YUGUS_NODE_vector_mix": ("A", "Vector")}
        if kind in passthroughs:
            input_name, output_name = passthroughs[kind]
            read = _vector if node.inputs[input_name].bl_idname == VECTOR else _number
            result[output_name] = read(context, node, input_name)
        return _output(node, result)
    if kind == "YUGUS_NODE_float_value":
        return _output(node, {"Value": math_ops.finite_number(node.value)})
    if kind == "YUGUS_NODE_vector_value":
        return _output(node, {"Vector": math_ops.finite_vector(node.value)})
    if kind == "YUGUS_NODE_math":
        operation = node.operation
        a = _number(context, node, "A")
        b = _number(context, node, "B") if operation != "ABSOLUTE" else 0.0
        c = _number(context, node, "C") if operation == "MULTIPLY_ADD" else 0.0
        return _output(node, {"Value": math_ops.scalar(operation, a, b, c)})
    if kind == "YUGUS_NODE_clamp":
        return _output(node, {"Value": math_ops.clamp(_number(context, node, "Value"), _number(context, node, "Min"), _number(context, node, "Max"))})
    if kind == "YUGUS_NODE_map_range":
        args = [_number(context, node, name) for name in ("Value", "From Min", "From Max", "To Min", "To Max")]
        return _output(node, {"Value": math_ops.map_range(*args, clamp_result=node.clamp_result)})
    if kind == "YUGUS_NODE_float_mix":
        return _output(node, {"Value": math_ops.mix(_number(context, node, "A"), _number(context, node, "B"), _number(context, node, "Factor"), clamp_factor=node.clamp_factor)})
    if kind == "YUGUS_NODE_combine_xyz":
        return _output(node, {"Vector": tuple(_number(context, node, name) for name in ("X", "Y", "Z"))})
    if kind == "YUGUS_NODE_separate_xyz":
        return _output(node, dict(zip(("X", "Y", "Z"), _vector(context, node, "Vector"))))
    if kind == "YUGUS_NODE_vector_math":
        operation = node.operation
        a = _vector(context, node, "Vector A")
        b = _vector(context, node, "Vector B") if operation in {"ADD", "SUBTRACT", "DOT_PRODUCT", "CROSS_PRODUCT", "DISTANCE"} else (0.0, 0.0, 0.0)
        scale = _number(context, node, "Scale") if operation == "SCALE" else 1.0
        result = math_ops.vector(operation, a, b, scale)
        values = {"Vector": (0.0, 0.0, 0.0), "Value": 0.0}
        values["Value" if operation in SCALAR_VECTOR_OPS else "Vector"] = result
        return _output(node, values)
    if kind == "YUGUS_NODE_vector_mix":
        return _output(node, {"Vector": math_ops.vector_mix(_vector(context, node, "A"), _vector(context, node, "B"), _number(context, node, "Factor"), clamp_factor=node.clamp_factor)})
    raise ValueError(f"Unsupported math node: {kind}")
