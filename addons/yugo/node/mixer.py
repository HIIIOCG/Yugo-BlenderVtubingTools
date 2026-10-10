"""One typed Mixer, with persistent socket banks and pure data evaluation."""

from dataclasses import dataclass
import math

import bpy
from mathutils import Matrix

from . import math_ops, processing
from .constants import (
    SOCKET_BONE_MAP, SOCKET_EXPRESSION_MAP, SOCKET_EXPRESSIONS,
    SOCKET_MOTION, SOCKET_MOTION_MASK,
)
from .data import BoneMapData, ExpressionFrame, ExpressionMapData, HumanoidMotion
from .map_values import mix_bone_maps, mix_expression_maps
from .nodes import YUGUS_NodeBase

NODE = "YUGUS_NODE_mixer"
FLOAT = "YUGUS_NS_float"
VECTOR = "YUGUS_NS_vector3"
PHYSICS_DESCRIPTION = "YUGUS_NS_physics_description"
OBJECT_TRANSFORMS = "YUGUS_NS_object_transforms"
TYPE_SPECS = {
    "FLOAT": ("Float", FLOAT),
    "VECTOR": ("Vector", VECTOR),
    "HUMANOID_MOTION": ("Humanoid Motion", SOCKET_MOTION),
    "BONE_TRANSFORMS": ("Bone Transforms", "YUGUS_NS_bone_rotation"),
    "EXPRESSIONS": ("Expressions", SOCKET_EXPRESSIONS),
    "BONE_MAP": ("Bone Map", SOCKET_BONE_MAP),
    "EXPRESSION_MAP": ("Expression Map", SOCKET_EXPRESSION_MAP),
    "PHYSICS_DESCRIPTION": ("Physics Description", PHYSICS_DESCRIPTION),
    "OBJECT_TRANSFORMS": ("Object Transforms", OBJECT_TRANSFORMS),
}
TYPE_ITEMS = tuple((kind, title, "") for kind, (title, _socket) in TYPE_SPECS.items())
BLEND_TYPES = frozenset({
    "FLOAT", "VECTOR", "HUMANOID_MOTION", "BONE_TRANSFORMS",
    "EXPRESSIONS", "OBJECT_TRANSFORMS",
})
_layout_busy = set()


@dataclass(frozen=True)
class InactiveOutput:
    node_name: str
    socket_name: str
    selected_type: str


def require_active(value):
    """Host input readers call this before consuming a linked Mixer value."""
    if isinstance(value, InactiveOutput):
        raise ValueError(
            f"Mixer {value.node_name}: {value.socket_name} is inactive; "
            f"selected type is {value.selected_type}. Change Type or reconnect Result"
        )
    return value


def socket_name(kind, role):
    return f"{TYPE_SPECS[kind][0]} {role}"


def input_names(node):
    kind = node.data_type
    if kind == "PHYSICS_DESCRIPTION":
        return tuple(socket_name(kind, f"Input {index + 1}")
                     for index in range(node.physics_input_count))
    return (socket_name(kind, "A"), socket_name(kind, "B"))


def output_name(node):
    return socket_name(node.data_type, "Result")


def active_socket_names(node):
    inputs = set(input_names(node))
    if node.data_type in BLEND_TYPES:
        inputs.add(socket_name(node.data_type, "Factor"))
    if node.data_type == "HUMANOID_MOTION":
        inputs.add(socket_name(node.data_type, "Mask"))
    return inputs, {output_name(node)}


def _socket_slot(socket):
    # Factor has a shared display name; keep its bank identity separately so
    # switching Type and reopening old files preserves values, IDs and wires.
    return getattr(socket, "mixer_slot", "") or socket.name.removeprefix("Inactive · ")


def _ensure_socket(collection, socket_type, name, default=None):
    socket = next((item for item in collection
                   if _socket_slot(item) == name), None)
    if socket is None:
        socket = collection.new(socket_type, name)
        if default is not None and hasattr(socket, "default_value"):
            socket.default_value = default
    elif socket.bl_idname != socket_type:
        raise ValueError(f"Mixer socket {name} has an incompatible saved type")
    return socket


