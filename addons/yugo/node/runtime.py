"""Demand-driven graph evaluation and native Blender output."""

from __future__ import annotations

from datetime import datetime, timezone
from pprint import pformat
import sys
import time

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import action_source, network, vrm_cache
from .constants import (
    MASK_REGIONS,
    SOCKET_BONE_MAP,
    SOCKET_EXPRESSION_MAP,
    SOCKET_META,
    SOCKET_MOTION_MASK,
    SOCKET_TRIGGER,
    TREE_ID,
    humanoid_region,
    normalize_name,
)
from .map_values import (
    bone_map_from_node,
    expression_map_from_node,
    mix_bone_maps,
    mix_expression_maps,
)
from .data import (
    BoneMapData,
    ExpressionFrame,
    ExpressionMapData,
    HumanoidMask,
    HumanoidMotion,
    HumanoidTransform,
    MetaData,
    NativeExpressions,
    NativePose,
    SpringData,
)
from .processing import (
    QuaternionSpring,
    ScalarSpring,
    VectorSpring,
    mirror_motion,
    mix_expressions,
    mix_motion,
    weighted_expressions,
    weighted_motion,
)
from .protocols import ifacial_expression_aliases
from .spring_solver import NativeSpringSolver


TIMER_INTERVAL = 1.0 / 120.0
FIXED_SPRING_STEP = 1.0 / 60.0
MAX_SPRING_SUBSTEPS = 1
ROTATION_EPSILON = 1.0e-10
VALUE_EPSILON = 1.0e-6
SPRING_DEBUG_TEXT_NAME = "Yugu Spring Debug"

_timer_registered = False
_last_output_time = {}
_active_output_ids = set()
_last_spring_time = {}
_active_spring_ids = set()
_node_state = {}
_pose_state = {}
_spring_output_state = {}
_expression_state = {}
_key_state = {}
_MISSING_OUTPUT = object()


def _tree_key(tree):
    try:
        return tree.as_pointer()
    except ReferenceError:
        return 0


def clear_node_state(uid):
    uid = str(uid)
    from . import motion_delay
    motion_delay.clear(uid)
    output_keys = {
        key for key in tuple(_pose_state) + tuple(_expression_state) if key[1] == uid
    }
    for key in output_keys:
        _restore_output_state(key)
        _last_output_time.pop(key, None)
        _active_output_ids.discard(key)
    for key in tuple(_spring_output_state):
        if key[1] == uid:
            _restore_spring_output_state(key)
            _active_spring_ids.discard(key)
    _node_state.pop(uid, None)
    _key_state.pop(uid, None)
    for key in tuple(_last_output_time):
        if key[1] == uid:
            _last_output_time.pop(key, None)
    for key in tuple(_last_spring_time):
        if key[1] == uid:
            _last_spring_time.pop(key, None)


def _linked_value(context, socket, fallback):
    if not socket.is_linked:
        return getattr(socket, "default_value", fallback)
    links = tuple(socket.links)
    if not links:
        return fallback
    link = links[0]
    values = context.evaluate(link.from_node)
    value = values.get(link.from_socket.identifier, _MISSING_OUTPUT)
    if value is _MISSING_OUTPUT:
        value = values.get(link.from_socket.name, fallback)
    from . import mixer
    return mixer.require_active(value)


class EvaluationContext:
    def __init__(self, tree, scene, delta_time):
        self.tree = tree
        self.scene = scene
        self.delta_time = delta_time
        self.cache = {}
        self.evaluating = set()

    def input(self, node, name, fallback):
        socket = node.inputs.get(name)
        return _linked_value(self, socket, fallback) if socket is not None else fallback

    def evaluate(self, node):
        uid = str(getattr(node, "runtime_id", "") or node.name)
        if uid in self.cache:
            return self.cache[uid]
        if uid in self.evaluating:
            raise RuntimeError(f"Cycle detected at {node.name}")
        self.evaluating.add(uid)
        try:
            values = _evaluate_node(self, node)
            self.cache[uid] = values
            return values
        finally:
            self.evaluating.discard(uid)


def _output(node, name, value):
    socket = node.outputs.get(name)
    return {socket.identifier if socket is not None else name: value, name: value}


def _numeric_parameter(context, node, name, property_name, minimum=0.0, maximum=1.0):
    """Read a linked numeric input; preserve saved/animated RNA when unlinked."""
    from .math_ops import finite_number
    fallback = finite_number(getattr(node, property_name))
    socket = node.inputs.get(name)
    value = context.input(node, name, fallback) if socket is not None and socket.is_linked else fallback
    try:
        value = finite_number(value)
    except (TypeError, ValueError) as error:
        raise type(error)(f"{node.bl_label} {name}: expected a finite Float input") from error
    return min(max(value, minimum), maximum)


def _set_monitor(node, lines):
    if not bool(getattr(node, "monitor_open", False)):
        return
    text = "\n".join(str(line) for line in lines) or "No data"
    if str(getattr(node, "monitor_text", "")) != text:
        node.monitor_text = text


def _motion_monitor_lines(motion, expressions=None):
    motion = motion if isinstance(motion, HumanoidMotion) else HumanoidMotion()
    expressions = (
        expressions if isinstance(expressions, ExpressionFrame) else ExpressionFrame()
    )
    lines = [f"Bones {len(motion.bones)} · Expressions {len(expressions.values)}"]
    for name, transform in tuple(motion.bones.items())[:5]:
        if transform.rotation is not None:
            q = transform.rotation
            value = f"R {q.w:.3f}, {q.x:.3f}, {q.y:.3f}, {q.z:.3f}"
        elif transform.location is not None:
            v = transform.location
            value = f"P {v.x:.3f}, {v.y:.3f}, {v.z:.3f}"
        else:
            value = "empty"
        lines.append(f"{name}: {value}")
    if len(motion.bones) > 5:
        lines.append(f"... {len(motion.bones) - 5} more bones")
    for name, value in tuple(expressions.values.items())[:4]:
        lines.append(f"{name}: {float(value):.3f}")
    if len(expressions.values) > 4:
        lines.append(f"... {len(expressions.values) - 4} more expressions")
    return lines


def _source_values(node):
    motion, expressions = network.source_snapshot(
        str(node.runtime_id), tuple(getattr(node, "calibration", (0.0, 0.0, 0.0)))
    )
    values = {}
    for name, value in (("Humanoid Motion", motion), ("Expressions", expressions)):
        socket = node.outputs.get(name)
        values[socket.identifier if socket is not None else name] = value
        values[name] = value
    status = network.status(str(node.runtime_id))
    node.connected = bool(status["running"])
    node.last_error = str(status["error"] or "")
    _set_monitor(node, _motion_monitor_lines(motion, expressions))
    return values


def _ifacial_source_values(node, delta_time):
    from .nodes import ensure_ifacial_blendshape_masks

    motion, raw_values = network.ifacial_source_snapshot(
        str(node.runtime_id),
        tuple(getattr(node, "calibration", (0.0, 0.0, 0.0))),
        bool(getattr(node, "mirror", False)),
    )
    ensure_ifacial_blendshape_masks(node, tuple(raw_values))
    raw_lookup = {
        normalize_name(name): float(value) for name, value in raw_values.items()
    }
    spring_state = _node_motion_state(node)["expressions"]
    output_values = {}
    for item in node.blendshape_masks:
        normalized = str(item.normalized_name or normalize_name(item.expression_name))
        if not item.normalized_name:
            item.normalized_name = normalized
        raw_value = raw_lookup.get(normalized, 0.0)
        item.raw_value = raw_value
        spring = spring_state.setdefault(normalized, ScalarSpring())
        output_value = spring.step(
            (raw_value / 100.0) * float(item.weight),
            float(item.smooth),
            delta_time,
        )
        item.output_value = output_value
        output_values[normalized] = output_value

    expressions = ExpressionFrame(output_values)
    values = {}
    for name, value in (("Humanoid Motion", motion), ("Expressions", expressions)):
        socket = node.outputs.get(name)
        values[socket.identifier if socket is not None else name] = value
        values[name] = value
    status = network.status(str(node.runtime_id))
    node.connected = bool(status["running"])
    node.last_error = str(status["error"] or "")
    return values


def _node_motion_state(node):
    return _node_state.setdefault(
        str(node.runtime_id), {"rotations": {}, "locations": {}, "expressions": {}}
    )


