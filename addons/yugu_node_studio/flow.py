"""Clock events pull pure data conversions; only Apply nodes mutate characters."""
from collections import deque
from dataclasses import dataclass, field
import math
import time

import bpy
from mathutils import Quaternion

from . import runtime, scheduler, targets, workflow
from .constants import TREE_ID, SOCKET_TRIGGER, SOCKET_MOTION, SOCKET_BONE_MAP, SOCKET_EXPRESSIONS, SOCKET_EXPRESSION_MAP, SOCKET_MOTION_MASK
from .data import HumanoidMotion, HumanoidMask, BoneMapData, ExpressionFrame, ExpressionMapData, SpringData
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase
from .spring_solver import NativeSpringSolver

EVENT = "YUGUS_NS_update_event"
ROTATIONS = "YUGUS_NS_bone_rotation"
SHAPES = "YUGUS_NS_shape_values"
STEP = 1.0 / 60.0  # Solver calibration is independent of output sampling Hz.
_clocks = {}
_springs = {}
_last_eval = {}
_owners = {}
_official = {}
_events = {}  # Primitive control state; no retained Blender RNA.
_edges = {}   # Numeric action-input rising-edge history.
_draining_workflow = False


@dataclass
class BoneRotations:
    armature_name: str = ""
    values: dict = field(default_factory=dict)
    spring_data: object = None


@dataclass
class ShapeValues:
    values: dict = field(default_factory=dict)  # (mesh object name, shape key name) -> value


