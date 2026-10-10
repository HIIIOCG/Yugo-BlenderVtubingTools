"""Pull-driven chat source and event nodes. Existing node IDs stay compatible."""
import bpy

from .value_nodes import FLOAT
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase

EVENT = "YUGUS_NS_update_event"
CHAT = "YUGUS_NS_chat_data"
TEXT = "YUGUS_NS_text"
SECRET = "YUGUS_NS_chat_api_key"
SOURCE = "YUGUS_NODE_yt_chat_source"
CHAT_EVENT = "YUGUS_NODE_yt_chat_event"
DEBUG = "YUGUS_NODE_chat_debug"
ACTION_TYPES = {CHAT_EVENT, DEBUG}

MODES = (
    ("MESSAGE", "Message", "Ordinary live chat text"),
    ("SUPER_CHAT", "Super Chat", "Paid Super Chat messages"),
    ("SUPER_STICKER", "Super Sticker", "Paid Super Stickers"),
    ("NEW_MEMBER", "New Member / Upgrade", "Paid membership joins and upgrades"),
    ("MEMBER_MILESTONE", "Member Milestone", "Membership milestone messages"),
    ("MEMBERSHIP_GIFT", "Membership Gift", "Purchases of gifted memberships"),
    ("GIFT_RECEIVED", "Gift Membership Received", "Viewers receiving gifted memberships"),
    ("ALL", "All Supported Events", "All supported message and membership types"),
)
SUPPORTED_TYPES = {item[0] for item in MODES if item[0] != "ALL"}
STRING_FIELDS = (
    ("Text", "text"), ("Author Name", "author"), ("Author ID", "author_id"),
    ("Message ID", "message_id"), ("Time", "timestamp"),
    ("Avatar URL", "avatar_url"),
    ("Currency", "currency"), ("Amount Display", "amount_display"),
    ("Member Level", "member_level"),
)
NUMBER_FIELDS = (("Amount", "amount"), ("Months", "member_months"), ("Gift Count", "gift_count"))


def _configuration_changed(node, _context):
    from . import chat_runtime
    chat_runtime.configuration_changed(node)


class YUGUS_NS_chat_data(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = CHAT
    bl_label = "Chat"

    def draw_color(self, _context, _node):
        return (0.18, 0.67, 0.75, 1.0)


class YUGUS_NS_text(bpy.types.NodeSocket):
    bl_idname = TEXT
    bl_label = "Text"
    default_value: bpy.props.StringProperty(name="Text", default="")

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        elif _node.bl_idname == SOURCE and self.name == "Live URL":
            layout.label(text=text or self.name)
            layout.prop(_node, "video", text="")
        elif _node.bl_idname == "YUGUS_NODE_meta_value":
            from .nodes import META_TEXT_FIELDS
            property_name = next(prop for name, prop, _key in META_TEXT_FIELDS if name == self.name)
            layout.prop(_node, property_name, text=text or self.name)
        elif _node.bl_idname == "YUGUS_NODE_expression_smooth" and self.name == "Names":
            layout.prop(_node, "names", text=text or self.name)
        else:
            from .nodes import source_connection_property
            property_name = source_connection_property(_node, self.name)
            if property_name:
                layout.prop(_node, property_name, text=text or self.name)
            else:
                layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.38, 0.68, 0.42, 1.0)


class YUGUS_NS_chat_api_key(bpy.types.NodeSocket):
    bl_idname = SECRET
    bl_label = "API Key"
    default_value: bpy.props.StringProperty(name="API Key", subtype="PASSWORD", options={"SKIP_SAVE"})

    def draw(self, _context, layout, node, text):
        layout.label(text=text or self.name)
        if not self.is_output and not self.is_linked:
            layout.prop(node, "api_key", text="")

    def draw_color(self, _context, _node):
        return (0.38, 0.68, 0.42, 1.0)


class YUGUS_ChatPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    youtube_api_key: bpy.props.StringProperty(
        name="YouTube API Key", subtype="PASSWORD", default="",
        description="YouTube Data API v3 key; stored in Blender preferences, never in the .blend",
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "youtube_api_key")
        layout.label(text="Enable YouTube Data API v3 for this key.")


class YUGUS_OT_yt_api_key(bpy.types.Operator):
    bl_idname = "yugu_node_studio.yt_api_key"
    bl_label = "YouTube API Key"
    api_key: bpy.props.StringProperty(name="API Key", subtype="PASSWORD", options={"SKIP_SAVE"})

    def invoke(self, context, _event):
        from . import chat_runtime
        preferences = chat_runtime.preferences(context)
        if preferences is None:
            self.report({"ERROR"}, "Enable Yugo Node through Blender Extensions first")
            return {"CANCELLED"}
        self.api_key = preferences.youtube_api_key
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "api_key")
        layout.label(text="Enable YouTube Data API v3 for this key.")

    def execute(self, context):
        from . import chat_runtime
        preferences = chat_runtime.preferences(context)
        if preferences is None:
            return {"CANCELLED"}
        preferences.youtube_api_key = self.api_key.strip()
        self.api_key = ""
        return {"FINISHED"}