def refresh_layout(node, _context=None):
    """Never remove a bank: switching Type preserves native IDs and wires."""
    pointer = node.as_pointer()
    if pointer in _layout_busy:
        return
    _layout_busy.add(pointer)
    try:
        kind = node.data_type
        socket_type = TYPE_SPECS[kind][1]
        for index, name in enumerate(input_names(node)):
            default = None
            if kind == "FLOAT":
                default = float(index)
            elif kind == "VECTOR":
                default = (float(index),) * 3
            _ensure_socket(node.inputs, socket_type, name, default)
        if kind in BLEND_TYPES:
            default = 0.5 if kind in {"FLOAT", "VECTOR", "OBJECT_TRANSFORMS"} else 1.0
            factor = _ensure_socket(node.inputs, FLOAT, socket_name(kind, "Factor"), default)
            factor.mixer_slot = socket_name(kind, "Factor")
            index = next(index for index, socket in enumerate(node.inputs) if socket == factor)
            if index:
                node.inputs.move(index, 0)
        if kind == "HUMANOID_MOTION":
            _ensure_socket(node.inputs, SOCKET_MOTION_MASK, socket_name(kind, "Mask"))
        _ensure_socket(node.outputs, socket_type, output_name(node))
        active_inputs, active_outputs = active_socket_names(node)
        for collection, active in ((node.inputs, active_inputs), (node.outputs, active_outputs)):
            for socket in collection:
                slot = _socket_slot(socket)
                selected = slot in active
                socket.hide = not selected and not socket.is_linked
                socket.hide_value = not selected
                # NodeSocket.label is read-only. Rename only the visible name;
                # the native identifier and semantic slot stay unchanged.
                name = "Factor" if selected and getattr(socket, "mixer_slot", "") else slot
                socket.name = name if selected else "Inactive · " + slot
    finally:
        _layout_busy.discard(pointer)


class YUGUS_NODE_mixer(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Mixer"
    bl_icon = "NODETREE"
    bl_description = "Blend typed values or merge maps and physics descriptions"
    data_type: bpy.props.EnumProperty(name="Type", items=TYPE_ITEMS, default="FLOAT", update=refresh_layout)
    clamp_factor: bpy.props.BoolProperty(name="Clamp Factor", default=True)
    expression_mode: bpy.props.EnumProperty(name="Mode", items=(
        ("REPLACE", "Replace", "Blend B over A"),
        ("ADD", "Add", "Add weighted B values"),
        ("MAX", "Maximum", "Keep the stronger weighted value"),
    ), default="REPLACE")
    physics_input_count: bpy.props.IntProperty(default=2, min=2, max=64,
                                             options={"HIDDEN"}, update=refresh_layout)

    def init(self, _context):
        self._init_base(245)
        refresh_layout(self)

    def update(self):
        refresh_layout(self)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "data_type")
        if self.data_type in {"FLOAT", "VECTOR"}:
            layout.prop(self, "clamp_factor")
        elif self.data_type == "EXPRESSIONS":
            layout.prop(self, "expression_mode")
        elif self.data_type in {"BONE_MAP", "EXPRESSION_MAP"}:
            layout.label(text="Merge · B overrides matching entries")
        elif self.data_type == "PHYSICS_DESCRIPTION":
            layout.label(text="Merge · identical IDs deduplicate")
            layout.label(text="Conflicting descriptions report an error")
            row = layout.row(align=True)
            for action, label, icon in (("ADD", "Add Input", "ADD"), ("REMOVE", "Remove Last", "REMOVE")):
                button = row.operator("yugu_node_studio.mixer_input", text=label, icon=icon)
                button.tree_name, button.node_name, button.action = self.id_data.name, self.name, action
        active_inputs, active_outputs = active_socket_names(self)
        count = sum(socket.is_linked and _socket_slot(socket) not in active
                    for sockets, active in ((self.inputs, active_inputs), (self.outputs, active_outputs))
                    for socket in sockets)
        if count:
            layout.label(text=f"{count} inactive connection(s) preserved", icon="INFO")


