"""Release scope, with inert RNA declarations for loading older graphs."""

RETIRED_NODE_TYPES = frozenset({
    "YUGUS_NODE_flow_parallel", "YUGUS_NODE_flow_wait_all",
    "YUGUS_NODE_float_receiver", "YUGUS_NODE_spring_simulation",
    "YUGUS_NODE_yt_chat_source", "YUGUS_NODE_yt_chat_event",
    "YUGUS_NODE_keyword_match", "YUGUS_NODE_chat_debug", "YUGUS_NODE_chat_list",
    "YUGUS_NODE_keyboard_data", "YUGUS_NODE_key_state", "YUGUS_NODE_state_logic",
    "YUGUS_NODE_state_events", "YUGUS_NODE_state_toggle", "YUGUS_NODE_state_value",
    "YUGUS_NODE_control_mode", "YUGUS_NODE_mode_match", "YUGUS_NODE_set_mode",
    "YUGUS_NODE_action_flow", "YUGUS_NODE_flow_entry", "YUGUS_NODE_flow_end",
    "YUGUS_NODE_wait_action", "YUGUS_NODE_test_action", "YUGUS_NODE_test_event",
    "YUGUS_NODE_key_bind", "YUGUS_NODE_keybind_expression",
})


def is_available(node_or_type):
    kind = node_or_type if isinstance(node_or_type, str) else node_or_type.bl_idname
    return kind not in RETIRED_NODE_TYPES


def require_available(node):
    if not is_available(node):
        raise ValueError(f"{node.bl_label} is unavailable in Yugo Node 0.10.0")


def _draw_unavailable(self, _context, layout):
    layout.label(text="Unavailable in Yugo Node 0.10.0", icon="INFO")


def _unavailable_label(self):
    return self.bl_label + " (Unavailable)"


def prepare_legacy_classes(classes):
    # Keep identifiers, properties and sockets so old files retain their data.
    # Their creation, evaluation and event execution are blocked separately.
    from .nodes import YUGUS_NodeBase
    for cls in classes:
        if getattr(cls, "bl_idname", "") in RETIRED_NODE_TYPES:
            cls.draw_buttons = _draw_unavailable
            cls.draw_label = _unavailable_label
            cls.copy = YUGUS_NodeBase.copy
            cls.free = YUGUS_NodeBase.free