class YUGUS_NS_update_event(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = EVENT
    bl_label = "Update Event"
    def draw_color(self, context, node):
        return (0.85, 0.12, 0.12, 1.0)


class YUGUS_NS_bone_rotation(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = ROTATIONS
    bl_label = "Bone Rotation"
    def draw_color(self, context, node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NS_shape_values(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SHAPES
    bl_label = "Shape Keys Values"
    def draw_color(self, context, node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NODE_hz_trigger(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_hz_trigger"
    bl_label = "Hz Trigger"
    bl_icon = "TIME"
    hz: bpy.props.EnumProperty(name="Rate", items=[(str(h), f"{h} Hz", "") for h in (8, 12, 24, 30, 60)], default="60")
    def init(self, context):
        self._init_base(180)
        self.outputs.new(EVENT, "Trigger")
    def draw_buttons(self, context, layout):
        layout.prop(self, "hz")


class YUGUS_NODE_motion_to_rotation(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_motion_to_rotation"
    bl_label = "Motion to Bone Rotation"
    bl_icon = "BONE_DATA"
    motion_type: bpy.props.EnumProperty(name="Motion Type", items=[("VRM_HUMANOID", "VRM Humanoid", "Canonical humanoid motion")])
    def init(self, context):
        self._init_base(230)
        self.inputs.new(SOCKET_MOTION, "Motion")
        self.inputs.new(SOCKET_BONE_MAP, "Bone Map")
        self.outputs.new(ROTATIONS, "Bone Rotation")
    def draw_buttons(self, context, layout):
        layout.prop(self, "motion_type", text="")


class YUGUS_NODE_bone_rotation_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_bone_rotation_mix"
    bl_label = "Bone Rotation Mix"
    bl_icon = "NODETREE"
    factor: bpy.props.FloatProperty(name="Factor", default=1.0, min=0.0, max=1.0, subtype="FACTOR")

    def init(self, context):
        self._init_base(230)
        self.inputs.new(ROTATIONS, "Base")
        self.inputs.new(ROTATIONS, "Overlay")
        self.outputs.new(ROTATIONS, "Bone Rotation")

    def draw_buttons(self, context, layout):
        layout.prop(self, "factor", slider=True)


def mix_bone_rotations(base, overlay, factor):
    """Blend local pose quaternions without writing to Blender or input values."""
    if not isinstance(base, BoneRotations) or not isinstance(overlay, BoneRotations):
        raise ValueError("Connect Bone Rotation to Base / Overlay")
    if base.armature_name and overlay.armature_name and base.armature_name != overlay.armature_name:
        raise ValueError("Bone Rotation Mix requires the same Armature")
    armature = base.armature_name or overlay.armature_name
    result = {name: q.copy() for name, q in base.values.items()}
    factor = min(max(float(factor), 0.0), 1.0)
    for name, rotation in overlay.values.items():
        influence = factor
        start = base.values.get(name, Quaternion()).normalized()
        result[name] = start.slerp(rotation.normalized(), influence).normalized()
    # Preserve physics ownership / same-armature safeguards through conversion.
    spring = base.spring_data if base.spring_data is not None else overlay.spring_data
    return BoneRotations(armature, result, spring)


class YUGUS_NODE_expression_to_shapes(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_to_shapes"
    bl_label = "Expressions to ShapeKeysValues"
    bl_icon = "SHAPEKEY_DATA"
    def init(self, context):
        self._init_base(270)
        self.inputs.new(SOCKET_EXPRESSIONS, "Expressions")
        self.inputs.new(SOCKET_EXPRESSION_MAP, "Expression Map")
        self.outputs.new(SHAPES, "Shape Keys Values")
    def draw_buttons(self, context, layout):
        layout.label(text="VRM mapped morph targets · downstream pull")


class _Apply:
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)
    last_summary: bpy.props.StringProperty(default="Connect Hz Trigger", options={"SKIP_SAVE"})
    def draw_buttons(self, context, layout):
        layout.prop(self, "enabled")
        layout.label(text=self.last_summary)


class YUGUS_NODE_apply_rotation(_Apply, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_apply_rotation"
    bl_label = "BoneRotations to Armature"
    armature: bpy.props.PointerProperty(name="Armature", type=bpy.types.Object, poll=targets.armature_poll)
    bl_icon = "ARMATURE_DATA"
    def init(self, context):
        self._init_base(260)
        self.inputs.new(EVENT, "Enter")
        self.inputs.new(ROTATIONS, "Bone Rotation")
        self.inputs.new(targets.ARMATURES, "Armatures")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        _Apply.draw_buttons(self, context, layout)


class YUGUS_NODE_apply_shapes(targets.ObjectSelection, _Apply, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_apply_shapes"
    bl_label = "ShapeKeysValues to Objects List"
    bl_icon = "MESH_DATA"
    def init(self, context):
        self._init_base(270)
        self.inputs.new(EVENT, "Enter")
        self.inputs.new(SHAPES, "Shape Keys Values")
        self.inputs.new(targets.OBJECTS, "Objects")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, context, layout):
        _Apply.draw_buttons(self, context, layout)


class YUGUS_OT_flow_spring_reload(bpy.types.Operator):
    bl_idname = "yugu_node_studio.flow_spring_reload"
    bl_label = "Reload Spring"
    bl_description = "Discard physics history; rebuild on the next downstream trigger"
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {"CANCELLED"}
        reload_spring(node)
        return {"FINISHED"}


APPLY_TYPES = {YUGUS_NODE_apply_rotation.bl_idname, YUGUS_NODE_apply_shapes.bl_idname}
CONTROL_TYPES = {
    "YUGUS_NODE_flow_if", "YUGUS_NODE_flow_gate", "YUGUS_NODE_flow_delay",
    "YUGUS_NODE_flow_wait_until", "YUGUS_NODE_flow_once",
    "YUGUS_NODE_flow_sequence", "YUGUS_NODE_flow_every_n",
}
FLOW_TYPES = APPLY_TYPES | CONTROL_TYPES
RELOAD_TYPES = {"YUGUS_NODE_vrm_data", "YUGUS_NODE_spring_simulation"}
CLASSES = (YUGUS_NS_update_event, YUGUS_NS_bone_rotation, YUGUS_NS_shape_values,
           YUGUS_NODE_hz_trigger, YUGUS_NODE_motion_to_rotation, YUGUS_NODE_bone_rotation_mix, YUGUS_NODE_expression_to_shapes,
           YUGUS_NODE_apply_rotation, YUGUS_NODE_apply_shapes, YUGUS_OT_flow_spring_reload)


def reload_spring(node):
    """Use the same history reset for the button and a Reload event."""
    uid = str(node.runtime_id)
    _springs.pop(uid, None)
    _last_eval.pop(uid, None)
    node.spring_active = False
    node.spring_last_error = ""
    node.spring_status = "Waiting for downstream trigger"


def reload_vrm(node):
    """Restore consumers and retire physics before replacing a VRM snapshot."""
    from . import vrm_cache
    for dependent in node.id_data.nodes:
        if _uid(node) not in _upstream_ids(dependent):
            continue
        if dependent.bl_idname == "YUGUS_NODE_spring_simulation":
            reload_spring(dependent)
        elif dependent.bl_idname in APPLY_TYPES:
            release(_uid(dependent))
    return vrm_cache.reload_node(node)


def calibrate_ifacial(node):
    """Capture the received head frame for both button and event calibration."""
    from . import network
    head = network.ifacial_head(str(node.runtime_id))
    if head is None:
        raise ValueError("No iFacialMocap head frame received yet")
    node.calibration = head
    node.last_error = ""
    return head


def _spring_boundary_at(state, timestamp):
    """Sample the driving pose at physics time, retaining unfinished time debt."""
    samples = state["boundaries"]
    while len(samples) > 1 and samples[1][0] <= timestamp:
        samples.popleft()
    before_time, before = samples[0]
    if len(samples) == 1:
        return before
    after_time, after = samples[1]
    factor = min(max((timestamp - before_time) / (after_time - before_time), 0.0), 1.0)
    return before.interpolate(after, factor)


def spring_value(node, data, now, elapsed):
    if not isinstance(data, SpringData):
        raise ValueError("Connect loaded VRM Spring Bone Map")
    armature = bpy.data.objects.get(data.armature_name)
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("Spring armature is unavailable")
    uid = str(node.runtime_id)
    state = _springs.get(uid)
    if state is None or state["data"] is not data:
        solver = NativeSpringSolver(armature, data)
        initial = {name: armature.pose.bones[name].matrix_basis.to_quaternion()
                   for name in solver.output_bone_names if name in armature.pose.bones}
        origin = now - max(0.0, elapsed)
        state = {"solver": solver, "data": data, "last": origin, "origin": origin,
                 "debt": 0.0, "rotations": initial, "previous_rotations": initial,
                 "steps": 0, "boundaries": deque(((origin, solver.sample_boundary(armature)),))}
        _springs[uid] = state
    boundary = state["solver"].sample_boundary(armature)
    if now > state["boundaries"][-1][0]:
        state["boundaries"].append((now, boundary))
    else:
        state["boundaries"][-1] = (state["boundaries"][-1][0], boundary)
    state["debt"] += max(0, now - state["last"])
    state["last"] = now
    count = min(int((state["debt"] + 1e-10) / STEP), 240)
    # Never discard elapsed time. A long UI stall leaves a visible catch-up debt.
    for index in range(count):
        physics_time = state["origin"] + (state["steps"] + index + 1) * STEP
        sampled = _spring_boundary_at(state, physics_time)
        state["previous_rotations"] = state["rotations"]
        state["rotations"] = state["solver"].step(armature, STEP, sampled)
    state["debt"] = max(0.0, state["debt"] - count * STEP)
    state["steps"] += count
    # One physics step of latency makes zero/two-step timer jitter continuous.
    # Interpolation changes presentation only; Verlet history stays fixed-rate.
    alpha = min(state["debt"] / STEP, 1.0)
    result = {}
    for name, rotation in state["rotations"].items():
        start = state["previous_rotations"].get(name, rotation)
        result[name] = start.slerp(rotation, alpha).normalized()
    node.spring_status = f"Pulled · {count} steps · {state['debt']:.3f}s pending"
    node.spring_last_error = ""
    return BoneRotations(armature.name, result, data)


def evaluate(context, node):
    kind = node.bl_idname
    if kind == YUGUS_NODE_bone_rotation_mix.bl_idname:
        return runtime._output(node, "Bone Rotation", mix_bone_rotations(
            context.input(node, "Base", BoneRotations()),
            context.input(node, "Overlay", BoneRotations()), node.factor))
    if kind == "YUGUS_NODE_armature_value":
        return runtime._output(node, "Armatures", targets.Armatures((node.armature.name,) if node.armature else ()))
    if kind == "YUGUS_NODE_objects_value":
        return runtime._output(node, "Objects", targets.selected_objects(node))
    if kind == "NodeReroute":
        return runtime._output(node, node.outputs[0].name, context.input(node, node.inputs[0].name, None))
    if kind == YUGUS_NODE_motion_to_rotation.bl_idname:
        motion = context.input(node, "Motion", HumanoidMotion())
        mapping = context.input(node, "Bone Map", None)
        pose = runtime._retarget_motion(motion, mapping)
        return runtime._output(node, "Bone Rotation", BoneRotations(pose.armature_name,
            {name: transform.rotation.copy() for name, transform in pose.bones.items() if transform.rotation is not None}))
    if kind == YUGUS_NODE_expression_to_shapes.bl_idname:
        mapping = context.input(node, "Expression Map", None)
        frame = context.input(node, "Expressions", ExpressionFrame())
        from . import keyboard
        frame = keyboard.mask_expressions(node, frame, mapping)
        values = {}
        if isinstance(mapping, ExpressionMapData):
            for definition in mapping.definitions.values():
                for target in definition.morph_targets:
                    values[(target.object_name, target.shape_key_name)] = 0.0
            for definition, weight in runtime._resolved_expression_values(frame, mapping).items():
                for target in definition.morph_targets:
                    key = (target.object_name, target.shape_key_name)
                    values[key] += weight * target.weight
        return runtime._output(node, "Shape Keys Values", ShapeValues({key: min(max(v, 0), 1) for key, v in values.items()}))
    if kind == "YUGUS_NODE_spring_simulation":
        data = context.input(node, "Spring Data", None)
        return runtime._output(node, "Bone Rotation", spring_value(node, data, getattr(context, "now", time.monotonic()), context.delta_time))
    return None


class PullContext(runtime.EvaluationContext):
    def __init__(self, tree, scene, now, delta):
        super().__init__(tree, scene, delta)
        self.now = now
        self.visited = set()
    def evaluate(self, node):
        uid = str(getattr(node, "runtime_id", "") or node.name)
        self.visited.add(uid)
        if uid in self.cache:
            return self.cache[uid]
        previous_delta = self.delta_time
        self.delta_time = max(0.0, self.now - _last_eval.get(uid, self.now - previous_delta))
        try:
            value = super().evaluate(node)
            _last_eval[uid] = self.now
            return value
        finally:
            self.delta_time = previous_delta


def _event_parent(node):
    socket = node.inputs.get("Enter") or node.inputs.get("Trigger")
    visited = set()
    while socket and socket.is_linked:
        link = socket.links[0]
        if link.is_muted or not link.is_valid:
            return None
        source = link.from_node
        if source.as_pointer() in visited or source.mute:
            return None
        visited.add(source.as_pointer())
        if source.bl_idname == YUGUS_NODE_hz_trigger.bl_idname:
            return source
        if source.bl_idname in FLOW_TYPES and link.from_socket.bl_idname == EVENT:
            return source if getattr(source, "enabled", True) else None
        if source.bl_idname != "NodeReroute":
            return None
        socket = source.inputs[0]
    return None


def _clock(node, visited=None):
    visited = set() if visited is None else visited
    uid = str(node.runtime_id)
    if uid in visited:
        node.last_summary = "Error · event cycle"
        return None
    visited.add(uid)
    parent = _event_parent(node)
    if parent is None:
        return None
    if parent.bl_idname == YUGUS_NODE_hz_trigger.bl_idname:
        return parent
    return _clock(parent, visited)


def _objects():
    return getattr(bpy.data, "objects", {})


def _object_key(obj):
    from . import extensions
    reference = extensions.object_ref(obj)
    return reference["session_uid"], reference["pointer"]


def _object_from_key(key):
    from . import extensions
    return extensions.resolve_object({"session_uid": key[0], "pointer": key[1]})


def _restore_target(target, base):
    kind, object_key, name = target
    obj = _object_from_key(object_key) if kind == "bone" else _objects().get(object_key)
    if obj is None:
        return
    if kind == "bone":
        from . import extensions
        if extensions.transform_owner(obj) is not None:
            return
        bone = obj.pose.bones.get(name) if obj.type == "ARMATURE" else None
        if bone:
            mode, quaternion, euler, axis = base
            bone.rotation_quaternion = quaternion
            bone.rotation_euler = euler
            bone.rotation_axis_angle = axis
            bone.rotation_mode = mode
    else:
        key = runtime._shape_key(object_key, name)
        if key:
            key.value = base


def release(uid):
    for target, base in _owners.pop(uid, {}).items():
        _restore_target(target, base)
    _release_official(uid)


def pose_writers(armature):
    """Existing per-bone Apply owners for an armature, without evaluating it."""
    key = _object_key(armature)
    writers = {uid for uid, owned in _owners.items()
               if any(target[0] == 'bone' and target[1] == key for target in owned)}
    # An official spring flag is retained even if the latest pull has no bone
    # values. It must be restored before a whole-armature owner takes over.
    writers.update(_official.get(key, {}).get("owners", ()))
    return tuple(writers)


def _release_official(uid, keep=None):
    for key, record in tuple(_official.items()):
        if key == keep:
            continue
        record["owners"].discard(uid)
        if not record["owners"]:
            armature = _object_from_key(key)
            spring = runtime._official_spring_bone(armature, record["data"]) if armature else None
            if spring is not None:
                spring.enable_animation = record["enabled"]
            del _official[key]


def apply(node, value, context=None):
    uid = str(node.runtime_id)
    desired = {}
    if node.bl_idname == YUGUS_NODE_apply_rotation.bl_idname:
        if not isinstance(value, BoneRotations):
            raise ValueError("Connect Bone Rotation")
        selection = targets.Armatures((node.armature.name,)) if node.armature else targets.Armatures((value.armature_name,))
        if context is not None:
            selection = context.input(node, "Armatures", selection)
        if not isinstance(selection, targets.Armatures) or not selection.names:
            raise ValueError("Select or connect Armatures")
        for target_name in dict.fromkeys(selection.names):
            armature = _objects().get(target_name)
            if armature is None or armature.type != "ARMATURE":
                raise ValueError("Target Armature is unavailable")
            from . import extensions
            if extensions.transform_owner(armature) is not None:
                raise ValueError('Another feature already controls this armature pose')
            if value.spring_data is not None and target_name != value.armature_name:
                raise ValueError("Spring rotations must target their simulated Armature")
            for name, rotation in value.values.items():
                bone = armature.pose.bones.get(name)
                if bone:
                    desired[("bone", _object_key(armature), name)] = (bone, rotation)
    else:
        if not isinstance(value, ShapeValues):
            raise ValueError("Connect Shape Keys Values")
        explicit = bool(node.objects)
        selection = targets.selected_objects(node) if explicit else targets.Objects(tuple(dict.fromkeys(name for name, key in value.values)))
        if context is not None:
            selection = context.input(node, "Objects", selection)
        if not isinstance(selection, targets.Objects):
            raise ValueError("Connect Objects")
        for object_name in dict.fromkeys(selection.names):
            obj = _objects().get(object_name)
            blocks = targets.shape_keys(obj)
            if blocks is None:
                continue
            matching = {key: weight for (source, key), weight in value.values.items() if source == object_name}
            if not matching:
                for (source, key), weight in value.values.items():
                    if key not in blocks:
                        continue
                    if key in matching and abs(matching[key] - weight) > 1e-6:
                        raise ValueError(f"Ambiguous Shape Key value: {key}; select its mapped object")
                    matching[key] = weight
            for name, weight in matching.items():
                key = blocks.get(name)
                if key is not None and key != blocks[0]:
                    desired[("shape", object_name, name)] = (key, weight)
    handoff = getattr(context, "handoff_owners", set())
    transfers = []
    for other, owned in _owners.items():
        overlap = desired.keys() & owned.keys() if other != uid else set()
        if overlap:
            if other not in handoff:
                raise ValueError("Another Apply node already controls this target")
            transfers.append((other, overlap))
    previous = _owners.setdefault(uid, {})
    # Alternative If branches share a target's original restoration value.
    for other, overlap in transfers:
        owned = _owners[other]
        for target in overlap:
            previous.setdefault(target, owned.pop(target))
        if not owned:
            _owners.pop(other)
            _release_official(other)
    for target in set(previous) - desired.keys():
        _restore_target(target, previous.pop(target))
    spring_armature = _objects().get(value.armature_name) if isinstance(value, BoneRotations) and value.spring_data is not None else None
    spring_target = _object_key(spring_armature) if spring_armature is not None else None
    _release_official(uid, spring_target)
    if spring_target:
        spring = runtime._official_spring_bone(spring_armature, value.spring_data)
        if spring is not None:
            record = _official.setdefault(spring_target, {"enabled": bool(spring.enable_animation), "data": value.spring_data, "owners": set()})
            record["owners"].add(uid)
            spring.enable_animation = False
    for target, (owner, result) in desired.items():
        if target[0] == "bone":
            previous.setdefault(target, (owner.rotation_mode, owner.rotation_quaternion.copy(), owner.rotation_euler.copy(), tuple(owner.rotation_axis_angle)))
            runtime._assign_rotation(owner, result)
        else:
            previous.setdefault(target, float(owner.value))
            owner.value = result
    summary = f"Applied {len(desired)} values"
    if node.last_summary != summary:
        node.last_summary = summary


def _uid(node):
    return str(getattr(node, "runtime_id", "") or node.name)


def _enabled(node):
    return not node.mute and bool(getattr(node, "enabled", True))


def _summary(node, text):
    if hasattr(node, "last_summary") and node.last_summary != text:
        node.last_summary = text


def _action_input(socket):
    node = socket.node
    from . import extensions
    spec = extensions.action_spec(node.bl_idname)
    if spec is not None:
        return socket.bl_idname == EVENT and socket.name in (*spec.inputs, "Cancel")
    if node.bl_idname in workflow.ACTION_TYPES:
        return socket.bl_idname == EVENT
    try:
        from . import input_runtime
        if node.bl_idname in input_runtime.ACTION_TYPES:
            return socket.name in input_runtime.action_inputs(node)
    except (ImportError, AttributeError):
        pass
    return (
        node.bl_idname in FLOW_TYPES and socket.name in {"Enter", "Reset"}
        or node.bl_idname in RELOAD_TYPES and socket.name == "Reload"
        or node.bl_idname == "YUGUS_NODE_ifacial_source" and socket.name == "Calibrate"
    )


def _source_socket(socket):
    """Resolve the single input link through live reroutes."""
    visited = set()
    while socket is not None and socket.is_linked:
        links = tuple(socket.links)
        if not links:
            return None
        link = links[0]
        if link.is_muted or not link.is_valid or not _enabled(link.from_node):
            return None
        source = link.from_socket
        node = source.node
        if node.bl_idname != "NodeReroute":
            return source
        uid = _uid(node)
        if uid in visited:
            return None
        visited.add(uid)
        socket = node.inputs[0] if node.inputs else None
    return None


def _event_targets(output):
    """Find live action inputs through reroutes, without following data wires."""
    found = []
    stack = [output]
    visited = set()
    while stack:
        socket = stack.pop()
        key = (socket.node.as_pointer(), socket.identifier)
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            if link.is_muted or not link.is_valid or not _enabled(link.to_node):
                continue
            target = link.to_socket
            if target.node.bl_idname == "NodeReroute":
                stack.extend(reversed(tuple(target.node.outputs)))
            elif _action_input(target):
                found.append(target)
    return found


def _event_outputs(node):
    return tuple(socket for socket in node.outputs if socket.bl_idname == EVENT)


def _route_nodes(inputs):
    """All potentially selected branches stay live while their event is wired."""
    active = set()
    controls = set()
    visited = set()
    stack = list(inputs)
    while stack:
        socket = stack.pop()
        node = socket.node
        uid = _uid(node)
        key = (uid, socket.name)
        if key in visited or not _enabled(node):
            continue
        visited.add(key)
        from . import input_runtime
        input_action = node.bl_idname in input_runtime.ACTION_TYPES and socket.name in input_runtime.action_inputs(node)
        if socket.name not in {"Enter", "Branch 1", "Branch 2", "Branch 3"} and not input_action:
            continue
        if node.bl_idname in APPLY_TYPES:
            active.add(uid)
        if node.bl_idname in CONTROL_TYPES:
            controls.add(uid)
        for output in _event_outputs(node):
            stack.extend(_event_targets(output))
    return active, controls


def _control_signature(node):
    source = _source_socket(node.inputs.get("Enter"))
    incoming = (_uid(source.node), source.identifier) if source else None
    outgoing = tuple(
        (output.name, _uid(target.node), target.name)
        for output in _event_outputs(node) for target in _event_targets(output)
    )
    definition = getattr(node, "workflow_tree", None)
    target = (definition.name, workflow.is_definition(definition)) if definition is not None else None
    from . import extensions
    return node.bl_idname, incoming, outgoing, target, extensions.configuration_signature(node)


def _event_priority(socket):
    # Source actions and Reset precede data pulls; body outputs precede Spring.
    priorities = []
    visited = set()
    stack = [socket]
    while stack:
        target = stack.pop()
        node = target.node
        uid = _uid(node)
        if (uid, target.name) in visited:
            continue
        visited.add((uid, target.name))
        if target.name in {"Reload", "Calibrate"}:
            priorities.append(0)
        elif target.name in {"Reset", "Cancel"}:
            priorities.append(1)
        elif node.bl_idname in APPLY_TYPES:
            priorities.append(3 if _has_spring(node) else 2)
        else:
            for output in _event_outputs(node):
                stack.extend(_event_targets(output))
    return min(priorities, default=2)


def _block_downstream(node):
    inputs = [target for output in _event_outputs(node) for target in _event_targets(output)]
    visited = {_uid(node)}
    while inputs:
        target = inputs.pop()
        child = target.node
        uid = _uid(child)
        if uid in visited or target.name != "Enter":
            continue
        visited.add(uid)
        if child.bl_idname in APPLY_TYPES:
            release(uid)
        _events.pop(uid, None)
        _summary(child, "Blocked · upstream did not complete")
        for output in _event_outputs(child):
            inputs.extend(_event_targets(output))


class _Pulse:
    """One event's synchronous routing and shared pure-data cache."""

    def __init__(self, tree, scene, now, elapsed, completed, context=None, *, run_id=None, payload=None, metadata=None):
        self.tree = tree
        self.now = now
        self.context = context or PullContext(tree, scene, now, elapsed)
        self.context.delta_time = elapsed
        self.completed = completed
        self.executed = set()
        self.run_id = run_id or workflow.begin(tree, payload)
        self.payload = workflow.payload(self.run_id)
        self.metadata = dict(metadata if metadata is not None else workflow.metadata(self.run_id))
        self.context.run_id = self.run_id
        self.context.payload = self.payload
        self._depth = 0

    def start(self):
        if self._depth == 0:
            workflow.enter_dispatch(self.run_id)
        self._depth += 1

    def close(self):
        self._depth -= 1
        if self._depth == 0:
            workflow.leave_dispatch(self.run_id)
            _drain_workflow(self.now)

    def deliver(self, sockets):
        self.start()
        try:
            for socket in sockets:
                self.receive(socket)
        finally:
            self.close()

    def fail(self, node, error):
        workflow.finish(self.run_id, "failed", str(error))
        uid = _uid(node)
        if node.bl_idname in APPLY_TYPES:
            release(uid)
        _events.pop(uid, None)
        _summary(node, f"Error · {error}")
        if node.bl_idname == "YUGUS_NODE_spring_simulation":
            node.spring_last_error = str(error)
        if node.bl_idname == "YUGUS_NODE_vrm_data":
            node.cache_status = "ERROR"
            node.cache_message = str(error)
        if node.bl_idname == "YUGUS_NODE_ifacial_source":
            node.last_error = str(error)
        self.tree.last_error = str(error)
        _block_downstream(node)

    def emit(self, output, path=()):
        self.start()
        try:
            if output is not None and workflow.running(self.run_id):
                workflow.note(self.run_id, output.node)
                for target in sorted(_event_targets(output), key=_event_priority):
                    self.receive(target, path)
        finally:
            self.close()

    def receive(self, socket, path=()):
        self.start()
        try:
            self._receive(socket, path)
        finally:
            self.close()

    def _receive(self, socket, path=()):
        node = socket.node
        uid = _uid(node)
        if not _enabled(node) or not workflow.running(self.run_id):
            return
        workflow.note(self.run_id, node)
        if uid in path:
            self.fail(node, ValueError("Event cycle"))
            return
        key = (uid, socket.name)
        if key in self.executed:
            return
        self.executed.add(key)
        path = (*path, uid)
        try:
            if workflow.receive(self, node, socket, path):
                return
            try:
                from . import input_runtime
                if node.bl_idname in input_runtime.ACTION_TYPES and input_runtime.handle_action(node, socket.name, self.context, self.now):
                    self.emit(node.outputs.get("End"), path)
                    return
            except ImportError:
                pass
            if socket.name in {"Reload", "Calibrate"}:
                self.action(node)
                return
            if socket.name == "Reset":
                old = _events.pop(uid, None)
                if old and old.get("run_id"):
                    workflow.cancel_run(old["run_id"])
                _summary(node, "Reset · waiting for Enter")
                return
            if node.bl_idname in APPLY_TYPES:
                name = "Bone Rotation" if node.bl_idname == YUGUS_NODE_apply_rotation.bl_idname else "Shape Keys Values"
                apply(node, self.context.input(node, name, None), self.context)
                bpy.context.view_layer.update()
                self.emit(node.outputs.get("End"), path)
                return
            self.control(node, path)
        except Exception as error:
            self.fail(node, error)

    def action(self, node):
        # Both successful and failed actions retire data sampled beforehand.
        self.context.cache.clear()
        if node.bl_idname == "YUGUS_NODE_spring_simulation":
            reload_spring(node)
        elif node.bl_idname == "YUGUS_NODE_ifacial_source":
            calibrate_ifacial(node)
        else:
            reload_vrm(node)

    def condition(self, node, name):
        return float(self.context.input(node, name, 1.0)) > 1e-6

    def control(self, node, path):
        kind = node.bl_idname
        uid = _uid(node)
        if kind == "YUGUS_NODE_flow_if":
            branch = "True" if self.condition(node, "Condition") else "False"
            _summary(node, f"Passed · {branch}")
            other = "False" if branch == "True" else "True"
            chosen, _ = _route_nodes(_event_targets(node.outputs[branch]))
            unchosen, _ = _route_nodes(_event_targets(node.outputs[other]))
            before = getattr(self.context, "handoff_owners", set())
            self.context.handoff_owners = before | (unchosen - chosen)
            try:
                self.emit(node.outputs.get(branch), path)
            finally:
                self.context.handoff_owners = before
            return
        if kind == "YUGUS_NODE_flow_gate":
            opened = self.condition(node, "Open")
            _summary(node, "Passed · gate open" if opened else "Held · gate closed")
            if opened:
                self.emit(node.outputs.get("End"), path)
            return
        if kind == "YUGUS_NODE_flow_sequence":
            _summary(node, "Passed · Then 1 / 2 / 3")
            for index in range(1, 4):
                self.emit(node.outputs.get(f"Then {index}"), path)
            return
        if uid in self.completed and kind in {"YUGUS_NODE_flow_delay", "YUGUS_NODE_flow_wait_until"}:
            return
        state = _events.setdefault(uid, {"signature": _control_signature(node)})
        if kind == "YUGUS_NODE_flow_once":
            if state.get("done"):
                _summary(node, "Held · reset to run again")
                return
            state["done"] = True
            _summary(node, "Passed · once")
            self.emit(node.outputs.get("End"), path)
        elif kind == "YUGUS_NODE_flow_every_n":
            every = max(1, int(node.every))
            state["count"] = state.get("count", 0) + 1
            if state["count"] >= every:
                state["count"] = 0
                _summary(node, f"Passed · every {every} events")
                self.emit(node.outputs.get("End"), path)
            else:
                _summary(node, f"Count · {state['count']} / {every}")
        elif kind in {"YUGUS_NODE_flow_delay", "YUGUS_NODE_flow_wait_until"}:
            if "armed" in state:
                return
            state["armed"] = self.now
            state["run_id"] = self.run_id
            state["metadata"] = dict(self.metadata)
            state["workflow_task"] = workflow.legacy_hold(self, node)
            state["handoff"] = tuple(getattr(self.context, "handoff_owners", ()))
            if kind == "YUGUS_NODE_flow_delay":
                seconds = max(0.0, float(node.seconds))
                state["deadline"] = self.now + seconds
                if seconds > 0.0:
                    _summary(node, f"Waiting · {seconds:.3g}s")
                    return
            elif not self.condition(node, "Condition"):
                _summary(node, "Waiting · condition")
                return
            state.pop("armed", None)
            state.pop("deadline", None)
            self.completed.add(uid)
            _summary(node, "Passed · End")
            self.emit(node.outputs.get("End"), path)
            workflow.complete_task(state.pop("workflow_task"), now=self.now)


def _drain_workflow(now):
    global _draining_workflow
    if _draining_workflow:
        return
    _draining_workflow = True
    try:
        # Large finite zero-duration Repeat blocks yield to the next tick.
        for _ in range(256):
            callback = workflow.next_callback()
            if callback is None:
                break
            kind = callback["kind"]
            if kind == "child_done":
                workflow.child_done(callback, now)
                continue
            if kind == "dispatch":
                tree = bpy.data.node_groups.get(callback["tree"])
                node = workflow._node(callback["tree"], callback["node"])
                rid = callback["run_id"]
                if tree is not None and node is not None and _enabled(node) and workflow.running(rid):
                    _Pulse(tree, bpy.context.scene, now, 0.0, set(), run_id=rid, metadata=callback["metadata"]).emit(node.outputs.get(callback["output"]), (_uid(node),))
                else:
                    workflow.cancel_run(rid)
                continue
            task = callback["task"]
            node = workflow._node(task["tree"], task["node"])
            state = callback["state"]
            try:
                valid = node is not None and _enabled(node) and _control_signature(node) == task["signature"]
            except Exception:
                valid = False
            if valid and task["kind"] != "legacy":
                count = workflow.running_for(node.id_data, node)
                error = str(callback.get("error", "")) if state == "failed" else ""
                summary = f"Running · {count}" if count else state.capitalize()
                if error:
                    summary = f"{summary} · {error}"
                    node.id_data.last_error = error
                _summary(node, summary)
                rid = task["run_id"]
                if not workflow.running(rid):
                    rid = workflow.begin(node.id_data, workflow.payload(rid), metadata={**task["metadata"], "completed_run_id": task["run_id"]})
                output = {"end": "End", "cancelled": "Cancelled", "failed": "Failed"}.get(state)
                _Pulse(node.id_data, bpy.context.scene, now, max(0.0, now - task["created"]), set(), run_id=rid, metadata=task["metadata"]).emit(node.outputs.get(output), (_uid(node),))
            workflow.settle(task)
    finally:
        _draining_workflow = False
    workflow.prune()


def emit_event(tree, source, output_name, payload=None, now=None):
    """Public event entry point used by keyboard snapshots and manual test events."""
    if scheduler.is_suspended():
        return None
    now = time.monotonic() if now is None else float(now)
    if isinstance(source, str):
        source = workflow._node(tree.name, source) or tree.nodes.get(source)
    if source is None or source.id_data != tree or not _enabled(source):
        raise ValueError("Event source is unavailable")
    output = source.outputs.get(output_name)
    if output is None or output.bl_idname != EVENT:
        raise ValueError("Select an event output")
    pulse = _Pulse(tree, bpy.context.scene, now, 0.0, set(), payload=payload)
    pulse.emit(output, (_uid(source),))
    return pulse.run_id


def drain_pending(now=None):
    """Drain completed actions without adding a timer or advancing legacy waits."""
    _drain_workflow(time.monotonic() if now is None else float(now))


def tick(now=None):
    if scheduler.is_suspended():
        return
    now = time.monotonic() if now is None else float(now)
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return
    active = set()
    live_clocks = set()
    live_controls = set()
    live_edges = set()
    demanded = set()
    workflow.tick(now, advance=False)
    for tree in getattr(bpy.data, "node_groups", ()):
        if tree.bl_idname != TREE_ID:
            continue
        clocks = []
        numeric = []
        roots = []
        tree_nodes = {_uid(node): node for node in tree.nodes}
        definition = workflow.is_definition(tree)
        for node in tree.nodes:
            if not _enabled(node):
                continue
            if node.bl_idname == YUGUS_NODE_hz_trigger.bl_idname and not definition:
                output = node.outputs.get("Trigger")
                targets = _event_targets(output) if output else []
                if targets:
                    clocks.append((node, output))
                    roots.extend(targets)
            elif node.bl_idname not in FLOW_TYPES | workflow.ACTION_TYPES:
                if not definition or workflow.active_in(tree):
                    for output in _event_outputs(node):
                        roots.extend(_event_targets(output))
            for socket in node.inputs:
                if not _action_input(socket):
                    continue
                source = _source_socket(socket)
                if source is not None and source.bl_idname != EVENT and not definition:
                    numeric.append((socket, source))
                    roots.append(socket)
        tree_active, tree_controls = _route_nodes(roots)
        active.update(tree_active)
        live_controls.update(tree_controls)
        for uid in (set(_owners) & tree_nodes.keys()) - tree_active:
            release(uid)
        for node in tree.nodes:
            uid = _uid(node)
            if node.bl_idname in APPLY_TYPES:
                if uid in tree_active:
                    demanded.update(_upstream_ids(node))
                else:
                    _summary(node, "Idle · connect Enter / enable Apply")
            elif node.bl_idname in CONTROL_TYPES:
                state = _events.get(uid)
                if uid not in tree_controls or (state is not None and state.get("signature") != _control_signature(node)):
                    old = _events.pop(uid, None)
                    if old and old.get("run_id"):
                        workflow.cancel_run(old["run_id"])
                    _summary(node, "Idle · connect Enter")
        completed = set()
        signal_context = PullContext(tree, scene, now, 0.0)
        rising = []
        signal_errors = []
        for socket, source in numeric:
            key = (_uid(socket.node), socket.name)
            live_edges.add(key)
            identity = (_uid(source.node), source.identifier)
            record = _edges.get(key)
            try:
                values = signal_context.evaluate(source.node)
                value = values.get(source.identifier, values.get(source.name, 0.0))
                high = float(value) > 1e-6
            except Exception as error:
                signal_errors.append((socket.node, error))
                high = False
            previous = record.get("high", False) if record and record["source"] == identity else False
            _edges[key] = {"source": identity, "high": high}
            if high and not previous:
                rising.append(socket)
        due = []
        for clock, output in clocks:
            uid = _uid(clock)
            live_clocks.add(uid)
            interval = 1.0 / int(clock.hz)
            record = _clocks.get(uid)
            if record is None or record["hz"] != clock.hz:
                record = {"hz": clock.hz, "next": now + interval, "last": now}
                _clocks[uid] = record
            if now + 1e-9 >= record["next"]:
                due.append((output, now - record["last"]))
                record["last"] = now
                record["next"] += (math.floor(max(0, now - record["next"]) / interval) + 1) * interval
        pending = any(_events.get(uid, {}).get("armed") is not None for uid in tree_controls)
        if rising or due or pending:
            tree.last_error = ""
        for node, error in signal_errors:
            _Pulse(tree, scene, now, 0.0, completed).fail(node, error)
        if rising:
            pulse = _Pulse(tree, scene, now, 0.0, completed, signal_context)
            pulse.deliver(sorted(rising, key=_event_priority))
        # Deliver Reset before a wait whose deadline falls on this same tick.
        for output, elapsed in due:
            _Pulse(tree, scene, now, elapsed, completed).emit(output, (_uid(output.node),))
        # Existing scheduler ticks finish waiting controls; no additional timer.
        for uid in tuple(tree_controls):
            state = _events.get(uid, {})
            if "armed" not in state:
                continue
            node = tree_nodes[uid]
            if state.get("run_id") and not workflow.running(state["run_id"]):
                _events.pop(uid, None)
                continue
            pulse = _Pulse(tree, scene, now, max(0.0, now - state["armed"]), completed, run_id=state.get("run_id"), metadata=state.get("metadata"))
            pulse.context.handoff_owners = set(state.get("handoff", ()))
            try:
                ready = (
                    now + 1e-9 >= state["deadline"]
                    if node.bl_idname == "YUGUS_NODE_flow_delay"
                    else pulse.condition(node, "Condition")
                )
                if not ready:
                    continue
                state.pop("armed", None)
                state.pop("deadline", None)
                completed.add(uid)
                _summary(node, "Passed · End")
                pulse.emit(node.outputs.get("End"), (uid,))
                workflow.complete_task(state.pop("workflow_task"), now=now)
                _drain_workflow(now)
            except Exception as error:
                pulse.fail(node, error)
        runs = sum(run["tree"] == tree.name and run["state"] == "running" for run in workflow._runs.values())
        tree.last_status = f"Flow · {runs} running invocations · {len(tree_active)} connected Apply nodes"
    for uid in set(_owners) - active:
        release(uid)
    for uid in set(_clocks) - live_clocks:
        _clocks.pop(uid, None)
    for uid in set(_events) - live_controls:
        old = _events.pop(uid, None)
        if old and old.get("run_id"):
            workflow.cancel_run(old["run_id"])
    for key in set(_edges) - live_edges:
        _edges.pop(key, None)
    for uid in set(_springs) - demanded:
        _springs.pop(uid, None)
    for uid in set(_last_eval) - demanded:
        _last_eval.pop(uid, None)
    workflow.tick(now)
    _drain_workflow(now)


def _upstream_ids(node, visited=None):
    visited = set() if visited is None else visited
    for socket in node.inputs:
        for link in socket.links:
            source = link.from_node
            uid = str(getattr(source, "runtime_id", "") or source.as_pointer())
            if uid not in visited:
                visited.add(uid)
                _upstream_ids(source, visited)
    return visited


def _has_spring(node):
    upstream = _upstream_ids(node)
    return any(str(n.runtime_id) in upstream and n.bl_idname == "YUGUS_NODE_spring_simulation" for n in node.id_data.nodes if hasattr(n, "runtime_id"))


def clear():
    for uid in tuple(_owners):
        release(uid)
    _clocks.clear()
    _springs.clear()
    _last_eval.clear()
    _events.clear()
    _edges.clear()
    workflow.clear()


def debug_report(node):
    state = _springs.get(str(node.runtime_id), {})
    solver = state.get("solver")
    return "\n".join((
        "===== YUGU PULL SPRING DEBUG =====",
        f"Node: {node.name}", f"Status: {node.spring_status}",
        f"Initialized: {solver is not None}",
        f"Fixed physics step: {STEP:.12g} seconds",
        f"Completed steps: {state.get('steps', 0)}",
        f"Pending real time: {state.get('debt', 0):.9f} seconds",
        f"Output interpolation: {min(state.get('debt', 0) / STEP, 1.0):.6f} (one physics step latency)",
        f"Buffered driving poses: {len(state.get('boundaries', ()))}",
        f"Last request timestamp: {state.get('last')}",
        f"Spring data: {state.get('data')!r}",
        f"Solved rotations: {state.get('rotations', {})!r}",
        f"Solver springs: {getattr(solver, 'springs', ())!r}",
        "No downstream request = no simulation step.",
        "===== END PULL SPRING DEBUG =====",
    ))


def migrate_flow_ui():
    from .constants import SOCKET_TRIGGER
    for tree in getattr(bpy.data, "node_groups", ()):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tree.nodes:
            if node.bl_idname == YUGUS_NODE_bone_rotation_mix.bl_idname:
                for name in ("Mask", "Bone Map"):
                    socket = node.inputs.get(name)
                    if socket is not None:
                        node.inputs.remove(socket)
            if node.bl_idname == YUGUS_NODE_expression_to_shapes.bl_idname:
                enter = node.inputs.get("Enter")
                end = node.outputs.get("End")
                source = enter.links[0].from_socket if enter is not None and enter.is_linked else None
                destinations = [link.to_socket for link in end.links] if end is not None else []
                if source is not None and source.node != node:
                    for destination in destinations:
                        if destination.node != node and destination.node != source.node:
                            tree.links.new(source, destination)
                if enter is not None:
                    node.inputs.remove(enter)
                if end is not None:
                    node.outputs.remove(end)
            if node.bl_idname == YUGUS_NODE_apply_shapes.bl_idname:
                old = node.inputs.get("Trigger")
                if old is not None and node.inputs.get("Enter") in (None, old):
                    old.name = "Enter"
                if node.inputs.get("Enter") is None:
                    node.inputs.new(EVENT, "Enter")
                if node.outputs.get("End") is None:
                    node.outputs.new(EVENT, "End")
            for sockets in (node.inputs, node.outputs):
                events = [s for s in sockets if s.bl_idname in {EVENT, SOCKET_TRIGGER} and s.name not in {"Reload", "Calibrate"}]
                for index, socket in enumerate(events):
                    sockets.move(list(sockets).index(socket), index)
                for name in ("Reload", "Calibrate"):
                    action_input = sockets.get(name)
                    if action_input is not None:
                        sockets.move(list(sockets).index(action_input), len(sockets) - 1)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    from . import flow_nodes
    for cls in flow_nodes.CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    clear()
    from . import flow_nodes
    for cls in reversed(flow_nodes.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
