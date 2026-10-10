"""Clock events pull pure data conversions; only Apply nodes mutate characters."""
from collections import deque
from dataclasses import dataclass, field
import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import runtime, scheduler, targets, workflow
from .constants import TREE_ID, SOCKET_TRIGGER, SOCKET_MOTION, SOCKET_BONE_MAP, SOCKET_EXPRESSIONS, SOCKET_EXPRESSION_MAP, SOCKET_MOTION_MASK
from .data import HumanoidMotion, HumanoidTransform, HumanoidMask, BoneMapData, ExpressionFrame, ExpressionMapData, SpringData
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase, ensure_node_id, ensure_numeric_parameter
from .spring_solver import NativeSpringSolver

EVENT = "YUGUS_NS_update_event"
# Serialized IDs stay stable so existing .blend links survive the rename.
TRANSFORMS = "YUGUS_NS_bone_rotation"
ROTATIONS = TRANSFORMS  # Compatibility for existing extension imports.
BONE_TRANSFORM_VERSION = 1
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


@dataclass(init=False)
class BoneTransforms:
    """Sparse native pose-bone channels; location is Blender local position.

    The second positional argument also accepts the old quaternion dictionary.
    Missing position/rotation means leave that channel under its current owner.
    """
    armature_name: str = ""
    bones: dict[str, HumanoidTransform] = field(default_factory=dict)
    spring_data: object = None
    root: HumanoidTransform | None = None
    hips_bones: frozenset[str] = field(default_factory=frozenset)
    physics_world_id: str = ""
    physics_revision: str = ""
    physics_tick: int = 0

    def __init__(self, armature_name="", bones=None, spring_data=None, *, values=None, locations=None, root=None, hips_bones=(), physics_world_id="", physics_revision="", physics_tick=0):
        if bones is not None and values is not None:
            raise TypeError("Specify bones or legacy values, not both")
        self.armature_name = str(armature_name)
        self.spring_data = spring_data
        self.physics_world_id, self.physics_revision, self.physics_tick = physics_world_id, physics_revision, physics_tick
        if root is not None and not isinstance(root, HumanoidTransform):
            raise TypeError("Root Transform requires HumanoidTransform")
        self.root = root.copy() if root is not None else None
        self.hips_bones = frozenset(hips_bones)
        self.bones = {}
        for name, value in (bones if bones is not None else values or {}).items():
            if isinstance(value, HumanoidTransform):
                self.bones[name] = value.copy()
            elif isinstance(value, Quaternion):
                self.bones[name] = HumanoidTransform(value.copy())
            else:
                raise TypeError("Bone Transform requires HumanoidTransform or Quaternion values")
        if locations is not None:
            self.locations = locations

    @property
    def values(self):
        """Legacy quaternion view. New consumers use .bones directly."""
        return {name: value.rotation for name, value in self.bones.items() if value.rotation is not None}

    @values.setter
    def values(self, values):
        for transform in self.bones.values():
            transform.rotation = None
        for name, value in values.items():
            self.bones.setdefault(name, HumanoidTransform()).rotation = value.copy()
        self.bones = {name: value for name, value in self.bones.items()
                      if value.rotation is not None or value.location is not None}

    @property
    def rotations(self):
        return self.values

    @rotations.setter
    def rotations(self, rotations):
        self.values = rotations

    @property
    def locations(self):
        return {name: value.location for name, value in self.bones.items() if value.location is not None}

    @locations.setter
    def locations(self, locations):
        for transform in self.bones.values():
            transform.location = None
        for name, value in locations.items():
            self.bones.setdefault(name, HumanoidTransform()).location = Vector(value)
        self.bones = {name: value for name, value in self.bones.items()
                      if value.rotation is not None or value.location is not None}

    @property
    def positions(self):
        return self.locations

    @positions.setter
    def positions(self, positions):
        self.locations = positions

    def copy(self):
        return BoneTransforms(self.armature_name, self.bones, self.spring_data, root=self.root, hips_bones=self.hips_bones,
                              physics_world_id=self.physics_world_id, physics_revision=self.physics_revision, physics_tick=self.physics_tick)


BoneRotations = BoneTransforms


@dataclass
class ShapeValues:
    values: dict = field(default_factory=dict)  # (mesh object name, shape key name) -> value


