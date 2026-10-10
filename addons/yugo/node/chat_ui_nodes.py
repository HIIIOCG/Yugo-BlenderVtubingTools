"""Generic scene references and a platform-independent chat list declaration.

Node drawing and data declarations do not generate messages. Runtime operations
are delegated only by explicit controls or the flow dispatcher's Enter/Reset.
"""
from dataclasses import dataclass

import bpy

from .chat_nodes import EVENT, TEXT
from .nodes import YUGUS_NodeBase
from .value_nodes import FLOAT


NODE = "YUGUS_NODE_chat_list"
OBJECT = "YUGUS_NS_object"
COLLECTION = "YUGUS_NS_collection"
OBJECT_VALUE = "YUGUS_NODE_object_value"
COLLECTION_VALUE = "YUGUS_NODE_collection_value"
IMAGE_SOCKET = "YUGUS_NS_image"


@dataclass(frozen=True)
class ObjectRef:
    name: str
    token: str = ""


@dataclass(frozen=True)
class CollectionRef:
    name: str
    token: str = ""


class YUGUS_NS_object(bpy.types.NodeSocket):
    bl_idname = OBJECT
    bl_label = "Object"
    default_value: bpy.props.PointerProperty(name="Object", type=bpy.types.Object)

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NS_collection(bpy.types.NodeSocket):
    bl_idname = COLLECTION
    bl_label = "Collection"
    default_value: bpy.props.PointerProperty(name="Collection", type=bpy.types.Collection)

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NODE_object_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = OBJECT_VALUE
    bl_label = "Object"
    bl_icon = "OBJECT_DATA"
    object: bpy.props.PointerProperty(name="Object", type=bpy.types.Object)

    def init(self, _context):
        self._init_base(220)
        self.outputs.new(OBJECT, "Object")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "object", text="")


class YUGUS_NODE_collection_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = COLLECTION_VALUE
    bl_label = "Collection"
    bl_icon = "OUTLINER_COLLECTION"
    collection: bpy.props.PointerProperty(name="Collection", type=bpy.types.Collection)

    def init(self, _context):
        self._init_base(220)
        self.outputs.new(COLLECTION, "Collection")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "collection", text="")


def _font_poll(_self, obj):
    return obj is not None and obj.type == "FONT"


def _mesh_poll(_self, obj):
    return obj is not None and obj.type == "MESH"


def _style_hidden_changed(node, context):
    from .availability import is_available
    if not is_available(node):
        return
    # A node may not have its inputs yet during creation/copy or RNA setup.
    if context is None:
        return
    try:
        style = node.inputs.get("Style")
        if style is None or (not style.is_linked and style.default_value is None):
            return
    except (AttributeError, ReferenceError):
        return
    from . import chat_ui_assets
    chat_ui_assets.set_style_hidden(node, context)