def _mask_weight(mask, name=None):
    if not isinstance(mask, HumanoidMask):
        return 1.0
    region = "root" if name is None else humanoid_region(name)
    return min(max(float(mask.weights.get(region, 1.0)), 0.0), 1.0)


def _smooth_channel(bank, name, factory, target, smooth, delta_time):
    """Repeated/untimed reads observe a filter without advancing its history."""
    spring = bank.get(name)
    if delta_time <= 0:
        if smooth <= 0 or spring is None:
            return target.copy() if hasattr(target, 'copy') else target
        if isinstance(spring, VectorSpring):
            return Vector(tuple(component.value for component in spring.components))
        return spring.value.copy() if hasattr(spring.value, 'copy') else spring.value
    if spring is None:
        spring = bank[name] = factory()
    return spring.step(target, smooth, delta_time)


def _smooth_motion(node, source, mask, delta_time, smooth=None):
    smooth = float(node.smooth) if smooth is None else float(smooth)
    result = source.copy()
    state = (_node_motion_state(node) if delta_time > 0 else
             _node_state.get(str(node.runtime_id), {"rotations": {}, "locations": {}}))
    for name, value in result.bones.items():
        influence = _mask_weight(mask, name)
        if value.rotation is not None:
            target = value.rotation.copy()
            smoothed = _smooth_channel(state["rotations"], name, QuaternionSpring,
                                       target, smooth * influence, delta_time)
            if target.dot(smoothed) < 0.0:
                smoothed.negate()
            value.rotation = smoothed
        if value.location is not None:
            target = value.location.copy()
            value.location = _smooth_channel(state["locations"], name, VectorSpring,
                                             target, smooth * influence, delta_time)
    if result.root is not None:
        influence = _mask_weight(mask)
        if result.root.rotation is not None:
            target = result.root.rotation.copy()
            smoothed = _smooth_channel(state["rotations"], "__root__", QuaternionSpring,
                                       target, smooth * influence, delta_time)
            if target.dot(smoothed) < 0.0:
                smoothed.negate()
            result.root.rotation = smoothed
        if result.root.location is not None:
            target = result.root.location.copy()
            result.root.location = _smooth_channel(state["locations"], "__root__", VectorSpring,
                                                   target, smooth * influence, delta_time)
    return result


def _head_profile(node, source):
    result = source.copy()
    source_head = source.bones.get("head")
    if source_head is None or source_head.rotation is None:
        return result
    # Canonical Blender axes are X pitch, Y roll, Z negative yaw. YXZ
    # matches Yugo's Unity Y(yaw) @ X(pitch) @ Z(roll) conversion.
    euler = source_head.rotation.to_euler("YXZ")
    for name, factors in (
        ("head", node.head),
        ("neck", node.neck),
        ("chest", node.chest),
        ("spine", node.spine),
    ):
        pitch, yaw, roll = (float(factor) for factor in factors)
        rotation = Euler(
            (euler.x * pitch, euler.y * roll, euler.z * yaw), "YXZ"
        ).to_quaternion()
        rotation.normalize()
        result.bones[name] = HumanoidTransform(rotation, None)
    return result


def _name_set(value):
    return {
        normalize_name(name)
        for name in str(value).split(",")
        if normalize_name(name)
    }


def _expression_mask(node, source):
    selected = _name_set(node.names)
    result = source.copy()
    for name in tuple(result.values):
        if normalize_name(name) in selected:
            result.values[name] *= float(node.weight)
    return result


def _smooth_expressions(node, source, delta_time, smooth=None, names=None):
    smooth = float(node.smooth) if smooth is None else float(smooth)
    selected = _name_set(node.names if names is None else names)
    state = (_node_motion_state(node)["expressions"] if delta_time > 0 else
             _node_state.get(str(node.runtime_id), {}).get("expressions", {}))
    result = source.copy()
    for name, value in result.values.items():
        if selected and normalize_name(name) not in selected:
            continue
        result.values[name] = _smooth_channel(state, normalize_name(name), ScalarSpring,
                                              value, smooth, delta_time)
    return result


def _expression_lock(node, source):
    result = source.copy()
    for name in tuple(result.values):
        normalized = normalize_name(name)
        is_eye = any(token in normalized for token in ("eye", "blink", "look"))
        is_mouth = any(token in normalized for token in ("mouth", "jaw", "tongue"))
        if (node.lock_eyes and is_eye) or (node.lock_mouth and is_mouth):
            result.values[name] = 0.0
    return result


def _keybind_signal(node):
    from . import keyboard
    return keyboard.signal(node)


def _legacy_keybind_value(node):
    return ExpressionFrame({normalize_name(node.expression): _keybind_signal(node)})


def _expression_action(node, trigger, expression_map):
    if not isinstance(expression_map, ExpressionMapData):
        return ExpressionFrame()
    key = normalize_name(getattr(node, "expression_key", ""))
    if key not in expression_map.definitions:
        return ExpressionFrame()
    value = min(
        max(float(trigger) * float(getattr(node, "strength", 1.0)), 0.0),
        1.0,
    )
    return ExpressionFrame({key: value})