class YUGUS_OT_yt_chat_connection(bpy.types.Operator):
    bl_idname = "yugu_node_studio.yt_chat_connection"
    bl_label = "YouTube Chat Connection"
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    disconnect: bpy.props.BoolProperty(default=False)

    def execute(self, _context):
        from . import chat_runtime
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != SOURCE:
            return {"CANCELLED"}
        try:
            if self.disconnect:
                chat_runtime.disconnect(node)
            else:
                chat_runtime.connect(node, retry=True)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_NODE_yt_chat_source(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = SOURCE
    bl_label = "YouTube Chat"
    bl_icon = "URL"
    video: bpy.props.StringProperty(name="Live URL / Video ID", default="", update=_configuration_changed)
    api_key: bpy.props.StringProperty(name="API Key", default="", subtype="PASSWORD", options={"SKIP_SAVE"}, update=_configuration_changed)
    include_history: bpy.props.BoolProperty(name="Include Recent History", default=False, update=_configuration_changed)
    connected: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    last_summary: bpy.props.StringProperty(default="Disconnected", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(285)
        self.inputs.new(TEXT, "Live URL")
        self.inputs.new(SECRET, "API Key")
        self.inputs.new(EVENT, "Connect").link_limit = 4095
        self.outputs.new(EVENT, "Trigger")
        self.outputs.new(CHAT, "Chat")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "include_history")
        row = layout.row(align=True)
        for disconnect, label in ((False, "Connect"), (True, "Disconnect")):
            operator = row.operator(YUGUS_OT_yt_chat_connection.bl_idname, text=label)
            operator.tree_name, operator.node_name = self.id_data.name, self.name
            operator.disconnect = disconnect
        layout.operator(YUGUS_OT_yt_api_key.bl_idname, text="Default API Key", icon="PREFERENCES")
        layout.label(text=self.last_summary)
        self._draw_monitor(layout)

    def copy(self, source):
        super().copy(source)
        self.connected = False
        self.last_summary = "Disconnected"

    def free(self):
        from . import chat_runtime
        chat_runtime.disconnect(self)
        super().free()


class YUGUS_NODE_yt_chat_event(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = CHAT_EVENT
    bl_label = "YT Chat Event"
    bl_icon = "OUTLINER_DATA_FONT"
    mode: bpy.props.EnumProperty(name="Event", items=MODES, default="MESSAGE", update=_configuration_changed)
    batch_limit: bpy.props.IntProperty(name="Max Messages / Enter", default=64, min=1, max=256)
    received_count: bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    missed_count: bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    last_summary: bpy.props.StringProperty(default="Connect Chat and Enter", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(265)
        self.inputs.new(EVENT, "Enter").link_limit = 4095
        self.inputs.new(CHAT, "Chat")
        self.outputs.new(EVENT, "End")
        for name, _field in STRING_FIELDS:
            self.outputs.new(TEXT, name)
        for name, _field in NUMBER_FIELDS:
            self.outputs.new(FLOAT, name)
        _configuration_changed(self, None)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "mode", text="")
        layout.prop(self, "batch_limit")
        layout.label(text=self.last_summary)
        self._draw_monitor(layout)

    def copy(self, source):
        super().copy(source)
        self.received_count = self.missed_count = 0
        self.last_summary = "Connect Chat and Enter"

    def free(self):
        from . import chat_runtime
        chat_runtime.remove_listener(str(self.runtime_id))
        super().free()


class YUGUS_NODE_chat_debug(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = DEBUG
    bl_label = "Chat Debug"
    bl_icon = "CONSOLE"
    count: bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    last_summary: bpy.props.StringProperty(default="Connect Enter + Text", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(265)
        self.inputs.new(EVENT, "Enter").link_limit = 4095
        self.inputs.new(TEXT, "Text")
        self.inputs.new(TEXT, "Author Name")
        self.outputs.new(EVENT, "End")

    def draw_buttons(self, _context, layout):
        layout.label(text=f"Received: {self.count}")
        layout.label(text=self.last_summary)
        self._draw_monitor(layout)


CLASSES = (YUGUS_NS_chat_data, YUGUS_NS_text, YUGUS_NS_chat_api_key, YUGUS_ChatPreferences,
           YUGUS_OT_yt_api_key, YUGUS_OT_yt_chat_connection,
           YUGUS_NODE_yt_chat_source, YUGUS_NODE_yt_chat_event, YUGUS_NODE_chat_debug)


def _replace_socket(node, collection, name, socket_type):
    """Migrate a socket without discarding incoming or outgoing data wires."""
    socket = collection.get(name)
    if socket is not None and socket.bl_idname == socket_type:
        return socket
    incoming = collection == node.inputs
    links = [(link.from_socket, link.to_socket, link.is_muted) for link in socket.links] if socket else []
    index = list(collection).index(socket) if socket is not None else len(collection)
    if socket is not None:
        collection.remove(socket)
    replacement = collection.new(socket_type, name)
    collection.move(len(collection) - 1, index)
    for source, target, muted in links:
        new_link = node.id_data.links.new(source if incoming else replacement,
                                          replacement if incoming else target, verify_limits=False)
        if muted:
            new_link.is_muted = True
    return replacement


def migrate():
    from .constants import TREE_ID
    for tree in getattr(bpy.data, "node_groups", ()):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tuple(tree.nodes):
            if node.bl_idname == SOURCE:
                for name, socket_type in (("Live URL", TEXT), ("API Key", SECRET), ("Connect", EVENT)):
                    _replace_socket(node, node.inputs, name, socket_type)
                node.inputs["Connect"].link_limit = 4095
                _replace_socket(node, node.outputs, "Trigger", EVENT)
                node.outputs.move(list(node.outputs).index(node.outputs["Trigger"]), 0)
            elif node.bl_idname == CHAT_EVENT:
                old = node.outputs.get("Event Type")
                if old is not None:
                    node.outputs.remove(old)
                for name, _field in NUMBER_FIELDS:
                    _replace_socket(node, node.outputs, name, FLOAT)
                from . import chat_runtime
                chat_runtime.configuration_changed(node, reset_cursor=False)