class YUGUS_OT_mixer_input(bpy.types.Operator):
    bl_idname = "yugu_node_studio.mixer_input"
    bl_label = "Change Mixer Inputs"
    bl_options = {"UNDO"}
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    action: bpy.props.EnumProperty(items=(("ADD", "Add", ""), ("REMOVE", "Remove", "")))

    def execute(self, _context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != NODE or node.data_type != "PHYSICS_DESCRIPTION":
            self.report({"WARNING"}, "Physics Description Mixer is unavailable")
            return {"CANCELLED"}
        if self.action == "ADD":
            if node.physics_input_count >= 64:
                self.report({"WARNING"}, "Mixer supports up to 64 inputs")
                return {"CANCELLED"}
            node.physics_input_count += 1
        else:
            if node.physics_input_count <= 2:
                self.report({"WARNING"}, "Keep at least two inputs")
                return {"CANCELLED"}
            last = node.inputs.get(input_names(node)[-1])
            if last is not None and last.is_linked:
                self.report({"WARNING"}, "Disconnect the last input before removing it")
                return {"CANCELLED"}
            node.physics_input_count -= 1
        return {"FINISHED"}


def _typed(context, node, name, kind, default):
    value = require_active(context.input(node, name, default))
    if not isinstance(value, kind):
        raise ValueError(f"Mixer {name} requires {kind.__name__}")
    return value


def _factor(context, node):
    slot = socket_name(node.data_type, "Factor")
    socket = next(socket for socket in node.inputs if _socket_slot(socket) == slot)
    return math_ops.finite_number(require_active(context.input(
        node, socket.name, socket.default_value,
    )))


def _matrix_components(record):
    matrix = Matrix(record.matrix_world)
    if len(matrix) != 4 or len(matrix[0]) != 4 or any(
            not math.isfinite(float(value)) for row in matrix for value in row):
        raise ValueError(f"Object Transform {record.object_name} requires a finite 4x4 matrix")
    if any(abs(matrix[3][index] - (1.0 if index == 3 else 0.0)) > 1e-6 for index in range(4)):
        raise ValueError(f"Object Transform {record.object_name} must be affine")
    if matrix.to_3x3().determinant() <= 1e-12:
        raise ValueError(f"Object Transform {record.object_name} cannot blend reflected or singular scale")
    position, rotation, scale = matrix.decompose()
    reconstructed = Matrix.LocRotScale(position, rotation, scale)
    tolerance = 2e-6 * max(1.0, max(abs(value) for row in matrix for value in row))
    if any(abs(matrix[row][column] - reconstructed[row][column]) > tolerance
           for row in range(4) for column in range(4)):
        raise ValueError(f"Object Transform {record.object_name} cannot blend shear")
    return position, rotation.normalized(), scale


def mix_object_transforms(base, overlay, factor):
    """Blend identical targets without merging incompatible physics sessions."""
    from .physics_data import ObjectTransform, ObjectTransforms
    if not isinstance(base, ObjectTransforms) or not isinstance(overlay, ObjectTransforms):
        raise ValueError("Connect Object Transforms to A / B")
    for field in ("world_id", "revision"):
        a, b = getattr(base, field), getattr(overlay, field)
        if a and b and a != b:
            raise ValueError(f"Object Transforms use different {field}")
    if base.world_id and overlay.world_id and base.tick != overlay.tick:
        raise ValueError("Object Transforms use different physics ticks")
    a = {record.object_name: record for record in base.records}
    b = {record.object_name: record for record in overlay.records}
    if len(a) != len(base.records) or len(b) != len(overlay.records):
        raise ValueError("Object Transforms contain duplicate targets")
    if a.keys() != b.keys():
        raise ValueError("Object Transform Mixer requires the same targets")
    factor = min(max(math_ops.finite_number(factor), 0.0), 1.0)
    records = []
    for name, record in a.items():
        start, finish = _matrix_components(record), _matrix_components(b[name])
        matrix = Matrix.LocRotScale(start[0].lerp(finish[0], factor),
                                   start[1].slerp(finish[1], factor).normalized(),
                                   start[2].lerp(finish[2], factor))
        records.append(ObjectTransform(name, tuple(tuple(float(value) for value in row) for row in matrix)))
    owner = base if base.world_id else overlay if overlay.world_id else base
    return ObjectTransforms(tuple(records), world_id=base.world_id or overlay.world_id,
                            revision=base.revision or overlay.revision, tick=owner.tick)


def _value(context, node):
    from . import flow
    kind = node.data_type
    names = input_names(node)
    if node.mute:
        if kind == "FLOAT":
            return math_ops.finite_number(require_active(context.input(node, names[0], node.inputs[names[0]].default_value)))
        if kind == "VECTOR":
            return math_ops.finite_vector(require_active(context.input(node, names[0], node.inputs[names[0]].default_value)))
        from .physics_data import ObjectTransforms, PhysicsDescription
        defaults = {
            "HUMANOID_MOTION": HumanoidMotion(), "BONE_TRANSFORMS": flow.BoneTransforms(),
            "EXPRESSIONS": ExpressionFrame(), "BONE_MAP": BoneMapData("", {}, {}),
            "EXPRESSION_MAP": ExpressionMapData({}), "PHYSICS_DESCRIPTION": PhysicsDescription(),
            "OBJECT_TRANSFORMS": ObjectTransforms(),
        }
        value = _typed(context, node, names[0], type(defaults[kind]), defaults[kind])
        return value.copy() if hasattr(value, "copy") else value
    if kind == "PHYSICS_DESCRIPTION":
        from .physics_data import PhysicsDescription, merge_descriptions
        values = [require_active(context.input(node, name, None)) for name in names]
        if any(value is not None and not isinstance(value, PhysicsDescription) for value in values):
            raise ValueError("Physics Description Mixer requires Physics Description inputs")
        return merge_descriptions(values)
    if kind == "FLOAT":
        values = [math_ops.finite_number(require_active(context.input(node, name, node.inputs[name].default_value))) for name in names]
        return math_ops.mix(*values, _factor(context, node), clamp_factor=node.clamp_factor)
    if kind == "VECTOR":
        values = [math_ops.finite_vector(require_active(context.input(node, name, node.inputs[name].default_value))) for name in names]
        return math_ops.vector_mix(*values, _factor(context, node), clamp_factor=node.clamp_factor)
    if kind == "HUMANOID_MOTION":
        values = [_typed(context, node, name, HumanoidMotion, HumanoidMotion()) for name in names]
        for value in values:
            flow._validate_bone_transforms(flow.BoneTransforms(bones=value.bones, root=value.root))
        mask = require_active(context.input(node, socket_name(kind, "Mask"), None))
        return processing.mix_motion(*values, _factor(context, node), mask)
    if kind == "BONE_TRANSFORMS":
        values = [_typed(context, node, name, flow.BoneTransforms, flow.BoneTransforms()) for name in names]
        return flow.mix_bone_transforms(*values, _factor(context, node))
    if kind == "EXPRESSIONS":
        values = [_typed(context, node, name, ExpressionFrame, ExpressionFrame()) for name in names]
        for value in values:
            for weight in value.values.values():
                math_ops.finite_number(weight)
        return processing.mix_expressions(*values, _factor(context, node), node.expression_mode)
    if kind == "BONE_MAP":
        values = [_typed(context, node, name, BoneMapData, BoneMapData("", {}, {})) for name in names]
        return mix_bone_maps(*values)
    if kind == "EXPRESSION_MAP":
        values = [_typed(context, node, name, ExpressionMapData, ExpressionMapData({})) for name in names]
        return mix_expression_maps(*values)
    if kind == "OBJECT_TRANSFORMS":
        from .physics_data import ObjectTransforms
        values = [_typed(context, node, name, ObjectTransforms, ObjectTransforms(())) for name in names]
        return mix_object_transforms(*values, _factor(context, node))
    raise ValueError(f"Unsupported Mixer type {kind}")


def evaluate(context, node):
    if node.bl_idname != NODE:
        return None
    value = _value(context, node)
    selected = output_name(node)
    result = {"Result": value}
    for socket in node.outputs:
        output = value if socket.name == selected else InactiveOutput(
            node.name, socket.name, TYPE_SPECS[node.data_type][0],
        )
        result[socket.name] = result[socket.identifier] = output
    return result


MENU_ENTRY = (NODE, "Mixer")
MENU_ENTRIES = (MENU_ENTRY,)
CLASSES = (YUGUS_NODE_mixer, YUGUS_OT_mixer_input)