def _evaluate_node(context, node):
    from .availability import require_available
    require_available(node)
    from . import flow
    kind = node.bl_idname
    from . import physics, mixer
    for evaluator in (physics.evaluate, mixer.evaluate):
        result = evaluator(context, node)
        if result is not None:
            return result
    from . import math_nodes
    math_result = math_nodes.evaluate(context, node)
    if math_result is not None:
        return math_result
    from . import chat_runtime, chat_ui_runtime, image_runtime, keyword_nodes, value_nodes
    value_result = value_nodes.evaluate(context, node)
    if value_result is not None:
        return value_result
    keyword_result = keyword_nodes.evaluate(context, node)
    if keyword_result is not None:
        return keyword_result
    image_result = image_runtime.evaluate(context, node)
    if image_result is not None:
        return image_result
    ui_result = chat_ui_runtime.evaluate(context, node)
    if ui_result is not None:
        return ui_result
    chat_result = chat_runtime.evaluate(context, node)
    if chat_result is not None:
        return chat_result
    from . import input_runtime, workflow
    input_result = input_runtime.evaluate(context, node)
    if input_result is not None:
        return input_result
    workflow_result = workflow.evaluate(context, node)
    if workflow_result is not None:
        return workflow_result
    if node.mute and node.outputs:
        if kind == "YUGUS_NODE_motion_delay":
            from . import motion_delay
            motion_delay.clear(str(node.runtime_id))
        output_socket = node.outputs[0]
        fallback = {
            "YUGUS_NS_expressions": ExpressionFrame(),
            "YUGUS_NS_native_expressions": NativeExpressions(),
            "YUGUS_NS_native_pose": NativePose(),
            SOCKET_BONE_MAP: BoneMapData("", {}, {}),
            SOCKET_EXPRESSION_MAP: ExpressionMapData({}),
            SOCKET_META: MetaData({}),
            SOCKET_MOTION_MASK: HumanoidMask(),
            SOCKET_TRIGGER: 0.0,
            flow.TRANSFORMS: flow.BoneTransforms(),
        }.get(output_socket.bl_idname, HumanoidMotion())
        passthrough = next(
            (
                socket
                for socket in node.inputs
                if socket.bl_idname == output_socket.bl_idname
            ),
            None,
        )
        value = (
            context.input(node, passthrough.name, fallback)
            if passthrough is not None
            else fallback
        )
        return _output(node, output_socket.name, value)

    from . import motion_delay
    delay_result = motion_delay.evaluate(context, node)
    if delay_result is not None:
        return delay_result

    if kind == "YUGUS_NODE_vrm_data":
        from .targets import Armatures, Objects
        snapshot = vrm_cache.get_snapshot(node)
        values = {
            "Bone Map": snapshot.bone_map if snapshot else None,
            "Expression Map": snapshot.expression_map if snapshot else None,
            "Spring Data": snapshot.spring_data if snapshot else None,
            "Meta": snapshot.meta if snapshot else None,
            "Armatures": Armatures((snapshot.armature_name,)) if snapshot else Armatures(),
            "Objects": Objects(snapshot.objects) if snapshot else Objects(),
        }
        result = {}
        for name, value in values.items():
            socket = node.outputs.get(name)
            result[socket.identifier if socket is not None else name] = value
            result[name] = value
        return result
    if kind == "YUGUS_NODE_bone_map_value":
        return _output(node, "Bone Map", bone_map_from_node(node))
    if kind == "YUGUS_NODE_meta_value":
        from .nodes import META_TEXT_FIELDS
        values = {}
        for name, property_name, key in META_TEXT_FIELDS:
            value = getattr(node, property_name)
            if node.inputs[name].is_linked:
                value = context.input(node, name, value)
            if not isinstance(value, str):
                raise ValueError(f"Meta Value {name}: expected text")
            values[key] = ((value,) if value else ()) if key == "authors" else value
        return _output(node, "Meta", MetaData(values))
    if kind == "YUGUS_NODE_bone_map_mix":
        return _output(
            node,
            "Bone Map",
            mix_bone_maps(
                context.input(node, "Base", None),
                context.input(node, "Overlay", None),
            ),
        )
    if kind == "YUGUS_NODE_expression_map_value":
        return _output(node, "Expression Map", expression_map_from_node(node))
    if kind == "YUGUS_NODE_expression_map_mix":
        return _output(
            node,
            "Expression Map",
            mix_expression_maps(
                context.input(node, "Base", None),
                context.input(node, "Overlay", None),
            ),
        )
    if kind == "YUGUS_NODE_vmc_source":
        return _source_values(node)
    if kind == "YUGUS_NODE_ifacial_source":
        return _ifacial_source_values(node, context.delta_time)
    if kind == "YUGUS_NODE_action_source":
        trigger = min(max(float(context.input(node, "Trigger", 1.0)), 0.0), 1.0)
        if trigger <= 0.0:
            _set_monitor(node, ["Trigger 0.000", "Action inactive"])
            return _output(node, "Humanoid Motion", HumanoidMotion())
        bone_map = context.input(node, "Source Bone Map", None)
        motion = action_source.sample_action(
            node.action,
            bone_map,
            float(context.scene.frame_current) + float(context.scene.frame_subframe),
            node.speed,
            node.frame_offset,
            node.cyclic,
        )
        if trigger < 1.0:
            motion = weighted_motion(motion, trigger)
        frame = float(context.scene.frame_current) + float(context.scene.frame_subframe)
        _set_monitor(
            node,
            [f"Trigger {trigger:.3f}", f"Frame {frame:.3f}"]
            + _motion_monitor_lines(motion),
        )
        return _output(node, "Humanoid Motion", motion)
    if kind == "YUGUS_NODE_key_bind":
        value = _keybind_signal(node)
        modifiers = "+".join(
            name
            for name, enabled in (
                ("Ctrl", node.use_ctrl),
                ("Shift", node.use_shift),
            )
            if enabled
        )
        key = f"{modifiers}+{node.key}" if modifiers else str(node.key)
        _set_monitor(
            node,
            [f"Trigger {value:.3f}", f"{key} · {str(node.mode).title()}"],
        )
        return _output(node, "Trigger", value)
    if kind == "YUGUS_NODE_keybind_expression":
        return _output(node, "Expressions", _legacy_keybind_value(node))
    if kind == "YUGUS_NODE_expression_action":
        return _output(
            node,
            "Expressions",
            _expression_action(
                node,
                context.input(node, "Trigger", 0.0),
                context.input(node, "Expression Map", None),
            ),
        )
    if kind == "YUGUS_NODE_head_profile":
        source = context.input(node, "Motion", HumanoidMotion())
        return _output(node, "Motion", _head_profile(node, source))
    if kind == "YUGUS_NODE_humanoid_mask":
        weights = {region: float(getattr(node, region)) for region in MASK_REGIONS}
        return _output(node, "Mask", HumanoidMask(weights))
    if kind == "YUGUS_NODE_motion_smooth":
        source = context.input(node, "Motion", HumanoidMotion())
        mask = context.input(node, "Mask", HumanoidMask())
        return _output(
            node,
            "Motion",
            _smooth_motion(
                node, source, mask, context.delta_time,
                _numeric_parameter(context, node, "Smooth", "smooth"),
            ),
        )
    if kind == "YUGUS_NODE_mirror_motion":
        return _output(
            node, "Motion", mirror_motion(context.input(node, "Motion", HumanoidMotion()))
        )
    if kind == "YUGUS_NODE_motion_weight":
        return _output(
            node,
            "Motion",
            weighted_motion(
                context.input(node, "Motion", HumanoidMotion()),
                _numeric_parameter(context, node, "Weight", "weight"),
                context.input(node, "Mask", HumanoidMask()),
            ),
        )
    if kind == "YUGUS_NODE_motion_mix":
        return _output(
            node,
            "Motion",
            mix_motion(
                context.input(node, "Base", HumanoidMotion()),
                context.input(node, "Overlay", HumanoidMotion()),
                _numeric_parameter(context, node, "Factor", "factor"),
                context.input(node, "Mask", HumanoidMask()),
            ),
        )
    if kind == "YUGUS_NODE_humanoid_retarget":
        motion = context.input(node, "Humanoid Motion", HumanoidMotion())
        bone_map = context.input(node, "Bone Map", None)
        return _output(node, "Native Pose", _retarget_motion(motion, bone_map))
    if kind == "YUGUS_NODE_expression_mask":
        return _output(
            node,
            "Expressions",
            _expression_mask(node, context.input(node, "Expressions", ExpressionFrame())),
        )
    if kind == "YUGUS_NODE_expression_smooth":
        names = node.names
        socket = node.inputs.get("Names")
        if socket is not None and socket.is_linked:
            names = context.input(node, "Names", names)
        if not isinstance(names, str):
            raise ValueError("Expression Smooth Names: expected text")
        return _output(
            node,
            "Expressions",
            _smooth_expressions(
                node,
                context.input(node, "Expressions", ExpressionFrame()),
                context.delta_time,
                _numeric_parameter(context, node, "Smooth", "smooth"),
                names,
            ),
        )
    if kind == "YUGUS_NODE_expression_weight":
        return _output(
            node,
            "Expressions",
            weighted_expressions(
                context.input(node, "Expressions", ExpressionFrame()),
                _numeric_parameter(context, node, "Weight", "weight", -5.0, 5.0),
            ),
        )
    if kind == "YUGUS_NODE_expression_mix":
        return _output(
            node,
            "Expressions",
            mix_expressions(
                context.input(node, "Base", ExpressionFrame()),
                context.input(node, "Overlay", ExpressionFrame()),
                _numeric_parameter(context, node, "Factor", "factor"),
                node.mode,
            ),
        )
    if kind == "YUGUS_NODE_expression_lock":
        return _output(
            node,
            "Expressions",
            _expression_lock(node, context.input(node, "Expressions", ExpressionFrame())),
        )
    flow_result = flow.evaluate(context, node)
    if flow_result is not None:
        return flow_result
    if kind == "YUGUS_NODE_expression_bind":
        expressions = context.input(node, "Expressions", ExpressionFrame())
        expression_map = context.input(node, "Expression Map", None)
        return _output(
            node,
            "Native Expressions",
            _bind_expressions(expressions, expression_map),
        )

    if kind == "YUGUS_NODE_data_viewer":
        bone_map = context.input(node, "Bone Map", None)
        expression_map = context.input(node, "Expression Map", None)
        spring_data = context.input(node, "Spring Data", None)
        meta = context.input(node, "Meta", None)
        node.summary = _viewer_summary(bone_map, expression_map, spring_data, meta)
        simulation = context.input(node, "Simulation Data", None)
        if isinstance(simulation, dict) and 'bones' in simulation:
            node.summary = f"{simulation['engine']} · {simulation['simulation_time_s']:.3f}s · {len(simulation['bones'])} Spring bones"
        return {}
    return {}


def _viewer_summary(bone_map, expression_map, spring_data, meta):
    parts = []
    if isinstance(bone_map, BoneMapData):
        parts.append(f"{len(bone_map.by_humanoid)} bones")
    if isinstance(expression_map, ExpressionMapData):
        parts.append(f"{len(expression_map.definitions)} expressions")
    if isinstance(spring_data, SpringData):
        parts.append(f"{len(spring_data.springs)} springs")
    if isinstance(meta, MetaData):
        parts.append(str(meta.values.get("name", "")))
    return ", ".join(part for part in parts if part) or "No data"


def _assign_rotation(owner, value):
    value = value.copy()
    value.normalize()
    changed = owner.rotation_mode != "QUATERNION"
    if changed:
        owner.rotation_mode = "QUATERNION"
    current = owner.rotation_quaternion.copy()
    current.normalize()
    if 1.0 - abs(current.dot(value)) > ROTATION_EPSILON:
        owner.rotation_quaternion = value
        changed = True
    return changed


