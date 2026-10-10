"""Ordered keyboard/state processing on Blender's shared main-thread scheduler.

Evaluation is read-only. Events, mode changes, toggle memory, and value easing
are updated here once, never as a side effect of a downstream data pull.
"""
from collections import deque
from dataclasses import dataclass
import re
import time

import bpy

from .constants import TREE_ID
from . import global_keyboard

ACTION_TYPES = {"YUGUS_NODE_state_toggle", "YUGUS_NODE_control_mode", "YUGUS_NODE_set_mode"}
NODE_TYPES = ACTION_TYPES | {
    "YUGUS_NODE_keyboard_data", "YUGUS_NODE_key_state", "YUGUS_NODE_state_logic",
    "YUGUS_NODE_state_events", "YUGUS_NODE_state_value", "YUGUS_NODE_mode_match",
}
ACTION_INPUTS = {
    "YUGUS_NODE_state_toggle": {"Toggle", "Set", "Reset"},
    "YUGUS_NODE_control_mode": {"Cycle", "Lock", "Unlock"},
    "YUGUS_NODE_set_mode": {"Enter"},
}


@dataclass(frozen=True)
class KeyboardFrame:
    source_id: str = ""
    keys: frozenset = frozenset()
    blocked_keys: frozenset = frozenset()
    sequence: int = 0
    listening: bool = False


@dataclass(frozen=True)
class ModeFrame:
    source_id: str = ""
    name: str = ""
    modes: tuple = ()
    locked: bool = True


_down = set()
_frames = {}
_source_blocked = {}
_filters = {}
_modes = {}
_toggles = {}
_values = {}
_detectors = {}
_pending = deque()
_sequence = 0
_enabled = False
_clearing = False
_refreshing = False
_refresh_again = False
_last_reconcile = 0.0
_backend_wanted = False
_retry_after = 0.0
_last_payload = {}
_emit_depth = 0
MAX_EVENT_DEPTH = 32

MODIFIERS = {
    "CTRL": {"CTRL", "LEFT_CTRL", "RIGHT_CTRL"},
    "SHIFT": {"SHIFT", "LEFT_SHIFT", "RIGHT_SHIFT"},
    "ALT": {"ALT", "LEFT_ALT", "RIGHT_ALT"},
    "OSKEY": {"OSKEY", "LEFT_OSKEY", "RIGHT_OSKEY"},
}
ALIASES = {
    "CONTROL": "CTRL", "CMD": "OSKEY", "COMMAND": "OSKEY", "WIN": "OSKEY",
    "WINDOWS": "OSKEY", "SUPER": "OSKEY", "ENTER": "RET", "RETURN": "RET",
    "ESCAPE": "ESC", "SPACEBAR": "SPACE", "LEFT": "LEFT_ARROW", "RIGHT": "RIGHT_ARROW",
    "UP": "UP_ARROW", "DOWN": "DOWN_ARROW", "DELETE": "DEL", "BACKSPACE": "BACK_SPACE",
    "ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4",
    "FIVE": "5", "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9",
}


def _uid(node):
    return str(getattr(node, "runtime_id", "") or node.name)


def _trees(include_definitions=False):
    return [tree for tree in getattr(bpy.data, "node_groups", ()) if tree.bl_idname == TREE_ID
            and (include_definitions or not getattr(tree, "is_workflow_definition", False))]


def _names(text):
    return tuple(dict.fromkeys(name.strip() for name in re.split(r"[,\n]", str(text)) if name.strip()))


def normalize_key(key):
    name = str(key).strip().upper().replace(" ", "_")
    return ALIASES.get(name, name)


def shortcut_keys(text):
    return tuple(dict.fromkeys(normalize_key(key) for key in str(text).split("+") if key.strip()))


def _expanded(keys):
    result = set(keys)
    for name, variants in MODIFIERS.items():
        if variants & result:
            result.add(name)
    return frozenset(result)


def _output(node, **values):
    result = dict(values)
    for name, value in values.items():
        socket = node.outputs.get(name)
        if socket is not None:
            result[socket.identifier] = value
    return result


def _context(tree, now):
    from .flow import PullContext
    return PullContext(tree, bpy.context.scene, now, 0.0)


