"""Pure text comparisons and invocation-scoped keyword event routing.

Reading data never emits events or writes Blender data. Enter samples the inputs
once, records JSON outputs in the workflow, and lets flow route one branch.
"""
import unicodedata

import bpy

from .chat_nodes import EVENT, TEXT
from .input_nodes import STATE
from .nodes import YUGUS_NodeBase

NODE = "YUGUS_NODE_keyword_match"
SECTION = "keyword_match"
MODES = (
    ("CONTAINS", "Contains", "Text contains the keyword anywhere"),
    ("EXACT", "Equals", "The complete text equals the keyword"),
    ("STARTS_WITH", "Starts With", "Text starts with the keyword"),
    ("ENDS_WITH", "Ends With", "Text ends with the keyword"),
)


class YUGUS_NODE_keyword_match(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Keyword Match"
    bl_icon = "VIEWZOOM"
    bl_description = "Compare text with a keyword; Enter sends Matched or Not Matched"
    mode: bpy.props.EnumProperty(name="Match", items=MODES, default="CONTAINS")
    case_sensitive: bpy.props.BoolProperty(name="Case Sensitive", default=False)
    last_summary: bpy.props.StringProperty(default="Connect Text and Keyword", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(255)
        self.inputs.new(EVENT, "Enter").link_limit = 4095
        self.inputs.new(TEXT, "Text")
        self.inputs.new(TEXT, "Keyword")
        self.outputs.new(EVENT, "Matched")
        self.outputs.new(EVENT, "Not Matched")
        self.outputs.new(STATE, "Result")
        self.outputs.new(TEXT, "Text")
        self.outputs.new(TEXT, "Keyword")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "mode", text="")
        layout.prop(self, "case_sensitive")
        layout.label(text=self.last_summary)
        self._draw_monitor(layout)

    def copy(self, source):
        YUGUS_NodeBase.copy(self, source)
        self.last_summary = "Connect Text and Keyword"
        self.monitor_text = "Waiting for data"


CLASSES = (YUGUS_NODE_keyword_match,)


def _uid(node):
    return str(node.runtime_id or node.name)


def match(text, keyword, mode="CONTAINS", case_sensitive=False):
    """Literal Unicode comparison; empty inputs never select Matched."""
    if not text or not keyword:
        return False
    text = unicodedata.normalize("NFC", text)
    keyword = unicodedata.normalize("NFC", keyword)
    if not case_sensitive:
        text, keyword = text.casefold(), keyword.casefold()
    if mode == "CONTAINS":
        return keyword in text
    if mode == "EXACT":
        return text == keyword
    if mode == "STARTS_WITH":
        return text.startswith(keyword)
    if mode == "ENDS_WITH":
        return text.endswith(keyword)
    raise ValueError("Unknown keyword match mode")


def _sample(node, context):
    text = str(context.input(node, "Text", node.inputs["Text"].default_value) or "")
    keyword = str(context.input(node, "Keyword", node.inputs["Keyword"].default_value) or "")
    return {"text": text, "keyword": keyword,
            "result": match(text, keyword, node.mode, node.case_sensitive)}


def _snapshot(node, context):
    uid = _uid(node)
    payload = getattr(context, "payload", {})
    values = payload.get(SECTION, {}) if isinstance(payload, dict) else {}
    found = values.get(uid) if isinstance(values, dict) else None
    if isinstance(found, dict):
        return found
    from . import workflow
    run_id = getattr(context, "run_id", None)
    seen = set()
    while run_id and run_id not in seen:
        seen.add(run_id)
        record = workflow.status(run_id)
        if record is None:
            break
        payload = record["payload"]
        values = payload.get(SECTION, {}) if isinstance(payload, dict) else {}
        found = values.get(uid) if isinstance(values, dict) else None
        if isinstance(found, dict):
            return found
        run_id = record.get("parent")
    return None


def evaluate(context, node):
    if node.bl_idname != NODE:
        return None
    from .runtime import _output
    value = {"text": "", "keyword": "", "result": False} if node.mute else _snapshot(node, context)
    if value is None:
        value = _sample(node, context)
    return {**_output(node, "Result", bool(value["result"])),
            **_output(node, "Text", value["text"]),
            **_output(node, "Keyword", value["keyword"])}


def receive(node, context):
    """Capture one Enter result, including its original text and keyword."""
    value = _sample(node, context)
    uid = _uid(node)
    run_id = getattr(context, "run_id", None)
    if run_id:
        from . import workflow
        workflow.remember_output(run_id, SECTION, uid, value)
    payload = getattr(context, "payload", None)
    if isinstance(payload, dict):
        values = payload.setdefault(SECTION, {})
        if not isinstance(values, dict):
            raise ValueError("Keyword output snapshots must be a dictionary")
        values[uid] = dict(value)
    if hasattr(context, "cache"):
        context.cache.clear()
    summary = "Matched" if value["result"] else "Not Matched"
    if node.last_summary != summary:
        node.last_summary = summary
    from .runtime import _set_monitor
    _set_monitor(node, [summary, f"Keyword: {value['keyword']}", f"Text: {value['text']}"])
    return value["result"]