def _assign_location(owner, value):
    if (owner.location - value).length_squared <= VALUE_EPSILON * VALUE_EPSILON:
        return False
    owner.location = value
    return True


def _retarget_motion(motion, bone_map):
    if not isinstance(motion, HumanoidMotion) or not isinstance(bone_map, BoneMapData):
        return NativePose()
    armature = bpy.data.objects.get(bone_map.armature_name)
    if armature is None or armature.type != "ARMATURE":
        return NativePose()
    result = NativePose(armature.name, root=motion.root.copy() if motion.root else None)
    for humanoid, transform in motion.bones.items():
        bone_name = bone_map.by_humanoid.get(humanoid)
        pose_bone = armature.pose.bones.get(bone_name) if bone_name else None
        if pose_bone is None:
            continue
        rest = pose_bone.bone.matrix_local.to_3x3().normalized()
        rest_rotation = rest.to_quaternion()
        local_rotation = None
        if transform.rotation is not None:
            local_rotation = rest_rotation.inverted() @ transform.rotation @ rest_rotation
            local_rotation.normalize()
        local_location = (
            rest.inverted() @ transform.location
            if transform.location is not None
            else None
        )
        result.bones[bone_name] = HumanoidTransform(local_rotation, local_location)
    return result


def _spring_output_key(node):
    return (_tree_key(node.id_data), str(node.runtime_id))


def _spring_step(node):
    return 1.0 / max(int(getattr(node, "spring_fps", 60)), 1)


def _capture_basis_rotations(armature, bone_names):
    rotations = {}
    for bone_name in bone_names:
        bone = armature.pose.bones.get(bone_name)
        if bone is None:
            continue
        rotation = bone.matrix_basis.to_quaternion()
        rotation.normalize()
        rotations[bone_name] = rotation
    return rotations


def _set_spring_reset_pose(armature, bone_names):
    bones = getattr(getattr(armature, "pose", None), "bones", None)
    if bones is None:
        return
    for bone_name in bone_names:
        bone = bones.get(bone_name)
        if bone is None:
            continue
        try:
            bone.matrix_basis = Matrix.Identity(4)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            continue


def _spring_reset_bone_names(armature, spring_data):
    names = []
    seen = set()

    def add_bone_and_children(bone, include_children):
        stack = [bone]
        while stack:
            current = stack.pop()
            if current.name not in seen:
                seen.add(current.name)
                names.append(current.name)
            if include_children:
                stack.extend(reversed(tuple(current.children)))

    pose_bones = armature.pose.bones
    include_children = str(spring_data.version).startswith("0")
    for spring in spring_data.springs:
        for joint in spring.get("joints", ()):
            bone = pose_bones.get(str(joint.get("bone", "") or ""))
            if bone is not None:
                add_bone_and_children(bone, include_children)
    return tuple(names)


def _official_spring_bone(armature, spring_data):
    if not str(spring_data.version).startswith("1"):
        return None
    extension = getattr(getattr(armature, "data", None), "vrm_addon_extension", None)
    return getattr(extension, "spring_bone1", None)


def _restore_spring_output_state(key, *, reset_pose=True):
    output_state = _spring_output_state.pop(key, None)
    if not output_state:
        return
    armature = bpy.data.objects.get(output_state["armature"])
    if armature is None or armature.type != "ARMATURE":
        return
    spring_bone = _official_spring_bone(armature, output_state["spring_data"])
    if spring_bone is not None:
        try:
            spring_bone.enable_animation = False
        except (AttributeError, ReferenceError, TypeError):
            pass
    if reset_pose:
        _set_spring_reset_pose(armature, output_state["bone_names"])
    try:
        bpy.context.view_layer.update()
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    if spring_bone is not None:
        try:
            spring_bone.enable_animation = bool(
                output_state.get("previous_enable_animation", False)
            )
        except (AttributeError, ReferenceError, TypeError):
            pass


def _clear_spring_solver_state(state):
    for name in tuple(state):
        if name.startswith("spring_"):
            state.pop(name, None)


def _initialize_spring(node, spring_data, context):
    if not isinstance(spring_data, SpringData):
        raise RuntimeError("Connect a loaded Spring Data input")
    if not spring_data.armature_name:
        raise RuntimeError("Spring Data has no target armature")
    if not spring_data.springs:
        raise RuntimeError("Spring Data contains no springs")
    armature = bpy.data.objects.get(spring_data.armature_name)
    if armature is None or armature.type != "ARMATURE":
        raise RuntimeError("The Spring Data armature is not available")

    key = _spring_output_key(node)
    state = _node_motion_state(node)
    _restore_spring_output_state(key)
    _clear_spring_solver_state(state)
    spring_bone = _official_spring_bone(armature, spring_data)
    previous_enable_animation = False
    if spring_bone is not None:
        previous_enable_animation = bool(
            getattr(spring_bone, "enable_animation", False)
        )
        spring_bone.enable_animation = False
    reset_names = _spring_reset_bone_names(armature, spring_data)
    _set_spring_reset_pose(armature, reset_names)
    try:
        context.view_layer.update()
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    try:
        solver = NativeSpringSolver(armature, spring_data)
    except Exception as error:
        if spring_bone is not None:
            spring_bone.enable_animation = previous_enable_animation
        raise RuntimeError(str(error)) from error
    reset_names = tuple(dict.fromkeys(reset_names + tuple(solver.bone_names)))
    state["spring_solver"] = solver
    state["spring_signature"] = (spring_data.armature_name, id(spring_data))
    state["spring_data"] = spring_data
    state["spring_clock_accumulator"] = 0.0
    state.pop("spring_error", None)
    _spring_output_state[key] = {
        "armature": armature.name,
        "bone_names": reset_names,
        "spring_data": spring_data,
        "previous_enable_animation": previous_enable_animation,
    }
    _last_spring_time[key] = time.monotonic()
    node.spring_active = True
    node.spring_last_error = ""
    node.spring_status = (
        f"Running · {len(solver.springs)} springs · {int(node.spring_fps)} FPS"
    )


def _spring_input_value(node, scene):
    context = EvaluationContext(node.id_data, scene, _spring_step(node))
    return context.input(node, "Spring Data", None)


def _spring_fixed_step_count(state, elapsed, fixed_step=FIXED_SPRING_STEP):
    fixed_step = max(float(fixed_step), 1.0e-6)
    accumulator = float(state.get("spring_clock_accumulator", 0.0)) + max(
        float(elapsed), 0.0
    )
    if accumulator + 1.0e-12 < fixed_step:
        state["spring_clock_accumulator"] = accumulator
        return 0
    # Spring rotations feed back through Blender's dependency graph. Running
    # several overdue steps against one evaluated pose makes the chain drift and
    # look slow, so retain only the fractional clock remainder.
    state["spring_clock_accumulator"] = accumulator % fixed_step
    return 1


def start_spring(node, context):
    from .availability import require_available
    require_available(node)
    if node.bl_idname != "YUGUS_NODE_spring_simulation":
        raise RuntimeError("Selected node is not a Spring Simulation")
    if node.spring_active:
        raise RuntimeError("Spring Bone physics is already running")
    spring_data = _spring_input_value(node, context.scene)
    _initialize_spring(node, spring_data, context)


def stop_spring(node, *, reset_pose=True):
    key = _spring_output_key(node)
    _restore_spring_output_state(key, reset_pose=reset_pose)
    state = _node_state.get(str(node.runtime_id))
    if state is not None:
        _clear_spring_solver_state(state)
    _last_spring_time.pop(key, None)
    _active_spring_ids.discard(key)
    try:
        node.spring_active = False
        node.spring_status = "Stopped"
    except ReferenceError:
        pass


def reload_spring(node, context):
    from .availability import require_available
    require_available(node)
    if not node.spring_active:
        raise RuntimeError("Start Spring Physics before reloading")
    stop_spring(node, reset_pose=True)
    spring_data = _spring_input_value(node, context.scene)
    _initialize_spring(node, spring_data, context)