def _mode_frame(node):
    choices = _names(node.modes)
    state = _modes.get(_uid(node), {})
    name = state.get("name", node.initial_mode)
    if name not in choices:
        name = choices[0] if choices else ""
    return ModeFrame(_uid(node), name, choices, bool(state.get("locked", False)))


def _match_key(node, frame, mode):
    if not isinstance(frame, KeyboardFrame) or not frame.listening:
        return False
    socket = node.inputs.get("Mode")
    if socket is not None and socket.is_linked:
        if not isinstance(mode, ModeFrame) or not mode.source_id or mode.locked:
            return False
        allowed = _names(node.allowed_modes)
        if allowed and mode.name not in allowed:
            return False
    keys = (normalize_key(node.key),) if node.match_mode == "RAW" else shortcut_keys(node.shortcut)
    if not keys or not all(key in frame.keys for key in keys):
        return False
    blocked = _expanded(frame.blocked_keys | frozenset(_filters.get(_uid(node), {}).get("blocked", ())))
    if set(keys) & blocked:
        return False
    if node.match_mode == "SHORTCUT" and node.exact_modifiers:
        required = {modifier for modifier, variants in MODIFIERS.items() if set(keys) & variants}
        pressed = {modifier for modifier, variants in MODIFIERS.items() if frame.keys & variants}
        if pressed != required:
            return False
    return True


def evaluate(context, node):
    kind = node.bl_idname
    if kind not in NODE_TYPES:
        return None
    if node.mute:
        if kind == "YUGUS_NODE_keyboard_data":
            return _output(node, Data=KeyboardFrame())
        if kind == "YUGUS_NODE_control_mode":
            return _output(node, Mode=ModeFrame(), Unlocked=False)
        return _output(node, **({"Value": 0.0} if kind == "YUGUS_NODE_state_value" else {"State": False}))
    uid = _uid(node)
    if kind == "YUGUS_NODE_keyboard_data":
        return _output(node, Data=_frames.get(uid, KeyboardFrame()) if node.listen else KeyboardFrame())
    if kind == "YUGUS_NODE_key_state":
        return _output(node, State=_match_key(node, context.input(node, "Keyboard", None), context.input(node, "Mode", None)))
    if kind == "YUGUS_NODE_state_logic":
        first = bool(context.input(node, "A", False))
        second = bool(context.input(node, "B", False)) if node.operation != "NOT" else False
        result = {"AND": first and second, "OR": first or second, "NOT": not first, "XOR": first != second}[node.operation]
        return _output(node, State=result)
    if kind == "YUGUS_NODE_state_toggle":
        return _output(node, State=bool(_toggles.get(uid, node.initial_state)))
    if kind == "YUGUS_NODE_state_value":
        return _output(node, Value=float(_values.get(uid, node.off_value)))
    if kind == "YUGUS_NODE_control_mode":
        value = _mode_frame(node)
        return _output(node, Mode=value, Unlocked=not value.locked)
    if kind == "YUGUS_NODE_mode_match":
        mode = context.input(node, "Mode", None)
        state = isinstance(mode, ModeFrame) and bool(mode.source_id) and mode.name == node.mode_name and (not node.respect_lock or not mode.locked)
        return _output(node, State=state)
    # Event-only nodes have no pull-time action or state mutation.
    return {}


def configuration_changed(node):
    if _clearing:
        return
    if not getattr(node, "listen", False):
        _frames.pop(_uid(node), None)
        _source_blocked.pop(_uid(node), None)


