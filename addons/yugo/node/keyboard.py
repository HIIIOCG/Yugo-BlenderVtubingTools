"""0.1.29 keyboard behavior adapted to native node triggers (one group per graph)."""
import math
import re
import time
import uuid

import bpy

from . import scheduler, runtime
from .constants import TREE_ID
from .data import ExpressionFrame

_windows = {}
_enabled = False
_retry_after = 0.0
CONFIRM_KEYS = {"RET", "NUMPAD_ENTER", "ESC", "RIGHTMOUSE"}
KEY_NODES = {"YUGUS_NODE_key_bind", "YUGUS_NODE_keybind_expression"}


def bindings(tree):
    return [node for node in tree.nodes if node.bl_idname in KEY_NODES]


def state(node):
    return runtime._key_state.setdefault(str(node.runtime_id),
                                        {"pressed": False, "toggled": False})


def reset():
    _windows.clear()
    runtime._key_state.clear()


def release_held():
    for value in runtime._key_state.values():
        value["pressed"] = False


def trees():
    return [tree for tree in getattr(bpy.data, "node_groups", ())
            if tree.bl_idname == TREE_ID and not getattr(tree, "is_workflow_definition", False)]


def process_event(event, scene=None):
    if not _enabled:
        return False
    matched = False
    for tree in trees():
        if scene is not None and tree != getattr(scene, "yugu_node_studio_tree", None):
            # Also support an explicitly opened graph, without activating other scenes.
            current = getattr(getattr(bpy.context, "space_data", None), "edit_tree", None)
            if tree != current:
                continue
        nodes = bindings(tree)
        for node in nodes:
            if node.key == "NONE" or node.key != event.type:
                continue
            value = state(node)
            if event.value == "RELEASE":
                if node.mode == "HOLD":
                    matched |= value["pressed"]
                    value["pressed"] = False
                continue
            if event.value != "PRESS":
                continue
            if (bool(node.use_ctrl) != bool(getattr(event, "ctrl", False))
                    or bool(node.use_shift) != bool(getattr(event, "shift", False))
                    or getattr(event, "alt", False) or getattr(event, "oskey", False)):
                continue
            matched = True
            if getattr(event, "is_repeat", False):
                continue
            if node.mode == "HOLD":
                value["pressed"] = True
            else:
                value["toggled"] = not value["toggled"]
                if node.mode == "SOLO" and value["toggled"]:
                    for other in nodes:
                        if other != node and other.mode == "SOLO":
                            state(other)["toggled"] = False
    return matched


def tick(_context, delta_time):
    live = set()
    for tree in trees():
        nodes = bindings(tree)
        for node in nodes:
            live.add(str(node.runtime_id))
            value = state(node)
            target = float(value["pressed"] if node.mode == "HOLD" else value["toggled"])
            if node.key == "NONE":
                target = 0.0
                value["pressed"] = value["toggled"] = False
            smooth = float(getattr(node, "smooth", 0.0))
            current = value.get("level", 0.0)
            if getattr(node, "binary", False) or smooth <= 0 or delta_time <= 0:
                current = target
            else:
                current += (target - current) * (1.0 - math.exp(-delta_time * 5 / (0.02 + smooth * 0.98)))
                if abs(target - current) < 0.0001:
                    current = target
            value["level"] = current
        active = [node for node in nodes if state(node)["level"] > 0.0]
        solos = [node for node in active if node.mode == "SOLO"]
        exclusive = next((node for node in active if getattr(node, "mask_exclusive", False)), None)
        allowed = solos or ([exclusive] if exclusive else active)
        for node in nodes:
            state(node)["output"] = state(node)["level"] * node.value if node in allowed else 0.0
    for key in set(runtime._key_state) - live:
        runtime._key_state.pop(key, None)


def signal(node):
    if not _enabled:
        return 0.0
    value = state(node)
    return value.get("output", 0.0)


def _upstream(socket, visited=None):
    visited = set() if visited is None else visited
    if socket is None:
        return []
    result = []
    for link in socket.links:
        node = link.from_node
        key = node.as_pointer()
        if key in visited:
            continue
        visited.add(key)
        result.append(node)
        for child in node.inputs:
            result.extend(_upstream(child, visited))
    return result