def stop_all_springs(*, reset_pose=True):
    node_by_key = {}
    node_groups = getattr(getattr(bpy, "data", None), "node_groups", ()) or ()
    for tree in tuple(node_groups):
        if getattr(tree, "bl_idname", "") != TREE_ID:
            continue
        for node in tuple(tree.nodes):
            if node.bl_idname == "YUGUS_NODE_spring_simulation":
                node_by_key[_spring_output_key(node)] = node
    for key in tuple(_spring_output_state):
        node = node_by_key.get(key)
        if node is not None:
            stop_spring(node, reset_pose=reset_pose)
        else:
            _restore_spring_output_state(key, reset_pose=reset_pose)


def _debug_vector(value):
    if value is None:
        return "None"
    try:
        return "(" + ", ".join(f"{float(component):.9g}" for component in value) + ")"
    except (TypeError, ValueError):
        return repr(value)


def _debug_matrix_rows(matrix):
    try:
        return tuple(
            "[" + ", ".join(f"{float(value):.9g}" for value in row) + "]"
            for row in matrix
        )
    except (TypeError, ValueError):
        return (repr(matrix),)


def _debug_is_ancestor(ancestor, descendant):
    current = getattr(descendant, "parent", None)
    while current is not None:
        if current == ancestor:
            return True
        current = current.parent
    return False