def _prepare_sources(now, manage_backend):
    global _backend_wanted, _retry_after
    nodes = [node for tree in _trees() for node in tree.nodes
             if node.bl_idname == "YUGUS_NODE_keyboard_data" and node.listen and not node.mute]
    live = {_uid(node) for node in nodes}
    new = live - _frames.keys()
    if manage_backend and nodes and not _backend_wanted and now >= _retry_after:
        _backend_wanted = global_keyboard.start()
        if not _backend_wanted:
            _retry_after = now + 2.0
        if _backend_wanted:
            _down.clear()
            _down.update(global_keyboard.snapshot())
    if manage_backend and not nodes and _backend_wanted:
        global_keyboard.stop()
        _backend_wanted = False
        _down.clear()
    for uid in new:
        _source_blocked[uid] = set(_down)
    for uid in set(_frames) - live:
        _frames.pop(uid, None)
        _source_blocked.pop(uid, None)
    _write_frames()
    for node in nodes:
        summary = global_keyboard.status() if manage_backend else "Listening · test input"
        if node.last_summary != summary:
            node.last_summary = summary
        if node.monitor_open:
            monitor = "Held: " + (", ".join(sorted(_expanded(_down))) or "None")
            if node.monitor_text != monitor:
                node.monitor_text = monitor
    return bool(new)


def _write_frames():
    for uid, blocked in _source_blocked.items():
        blocked.intersection_update(_down)
        _frames[uid] = KeyboardFrame(uid, _expanded(_down), frozenset(blocked), _sequence, True)


def _prepare_filters(now):
    live = set()
    for tree in _trees():
        context = _context(tree, now)
        for node in tree.nodes:
            if node.bl_idname != "YUGUS_NODE_key_state":
                continue
            uid = _uid(node)
            live.add(uid)
            socket = node.inputs.get("Mode")
            try:
                mode = context.input(node, "Mode", None)
            except Exception:
                mode = None
            allowed = not node.mute
            if socket is not None and socket.is_linked:
                allowed &= isinstance(mode, ModeFrame) and bool(mode.source_id) and not mode.locked
                names = _names(node.allowed_modes)
                allowed &= not names or isinstance(mode, ModeFrame) and mode.name in names
            signature = (node.match_mode, node.key, node.shortcut, node.exact_modifiers, node.allowed_modes,
                         tuple((_uid(link.from_node), link.from_socket.identifier) for link in socket.links))
            state = _filters.get(uid)
            if state is None or state["signature"] != signature:
                state = {"signature": signature, "allowed": allowed, "blocked": set()}
                _filters[uid] = state
            if not allowed or allowed != state["allowed"]:
                state["blocked"].update(_down)
            state["blocked"].intersection_update(_down)
            state["allowed"] = allowed
    for uid in set(_filters) - live:
        _filters.pop(uid, None)


def _detector_signature(node):
    visited = set()
    records = []
    stack = [node]
    while stack:
        item = stack.pop()
        uid = _uid(item)
        if uid in visited:
            continue
        visited.add(uid)
        properties = tuple((name, str(getattr(item, name))) for name in
                           ("match_mode", "key", "shortcut", "exact_modifiers", "allowed_modes", "operation", "mode_name", "respect_lock") if hasattr(item, name))
        records.append((uid, item.mute, properties))
        for socket in item.inputs:
            for link in socket.links:
                records.append((uid, socket.name, _uid(link.from_node), link.from_socket.identifier, link.is_muted))
                stack.append(link.from_node)
    for output in node.outputs:
        records.extend((output.name, _uid(link.to_node), link.to_socket.name) for link in output.links)
    return tuple(records)


def _emit(tree, node, output, payload, now):
    global _emit_depth
    from . import flow
    emit = getattr(flow, "emit_event", None)
    if emit is not None:
        if _emit_depth >= MAX_EVENT_DEPTH:
            raise ValueError("Input event cycle exceeded 32 nested events")
        _emit_depth += 1
        try:
            emit(tree, node, output, dict(payload), now=now)
        finally:
            _emit_depth -= 1


