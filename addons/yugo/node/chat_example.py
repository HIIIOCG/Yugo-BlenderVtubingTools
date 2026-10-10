"""A ready-to-connect chat graph; creating it never starts network access."""
import bpy

from .constants import TREE_ID


def build_example(name="YouTube Chat Example"):
    tree = bpy.data.node_groups.new(name, TREE_ID)
    source = tree.nodes.new("YUGUS_NODE_yt_chat_source")
    source.location = (-660, 80)
    source.label = "1. Live URL + API Key, then Connect"
    source.monitor_open = True
    for index, (mode, label) in enumerate((("MESSAGE", "Message"),
                                           ("SUPER_CHAT", "Super Chat"),
                                           ("NEW_MEMBER", "New Member / Upgrade"))):
        event = tree.nodes.new("YUGUS_NODE_yt_chat_event")
        event.location = (-190 + index * 580, 200)
        event.mode = mode
        event.label = label
        debug = tree.nodes.new("YUGUS_NODE_chat_debug")
        debug.location = (100 + index * 580, 200)
        debug.label = label + " preview"
        debug.monitor_open = True
        tree.links.new(source.outputs["Chat"], event.inputs["Chat"])
        tree.links.new(source.outputs["Trigger"], event.inputs["Enter"])
        tree.links.new(event.outputs["End"], debug.inputs["Enter"])
        tree.links.new(event.outputs["Text"], debug.inputs["Text"])
        tree.links.new(event.outputs["Author Name"], debug.inputs["Author Name"])
        if mode == "SUPER_CHAT":
            receiver = tree.nodes.new("YUGUS_NODE_float_receiver")
            receiver.location = (100 + index * 580, -60)
            receiver.label = "Amount: right-click Received Value"
            tree.links.new(event.outputs["End"], receiver.inputs["Enter"])
            tree.links.new(event.outputs["Amount"], receiver.inputs["Value"])

    all_events = tree.nodes.new("YUGUS_NODE_yt_chat_event")
    all_events.location = (-190, -540)
    all_events.mode = "ALL"
    all_events.label = "ALL: display every supported chat event"
    chat_list = tree.nodes.new("YUGUS_NODE_chat_list")
    chat_list.location = (440, -540)
    chat_list.label = "Floating UI: TEST works without a live connection"
    chat_list.inputs["Style"].default_value = bpy.data.collections.get("UI_Style")
    chat_list.inputs["Parent"].default_value = bpy.data.objects.get("UI_Transform")
    chat_list.max_messages = 5
    chat_list.scroll_seconds = 0.25
    tree.links.new(source.outputs["Chat"], all_events.inputs["Chat"])
    tree.links.new(source.outputs["Trigger"], all_events.inputs["Enter"])
    tree.links.new(all_events.outputs["End"], chat_list.inputs["Enter"])
    tree.links.new(all_events.outputs["Text"], chat_list.inputs["Text"])
    tree.links.new(all_events.outputs["Author Name"], chat_list.inputs["User Name"])
    image = tree.nodes.new("YUGUS_NODE_image_from_url")
    image.location = (100, -900)
    image.label = "Avatar URL: one image per message"
    tree.links.new(all_events.outputs["Avatar URL"], image.inputs["URL"])
    tree.links.new(image.outputs["Image"], chat_list.inputs["Avatar"])
    keyword = tree.nodes.new("YUGUS_NODE_keyword_match")
    keyword.location = (100, -1260)
    keyword.label = "Contains 你好: matched messages only"
    keyword.inputs["Keyword"].default_value = "你好"
    keyword.monitor_open = True
    keyword_debug = tree.nodes.new("YUGUS_NODE_chat_debug")
    keyword_debug.location = (440, -1260)
    keyword_debug.label = "Keyword match preview"
    keyword_debug.monitor_open = True
    tree.links.new(all_events.outputs["End"], keyword.inputs["Enter"])
    tree.links.new(all_events.outputs["Text"], keyword.inputs["Text"])
    tree.links.new(keyword.outputs["Matched"], keyword_debug.inputs["Enter"])
    tree.links.new(keyword.outputs["Text"], keyword_debug.inputs["Text"])
    tree.links.new(all_events.outputs["Author Name"], keyword_debug.inputs["Author Name"])
    return tree


class YUGUS_OT_chat_example(bpy.types.Operator):
    bl_idname = "yugu_node_studio.chat_example"
    bl_label = "Create YouTube Chat Example"
    bl_description = "Create chat previews, a Float Receiver, an avatar Chat List and a Keyword Match branch"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = build_example()
        context.scene.yugu_node_studio_tree = tree
        space = getattr(context, "space_data", None)
        if getattr(space, "type", "") == "NODE_EDITOR":
            space.tree_type = TREE_ID
            space.node_tree = tree
        self.report({"INFO"}, "Set Live URL and API Key, then Connect; use Chat List TEST for an offline preview")
        return {"FINISHED"}


CLASSES = (YUGUS_OT_chat_example,)