def spring_debug_report(node, scene):
    """Return a copyable snapshot of Spring configuration and live solver state."""
    if node is not None and any(
        socket.bl_idname == "YUGUS_NS_bone_rotation" for socket in node.outputs
    ):
        from . import flow
        return flow.debug_report(node)

    if node is None or node.bl_idname != "YUGUS_NODE_spring_simulation":
        raise RuntimeError("Selected node is not a Spring Simulation")

    warnings = []
    package = sys.modules.get(__package__)
    version = tuple(getattr(package, "bl_info", {}).get("version", ()))
    version_text = ".".join(str(value) for value in version) or "unknown"
    spring_step = _spring_step(node)
    lines = [
        "===== YUGU SPRING DEBUG BEGIN =====",
        "Report Format: 2",
        f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Yugo Node: {version_text}",
        f"Blender: {bpy.app.version_string}",
        f"Platform: {sys.platform}",
        "",
        "[NODE]",
        f"Tree: {getattr(getattr(node, 'id_data', None), 'name', '')!r}",
        f"Node: {node.name!r}",
        f"Runtime ID: {str(getattr(node, 'runtime_id', ''))!r}",
        f"Active: {bool(getattr(node, 'spring_active', False))}",
        f"Muted: {bool(getattr(node, 'mute', False))}",
        f"FPS: {int(getattr(node, 'spring_fps', 60))}",
        f"Status: {str(getattr(node, 'spring_status', ''))!r}",
        f"Last Error: {str(getattr(node, 'spring_last_error', ''))!r}",
    ]
    input_socket = node.inputs.get("Spring Data")
    input_link = None
    if input_socket is not None and input_socket.is_linked:
        links = tuple(input_socket.links)
        input_link = links[0] if links else None
    lines.extend(
        (
            f"Input Linked: {input_link is not None}",
            (
                "Input Source: "
                + (
                    f"{input_link.from_node.name!r} "
                    f"({input_link.from_node.bl_idname}) / {input_link.from_socket.name!r}"
                    if input_link is not None
                    else "None"
                )
            ),
            "",
            "[CLOCK]",
            f"Fixed Step: {spring_step:.12g}",
            f"Timer Interval: {TIMER_INTERVAL:.12g}",
            f"Maximum Substeps: {MAX_SPRING_SUBSTEPS}",
            f"Scene Frame: {float(scene.frame_current_final):.9g}",
            f"Scene FPS: {float(scene.render.fps) / max(float(scene.render.fps_base), 1.0e-9):.9g}",
            f"Timeline Playing: {bool(getattr(bpy.context.screen, 'is_animation_playing', False))}",
        )
    )

    key = _spring_output_key(node)
    state = _node_state.get(str(node.runtime_id), {})
    solver = state.get("spring_solver")
    runtime_data = state.get("spring_data")
    try:
        input_data = _spring_input_value(node, scene)
        input_error = ""
    except Exception as error:
        input_data = None
        input_error = f"{type(error).__name__}: {error}"
    spring_data = input_data if isinstance(input_data, SpringData) else runtime_data
    last_time = _last_spring_time.get(key)
    lines.extend(
        (
            f"Clock Accumulator: {float(state.get('spring_clock_accumulator', 0.0)):.12g}",
            f"Last Tick Age: {(time.monotonic() - last_time):.9g}" if last_time else "Last Tick Age: None",
            f"Registered Active: {key in _active_spring_ids}",
            f"Output State Present: {key in _spring_output_state}",
            "",
            "[SPRING DATA]",
            f"Input Evaluation Error: {input_error or 'None'}",
            f"Input Type: {type(input_data).__name__}",
            f"Runtime Data Type: {type(runtime_data).__name__}",
        )
    )
    if not isinstance(spring_data, SpringData):
        warnings.append("No usable SpringData was available from the input or solver state.")
        lines.extend(
            (
                "SpringData: unavailable",
                "",
                "[WARNINGS]",
                *tuple(f"- {warning}" for warning in warnings),
                "===== YUGU SPRING DEBUG END =====",
            )
        )
        return "\n".join(lines)

    lines.extend(
        (
            f"Version: {spring_data.version!r}",
            f"Armature: {spring_data.armature_name!r}",
            f"Configured Springs: {len(spring_data.springs)}",
            f"Configured Colliders: {len(spring_data.colliders)}",
            f"Configured Collider Groups: {len(spring_data.collider_groups)}",
            f"Input Is Runtime Data: {input_data is runtime_data}",
            "",
            "[RAW SPRINGS]",
        )
    )
    for index, spring in enumerate(spring_data.springs):
        lines.append(f"Spring {index}:")
        lines.extend(f"  {line}" for line in pformat(spring, width=120, sort_dicts=True).splitlines())
    lines.extend(("", "[RAW COLLIDER GROUPS]"))
    for index, group in enumerate(spring_data.collider_groups):
        lines.append(f"Collider Group {index}:")
        lines.extend(f"  {line}" for line in pformat(group, width=120, sort_dicts=True).splitlines())
    lines.extend(("", "[RAW COLLIDERS]"))
    for index, collider in enumerate(spring_data.colliders):
        lines.append(f"Collider {index}:")
        lines.extend(f"  {line}" for line in pformat(collider, width=120, sort_dicts=True).splitlines())

    armature = bpy.data.objects.get(spring_data.armature_name)
    lines.extend(("", "[ARMATURE]"))
    if armature is None or armature.type != "ARMATURE":
        warnings.append("SpringData armature is missing or is not an Armature object.")
        lines.append("Armature object: unavailable")
    else:
        world_location, world_rotation, world_scale = armature.matrix_world.decompose()
        determinant = armature.matrix_world.to_3x3().determinant()
        lines.extend(
            (
                f"Name: {armature.name!r}",
                f"Pose Position: {str(getattr(armature.data, 'pose_position', ''))!r}",
                f"Object Location: {_debug_vector(armature.location)}",
                f"Object Rotation Quaternion: {_debug_vector(armature.rotation_quaternion)}",
                f"Object Scale: {_debug_vector(armature.scale)}",
                f"World Location: {_debug_vector(world_location)}",
                f"World Rotation Quaternion: {_debug_vector(world_rotation)}",
                f"World Scale: {_debug_vector(world_scale)}",
                f"World 3x3 Determinant: {determinant:.9g}",
                "World Matrix:",
                *tuple(f"  {row}" for row in _debug_matrix_rows(armature.matrix_world)),
            )
        )
        scale_values = tuple(abs(float(value)) for value in world_scale)
        if min(scale_values, default=0.0) <= 1.0e-8:
            warnings.append("Armature world scale contains a zero axis.")
        elif max(scale_values) - min(scale_values) > 1.0e-5:
            warnings.append("Armature world scale is non-uniform.")
        if determinant < 0.0:
            warnings.append("Armature world transform has a negative determinant.")
        if str(getattr(armature.data, "pose_position", "POSE")) != "POSE":
            warnings.append("Armature is not displaying Pose Position.")

        official = _official_spring_bone(armature, spring_data)
        lines.append(
            "Official VRM Spring Animation: "
            + (
                str(bool(getattr(official, "enable_animation", False)))
                if official is not None
                else "Not available"
            )
        )

        configured_names = []
        for spring_index, spring in enumerate(spring_data.springs):
            joints = tuple(spring.get("joints", ()))
            center_name = str(spring.get("center", "") or "")
            if center_name:
                configured_names.append(center_name)
            if spring_data.version.startswith("1") and len(joints) < 2:
                warnings.append(f"Spring {spring_index} has fewer than two VRM 1.x joints.")
            previous_bone = None
            seen = set()
            for joint_index, joint in enumerate(joints):
                bone_name = str(joint.get("bone", "") or "")
                configured_names.append(bone_name)
                bone = armature.pose.bones.get(bone_name)
                if not bone_name or bone is None:
                    warnings.append(
                        f"Spring {spring_index} joint {joint_index} bone {bone_name!r} is missing."
                    )
                    previous_bone = None
                    continue
                if bone_name in seen:
                    warnings.append(f"Spring {spring_index} repeats bone {bone_name!r}.")
                seen.add(bone_name)
                if (
                    spring_data.version.startswith("1")
                    and previous_bone is not None
                    and not _debug_is_ancestor(previous_bone, bone)
                ):
                    warnings.append(
                        f"Spring {spring_index}: {previous_bone.name!r} is not an ancestor of {bone.name!r}."
                    )
                previous_bone = bone

        solver_names = tuple(getattr(solver, "bone_names", ())) if solver is not None else ()
        bone_names = tuple(dict.fromkeys(name for name in configured_names + list(solver_names) if name))
        lines.extend(("", "[LIVE BONES]"))
        for bone_name in bone_names:
            bone = armature.pose.bones.get(bone_name)
            if bone is None:
                lines.append(f"Bone {bone_name!r}: MISSING")
                continue
            basis_location, basis_rotation, basis_scale = bone.matrix_basis.decompose()
            pose_location, pose_rotation, pose_scale = bone.matrix.decompose()
            world_head = armature.matrix_world @ bone.head
            world_tail = armature.matrix_world @ bone.tail
            lines.extend(
                (
                    f"Bone {bone.name!r}:",
                    f"  Parent: {getattr(bone.parent, 'name', None)!r}",
                    f"  Connected: {bool(bone.bone.use_connect)}",
                    f"  Inherit Rotation: {bool(bone.bone.use_inherit_rotation)}",
                    f"  Rest Head Local: {_debug_vector(bone.bone.head_local)}",
                    f"  Rest Tail Local: {_debug_vector(bone.bone.tail_local)}",
                    f"  Rest Length: {float(bone.bone.length):.9g}",
                    f"  Pose Head Object: {_debug_vector(bone.head)}",
                    f"  Pose Tail Object: {_debug_vector(bone.tail)}",
                    f"  Pose Head World: {_debug_vector(world_head)}",
                    f"  Pose Tail World: {_debug_vector(world_tail)}",
                    f"  Basis Location: {_debug_vector(basis_location)}",
                    f"  Basis Rotation: {_debug_vector(basis_rotation)}",
                    f"  Basis Scale: {_debug_vector(basis_scale)}",
                    f"  Pose Location: {_debug_vector(pose_location)}",
                    f"  Pose Rotation: {_debug_vector(pose_rotation)}",
                    f"  Pose Scale: {_debug_vector(pose_scale)}",
                    f"  Constraints: {[(item.name, item.type, bool(item.mute), float(item.influence)) for item in bone.constraints]!r}",
                )
            )

    lines.extend(("", "[SOLVER]"))
    if solver is None:
        lines.append("Initialized: False")
        if bool(getattr(node, "spring_active", False)):
            warnings.append("Spring node is active but no solver is initialized.")
    else:
        lines.extend(
            (
                "Initialized: True",
                f"Type: {type(solver).__name__}",
                f"Armature: {str(getattr(solver, 'armature_name', ''))!r}",
                f"Spring States: {len(getattr(solver, 'springs', ()))}",
                f"Collider Sources: {len(getattr(solver, 'colliders', ()))}",
                f"Driven Bones: {sorted(getattr(solver, 'bone_names', ()))!r}",
            )
        )
        if armature is not None and armature.type == "ARMATURE":
            for index, source in enumerate(getattr(solver, "colliders", ())):
                try:
                    world_collider = source.world(armature)
                except Exception as error:
                    world_collider = f"ERROR {type(error).__name__}: {error}"
                lines.append(f"Collider Source {index}: {source!r}")
                lines.append(f"  World: {world_collider!r}")
        for spring_index, spring in enumerate(getattr(solver, "springs", ())):
            lines.extend(
                (
                    f"Solver Spring {spring_index}:",
                    f"  Center: {str(getattr(spring, 'center_name', ''))!r}",
                    f"  Previous Center: {_debug_vector(getattr(spring, 'previous_center', None))}",
                    f"  Collider Indices: {tuple(getattr(spring, 'collider_indices', ()))!r}",
                )
            )
            joints = tuple(getattr(spring, "joints", ()))
            if joints:
                lines.extend(
                    (
                        f"  Stiffness: {float(getattr(spring, 'stiffness', 0.0)):.9g}",
                        f"  Drag Force: {float(getattr(spring, 'drag_force', 0.0)):.9g}",
                        f"  Gravity Direction: {_debug_vector(getattr(spring, 'gravity_dir', None))}",
                        f"  Gravity Power: {float(getattr(spring, 'gravity_power', 0.0)):.9g}",
                        f"  Hit Radius: {float(getattr(spring, 'hit_radius', 0.0)):.9g}",
                        f"  Joints: {len(joints)}",
                    )
                )
                for joint_index, joint in enumerate(joints):
                    previous_tail = getattr(joint, "previous_tail", None)
                    current_tail = getattr(joint, "current_tail", None)
                    velocity = (
                        current_tail - previous_tail
                        if current_tail is not None and previous_tail is not None
                        else None
                    )
                    lines.extend(
                        (
                            f"    Joint {joint_index}: {str(getattr(joint, 'bone_name', ''))!r}",
                            f"      Parent Index: {getattr(joint, 'parent_index', None)!r}",
                            f"      External Parent: {str(getattr(joint, 'external_parent_name', ''))!r}",
                            f"      Local Axis: {_debug_vector(getattr(joint, 'local_axis', None))}",
                            f"      Length: {float(getattr(joint, 'length', 0.0)):.9g}",
                            f"      Previous Tail World: {_debug_vector(previous_tail)}",
                            f"      Current Tail World: {_debug_vector(current_tail)}",
                            f"      Tail Delta: {_debug_vector(velocity)}",
                            f"      Tail Delta Length: {float(velocity.length) if velocity is not None else 0.0:.9g}",
                        )
                    )
                continue
            for chain_index, chain in enumerate(getattr(spring, "chains", ())):
                pairs = tuple(getattr(chain, "pairs", ()))
                lines.append(
                    f"  Chain {chain_index}: center_space={bool(getattr(chain, 'use_center_space', False))} pairs={len(pairs)}"
                )
                for pair_index, pair in enumerate(pairs):
                    previous_tail = getattr(pair, "previous_tail", None)
                    current_tail = getattr(pair, "current_tail", None)
                    velocity = (
                        current_tail - previous_tail
                        if current_tail is not None and previous_tail is not None
                        else None
                    )
                    lines.extend(
                        (
                            f"    Pair {pair_index}: {pair.head_name!r} -> {pair.tail_name!r}",
                            f"      Stiffness: {float(pair.stiffness):.9g}",
                            f"      Drag Force: {float(pair.drag_force):.9g}",
                            f"      Gravity Direction: {_debug_vector(pair.gravity_dir)}",
                            f"      Gravity Power: {float(pair.gravity_power):.9g}",
                            f"      Hit Radius: {float(pair.hit_radius):.9g}",
                            f"      Previous Tail World: {_debug_vector(previous_tail)}",
                            f"      Current Tail World: {_debug_vector(current_tail)}",
                            f"      Tail Delta: {_debug_vector(velocity)}",
                            f"      Tail Delta Length: {float(velocity.length) if velocity is not None else 0.0:.9g}",
                            f"      Head Rest Translation: {_debug_vector(pair.head_rest_matrix.to_translation())}",
                            f"      Tail Rest Translation: {_debug_vector(pair.tail_rest_matrix.to_translation())}",
                        )
                    )

    lines.extend(("", "[WARNINGS]"))
    if warnings:
        lines.extend(f"- {warning}" for warning in dict.fromkeys(warnings))
    else:
        lines.append("- None detected by the snapshot checks.")
    lines.append("===== YUGU SPRING DEBUG END =====")
    return "\n".join(lines)