def _refresh_detectors(now, payload=None, silent=False):
    global _refreshing, _refresh_again
    if _refreshing:
        _refresh_again = True
        return
    _refreshing = True
    try:
        for iteration in range(64):
            _refresh_again = False
            notifications = []
            live = set()
            for tree in _trees():
                context = _context(tree, now)
                for node in tree.nodes:
                    if node.bl_idname != "YUGUS_NODE_state_events" or node.mute:
                        continue
                    uid = _uid(node)
                    live.add(uid)
                    signature = _detector_signature(node)
                    try:
                        high = bool(context.input(node, "State", False))
                    except Exception as error:
                        summary = f"Error · {error}"
                        if node.last_summary != summary:
                            node.last_summary = summary
                        _detectors.pop(uid, None)
                        continue
                    prior = _detectors.get(uid)
                    _detectors[uid] = {"signature": signature, "high": high}
                    summary = "State · On" if high else "State · Off"
                    if node.last_summary != summary:
                        node.last_summary = summary
                    if silent or prior is None or prior["signature"] != signature or prior["high"] == high:
                        continue
                    data = dict(payload or _last_payload)
                    data.update({"state": high, "state_node": uid})
                    notifications.append((tree, node, "On" if high else "Off", data))
            for uid in set(_detectors) - live:
                _detectors.pop(uid, None)
            for tree, node, output, data in notifications:
                _emit(tree, node, output, data, now)
                _emit(tree, node, "Changed", data, now)
            if not _refresh_again:
                break
        else:
            for tree in _trees():
                tree.last_error = "State event cycle exceeded 64 updates"
    finally:
        _refreshing = False


def _update_values(now, delta):
    live = set()
    for tree in _trees():
        context = _context(tree, now)
        for node in tree.nodes:
            if node.bl_idname != "YUGUS_NODE_state_value":
                continue
            uid = _uid(node)
            live.add(uid)
            if node.mute:
                _values.pop(uid, None)
                continue
            high = bool(context.input(node, "State", False))
            target = node.on_value if high else node.off_value
            value = _values.get(uid, node.off_value)
            seconds = node.attack if high else node.release
            step = abs(node.on_value - node.off_value) * max(0.0, delta) / seconds if seconds > 0 else float("inf")
            difference = target - value
            value = target if abs(difference) <= step else value + (step if difference > 0 else -step)
            _values[uid] = value
            summary = f"Value · {value:.3f}"
            if node.last_summary != summary:
                node.last_summary = summary
    for uid in set(_values) - live:
        _values.pop(uid, None)


def feed_event(key, down, now=None, repeat=False):
    """Primitive event injection for deterministic tests or other input adapters."""
    stamp = time.monotonic() if now is None else float(now)
    _pending.append(global_keyboard.KeyEvent(0, normalize_key(key), bool(down), bool(repeat), stamp))


def tick(_context_unused, delta_time, *, now=None, events=None, manage_backend=True):
    global _sequence, _last_reconcile, _last_payload, _backend_wanted
    from . import scheduler
    if not _enabled or scheduler.is_suspended():
        return
    now = time.monotonic() if now is None else float(now)
    new_sources = _prepare_sources(now, manage_backend)
    _prepare_filters(now)
    _refresh_detectors(now, silent=new_sources)
    incoming = list(events) if events is not None else (global_keyboard.drain() if manage_backend else [])
    incoming.extend(_pending)
    _pending.clear()
    for item in incoming:
        _sequence += 1
        if item.reset_keys is not None:
            _down.clear()
            _down.update(item.reset_keys)
            for blocked in _source_blocked.values():
                blocked.update(_down)
            _write_frames()
            _prepare_filters(item.timestamp)
            _refresh_detectors(item.timestamp, silent=True)
            continue
        key = normalize_key(item.key)
        if item.down:
            _down.add(key)
        else:
            _down.discard(key)
        _last_payload = {"event_id": f"keyboard:{_sequence}", "key": key, "down": item.down,
                         "repeat": item.repeat, "timestamp": item.timestamp, "source": "keyboard"}
        _write_frames()
        _prepare_filters(item.timestamp)
        _refresh_detectors(item.timestamp, _last_payload)
    if manage_backend and _backend_wanted and now - _last_reconcile >= 0.5:
        _last_reconcile = now
        physical = set(global_keyboard.snapshot())
        if physical != _down:
            # Recovery after a missed release/desktop switch is a baseline, not a user action.
            _down.clear()
            _down.update(physical)
            for blocked in _source_blocked.values():
                blocked.update(_down)
            _write_frames()
            _prepare_filters(now)
            _refresh_detectors(now, silent=True)
        if not global_keyboard.is_running():
            _backend_wanted = False
    _update_values(now, float(delta_time))
    live = {_uid(node) for tree in _trees(include_definitions=True) for node in tree.nodes}
    for registry in (_modes, _toggles):
        for uid in set(registry) - live:
            registry.pop(uid, None)