def mask_expressions(output, frame, expression_map):
    """Only bindings feeding this expression branch may lock its other channels."""
    if not _enabled:
        return frame
    branch = _upstream(output.inputs.get("Expressions"))
    active = [node for node in branch if node.bl_idname in KEY_NODES and signal(node) > 0]
    eyes = any(getattr(node, "mask_eyes", False) for node in active)
    mouth = any(getattr(node, "mask_mouth", False) for node in active)
    if not (eyes or mouth):
        return frame
    protected = set()
    for node in branch:
        if node.bl_idname == "YUGUS_NODE_expression_action":
            if any(binding in active for binding in _upstream(node.inputs.get("Trigger"))):
                protected.add(re.sub(r"[^a-z0-9]", "", node.expression_key.lower()))
        elif node in active and hasattr(node, "expression"):
            protected.add(re.sub(r"[^a-z0-9]", "", node.expression.lower()))
    result = ExpressionFrame(dict(frame.values))
    names = set(result.values)
    if expression_map is not None:
        names.update(expression_map.definitions)
    for name in names:
        normalized = re.sub(r"[^a-z0-9]", "", name.lower())
        if normalized in protected:
            continue
        is_eye = normalized.startswith(("eye", "blink", "look"))
        is_mouth = normalized.startswith(("mouth", "jaw")) or normalized in {"a", "i", "u", "e", "o", "aa", "ih", "ou", "ee", "oh"}
        if (eyes and is_eye) or (mouth and is_mouth):
            result.values[name] = 0.0
    return result


def dispatch(context, event):
    if scheduler.is_suspended():
        return {"PASS_THROUGH"}
    if event.type == "WINDOW_DEACTIVATE":
        release_held()
        return {"PASS_THROUGH"}
    matched = process_event(event, None if event.value == "RELEASE" else context.scene)
    if event.type in CONFIRM_KEYS:
        return {"PASS_THROUGH"}
    return {"PASS_THROUGH"}


class YUGUS_OT_keyboard_listener(bpy.types.Operator):
    bl_idname = "yugu_node_studio.keyboard_listener"
    bl_label = "Yugo Node Keyboard Listener"
    bl_options = {"INTERNAL"}
    token: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    window_key: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if not _enabled or context.window is None:
            return {"CANCELLED"}
        key = str(context.window.as_pointer())
        if key in _windows:
            return {"CANCELLED"}
        self.token, self.window_key = uuid.uuid4().hex, key
        context.window_manager.modal_handler_add(self)
        _windows[key] = self.token
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _retry_after
        try:
            if not _enabled or _windows.get(self.window_key) != self.token:
                return {"CANCELLED", "PASS_THROUGH"}
            return dispatch(context, event)
        except Exception as error:
            _retry_after = time.monotonic() + 2.0
            _windows.clear()
            release_held()
            print(f"Yugo Node keyboard will retry: {error}")
            return {"CANCELLED", "PASS_THROUGH"}

    def cancel(self, _context):
        if _windows.get(self.window_key) == self.token:
            _windows.pop(self.window_key, None)
            release_held()


def maintain(context, _delta_time):
    if not _enabled or bpy.app.background or scheduler.is_suspended() or time.monotonic() < _retry_after:
        return
    if not any(bindings(tree) for tree in trees()):
        return
    live = {str(window.as_pointer()) for window in context.window_manager.windows}
    for key in set(_windows) - live:
        _windows.pop(key, None)
        release_held()
    for window in context.window_manager.windows:
        key = str(window.as_pointer())
        tokens = {operator.token for operator in window.modal_operators
                  if isinstance(operator, YUGUS_OT_keyboard_listener)}
        if key in _windows and _windows[key] not in tokens:
            _windows.pop(key, None)
            release_held()
        if key not in _windows:
            with context.temp_override(window=window):
                bpy.ops.yugu_node_studio.keyboard_listener("EXEC_DEFAULT")


def register():
    global _enabled, _retry_after
    reset()
    _retry_after = 0.0
    bpy.utils.register_class(YUGUS_OT_keyboard_listener)
    _enabled = True
    scheduler.add_motion("keyboard_values", tick, priority=0)
    scheduler.add_system("keyboard", maintain, priority=400)


def unregister():
    global _enabled
    _enabled = False
    scheduler.remove_motion("keyboard_values")
    scheduler.remove_system("keyboard")
    reset()
    bpy.utils.unregister_class(YUGUS_OT_keyboard_listener)