def _assign_basis_rotation(bone, rotation):
    rotation = rotation.copy()
    rotation.normalize()
    matrix = bone.matrix_basis.copy()
    location, current, scale = matrix.decompose()
    current.normalize()
    if 1.0 - abs(current.dot(rotation)) <= ROTATION_EPSILON:
        return False
    bone.matrix_basis = (
        Matrix.Translation(location)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )
    return True


def _evaluate_spring(node, spring_data, delta_time=FIXED_SPRING_STEP):
    if not node.spring_active or node.mute:
        return 0
    if not isinstance(spring_data, SpringData):
        message = "Spring Data was disconnected"
        stop_spring(node, reset_pose=True)
        node.spring_last_error = message
        return 0
    state = _node_state.get(str(node.runtime_id), {})
    solver = state.get("spring_solver")
    if solver is None:
        node.spring_last_error = "Spring solver is not initialized; restart physics"
        return 0
    signature = (getattr(spring_data, "armature_name", ""), id(spring_data))
    if signature != state.get("spring_signature"):
        node.spring_last_error = "Spring Data changed · press Reload"
    armature = bpy.data.objects.get(state["spring_data"].armature_name)
    if armature is None or armature.type != "ARMATURE":
        node.spring_last_error = "The Spring Data armature is no longer available"
        stop_spring(node, reset_pose=False)
        return 0
    try:
        solved_rotations = solver.step(armature, _spring_step(node))
        if signature == state.get("spring_signature"):
            node.spring_last_error = ""
    except Exception as error:
        message = str(error)
        stop_spring(node, reset_pose=True)
        node.spring_last_error = message
        return 0
    changed = 0
    for bone_name, solved in solved_rotations.items():
        bone = armature.pose.bones.get(bone_name)
        if bone is not None:
            changed += int(_assign_basis_rotation(bone, solved))
    node.spring_status = (
        f"Running · {len(solver.springs)} springs · {int(node.spring_fps)} FPS"
    )
    return changed


def _write_native_pose(node, pose):
    if not isinstance(pose, NativePose) or not pose.armature_name:
        return 0, None, set()
    armature = bpy.data.objects.get(pose.armature_name)
    if armature is None or armature.type != "ARMATURE":
        return 0, None, set()
    changed = 0
    current_bones = set()
    for bone_name, transform in pose.bones.items():
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        current_bones.add(bone_name)
        if transform.rotation is not None:
            changed += int(_assign_rotation(pose_bone, transform.rotation))
        if node.write_locations and transform.location is not None:
            changed += int(_assign_location(pose_bone, transform.location))
    if node.write_root and pose.root is not None:
        if pose.root.rotation is not None:
            changed += int(_assign_rotation(armature, pose.root.rotation))
        if pose.root.location is not None:
            changed += int(_assign_location(armature, pose.root.location))
    return changed, armature, current_bones


def _apply_pose(tree, node, pose):
    key = (_tree_key(tree), str(node.runtime_id))
    state = _pose_state.setdefault(key, {"bones": set(), "armature": "", "root": False})
    if not isinstance(pose, NativePose) or not pose.armature_name:
        if state["armature"]:
            _restore_output_state(key)
        return 0
    changed, armature, current_bones = _write_native_pose(node, pose)
    if armature is None:
        return 0
    for bone_name in state["bones"] - current_bones:
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        changed += int(_assign_rotation(pose_bone, Quaternion()))
        if node.write_locations:
            changed += int(_assign_location(pose_bone, Vector((0.0, 0.0, 0.0))))
    state["bones"] = current_bones
    state["armature"] = armature.name
    if node.write_root and pose.root is not None:
        state["root"] = True
    elif state["root"]:
        changed += int(_assign_rotation(armature, Quaternion()))
        changed += int(_assign_location(armature, Vector((0.0, 0.0, 0.0))))
        state["root"] = False
    return changed


def _resolve_definition(expression_map, channel):
    normalized = normalize_name(channel)
    for alias in ifacial_expression_aliases(channel):
        definition = expression_map.definitions.get(normalize_name(alias))
        if definition is not None:
            return definition
    if normalized.endswith("l"):
        return expression_map.definitions.get(normalized[:-1] + "left")
    if normalized.endswith("r"):
        return expression_map.definitions.get(normalized[:-1] + "right")
    return None


def _shape_key(object_name, key_name):
    obj = bpy.data.objects.get(object_name)
    keys = getattr(getattr(getattr(obj, "data", None), "shape_keys", None), "key_blocks", None)
    return keys.get(key_name) if keys is not None else None


def _bind_expressions(frame, expression_map):
    if not isinstance(expression_map, ExpressionMapData):
        return NativeExpressions()
    frame = frame if isinstance(frame, ExpressionFrame) else ExpressionFrame()
    return NativeExpressions(
        expression_map,
        _resolved_expression_values(frame, expression_map),
    )


def _apply_expressions(tree, node, native_expressions):
    key = (_tree_key(tree), str(node.runtime_id))
    if (
        not isinstance(native_expressions, NativeExpressions)
        or not isinstance(native_expressions.expression_map, ExpressionMapData)
    ):
        _restore_expression_state(key)
        return 0
    expression_map = native_expressions.expression_map
    state = _expression_state.setdefault(key, {"base": {}, "targets": set()})
    target_values = {}
    material_contributions = {}
    all_targets = set()
    all_material_targets = set()
    for definition in expression_map.definitions.values():
        for target in definition.morph_targets:
            all_targets.add((target.object_name, target.shape_key_name))
        for target in definition.material_targets:
            all_material_targets.add(
                (target.material_name, target.bind_type, target.property_name)
            )
    for definition, value in native_expressions.values.items():
        for target in definition.morph_targets:
            target_key = (target.object_name, target.shape_key_name)
            target_values[target_key] = target_values.get(target_key, 0.0) + value * target.weight
        for target in definition.material_targets:
            target_key = (target.material_name, target.bind_type, target.property_name)
            material_contributions.setdefault(target_key, []).append(
                (value, tuple(target.target_value))
            )
    changed = 0
    for target_key in all_targets:
        shape_key = _shape_key(*target_key)
        if shape_key is None:
            continue
        state["base"].setdefault(target_key, float(shape_key.value))
        value = min(max(target_values.get(target_key, 0.0), 0.0), 1.0)
        if abs(float(shape_key.value) - value) > VALUE_EPSILON:
            shape_key.value = value
            changed += 1
    state["targets"] = all_targets
    material_changed = _apply_material_targets(
        state, all_material_targets, material_contributions
    )
    return changed + material_changed


def _expression_category(definition):
    name = normalize_name(definition.name)
    if name in {"aa", "ih", "ou", "ee", "oh"}:
        return "mouth"
    if name in {"blink", "blinkleft", "blinkright"}:
        return "blink"
    if name in {"lookup", "lookdown", "lookleft", "lookright"}:
        return "look"
    return ""


def _resolved_expression_values(frame, expression_map):
    resolved = {}
    for channel, raw_value in frame.values.items():
        definition = _resolve_definition(expression_map, channel)
        if definition is None:
            continue
        value = min(max(float(raw_value), 0.0), 1.0)
        if definition.is_binary:
            value = 1.0 if value > 0.5 else 0.0
        resolved[definition] = max(resolved.get(definition, 0.0), value)
    blocks = {"mouth": 0.0, "blink": 0.0, "look": 0.0}
    for definition, value in resolved.items():
        for category, override in (
            ("mouth", definition.override_mouth),
            ("blink", definition.override_blink),
            ("look", definition.override_look_at),
        ):
            override = str(override).casefold()
            if override == "block":
                blocks[category] = 1.0
            elif override == "blend":
                blocks[category] = min(1.0, blocks[category] + value)
    return {
        definition: value * (1.0 - blocks.get(_expression_category(definition), 0.0))
        for definition, value in resolved.items()
    }