class YUGUS_NS_update_event(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = EVENT
    bl_label = "Update Event"
    def draw(self, context, layout, node, text):
        if not self.is_output and node.bl_idname == "YUGUS_NODE_physics_solver" and self.name in {"Reset", "Stop"}:
            from . import physics
            row = layout.row(align=True)
            row.label(text=text or self.name)
            icon = "FILE_REFRESH" if self.name == "Reset" else "SNAP_FACE"
            physics._button(row, node, self.name.upper(), text="", icon=icon)
            return
        super().draw(context, layout, node, text)
    def draw_color(self, context, node):
        return (0.85, 0.12, 0.12, 1.0)


class YUGUS_NS_bone_transform(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = TRANSFORMS
    bl_label = "Bone Transform"
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


class YUGUS_NODE_button_trigger(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_button_trigger"
    bl_label = "Button Trigger"
    bl_description = "Send one event from Trigger each time the button is pressed"
    bl_icon = "PLAY"

    def init(self, context):
        self._init_base(180)
        self.outputs.new(EVENT, "Trigger")

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.enabled = not self.mute and not scheduler.is_suspended()
        operator = row.operator("yugu_node_studio.button_trigger", text="Trigger", icon="PLAY")
        operator.tree_name, operator.node_name = self.id_data.name, self.name


class YUGUS_OT_button_trigger(bpy.types.Operator):
    bl_idname = "yugu_node_studio.button_trigger"
    bl_label = "Trigger"
    bl_description = "Send one trigger event to the connected flow inputs"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree and tree.bl_idname == TREE_ID else None
        if (node is None or node.bl_idname != YUGUS_NODE_button_trigger.bl_idname
                or not _enabled(node) or scheduler.is_suspended()):
            return {"CANCELLED"}
        try:
            run_id = emit_event(tree, node, "Trigger")
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"} if run_id is not None else {"CANCELLED"}


class YUGUS_NODE_motion_to_transform(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_motion_to_rotation"
    bl_label = "Motion to Bone Transform"
    bl_icon = "BONE_DATA"
    motion_type: bpy.props.EnumProperty(name="Motion Type", items=[("VRM_HUMANOID", "VRM Humanoid", "Canonical humanoid motion")])
    def init(self, context):
        self._init_base(230)
        self.inputs.new(SOCKET_MOTION, "Motion")
        self.inputs.new(SOCKET_BONE_MAP, "Bone Map")
        self.outputs.new(TRANSFORMS, "Bone Transform")
    def draw_buttons(self, context, layout):
        layout.prop(self, "motion_type", text="")


class YUGUS_NODE_bone_transform_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_bone_rotation_mix"
    bl_label = "Bone Transform Mix"
    bl_icon = "NODETREE"
    factor: bpy.props.FloatProperty(name="Factor", default=1.0, min=0.0, max=1.0, subtype="FACTOR")

    def init(self, context):
        self._init_base(230)
        self.inputs.new(TRANSFORMS, "Base")
        self.inputs.new(TRANSFORMS, "Overlay")
        ensure_numeric_parameter(self)
        self.outputs.new(TRANSFORMS, "Bone Transform")


def _validate_bone_transforms(value):
    """Validate the entire packet before any pose/ownership mutations."""
    if not all(isinstance(name, str) and name for name in value.hips_bones):
        raise ValueError("Mapped Hips metadata requires bone names")
    channels = tuple(value.bones.items()) + (("root object", value.root),) if value.root is not None else tuple(value.bones.items())
    for name, transform in channels:
        if not isinstance(transform, HumanoidTransform):
            raise ValueError(f"Invalid Bone Transform: {name}")
        if transform.location is not None:
            location = transform.location
            if len(location) != 3 or not all(math.isfinite(float(component)) for component in location):
                raise ValueError(f"Invalid bone position: {name}")
        if transform.rotation is not None:
            rotation = transform.rotation
            if (len(rotation) != 4 or not all(math.isfinite(float(component)) for component in rotation)
                    or rotation.magnitude <= 1e-12):
                raise ValueError(f"Invalid bone rotation: {name}")


def mix_bone_transforms(base, overlay, factor):
    """Blend position with LERP and rotation with SLERP, per sparse channel."""
    if not isinstance(base, BoneTransforms) or not isinstance(overlay, BoneTransforms):
        raise ValueError("Connect Bone Transform to Base / Overlay")
    if base.armature_name and overlay.armature_name and base.armature_name != overlay.armature_name:
        raise ValueError("Bone Transform Mix requires the same Armature")
    _validate_bone_transforms(base)
    _validate_bone_transforms(overlay)
    armature = base.armature_name or overlay.armature_name
    result = {name: value.copy() for name, value in base.bones.items()}
    root = base.root.copy() if base.root is not None else None
    factor = float(factor)
    if not math.isfinite(factor):
        raise ValueError("Bone Transform Mix factor must be finite")
    factor = min(max(factor, 0.0), 1.0)
    if factor > 0 and base.physics_world_id and overlay.physics_world_id:
        if (base.physics_world_id, base.physics_revision, base.physics_tick) != (overlay.physics_world_id, overlay.physics_revision, overlay.physics_tick):
            raise ValueError('Bone Transform Mix requires the same physics world and tick')
    if factor > 0.0:
        channels = list(overlay.bones.items())
        if overlay.root is not None:
            channels.append((None, overlay.root))
        for name, transform in channels:
            if transform.rotation is None and transform.location is None:
                continue
            if name is None:
                if root is None:
                    root = HumanoidTransform()
                value = root
            else:
                value = result.setdefault(name, HumanoidTransform())
            if transform.rotation is not None:
                start = value.rotation if value.rotation is not None else Quaternion()
                value.rotation = start.normalized().slerp(transform.rotation.normalized(), factor).normalized()
            if transform.location is not None:
                start = value.location if value.location is not None else Vector((0, 0, 0))
                value.location = start.lerp(transform.location, factor)
    # Preserve physics ownership / same-armature safeguards through conversion.
    spring = base.spring_data if base.spring_data is not None else overlay.spring_data if factor > 0 else None
    hips_bones = base.hips_bones | (overlay.hips_bones if factor > 0 else frozenset())
    physics = base if base.physics_world_id else overlay if factor > 0 else base
    return BoneTransforms(armature, result, spring, root=root, hips_bones=hips_bones,
                          physics_world_id=physics.physics_world_id, physics_revision=physics.physics_revision, physics_tick=physics.physics_tick)


mix_bone_rotations = mix_bone_transforms


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


class YUGUS_NODE_apply_transform(_Apply, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_apply_rotation"
    bl_label = "Bone Transforms to Armature"
    armature: bpy.props.PointerProperty(name="Armature", type=bpy.types.Object, poll=targets.armature_poll)
    bl_icon = "ARMATURE_DATA"
    def init(self, context):
        self._init_base(260)
        self.inputs.new(EVENT, "Enter")
        self.inputs.new(TRANSFORMS, "Bone Transform")
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


# Public aliases for scripts and extensions written against the old API.
YUGUS_NS_bone_rotation = YUGUS_NS_bone_transform
YUGUS_NODE_motion_to_rotation = YUGUS_NODE_motion_to_transform
YUGUS_NODE_bone_rotation_mix = YUGUS_NODE_bone_transform_mix
YUGUS_NODE_apply_rotation = YUGUS_NODE_apply_transform


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


APPLY_TYPES = {YUGUS_NODE_apply_rotation.bl_idname, YUGUS_NODE_apply_shapes.bl_idname, "YUGUS_NODE_apply_object_transform"}
CONTROL_TYPES = {
    "YUGUS_NODE_flow_if", "YUGUS_NODE_flow_gate", "YUGUS_NODE_flow_delay",
    "YUGUS_NODE_flow_wait_until", "YUGUS_NODE_flow_once",
    "YUGUS_NODE_flow_sequence", "YUGUS_NODE_flow_every_n",
}
FLOW_TYPES = APPLY_TYPES | CONTROL_TYPES | {
    "YUGUS_NODE_yt_chat_event", "YUGUS_NODE_chat_debug",
    "YUGUS_NODE_chat_list", "YUGUS_NODE_keyword_match",
}
RELOAD_TYPES = {"YUGUS_NODE_vrm_data"}
CLASSES = (YUGUS_NS_update_event, YUGUS_NS_bone_rotation, YUGUS_NS_shape_values,
           YUGUS_NODE_hz_trigger, YUGUS_NODE_button_trigger, YUGUS_OT_button_trigger,
           YUGUS_NODE_motion_to_rotation, YUGUS_NODE_bone_rotation_mix, YUGUS_NODE_expression_to_shapes,
           YUGUS_NODE_apply_rotation, YUGUS_NODE_apply_shapes)


def reload_spring(node):
    """Use the same history reset for the button and a Reload event."""
    from .availability import require_available
    require_available(node)
    uid = str(node.runtime_id)
    from . import spring_trace
    spring_trace.finish(uid, 'Spring reloaded')
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
        if dependent.bl_idname in APPLY_TYPES:
            release(_uid(dependent))
    return vrm_cache.reload_node(node)


def _source_connection_values(node, context):
    from .nodes import SOURCE_CONNECTION_PARAMETERS
    values = {}
    for name, property_name, kind in SOURCE_CONNECTION_PARAMETERS[node.bl_idname]:
        if kind == "YUGUS_NS_float":
            values[property_name] = int(runtime._numeric_parameter(
                context, node, name, property_name, 1.0, 65535.0))
        else:
            value = getattr(node, property_name)
            socket = node.inputs.get(name)
            if socket is not None and socket.is_linked:
                value = context.input(node, name, value)
            if not isinstance(value, str):
                raise ValueError(f"{node.bl_label} {name}: expected a Text input")
            values[property_name] = value.strip()
    return values


def source_connection_action(node, action, *, retry=False, context=None):
    """Set a live receiver state for both socket buttons and incoming pulses."""
    from . import network
    if node.bl_idname not in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}:
        raise ValueError("Selected node is not a live source")
    if action not in {"CONNECT", "DISCONNECT"}:
        raise ValueError("Unknown source connection action")
    uid = ensure_node_id(node)
    try:
        if action == "DISCONNECT":
            network.stop(uid)
        else:
            if context is None:
                context = PullContext(node.id_data, bpy.context.scene,
                                      time.monotonic(), scheduler.MOTION_INTERVAL)
            values = _source_connection_values(node, context)
            if node.bl_idname == "YUGUS_NODE_vmc_source":
                network.start_vmc(uid, values["address"], values["port"])
            else:
                network.start_ifacial(
                    uid,
                    values["address"],
                    values["device_port"],
                    str(node.listen_address).strip(),
                    int(node.listen_port),
                    retry=retry,
                )
        node.connected = network.is_running(uid)
        node.last_error = ""
    except Exception as error:
        network.stop(uid)
        node.connected = False
        node.last_error = str(error)
        raise


def vmc_connection_action(node, action):
    """Keep the VMC-only entry point available for existing callers."""
    if node.bl_idname != "YUGUS_NODE_vmc_source":
        raise ValueError("Selected node is not a VMC Receiver")
    return source_connection_action(node, action)


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


def _spring_presentation(state, data):
    """Return owned display channels without sampling or advancing the solver."""
    if state is None or state["data"] is not data:
        return BoneTransforms(data.armature_name, spring_data=data)
    # One physics step of latency makes zero/two-step timer jitter continuous.
    # Interpolation changes presentation only; Verlet history stays fixed-rate.
    alpha = min(state["debt"] / STEP, 1.0)
    result = {}
    for name, rotation in state["rotations"].items():
        start = state["previous_rotations"].get(name, rotation)
        result[name] = start.slerp(rotation, alpha).normalized()
    return BoneTransforms(data.armature_name, result, data)


def spring_value(node, data, now, elapsed):
    from .availability import require_available
    require_available(node)
    if not isinstance(data, SpringData):
        raise ValueError("Connect loaded VRM Spring Bone Map")
    uid = str(node.runtime_id)
    state = _springs.get(uid)
    # Ordinary graph inspection is a read. Only a real downstream clock can
    # initialize physics, replace its data snapshot, or sample driving bones.
    if now is None:
        return _spring_presentation(state, data)
    try:
        now = float(now)
        elapsed = float(elapsed)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Spring timestamp and elapsed time must be finite") from exc
    if not math.isfinite(now) or not math.isfinite(elapsed):
        raise ValueError("Spring timestamp and elapsed time must be finite")
    # Several consumers can pull the same node at the same instant. A duplicate
    # or a stale clock must also leave pending catch-up debt untouched.
    if state is not None and now <= state["last"]:
        return _spring_presentation(state, data)
    origin = now - max(0.0, elapsed)
    elapsed_since_last = now - state["last"] if state is not None else max(0.0, elapsed)
    if not math.isfinite(origin) or not math.isfinite(elapsed_since_last):
        raise ValueError("Spring timestamp and elapsed time must be finite")
    if state is not None and not math.isfinite(state["debt"] + elapsed_since_last):
        raise ValueError("Spring timestamp and elapsed time must be finite")
    armature = bpy.data.objects.get(data.armature_name)
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("Spring armature is unavailable")
    initialized = state is None or state["data"] is not data
    if state is not None and state["data"] is not data:
        from . import spring_trace
        spring_trace.finish(uid, 'Spring input changed')
    if initialized:
        # Match the early Yugu Node start behavior: simulated bones start from
        # their neutral pose, never from the output of the previous simulation.
        # The solver constructs this reference in memory; this pull is pure.
        solver = NativeSpringSolver(armature, data, initialize_from_rest=True)
        initial = {name: rotation.copy() for name, rotation in solver.initial_rotations.items()}
        state = {"solver": solver, "data": data, "last": origin, "origin": origin,
                 "debt": 0.0, "rotations": initial, "previous_rotations": initial,
                 "steps": 0, "boundaries": deque(((origin, solver.initial_boundary or solver.sample_boundary(armature)),))}
        _springs[uid] = state
    if now > state["boundaries"][-1][0]:
        # The first pull must use the same neutral scene as initialization.
        # Sampling the old applied Spring pose here would reintroduce its
        # centers/colliders during the very first fixed-step catch-up.
        boundary = state["solver"].initial_boundary if initialized else None
        state["boundaries"].append((now, boundary or state["solver"].sample_boundary(armature)))
    state["debt"] += max(0, now - state["last"])
    state["last"] = now
    count = int(min((state["debt"] + 1e-10) / STEP, 240))
    from . import spring_trace
    tracing = spring_trace.interested(node)
    state["solver"].trace_enabled = tracing
    # Never discard elapsed time. A long UI stall leaves a visible catch-up debt.
    for index in range(count):
        physics_time = state["origin"] + (state["steps"] + index + 1) * STEP
        sampled = _spring_boundary_at(state, physics_time)
        state["previous_rotations"] = state["rotations"]
        if tracing:
            started = time.perf_counter()
            state["rotations"] = state["solver"].step(armature, STEP, sampled)
            step_ms = (time.perf_counter() - started) * 1000
            spring_trace.capture_legacy(node, state, sampled, state["steps"] + index + 1, physics_time, step_ms)
        else:
            state["rotations"] = state["solver"].step(armature, STEP, sampled)
    state["debt"] = max(0.0, state["debt"] - count * STEP)
    state["steps"] += count
    node.spring_status = f"Pulled · {count} steps · {state['debt']:.3f}s pending"
    node.spring_last_error = ""
    return _spring_presentation(state, data)


def _transform_output(node, value):
    socket = node.outputs.get("Bone Transform") or node.outputs.get("Bone Rotation")
    if socket is None:
        socket = next((socket for socket in node.outputs if socket.bl_idname == TRANSFORMS), None)
    # Legacy names/identifiers remain readable for saved graphs and extensions.
    result = {"Bone Transform": value, "Bone Rotation": value}
    if socket is not None:
        result[socket.identifier] = value
        result[socket.name] = value
    return result


def _transform_input(context, node, default=None):
    socket = node.inputs.get("Bone Transform") or node.inputs.get("Bone Rotation")
    if socket is None:
        socket = next((socket for socket in node.inputs if socket.bl_idname == TRANSFORMS), None)
    return context.input(node, socket.name if socket is not None else "Bone Transform", default)


def evaluate(context, node):
    kind = node.bl_idname
    if kind == YUGUS_NODE_bone_rotation_mix.bl_idname:
        return _transform_output(node, mix_bone_transforms(
            context.input(node, "Base", BoneTransforms()),
            context.input(node, "Overlay", BoneTransforms()),
            runtime._numeric_parameter(context, node, "Factor", "factor")))
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
        hips = mapping.by_humanoid.get("hips") if isinstance(mapping, BoneMapData) else None
        value = BoneTransforms(pose.armature_name, pose.bones, root=pose.root, hips_bones=(hips,) if hips else ())
        _validate_bone_transforms(value)
        return _transform_output(node, value)
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
        from . import spring_trace
        data = context.input(node, "Spring Data", None)
        output = _transform_output(node, spring_value(node, data, getattr(context, "now", None), context.delta_time))
        output.update(runtime._output(node, spring_trace.OUTPUT, spring_trace.output(node)))
        return output
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


def _restore_target(target, base):
    kind, object_name, name = target
    obj = _objects().get(object_name)
    if obj is None:
        from . import physics
        obj = physics.resolve_bone_object(object_name)
    if obj is None:
        return
    if kind in {"bone", "object"}:
        bone = obj if kind == "object" else obj.pose.bones.get(name) if obj.type == "ARMATURE" else None
        if bone is not None:
            mode, quaternion, euler, axis = base
            # Changing mode converts the active representation; restore it first.
            bone.rotation_mode = mode
            bone.rotation_quaternion = quaternion
            bone.rotation_euler = euler
            bone.rotation_axis_angle = axis
    elif kind in {"bone_location", "object_location"}:
        bone = obj if kind == "object_location" else obj.pose.bones.get(name) if obj.type == "ARMATURE" else None
        if bone is not None:
            bone.location = base
    else:
        key = runtime._shape_key(object_name, name)
        if key:
            key.value = base


def _snapshot_target(target):
    kind, object_name, name = target
    obj = _objects().get(object_name)
    if obj is None:
        from . import physics
        obj = physics.resolve_bone_object(object_name)
    if obj is None:
        return None
    if kind in {"bone", "bone_location"}:
        owner = obj.pose.bones.get(name) if obj.type == "ARMATURE" else None
    elif kind in {"object", "object_location"}:
        owner = obj
    else:
        owner = runtime._shape_key(object_name, name)
    if owner is None:
        return None
    if kind in {"bone", "object"}:
        return (owner.rotation_mode, owner.rotation_quaternion.copy(),
                owner.rotation_euler.copy(), tuple(owner.rotation_axis_angle))
    if kind in {"bone_location", "object_location"}:
        return owner.location.copy()
    return float(owner.value)


def suspend_apply_for_save():
    """Expose authored channels for serialization without retiring live leases."""
    current, originals, official = {}, {}, {}
    for owned in _owners.values():
        for target, original in owned.items():
            try:
                value = _snapshot_target(target)
                if value is not None:
                    current[target] = value
                    originals[target] = original
            except Exception as error:
                print(f"Yugo could not inspect the Apply target before saving {target}: {error}")
    for name, record in _official.items():
        try:
            armature = _objects().get(name)
            spring = runtime._official_spring_bone(armature, record["data"]) if armature else None
            if spring is not None:
                official[name] = (record["data"], bool(spring.enable_animation))
        except Exception as error:
            print(f"Yugo could not inspect Spring settings before saving {name}: {error}")
    snapshot = {"channels": current, "official": official}
    # Blender continues writing even if a save_pre handler raises. Keep every
    # successful restoration in place until save_post instead of rolling it back.
    for target, original in originals.items():
        try:
            _restore_target(target, original)
        except Exception as error:
            print(f"Yugo could not restore authored channels before saving {target}: {error}")
    for name in official:
        try:
            record = _official[name]
            spring = runtime._official_spring_bone(_objects()[name], record["data"])
            spring.enable_animation = record["enabled"]
        except Exception as error:
            print(f"Yugo could not restore authored Spring settings before saving {name}: {error}")
    return snapshot


def resume_apply_after_save(snapshot):
    """Restore the preview after a successful or failed file save."""
    if snapshot is None:
        return
    for target, value in snapshot["channels"].items():
        try:
            _restore_target(target, value)
        except Exception as error:
            print(f"Yugo could not restore the preview for {target}: {error}")
    for name, (data, enabled) in snapshot["official"].items():
        try:
            armature = _objects().get(name)
            spring = runtime._official_spring_bone(armature, data) if armature else None
            if spring is not None:
                spring.enable_animation = enabled
        except Exception as error:
            print(f"Yugo could not restore Spring preview for {name}: {error}")


def release(uid):
    from . import object_transforms
    object_transforms.release(uid)
    for target, base in _owners.pop(uid, {}).items():
        _restore_target(target, base)
    _release_official(uid)
    from . import physics
    physics.release_bone_apply(uid)


def _release_official(uid, keep=None):
    for name, record in tuple(_official.items()):
        if name == keep:
            continue
        record["owners"].discard(uid)
        if not record["owners"]:
            armature = _objects().get(name)
            if armature is None:
                from . import physics
                armature = physics.resolve_bone_object(name)
            spring = runtime._official_spring_bone(armature, record["data"]) if armature else None
            if spring is not None:
                spring.enable_animation = record["enabled"]
            del _official[name]


def _bone_position_targets(armature, transforms, uid, disconnected=(), handoff=()):
    """Route connected translations through their nearest movable ancestor.

    Blender stores location on connected pose bones but omits it from their
    evaluated matrices.  Evaluate translation directions with Blender's bone
    conversion API, including Local Location and inheritance, without touching
    the rig.  A routed value starts from the owner's original helper position,
    so repeated pulls never add the same movement again.
    """
    positions = {}
    poses = {}
    bases = {}
    routed = {}
    released = {uid, *handoff}

    def original_location(bone):
        target = ("bone_location", armature.name, bone.name)
        for owned in _owners.values():
            if target in owned:
                return Vector(owned[target])
        return bone.location.copy()

    def released_channel(kind, bone):
        target = (kind, armature.name, bone.name)
        return next((owned[target] for owner, owned in _owners.items()
                     if owner in released and target in owned), None)

    def basis(bone):
        if bone.name not in bases:
            transform = transforms.get(bone.name)
            location, rotation, scale = bone.matrix_basis.decompose()
            if transform is not None and transform.rotation is not None:
                rotation = transform.rotation.normalized()
            else:
                original = released_channel("bone", bone)
                if original is not None:
                    mode, quaternion, euler, axis = original
                    if mode == "QUATERNION":
                        rotation = quaternion.normalized()
                    elif mode == "AXIS_ANGLE":
                        vector = Vector(axis[1:])
                        rotation = Quaternion(vector, axis[0]) if vector.length_squared > 1e-16 else Quaternion()
                    else:
                        rotation = euler.to_quaternion()
            if transform is not None and transform.location is not None:
                location = transform.location.copy()
            elif released_channel("bone_location", bone) is not None:
                # This node is releasing the absent channel after preflight.
                location = original_location(bone)
            if bone.bone.use_connect and bone.name not in disconnected:
                location = Vector((0, 0, 0))
            bases[bone.name] = Matrix.LocRotScale(location, rotation, scale)
        return bases[bone.name]

    def convert(bone, local):
        kwargs = {}
        if bone.parent is not None:
            kwargs = {"parent_matrix": pose(bone.parent),
                      "parent_matrix_local": bone.parent.bone.matrix_local}
        return bone.bone.convert_local_to_pose(local, bone.bone.matrix_local, **kwargs)

    def pose(bone):
        if bone.name not in poses:
            poses[bone.name] = convert(bone, basis(bone))
        return poses[bone.name]

    for name, transform in transforms.items():
        bone = armature.pose.bones.get(name)
        if bone is None or transform.location is None:
            continue
        if not bone.bone.use_connect or name in disconnected:
            positions[name] = transform.location.copy()
            continue
        # Keep the ignored RNA channel writable/restorable for compatibility;
        # a zero connected channel does not request movement of its ancestor.
        positions[name] = transform.location.copy()
        if transform.location.length_squared <= runtime.VALUE_EPSILON * runtime.VALUE_EPSILON:
            continue
        local = basis(bone).copy()
        local.translation = transform.location
        displacement = convert(bone, local).translation - pose(bone).translation
        ancestor = bone.parent
        while ancestor is not None and ancestor.bone.use_connect and ancestor.name not in disconnected:
            ancestor = ancestor.parent
        if ancestor is None:
            raise ValueError(f"Connected bone {name} has no movable ancestor")
        previous = routed.get(ancestor.name)
        if previous is not None and (previous[1] - displacement).length > 2e-5:
            raise ValueError(f"Connected bone positions for {previous[0]} and {name} conflict at {ancestor.name}")
        routed[ancestor.name] = (name, displacement)

    for name, (source, displacement) in routed.items():
        bone = armature.pose.bones[name]
        local = basis(bone).copy()
        # An unexported helper's captured pre-Apply position is the origin.
        if name not in positions:
            local.translation = original_location(bone)
        start = convert(bone, local).translation
        columns = []
        for axis in range(3):
            moved = local.copy()
            moved.translation[axis] += 1.0
            columns.append(convert(bone, moved).translation - start)
        mapping = Matrix(columns).transposed()
        if abs(mapping.determinant()) <= 1e-12:
            raise ValueError(f"Connected bone {source} cannot translate through zero-scale ancestor {name}")
        positions[name] = local.translation + mapping.inverted() @ displacement
    return positions


def _disconnect_hips(choices):
    """Permanently unlock mapped hips with one brief, context-preserving edit.

    Bone.use_connect is read-only outside Edit Mode.  Only connection flags are
    changed; rest geometry is untouched.  All targets and ownership conflicts
    have already been validated when this function is called.
    """
    groups = {}
    for armature, names in choices:
        if names:
            entry = groups.setdefault(armature.data.as_pointer(), (armature, set()))
            entry[1].update(names)
    if not groups:
        return
    layer = bpy.context.view_layer
    active = layer.objects.active
    mode = active.mode if active is not None else "OBJECT"
    selected = tuple(obj for obj in layer.objects if obj.select_get(view_layer=layer))
    targets_to_edit = tuple(entry[0] for entry in groups.values())
    hidden = {obj.name: (obj.hide_viewport, obj.hide_get(view_layer=layer)) for obj in targets_to_edit}
    poses = {}
    for obj in _objects().values():
        if obj.type == "ARMATURE" and obj.data.as_pointer() in groups:
            poses[obj.name] = {
                bone.name: (bone.rotation_mode, bone.rotation_quaternion.copy(), bone.rotation_euler.copy(),
                            tuple(bone.rotation_axis_angle), bone.location.copy(), bone.scale.copy())
                for bone in obj.pose.bones}
    changed = []
    try:
        if active is not None and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in layer.objects:
            obj.select_set(False, view_layer=layer)
        for obj in targets_to_edit:
            obj.hide_viewport = False
            obj.hide_set(False, view_layer=layer)
            obj.select_set(True, view_layer=layer)
        layer.objects.active = targets_to_edit[0]
        bpy.ops.object.mode_set(mode="EDIT")
        for armature, names in groups.values():
            for name in names:
                bone = armature.data.edit_bones.get(name)
                if bone is None:
                    raise ValueError(f"Mapped Hips bone is unavailable: {name}")
                changed.append((armature, name, bool(bone.use_connect)))
                bone.use_connect = False
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as error:
        # An edit failure must not leave an earlier selected target changed.
        try:
            if layer.objects.active is not None and layer.objects.active.mode != "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")
            for armature, name, connected in changed:
                bone = armature.data.edit_bones.get(name)
                if bone is not None:
                    bone.use_connect = connected
        except (AttributeError, RuntimeError, ValueError):
            pass
        raise ValueError(f"Cannot disconnect mapped Hips: {error}") from error
    finally:
        if layer.objects.active is not None and layer.objects.active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        # Edit Mode rebuilds pose RNA and can zero previously ignored connected
        # locations.  Preserve every affected instance before Apply captures its
        # restoration values, including unowned helpers and sparse channels.
        for object_name, channels in poses.items():
            obj = _objects().get(object_name)
            if obj is None:
                continue
            for name, values in channels.items():
                bone = obj.pose.bones.get(name)
                if bone is not None:
                    bone.rotation_mode, quaternion, euler, axis, location, scale = values
                    bone.rotation_quaternion = quaternion
                    bone.rotation_euler = euler
                    bone.rotation_axis_angle = axis
                    bone.location = location
                    bone.scale = scale
        for obj in layer.objects:
            obj.select_set(False, view_layer=layer)
        for obj in selected:
            obj.select_set(True, view_layer=layer)
        layer.objects.active = active
        if active is not None and mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)
        for obj in targets_to_edit:
            hide_viewport, hide_local = hidden[obj.name]
            obj.hide_viewport = hide_viewport
            obj.hide_set(hide_local, view_layer=layer)


def apply(node, value, context=None):
    uid = str(node.runtime_id)
    desired = {}
    disconnect = []
    if node.bl_idname == YUGUS_NODE_apply_rotation.bl_idname:
        if not isinstance(value, BoneTransforms):
            raise ValueError("Connect Bone Transform")
        _validate_bone_transforms(value)
        from . import physics
        physics.authorize_bones(value)
        selection = targets.Armatures((node.armature.name,)) if node.armature else targets.Armatures((value.armature_name,))
        if context is not None:
            selection = context.input(node, "Armatures", selection)
        if not isinstance(selection, targets.Armatures) or not selection.names:
            raise ValueError("Select or connect Armatures")
        for target_name in dict.fromkeys(selection.names):
            armature = _objects().get(target_name)
            if armature is None or armature.type != "ARMATURE":
                raise ValueError("Target Armature is unavailable")
            if value.spring_data is not None and target_name != value.armature_name:
                raise ValueError("Spring transforms must target their simulated Armature")
            hips = {name for name in value.hips_bones if name in value.bones
                    and (value.bones[name].location is not None or value.bones[name].rotation is not None)
                    and name in armature.data.bones and armature.data.bones[name].use_connect}
            if hips:
                if armature.name not in bpy.context.view_layer.objects or not armature.data.is_editable:
                    raise ValueError("Mapped Hips requires an editable Armature in the current view layer")
                disconnect.append((armature, hips))
            positions = _bone_position_targets(armature, value.bones, uid, hips, getattr(context, "handoff_owners", ()))
            for name, transform in value.bones.items():
                bone = armature.pose.bones.get(name)
                if bone:
                    if transform.rotation is not None:
                        desired[("bone", armature.name, name)] = (bone, transform.rotation)
            for name, position in positions.items():
                desired[("bone_location", armature.name, name)] = (armature.pose.bones[name], position)
            if value.root is not None:
                if value.root.rotation is not None:
                    desired[("object", armature.name, "")] = (armature, value.root.rotation)
                if value.root.location is not None:
                    desired[("object_location", armature.name, "")] = (armature, value.root.location)
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
    from . import object_transforms
    for kind, object_name, name in desired:
        if kind in {'object', 'object_location'} and any(
                object_name in record['originals'] for record in object_transforms._owners.values()):
            raise ValueError('Another Apply Transform node already controls this object')
    transfers = []
    for other, owned in _owners.items():
        overlap = desired.keys() & owned.keys() if other != uid else set()
        if overlap:
            if other not in handoff:
                raise ValueError("Another Apply node already controls this target")
            transfers.append((other, overlap))
    _disconnect_hips(disconnect)
    # Edit Mode can rebuild pose RNA; resolve channels again before writing.
    if disconnect:
        for target, (owner, result) in tuple(desired.items()):
            kind, object_name, name = target
            if kind in {"bone", "bone_location"}:
                desired[target] = (_objects()[object_name].pose.bones[name], result)
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
    spring_target = value.armature_name if isinstance(value, BoneTransforms) and value.spring_data is not None else None
    _release_official(uid, spring_target)
    if spring_target:
        armature = _objects().get(spring_target)
        spring = runtime._official_spring_bone(armature, value.spring_data)
        if spring is not None:
            record = _official.setdefault(armature.name, {"enabled": bool(spring.enable_animation), "data": value.spring_data, "owners": set()})
            record["owners"].add(uid)
            if spring.enable_animation:
                spring.enable_animation = False
    for target, (owner, result) in desired.items():
        if target[0] in {"bone", "object"}:
            previous.setdefault(target, (owner.rotation_mode, owner.rotation_quaternion.copy(), owner.rotation_euler.copy(), tuple(owner.rotation_axis_angle)))
            runtime._assign_rotation(owner, result)
        elif target[0] in {"bone_location", "object_location"}:
            previous.setdefault(target, owner.location.copy())
            runtime._assign_location(owner, result)
        else:
            previous.setdefault(target, float(owner.value))
            owner.value = result
    if not previous:
        _owners.pop(uid, None)
    if isinstance(value, BoneTransforms):
        from . import physics
        physics.sync_bone_apply(node, value, previous)
    summary = f"Applied {len(desired)} values"
    if node.last_summary != summary:
        node.last_summary = summary


def _uid(node):
    return str(getattr(node, "runtime_id", "") or node.name)


def _enabled(node):
    from .availability import is_available
    return is_available(node) and not node.mute and bool(getattr(node, "enabled", True))


def _summary(node, text):
    if hasattr(node, "last_summary") and node.last_summary != text:
        node.last_summary = text


def _action_input(socket):
    node = socket.node
    if node.bl_idname == "YUGUS_NODE_physics_solver":
        return socket.name in {"Reset", "Stop"}
    if node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"} and socket.name in {"Connect", "Disconnect"}:
        return True
    if node.bl_idname == "YUGUS_NODE_yt_chat_source":
        return socket.name == "Connect"
    if node.bl_idname in {"YUGUS_NODE_yt_chat_event", "YUGUS_NODE_chat_debug", "YUGUS_NODE_keyword_match"}:
        return socket.name == "Enter"
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


def live_apply_ids(tree):
    """Find Apply sinks reachable through live event sources and branches."""
    roots = []
    definition = workflow.is_definition(tree)
    for node in tree.nodes:
        if not _enabled(node):
            continue
        if (node.bl_idname == YUGUS_NODE_hz_trigger.bl_idname and not definition
                or node.bl_idname not in FLOW_TYPES | workflow.ACTION_TYPES
                and (not definition or workflow.active_in(tree))):
            for output in _event_outputs(node):
                roots.extend(_event_targets(output))
        if not definition:
            for socket in node.inputs:
                if _action_input(socket):
                    source = _source_socket(socket)
                    if source is not None and source.bl_idname != EVENT:
                        roots.append(socket)
    return _route_nodes(roots)[0]


def _control_signature(node):
    source = _source_socket(node.inputs.get("Enter"))
    incoming = (_uid(source.node), source.identifier) if source else None
    outgoing = tuple(
        (output.name, _uid(target.node), target.name)
        for output in _event_outputs(node) for target in _event_targets(output)
    )
    definition = getattr(node, "workflow_tree", None)
    target = (definition.name, workflow.is_definition(definition)) if definition is not None else None
    return node.bl_idname, incoming, outgoing, target


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
        if node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"} and target.name in {"Connect", "Disconnect"}:
            # Connect first, then Disconnect, before any data pull. A pulse
            # wired to both actions therefore leaves the receiver stopped.
            priorities.append(-2 if target.name == "Connect" else -1)
        elif target.name in {"Reload", "Calibrate"}:
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
        if workflow.HANDOFF_KEY in self.metadata:
            self.context.handoff_owners = set(self.metadata[workflow.HANDOFF_KEY])
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
        if node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}:
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
            if node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"} and socket.name in {"Connect", "Disconnect"}:
                source_connection_action(node, socket.name.upper(), context=self.context)
                self.context.cache.clear()
                return
            if node.bl_idname == "YUGUS_NODE_physics_solver":
                from . import physics
                physics.action(node, socket.name, scene=self.context.scene)
                self.context.cache.clear()
                return
            if node.bl_idname == "YUGUS_NODE_yt_chat_source" and socket.name == "Connect":
                from . import chat_runtime
                chat_runtime.connect(node, context=self.context)
                return
            if node.bl_idname == "YUGUS_NODE_keyword_match" and socket.name == "Enter":
                from . import keyword_nodes
                matched = keyword_nodes.receive(node, self.context)
                branch = "Matched" if matched else "Not Matched"
                other = "Not Matched" if matched else "Matched"
                chosen, _ = _route_nodes(_event_targets(node.outputs[branch]))
                unchosen, _ = _route_nodes(_event_targets(node.outputs[other]))
                before = getattr(self.context, "handoff_owners", set())
                self.context.handoff_owners = before | (unchosen - chosen)
                try:
                    self.emit(node.outputs.get(branch), path)
                finally:
                    self.context.handoff_owners = before
                return
            if node.bl_idname == "YUGUS_NODE_chat_list":
                from . import chat_ui_runtime
                if socket.name == "Reset":
                    chat_ui_runtime.reset(node, self.context)
                else:
                    chat_ui_runtime.append(node, self.context)
                    self.emit(node.outputs.get("End"), path)
                return
            if node.bl_idname == "YUGUS_NODE_yt_chat_event":
                if workflow.is_definition(node.id_data) or workflow._runs[self.run_id].get("parent") is not None:
                    raise ValueError("Place YT Chat Event before Action Flow; connect End to Action Flow Enter")
                from . import chat_runtime
                for message in chat_runtime.pull_events(node, self.context):
                    # A fresh invocation and cache for each message, including
                    # multiple messages returned by the same 60 Hz Enter.
                    payload = chat_runtime.event_payload(node, self.payload, message)
                    child = _Pulse(self.tree, self.context.scene, self.now, self.context.delta_time,
                                   set(), payload=payload, metadata=self.metadata)
                    child.emit(node.outputs.get("End"), path)
                return
            if node.bl_idname == "YUGUS_NODE_chat_debug":
                from . import chat_runtime
                chat_runtime.debug(node, self.context)
                self.emit(node.outputs.get("End"), path)
                return
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
                if node.bl_idname == "YUGUS_NODE_apply_object_transform":
                    from . import object_transforms
                    object_transforms.apply(node, self.context.input(node, "Object Transforms", None))
                else:
                    value = (_transform_input(self.context, node)
                             if node.bl_idname == YUGUS_NODE_apply_rotation.bl_idname
                             else self.context.input(node, "Shape Keys Values", None))
                    apply(node, value, self.context)
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
            valid = node is not None and _enabled(node) and _control_signature(node) == task["signature"]
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
    live_motion_delays = set()
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
            if node.bl_idname == "YUGUS_NODE_motion_delay":
                motion = node.inputs.get("Motion")
                output = node.outputs.get("Motion")
                if motion is not None and motion.is_linked and output is not None and output.is_linked:
                    live_motion_delays.add(_uid(node))
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
        status_text = f"Flow · {runs} running invocations · {len(tree_active)} connected Apply nodes"
        if tree.last_status != status_text:
            tree.last_status = status_text
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
    from . import spring_trace
    demanded.update(spring_trace.legacy_ids())
    for uid in set(_springs) - demanded:
        _springs.pop(uid, None)
    for uid in set(_last_eval) - demanded:
        _last_eval.pop(uid, None)
    from . import motion_delay
    # Motion buffers can also be pulled by short Action Flow invocations in a
    # definition tree. Keep connected nodes between invocations and clock ticks.
    motion_delay.prune(live_motion_delays)
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
    from . import motion_delay, spring_trace
    spring_trace.clear()
    motion_delay.clear()
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
    from .availability import is_available
    for tree in getattr(bpy.data, "node_groups", ()):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tree.nodes:
            if not is_available(node):
                continue
            if node.bl_idname == "YUGUS_NODE_physics_solver":
                from . import physics
                physics.ensure_solver_sockets(node)
                continue
            if node.bl_idname == YUGUS_NODE_bone_rotation_mix.bl_idname:
                ensure_numeric_parameter(node)
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
            bottom_actions = ("Reload", "Calibrate")
            if node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}:
                bottom_actions = ("Reload", "Connect", "Disconnect", "Calibrate")
            for sockets in (node.inputs, node.outputs):
                events = [s for s in sockets if s.bl_idname in {EVENT, SOCKET_TRIGGER} and s.name not in bottom_actions]
                for index, socket in enumerate(events):
                    sockets.move(list(sockets).index(socket), index)
                for name in bottom_actions:
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