class YUGUS_OT_chat_list_control(bpy.types.Operator):
    bl_idname = "yugu_node_studio.chat_list_control"
    bl_label = "Chat List Control"
    bl_description = "Import Floating UI, test a message, clear messages, or refresh the chat style"
    bl_options = {"UNDO"}
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    action: bpy.props.EnumProperty(
        items=(
            ("IMPORT", "Import Floating UI", "Import the built-in Floating UI collection and select its style objects"),
            ("TEST", "Test Message", "Create one sample message"),
            ("CLEAR", "Clear", "Remove this Chat List's generated messages"),
            ("REFRESH", "Refresh Style", "Update generated messages from the style"),
        ),
        default="TEST",
    )

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != NODE:
            self.report({"ERROR"}, "Chat List is unavailable")
            return {"CANCELLED"}
        try:
            from . import chat_ui_runtime
            chat_ui_runtime.control(node, self.action, context)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_NODE_chat_list(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Chat List"
    bl_icon = "OUTLINER_COLLECTION"
    bl_description = "Create a styled message on Enter and arrange messages as a scrolling list"
    max_messages: bpy.props.IntProperty(name="Max Messages", default=5, min=1, max=500)
    gap: bpy.props.FloatProperty(name="Gap", default=0.05, min=0.0)
    scroll_seconds: bpy.props.FloatProperty(name="Scroll Seconds", default=0.25, min=0.0)
    direction: bpy.props.EnumProperty(
        name="Direction",
        items=(
            ("Y_NEG", "Down (-Y)", "Place successive messages toward negative Y"),
            ("Y_POS", "Up (+Y)", "Place successive messages toward positive Y"),
            ("X_NEG", "Left (-X)", "Place successive messages toward negative X"),
            ("X_POS", "Right (+X)", "Place successive messages toward positive X"),
            ("Z_NEG", "Back (-Z)", "Place successive messages toward negative Z"),
            ("Z_POS", "Forward (+Z)", "Place successive messages toward positive Z"),
        ),
        default="Y_NEG",
    )
    text_width: bpy.props.FloatProperty(
        name="Text Width", default=100.0, min=0.0,
        description="Blender text box width; zero preserves the template text box",
    )
    name_width: bpy.props.FloatProperty(
        name="User Name Width", default=10.0, min=0.0,
        description="Maximum user name width; zero preserves the template",
    )
    fit_panel: bpy.props.BoolProperty(
        name="Fit Panel", default=True,
        description="Extend the message panel downward to fit its text",
    )
    panel_object: bpy.props.PointerProperty(name="Panel Object", type=bpy.types.Object, poll=_mesh_poll)
    sync_style: bpy.props.BoolProperty(name="Sync Style", default=True)
    text_object: bpy.props.PointerProperty(name="Text Object", type=bpy.types.Object, poll=_font_poll)
    name_object: bpy.props.PointerProperty(name="User Name Object", type=bpy.types.Object, poll=_font_poll)
    icon_object: bpy.props.PointerProperty(name="Avatar Object", type=bpy.types.Object, poll=_mesh_poll)
    icon_material: bpy.props.PointerProperty(name="Avatar Material", type=bpy.types.Material)
    hide_style: bpy.props.BoolProperty(name="Hide Style", default=True, update=_style_hidden_changed)
    show_settings: bpy.props.BoolProperty(name="Settings", default=False)
    last_summary: bpy.props.StringProperty(default="Ready", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(320)
        for name in ("Enter", "Reset"):
            self.inputs.new(EVENT, name).link_limit = 4095
        self.inputs.new(TEXT, "Text")
        self.inputs.new(TEXT, "User Name")
        self.inputs.new(IMAGE_SOCKET, "Avatar")
        style = self.inputs.new(COLLECTION, "Style")
        parent = self.inputs.new(OBJECT, "Parent")
        self.outputs.new(EVENT, "End")
        self.outputs.new(OBJECT, "Message Empty")
        self.outputs.new(FLOAT, "Count")
        style.default_value = bpy.data.collections.get("UI_Style")
        parent.default_value = bpy.data.objects.get("UI_Transform")
        text = bpy.data.objects.get("ChatText")
        author = bpy.data.objects.get("UserName")
        panel = bpy.data.objects.get("ChatPanel")
        icon = bpy.data.objects.get("UserIcon")
        self.text_object = text if _font_poll(self, text) else None
        self.name_object = author if _font_poll(self, author) else None
        self.panel_object = panel if _mesh_poll(self, panel) else None
        self.icon_object = icon if _mesh_poll(self, icon) else None
        self.icon_material = bpy.data.materials.get("UserPanel")

    def draw_buttons(self, _context, layout):
        row = layout.row(align=True)
        operator = row.operator(YUGUS_OT_chat_list_control.bl_idname, text="Import Floating UI", icon="IMPORT")
        operator.tree_name, operator.node_name, operator.action = self.id_data.name, self.name, "IMPORT"
        row.prop(self, "hide_style", text="Hide Style", toggle=True,
                 icon="HIDE_ON" if self.hide_style else "HIDE_OFF")
        row = layout.row(align=True)
        for action, title in (("TEST", "Test Message"), ("CLEAR", "Clear"), ("REFRESH", "Refresh Style")):
            operator = row.operator(YUGUS_OT_chat_list_control.bl_idname, text=title)
            operator.tree_name, operator.node_name, operator.action = self.id_data.name, self.name, action
        layout.label(text=self.last_summary)
        layout.prop(self, "show_settings", text="Settings", emboss=False,
                    icon="TRIA_DOWN" if self.show_settings else "TRIA_RIGHT")
        if self.show_settings:
            column = layout.column(align=True)
            for name in ("max_messages", "gap", "scroll_seconds", "direction", "text_width",
                         "name_width", "fit_panel", "panel_object", "sync_style"):
                column.prop(self, name)
            column.prop(self, "text_object")
            column.prop(self, "name_object")
            column.prop(self, "icon_object")
            column.prop(self, "icon_material")
        self._draw_monitor(layout)

    def copy(self, source):
        YUGUS_NodeBase.copy(self, source)
        self.last_summary = "Ready"
        self.monitor_text = "Waiting for data"
        from . import chat_ui_runtime
        copied = getattr(chat_ui_runtime, "copied", None)
        if copied is not None:
            copied(self, source)

    def free(self):
        from . import chat_ui_runtime
        chat_ui_runtime.forget(self)
        YUGUS_NodeBase.free(self)


SOCKET_CLASSES = (YUGUS_NS_object, YUGUS_NS_collection)
NODE_CLASSES = (YUGUS_NODE_object_value, YUGUS_NODE_collection_value, YUGUS_NODE_chat_list)
OPERATOR_CLASSES = (YUGUS_OT_chat_list_control,)
CLASSES = SOCKET_CLASSES + NODE_CLASSES + OPERATOR_CLASSES
