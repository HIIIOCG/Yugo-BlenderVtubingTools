"""Main-thread chat pulls. Background workers never retain Blender objects."""
from copy import deepcopy
from dataclasses import dataclass
import uuid
import hashlib
import time

import bpy

from . import chat_nodes, runtime, scheduler
from .chat_transport import YouTubeChatTransport
from .constants import TREE_ID

_sessions = {}
_listeners = {}
_last_messages = {}
debug_logs = []


@dataclass(frozen=True)
class ChatRef:
    source_id: str
    session_id: str


def preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _uid(node):
    return str(node.runtime_id)


def _nodes():
    return (node for tree in getattr(bpy.data, "node_groups", ()) if tree.bl_idname == TREE_ID
            for node in tree.nodes if node.bl_idname in {chat_nodes.SOURCE, chat_nodes.CHAT_EVENT, chat_nodes.DEBUG})


def connection_values(node, context=None, api_key=None):
    context = context or runtime.EvaluationContext(node.id_data, bpy.context.scene, 0)
    url_socket = node.inputs.get("Live URL")
    key_socket = node.inputs.get("API Key")
    video = context.input(node, "Live URL", "") if url_socket is not None and url_socket.is_linked else (
        node.video or getattr(url_socket, "default_value", ""))
    if api_key is not None:
        key = api_key
    elif key_socket is not None and key_socket.is_linked:
        key = context.input(node, "API Key", "")
    else:
        prefs = preferences()
        key = node.api_key or getattr(key_socket, "default_value", "") or getattr(prefs, "youtube_api_key", "")
    return str(video or "").strip(), str(key or "").strip()


def _configuration(node, context=None, api_key=None):
    video, key = connection_values(node, context, api_key)
    return (video, bool(node.include_history), hashlib.sha256(key.encode("utf-8")).digest())


def connect(node, api_key=None, context=None, retry=False):
    from .availability import require_available
    require_available(node)
    if not getattr(bpy.app, "online_access", True):
        raise ValueError("Enable Allow Online Access in Blender Preferences first")
    if node.mute:
        raise ValueError("Enable the YouTube Chat node before connecting")
    video, key = connection_values(node, context, api_key)
    if not key:
        raise ValueError("Set a YouTube API Key first")
    configuration = _configuration(node, context, api_key)
    previous = _sessions.get(_uid(node))
    if previous and previous["configuration"] == configuration:
        terminal = previous["transport"].status()["state"] in {"ERROR", "ENDED", "QUOTA_EXCEEDED", "STOPPED"}
        if not terminal or not retry:
            if context is not None:
                context.cache.clear()
            return _source_ref(node)
    transport = YouTubeChatTransport(video, key, include_history=node.include_history)
    attach_transport(node, transport, configuration=configuration, watch_configuration=_configuration(node, context))
    try:
        transport.start()
    except Exception:
        disconnect(node)
        raise
    node.last_summary = "Connecting"
    if context is not None:
        context.cache.clear()
    return _source_ref(node)


def attach_transport(source, transport, configuration=None, watch_configuration=None):
    """Also used to run real Blender graphs with deterministic API fixtures."""
    from .availability import require_available
    require_available(source)
    disconnect(source)
    uid = _uid(source)
    _sessions[uid] = {"session": uuid.uuid4().hex, "transport": transport,
                      "configuration": configuration or _configuration(source),
                      "watch_configuration": watch_configuration or _configuration(source),
                      "notified_cursor": 0, "continuations": set()}
    source.connected = True
    source.last_summary = "Connected"


def disconnect(source):
    uid = source if isinstance(source, str) else _uid(source)
    record = _sessions.pop(uid, None)
    if record is not None:
        record["transport"].stop()
    for listener_id, listener in tuple(_listeners.items()):
        if listener["source"] == uid:
            remove_listener(listener_id)
    if not isinstance(source, str):
        source.connected = False
        source.last_summary = "Disconnected"


def remove_listener(uid):
    _listeners.pop(uid, None)
    _last_messages.pop(uid, None)


