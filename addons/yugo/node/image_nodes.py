"""Local image values, pure URL references and a generic image data socket."""
from dataclasses import dataclass

import bpy

from .chat_nodes import TEXT
from .nodes import YUGUS_NodeBase

IMAGE = "YUGUS_NS_image"
NODE = "YUGUS_NODE_image_from_url"
VALUE = "YUGUS_NODE_image_value"


@dataclass(frozen=True)
class UrlImageRef:
    url: str


class YUGUS_NS_image(bpy.types.NodeSocket):
    bl_idname = IMAGE
    bl_label = "Image"
    default_value: bpy.props.PointerProperty(name="Image", type=bpy.types.Image)

    def draw(self, _context, layout, _node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name)

    def draw_color(self, _context, _node):
        return (0.5, 0.5, 0.5, 1.0)


class YUGUS_NODE_image_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = VALUE
    bl_label = "Image"
    bl_icon = "IMAGE_DATA"
    bl_description = "Select or open a Blender image and output it as Image data"
    image: bpy.props.PointerProperty(name="Image", type=bpy.types.Image)

    def init(self, _context):
        self._init_base(230)
        self.outputs.new(IMAGE, "Image")

    def draw_buttons(self, _context, layout):
        layout.template_ID(self, "image", open="image.open")


class YUGUS_OT_image_url_retry(bpy.types.Operator):
    bl_idname = "yugu_node_studio.image_url_retry"
    bl_label = "Retry Image"
    bl_description = "Retry downloading the sampled HTTP or HTTPS image"
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != NODE or node.mute:
            self.report({"ERROR"}, "Image From URL is unavailable")
            return {"CANCELLED"}
        from . import image_runtime
        image_runtime.retry_node(node, context.scene)
        return {"FINISHED"}


class YUGUS_NODE_image_from_url(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = NODE
    bl_label = "Image From URL"
    bl_icon = "IMAGE_DATA"
    bl_description = "Sample an image URL; connected consumers request its download"
    image_status: bpy.props.StringProperty(default="Waiting for a consumer", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(250)
        self.inputs.new(TEXT, "URL")
        self.outputs.new(IMAGE, "Image")

    def draw_buttons(self, _context, layout):
        layout.label(text=self.image_status)
        op = layout.operator("yugu_node_studio.image_url_retry", text="Retry", icon="FILE_REFRESH")
        op.tree_name, op.node_name = self.id_data.name, self.name


SOCKET_CLASSES = (YUGUS_NS_image,)
NODE_CLASSES = (YUGUS_NODE_image_value, YUGUS_NODE_image_from_url)
OPERATOR_CLASSES = (YUGUS_OT_image_url_retry,)
CLASSES = SOCKET_CLASSES + NODE_CLASSES + OPERATOR_CLASSES
