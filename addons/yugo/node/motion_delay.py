"""Timestamped Humanoid Motion delay, driven only by downstream data pulls."""
from bisect import bisect_right
from collections import deque
import math

import bpy
from mathutils import Quaternion, Vector

from .constants import SOCKET_MOTION
from .data import HumanoidMotion, HumanoidTransform
from .nodes import YUGUS_NodeBase, ensure_numeric_parameter

NODE = "YUGUS_NODE_motion_delay"
MAX_DELAY = 10.0
MAX_SAMPLES = 4096
_states = {}


class YUGUS_NODE_motion_delay(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Motion Delay"
    bl_icon = "TIME"
    bl_description = "Delay Humanoid Motion by a number of seconds"
    delay_seconds: bpy.props.FloatProperty(
        name="Delay Seconds", default=0.2, min=0.0, max=MAX_DELAY, unit="TIME",
    )

    def init(self, _context):
        self._init_base(220)
        self.inputs.new(SOCKET_MOTION, "Motion")
        ensure_numeric_parameter(self)
        self.outputs.new(SOCKET_MOTION, "Motion")

    def draw_buttons(self, _context, layout):
        button = layout.operator("yugu_node_studio.motion_delay_reset", text="Reset", icon="FILE_REFRESH")
        button.tree_name, button.node_name = self.id_data.name, self.name

    def copy(self, source):
        YUGUS_NodeBase.copy(self, source)
        clear(str(self.runtime_id))

    def free(self):
        clear(str(self.runtime_id))
        YUGUS_NodeBase.free(self)


class YUGUS_OT_motion_delay_reset(bpy.types.Operator):
    bl_idname = "yugu_node_studio.motion_delay_reset"
    bl_label = "Reset Motion Delay"
    bl_description = "Clear recorded motion and begin again with the next sample"
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()

    def execute(self, _context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        if node is None or node.bl_idname != NODE:
            return {"CANCELLED"}
        clear(str(node.runtime_id))
        return {"FINISHED"}


def clear(uid=None):
    if uid is None:
        _states.clear()
    else:
        _states.pop(str(uid), None)


def prune(live_ids):
    """Keep connected live nodes between triggers, including Action Flow pulls."""
    live_ids = {str(uid) for uid in live_ids}
    for uid in tuple(_states):
        if uid not in live_ids:
            clear(uid)


def _identity(value):
    return value.as_pointer() if hasattr(value, "as_pointer") else id(value)


def _signature(context, node):
    """Fingerprint Motion's upstream wiring without evaluating any source."""
    edges, assets, visited = [], [], set()

    def walk(current, selected=None):
        pointer = current.as_pointer()
        if pointer in visited:
            return
        visited.add(pointer)
        # Switching an Action or source Armature is a new stream even when
        # its node and wires stay in place. Animated numeric controls are not
        # part of this identity and can change without discarding history.
        assets.append((pointer, *(
            value.as_pointer() if isinstance(value, bpy.types.ID) else 0
            for value in (getattr(current, "action", None), getattr(current, "armature", None))
        )))
        for socket in selected if selected is not None else current.inputs:
            for link in socket.links:
                source = link.from_node
                edges.append((pointer, socket.identifier, source.as_pointer(),
                              link.from_socket.identifier, bool(link.is_muted), bool(link.is_valid),
                              bool(source.mute), str(getattr(source, "data_type", ""))))
                walk(source)

    motion = node.inputs.get("Motion")
    walk(node, (motion,) if motion is not None else ())
    return _identity(getattr(context, "scene", None)), tuple(sorted(edges)), tuple(sorted(assets))


def _validate_motion(value):
    if not isinstance(value, HumanoidMotion) or not isinstance(value.bones, dict):
        raise ValueError("Motion Delay requires Humanoid Motion")
    transforms = list(value.bones.values()) + ([value.root] if value.root is not None else [])
    for transform in transforms:
        if not isinstance(transform, HumanoidTransform):
            raise ValueError("Motion Delay requires Humanoid Transform channels")
        if transform.location is not None and (
                not isinstance(transform.location, Vector) or len(transform.location) != 3
                or not all(math.isfinite(component) for component in transform.location)):
            raise ValueError("Motion Delay requires finite three-component positions")
        if transform.rotation is not None:
            rotation = transform.rotation
            if (not isinstance(rotation, Quaternion) or not all(math.isfinite(component) for component in rotation)
                    or math.hypot(*rotation) <= 1e-12):
                raise ValueError("Motion Delay requires finite nonzero quaternions")
    return value


def _current(context, node):
    return _validate_motion(context.input(node, "Motion", HumanoidMotion())).copy()


def _normalized(rotation):
    length = math.hypot(*rotation)
    return Quaternion(tuple(component / length for component in rotation))


def interpolate_motion(left, right, factor):
    """Interpolate shared channels; sparse topology is held from the left."""
    if factor <= 0:
        return left.copy()
    if factor >= 1:
        return right.copy()
    result = left.copy()

    def blend(lower, upper):
        if lower.location is not None and upper.location is not None:
            lower.location = Vector(tuple(a * (1.0 - factor) + b * factor
                                          for a, b in zip(lower.location, upper.location)))
        if lower.rotation is not None and upper.rotation is not None:
            lower.rotation = _normalized(lower.rotation).slerp(_normalized(upper.rotation), factor).normalized()

    for name in result.bones.keys() & right.bones.keys():
        blend(result.bones[name], right.bones[name])
    if result.root is not None and right.root is not None:
        blend(result.root, right.root)
    return result


def _at(samples, target):
    if target <= samples[0][0]:
        return samples[0][1].copy()
    if target >= samples[-1][0]:
        return samples[-1][1].copy()
    index = bisect_right(samples, target, key=lambda sample: sample[0]) - 1
    left_time, left = samples[index]
    right_time, right = samples[index + 1]
    return interpolate_motion(left, right, (target - left_time) / (right_time - left_time))


def _trim(state, now):
    samples = state["samples"]
    cutoff = now - MAX_DELAY
    while len(samples) > 1 and samples[1][0] <= cutoff:
        samples.popleft()
    if len(samples) > MAX_SAMPLES:
        # Thin very dense histories while preserving the window's first and
        # latest samples, rather than shortening the available delay duration.
        middle = list(samples)[1:-1:2]
        state["samples"] = deque((samples[0], *middle, samples[-1]))


def evaluate(context, node):
    if node.bl_idname != NODE:
        return None
    from . import runtime
    uid = str(node.runtime_id)
    if node.mute:
        clear(uid)
        return runtime._output(node, "Motion", _current(context, node))
    state = _states.get(uid)
    timed = hasattr(context, "now")
    if timed:
        try:
            now = float(context.now)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Motion Delay requires a finite absolute time") from error
        if not math.isfinite(now):
            raise ValueError("Motion Delay requires a finite absolute time")
    signature = _signature(context, node)
    if state is not None and signature != state["signature"]:
        clear(uid)
        state = None
    if not timed:
        value = state["published"].copy() if state is not None else _current(context, node)
        return runtime._output(node, "Motion", value)
    # Separate event pulses have separate EvaluationContext caches. Deduplicate
    # here, before reading Motion or the linked numeric parameter.
    if state is not None and now <= state["last"]:
        return runtime._output(node, "Motion", state["published"].copy())
    if state is not None and now - state["last"] > MAX_DELAY:
        clear(uid)
        state = None
    delay = runtime._numeric_parameter(context, node, "Delay Seconds", "delay_seconds", 0.0, MAX_DELAY)
    value = _current(context, node)
    if state is None:
        state = {"signature": signature, "last": now, "samples": deque(), "published": value.copy()}
        _states[uid] = state
    state["samples"].append((now, value))
    state["last"] = now
    _trim(state, now)
    state["published"] = value.copy() if delay == 0 else _at(state["samples"], now - delay)
    return runtime._output(node, "Motion", state["published"].copy())


MENU_ENTRY = (NODE, "Motion Delay")
CLASSES = (YUGUS_NODE_motion_delay, YUGUS_OT_motion_delay_reset)