def _find_mode(reference):
    if getattr(reference, "bl_idname", None) == "YUGUS_NODE_control_mode":
        return reference
    uid = reference.source_id if isinstance(reference, ModeFrame) else str(reference)
    for tree in _trees(include_definitions=True):
        for node in tree.nodes:
            if node.bl_idname == "YUGUS_NODE_control_mode" and _uid(node) == uid:
                return node
    raise ValueError("Connect a Control Mode node")


def mode_state(reference):
    return _mode_frame(_find_mode(reference))


def _change_mode(node, name=None, locked=None, now=None):
    if node.mute:
        raise ValueError("Control Mode is muted")
    now = time.monotonic() if now is None else float(now)
    previous = _mode_frame(node)
    selected = previous.name if name is None else str(name)
    if selected not in previous.modes:
        raise ValueError(f"Unknown mode: {selected}")
    _modes[_uid(node)] = {"name": selected, "locked": previous.locked if locked is None else bool(locked)}
    result = _mode_frame(node)
    node.last_summary = f"{result.name} · {'Locked' if result.locked else 'Unlocked'}"
    if result != previous:
        _prepare_filters(now)
        _refresh_detectors(now, {"source": "mode", "mode": result.name, "locked": result.locked})
        _emit(node.id_data, node, "Changed", {"mode": result.name, "locked": result.locked}, now)
    return result


def set_mode(reference, name, now=None):
    return _change_mode(_find_mode(reference), name=name, now=now)


def cycle_mode(reference, now=None):
    node = _find_mode(reference)
    current = _mode_frame(node)
    if not current.modes:
        raise ValueError("Add at least one mode")
    return set_mode(node, current.modes[(current.modes.index(current.name) + 1) % len(current.modes)], now)


def set_locked(reference, locked, now=None):
    return _change_mode(_find_mode(reference), locked=locked, now=now)


def action_inputs(node):
    return ACTION_INPUTS.get(node.bl_idname, set())


action_sockets = action_inputs


def handle_action(node, input_name, context, now):
    if input_name not in action_inputs(node):
        return False
    if node.mute:
        return True
    uid = _uid(node)
    if node.bl_idname == "YUGUS_NODE_state_toggle":
        previous = bool(_toggles.get(uid, node.initial_state))
        result = not previous if input_name == "Toggle" else input_name == "Set"
        _toggles[uid] = result
        context.cache.clear()
        node.last_summary = "State · On" if result else "State · Off"
        _refresh_detectors(now, {"source": "toggle", "state": result})
    elif node.bl_idname == "YUGUS_NODE_control_mode":
        context.cache.clear()
        if input_name == "Cycle":
            cycle_mode(node, now)
        else:
            set_locked(node, input_name == "Lock", now)
    else:
        mode = context.input(node, "Mode", None)
        context.cache.clear()
        set_mode(mode, node.mode_name, now)
    context.cache.clear()
    return True


def receive(pulse, node, socket, path):
    """Optional dispatcher adapter; use this OR handle_action, not both."""
    if not handle_action(node, socket.name, pulse.context, pulse.now):
        return False
    pulse.emit(node.outputs.get("End"), path)
    return True


def clear(stop_listening=True):
    global _clearing, _backend_wanted, _refreshing, _refresh_again, _last_payload
    _clearing = True
    try:
        global_keyboard.stop()
        _backend_wanted = False
        _down.clear()
        _pending.clear()
        for registry in (_frames, _source_blocked, _filters, _modes, _toggles, _values, _detectors):
            registry.clear()
        _refreshing = _refresh_again = False
        _last_payload = {}
        if stop_listening:
            for tree in _trees(include_definitions=True):
                for node in tree.nodes:
                    if node.bl_idname == "YUGUS_NODE_keyboard_data":
                        node.listen = False
                        node.last_summary = "Stopped"
    finally:
        _clearing = False


def register():
    global _enabled
    from . import scheduler
    clear()
    _enabled = True
    scheduler.add_motion("input_values", tick, priority=5)


def unregister():
    global _enabled
    from . import scheduler
    _enabled = False
    scheduler.remove_motion("input_values")
    clear()