def configuration_changed(node, *, reset_cursor=True):
    if node.bl_idname == chat_nodes.SOURCE:
        if _uid(node) in _sessions:
            disconnect(node)
        return
    if node.bl_idname != chat_nodes.CHAT_EVENT:
        return
    listener = _listeners.get(_uid(node))
    if listener is not None and reset_cursor:
        source = _sessions.get(listener["source"])
        if source:
            listener["cursor"] = source["transport"].latest_cursor()
        listener["mode"] = node.mode
    paid = node.mode in {"SUPER_CHAT", "SUPER_STICKER", "ALL"}
    member = node.mode in {"NEW_MEMBER", "MEMBER_MILESTONE", "MEMBERSHIP_GIFT", "GIFT_RECEIVED", "ALL"}
    for name in ("Amount", "Currency", "Amount Display"):
        socket = node.outputs.get(name)
        if socket is not None:
            socket.hide = not paid and not socket.is_linked
    for name in ("Member Level", "Months", "Gift Count"):
        socket = node.outputs.get(name)
        if socket is not None:
            relevant = member and (name != "Gift Count" or node.mode in {"MEMBERSHIP_GIFT", "ALL"})
            socket.hide = not relevant and not socket.is_linked


def _source_ref(node):
    record = _sessions.get(_uid(node)) if not node.mute else None
    return ChatRef(_uid(node), record["session"] if record else "")


def _reference(node, context):
    reference = context.input(node, "Chat", None)
    if not isinstance(reference, ChatRef) or not reference.session_id:
        return None, None
    record = _sessions.get(reference.source_id)
    if record is None or record["session"] != reference.session_id:
        return None, None
    return reference, record


def pull_events(node, context):
    reference, record = _reference(node, context)
    if record is None:
        node.last_summary = "Disconnected · connect Chat"
        return []
    uid = _uid(node)
    listener = _listeners.get(uid)
    if listener is None or (listener["source"], listener["session"]) != (reference.source_id, reference.session_id):
        cursor = 0 if listener is None else record["transport"].latest_cursor()
        listener = {"source": reference.source_id, "session": reference.session_id,
                    "mode": node.mode, "cursor": cursor}
        _listeners[uid] = listener
    elif listener["mode"] != node.mode:
        listener.update(mode=node.mode, cursor=record["transport"].latest_cursor())
    batch = record["transport"].read(listener["cursor"], limit=node.batch_limit)
    listener["cursor"] = batch.cursor
    if batch.overrun:
        node.missed_count += batch.overrun
    messages = [message for message in batch.messages if message["type"] in chat_nodes.SUPPORTED_TYPES
                and (node.mode == "ALL" or message["type"] == node.mode)]
    if messages:
        node.received_count += len(messages)
        _last_messages[uid] = deepcopy(messages[-1])
        latest = messages[-1]
        runtime._set_monitor(node, [f"{latest['author']}: {latest['text']}",
                                    f"Message ID: {latest['message_id']}", f"Received: {node.received_count}"])
    suffix = f" · {node.missed_count} missed (buffer full)" if node.missed_count else ""
    summary = f"Received {node.received_count} · {len(messages)} this Enter{suffix}"
    if node.last_summary != summary:
        node.last_summary = summary
    return messages


def event_payload(node, incoming, message):
    payload = deepcopy(incoming) if isinstance(incoming, dict) else {"incoming": deepcopy(incoming)}
    events = payload.setdefault("chat_events", {})
    if not isinstance(events, dict):
        events = payload["chat_events"] = {}
    events[_uid(node)] = deepcopy(message)
    payload["chat_message"] = deepcopy(message)
    payload["event_id"] = f"youtube:{message['message_id']}"
    return payload


def evaluate(context, node):
    if node.bl_idname == chat_nodes.SOURCE:
        return runtime._output(node, "Chat", _source_ref(node))
    if node.bl_idname != chat_nodes.CHAT_EVENT:
        return None
    # Evaluation is pure: only Enter advances the reader cursor.
    payload = getattr(context, "payload", {})
    events = payload.get("chat_events", {}) if isinstance(payload, dict) else {}
    message = events.get(_uid(node), _last_messages.get(_uid(node), {}))
    if node.mute:
        message = {}
    values = {}
    for name, field in chat_nodes.STRING_FIELDS:
        values.update(runtime._output(node, name, str(message.get(field, "") or "")))
    for name, field in chat_nodes.NUMBER_FIELDS:
        values.update(runtime._output(node, name, float(message.get(field, 0) or 0)))
    return values


def debug(node, context):
    text = str(context.input(node, "Text", ""))
    author = str(context.input(node, "Author Name", ""))
    node.count += 1
    node.last_summary = f"{author}: {text}" if author else text
    runtime._set_monitor(node, [node.last_summary, f"Received: {node.count}"])
    debug_logs.append({"text": text, "author": author, "payload": deepcopy(getattr(context, "payload", {}))})
    del debug_logs[:-256]