def _material_socket(material, bind_type, property_name=""):
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    normalized_type = normalize_name(bind_type)
    normalized_property = normalize_name(property_name)
    candidates = {
        "color": ("basecolor", "litcolor", "color"),
        "emissioncolor": ("emissioncolor", "emission"),
        "shadecolor": ("shadecolor",),
        "matcapcolor": ("matcapcolor", "matcapfactor"),
        "rimcolor": ("rimcolor",),
        "outlinecolor": ("outlinecolor",),
    }.get(normalized_type, ())
    if normalized_type == "vrm0property":
        if "emission" in normalized_property:
            candidates = ("emissioncolor", "emission")
        elif "shade" in normalized_property:
            candidates = ("shadecolor",)
        elif "rim" in normalized_property:
            candidates = ("rimcolor",)
        elif "outline" in normalized_property:
            candidates = ("outlinecolor",)
        else:
            candidates = ("basecolor", "litcolor", "color")
    for node in tree.nodes:
        for socket in node.inputs:
            if not hasattr(socket, "default_value"):
                continue
            socket_name = normalize_name(socket.name)
            if socket_name in candidates:
                try:
                    value = tuple(float(item) for item in socket.default_value)
                except TypeError:
                    continue
                if len(value) >= 3:
                    return node.name, socket.name, socket
    return None


def _apply_material_targets(state, all_targets, contributions):
    material_state = state.setdefault("materials", {})
    binding_state = state.setdefault("material_bindings", {})
    changed = 0
    for key in all_targets:
        material_name, bind_type, property_name = key
        if normalize_name(bind_type) == "texturetransform":
            continue
        material = bpy.data.materials.get(material_name)
        if material is None:
            continue
        if key not in binding_state:
            resolved = _material_socket(material, bind_type, property_name)
            if resolved is not None:
                binding_state[key] = (material_name, resolved[0], resolved[1])
            elif normalize_name(bind_type) == "color":
                binding_state[key] = (material_name, "", "diffuse_color")
            else:
                binding_state[key] = None
        locator = binding_state[key]
        if locator is None:
            continue
        _material_name, node_name, socket_name = locator
        if not node_name and socket_name == "diffuse_color":
            socket = None
            base = tuple(float(item) for item in material.diffuse_color)
        else:
            material_node = material.node_tree.nodes.get(node_name) if material.node_tree else None
            socket = material_node.inputs.get(socket_name) if material_node else None
            if socket is None or not hasattr(socket, "default_value"):
                continue
            base = tuple(float(item) for item in socket.default_value)
        record = material_state.setdefault(locator, {"base": base})
        base = record["base"]
        result = list(base)
        for value, target in contributions.get(key, ()):
            for index in range(min(len(result), len(target))):
                result[index] += (float(target[index]) - base[index]) * value
        result = tuple(min(max(value, 0.0), 1.0) for value in result)
        if socket is None:
            current = tuple(float(item) for item in material.diffuse_color)
            if any(abs(a - b) > VALUE_EPSILON for a, b in zip(current, result)):
                material.diffuse_color = result
                changed += 1
        else:
            current = tuple(float(item) for item in socket.default_value)
            if any(abs(a - b) > VALUE_EPSILON for a, b in zip(current, result)):
                socket.default_value = result
                changed += 1
    return changed


def _restore_output_state(key):
    pose = _pose_state.pop(key, None)
    if pose:
        armature = bpy.data.objects.get(pose["armature"])
        if armature is not None and armature.type == "ARMATURE":
            for bone_name in pose["bones"]:
                bone = armature.pose.bones.get(bone_name)
                if bone is not None:
                    _assign_rotation(bone, Quaternion())
                    _assign_location(bone, Vector((0.0, 0.0, 0.0)))
            if pose["root"]:
                _assign_rotation(armature, Quaternion())
                _assign_location(armature, Vector((0.0, 0.0, 0.0)))
    _restore_expression_state(key)


def _restore_expression_state(key):
    expressions = _expression_state.pop(key, None)
    if expressions:
        for target_key, value in expressions["base"].items():
            shape_key = _shape_key(*target_key)
            if shape_key is not None:
                shape_key.value = value
        for locator, record in expressions.get("materials", {}).items():
            material_name, node_name, socket_name = locator
            material = bpy.data.materials.get(material_name)
            if material is None:
                continue
            if not node_name and socket_name == "diffuse_color":
                material.diffuse_color = record["base"]
                continue
            material_node = (
                material.node_tree.nodes.get(node_name)
                if material.node_tree is not None
                else None
            )
            socket = material_node.inputs.get(socket_name) if material_node else None
            if socket is not None and hasattr(socket, "default_value"):
                socket.default_value = record["base"]


def restore_tree(tree):
    tree_pointer = _tree_key(tree)
    for key in tuple(_spring_output_state):
        if key[0] == tree_pointer:
            _restore_spring_output_state(key)
    for key in tuple(_pose_state):
        if key[0] == tree_pointer:
            _restore_output_state(key)
    for key in tuple(_expression_state):
        if key[0] == tree_pointer:
            _restore_output_state(key)


def _output_key(node):
    return (_tree_key(node.id_data), str(node.runtime_id))


def _validate_character_output(node, scene):
    socket = node.inputs.get("Bone Map")
    if socket is None or not socket.is_linked:
        return False, "Connect Bone Map before activating this output"
    context = EvaluationContext(node.id_data, scene, 1.0 / 60.0)
    bone_map = context.input(node, "Bone Map", None)
    if not isinstance(bone_map, BoneMapData):
        return False, "Bone Map has no loaded value"
    armature = bpy.data.objects.get(bone_map.armature_name)
    if armature is None or armature.type != "ARMATURE":
        return False, "Bone Map has no available Armature"
    hips_name = str(bone_map.by_humanoid.get("hips", "") or "")
    if not hips_name:
        return False, "Bone Map has no hips remap target"
    if armature.pose.bones.get(hips_name) is None:
        return False, f'Hips target "{hips_name}" is not on {armature.name}'
    return True, f"hips → {hips_name}"


def output_active_changed(node, context):
    # Old files remain readable, but the legacy character scheduler is retired.
    node.last_summary = "Retired · use Hz Trigger and Apply nodes"


def _evaluate_character_output(tree, node, scene, delta_time):
    context = EvaluationContext(tree, scene, delta_time)
    motion = context.input(node, "Humanoid Motion", HumanoidMotion())
    bone_map = context.input(node, "Bone Map", None)
    expressions = context.input(node, "Expressions", ExpressionFrame())
    expression_map = context.input(node, "Expression Map", None)
    from . import keyboard
    expressions = keyboard.mask_expressions(node, expressions, expression_map)
    pose = _retarget_motion(motion, bone_map)
    native_expressions = _bind_expressions(expressions, expression_map)
    changed = _apply_pose(tree, node, pose)
    changed += _apply_expressions(tree, node, native_expressions)
    node.last_summary = (
        f"Active · {len(pose.bones)} bones / "
        f"{len(native_expressions.values)} expressions"
    )
    return changed


def evaluate_tree(tree, scene, delta_time):
    outputs = [
        node
        for node in tree.nodes
        if node.bl_idname == "YUGUS_NODE_blender_output" and node.is_active_output
    ]
    total_changed = sum(
        _evaluate_character_output(tree, node, scene, delta_time)
        for node in outputs
        if node.active_validated
    )
    tree.last_error = ""
    tree.last_status = (
        f"{len(outputs)} active output · {total_changed} writes"
        if outputs
        else "Idle · activate a Character Output"
    )
    return total_changed


def _timer():
    from . import flow
    try:
        flow.tick()
    except Exception as error:
        print(f"Yugu trigger flow recovered from: {error}")
    return TIMER_INTERVAL


def _motion_tick(_context, _delta_time):
    _timer()


def handle_key_event(event):
    from . import keyboard
    return keyboard.process_event(event)


def register():
    global _timer_registered
    from . import scheduler
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    scheduler.add_motion("flow", _motion_tick, priority=100)
    _timer_registered = True


def unregister():
    global _timer_registered
    from . import scheduler
    scheduler.remove_motion("flow")
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    stop_all_springs(reset_pose=True)
    for tree in tuple(getattr(bpy.data, "node_groups", ())):
        if tree.bl_idname == TREE_ID:
            restore_tree(tree)
            tree.runtime_enabled = False
            tree.last_status = "Stopped"
            for node in tree.nodes:
                if node.bl_idname == "YUGUS_NODE_blender_output":
                    node.is_active_output = False
                    node.active_validated = False
                elif node.bl_idname == "YUGUS_NODE_spring_simulation":
                    node.spring_active = False
    _timer_registered = False
    _last_output_time.clear()
    _active_output_ids.clear()
    _last_spring_time.clear()
    _active_spring_ids.clear()
    _node_state.clear()
    _spring_output_state.clear()
    _key_state.clear()
    action_source.clear_cache()