def maintenance(_context=None, _delta=0):
    live_sources, live_listeners = set(), set()
    for node in tuple(_nodes()):
        uid = _uid(node)
        if node.bl_idname == chat_nodes.SOURCE:
            live_sources.add(uid)
            record = _sessions.get(uid)
            if record:
                try:
                    changed = record["watch_configuration"] != _configuration(node)
                except Exception:
                    changed = True
                if node.mute or changed:
                    disconnect(node)
                    record = None
            if record:
                status = record["transport"].status()
                state = str(status.get("state", "CONNECTED"))
                connected = state not in {"ENDED", "ERROR", "STOPPED", "QUOTA_EXCEEDED"}
                if node.connected != connected:
                    node.connected = connected
                summary = str(status.get("error") or state.title())
                if node.last_summary != summary:
                    node.last_summary = summary
                runtime._set_monitor(node, [node.last_summary,
                                           f"Buffered: {status.get('buffered', 0)}",
                                           f"Received: {status.get('received', record['transport'].latest_cursor())}"])
        elif node.bl_idname == chat_nodes.CHAT_EVENT:
            live_listeners.add(uid)
            if node.mute:
                context = runtime.EvaluationContext(node.id_data, bpy.context.scene, 0)
                reference, record = _reference(node, context)
                if record is not None:
                    _listeners[uid] = {"source": reference.source_id, "session": reference.session_id,
                                       "mode": node.mode, "cursor": record["transport"].latest_cursor()}
    for uid in set(_sessions) - live_sources:
        disconnect(uid)
    for uid in set(_listeners) - live_listeners:
        remove_listener(uid)


def tick(now=None):
    """Notify from flow's existing 60 Hz tick, never from network threads."""
    from . import flow
    now = time.monotonic() if now is None else float(now)
    for node in tuple(_nodes()):
        if node.bl_idname != chat_nodes.SOURCE or node.mute:
            continue
        record = _sessions.get(_uid(node))
        if record is None:
            continue
        latest = record["transport"].latest_cursor()
        output = node.outputs.get("Trigger")
        targets = flow._event_targets(output) if output else []
        # Follow only enabled event routes; an unrelated old reader cannot
        # cause permanent repeated source events.
        reachable = set()
        stack = list(targets)
        while stack:
            target = stack.pop()
            target_id = _uid(target.node)
            if target_id in reachable:
                continue
            reachable.add(target_id)
            for event_output in flow._event_outputs(target.node):
                stack.extend(flow._event_targets(event_output))
        pending = {uid for uid in record["continuations"] if uid in reachable
                   and uid in _listeners and _listeners[uid]["source"] == _uid(node)
                   and _listeners[uid]["session"] == record["session"]
                   and _listeners[uid]["cursor"] < latest}
        if latest <= record["notified_cursor"] and not pending:
            continue
        record["notified_cursor"] = latest
        record["continuations"] = set()
        if not targets:
            continue
        before = {uid: listener["cursor"] for uid, listener in _listeners.items()}
        flow.emit_event(node.id_data, node, "Trigger",
                        {"chat_source": _uid(node), "chat_sequence": latest}, now=now)
        # Continue bounded reads only when this pulse actually made progress.
        # Gate/Once/disabled routes stop continuation instead of firing forever.
        record["continuations"] = {
            uid for uid, listener in _listeners.items()
            if uid in reachable and listener["source"] == _uid(node)
            and listener["session"] == record["session"]
            and before.get(uid, 0) < listener["cursor"] < latest
        }


def clear():
    for uid in tuple(_sessions):
        disconnect(uid)
    _listeners.clear()
    _last_messages.clear()
    debug_logs.clear()
    for node in tuple(_nodes()):
        if node.bl_idname == chat_nodes.SOURCE:
            node.connected, node.last_summary = False, "Disconnected"
        elif node.bl_idname == chat_nodes.CHAT_EVENT:
            node.received_count = node.missed_count = 0
            node.last_summary = "Connect Chat and Enter"
        else:
            node.count = 0
            node.last_summary = "Connect Enter + Text"


def register():
    scheduler.add_system("chat", maintenance, priority=-30)


def unregister():
    scheduler.remove_system("chat")
    clear()
