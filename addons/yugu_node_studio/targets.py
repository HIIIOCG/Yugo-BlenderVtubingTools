"""Explicit native targets shared by VRM cache, value nodes and Apply nodes."""
from dataclasses import dataclass
import bpy
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase

ARMATURES = "YUGUS_NS_armatures"
OBJECTS = "YUGUS_NS_objects"


@dataclass(frozen=True)
class Armatures:
    names: tuple = ()


@dataclass(frozen=True)
class Objects:
    names: tuple = ()


def armature_poll(self, obj):
    return obj.type == "ARMATURE"


def shape_keys(obj):
    return getattr(getattr(getattr(obj, "data", None), "shape_keys", None), "key_blocks", None)


class YUGUS_NS_armatures(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = ARMATURES
    bl_label = "Armatures"
    def draw(self, context, layout, node, text):
        column = layout.column(align=True)
        column.label(text=text or self.name)
        if not self.is_output and hasattr(node, "armature"):
            picker = column.column(align=True)
            picker.enabled = not self.is_linked
            picker.prop(node, "armature", text="")
            if node.bl_idname == "YUGUS_NODE_apply_rotation" and not node.armature and not self.is_linked:
                picker.label(text="Empty: target from Bone Map")
    def draw_color(self, context, node):
        return (0.35, 0.65, 0.8, 1)


class YUGUS_NS_objects(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = OBJECTS
    bl_label = "Objects List"
    def draw(self, context, layout, node, text):
        column = layout.column(align=True)
        column.label(text="Objects List")
        if not self.is_output and hasattr(node, "objects"):
            picker = column.column(align=True)
            picker.enabled = not self.is_linked
            draw_objects(node, picker)
            if not node.objects and not self.is_linked:
                picker.label(text="Empty: targets from expression mapping")
    def draw_color(self, context, node):
        return (0.5, 0.5, 0.5, 1)


class YUGUS_PG_target_object(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(name="Object", type=bpy.types.Object)


class ObjectSelection:
    objects: bpy.props.CollectionProperty(type=YUGUS_PG_target_object)


def migrate_legacy_collection(node):
    """Keep old saved selections as explicit objects, without live membership."""
    legacy = node.get("collection")
    if isinstance(legacy, bpy.types.Collection):
        existing = {entry.object for entry in node.objects if entry.object}
        for obj in legacy.all_objects:
            if obj not in existing:
                node.objects.add().object = obj
                existing.add(obj)
        del node["collection"]


def selected_objects(node):
    result = [entry.object for entry in node.objects if entry.object]
    return Objects(tuple(dict.fromkeys(obj.name for obj in result)))


def draw_objects(node, layout):
    for index, item in enumerate(node.objects):
        row = layout.row(align=True)
        row.prop(item, "object", text="")
        op = row.operator("yugu_node_studio.target_object", text="", icon="REMOVE")
        op.tree_name, op.node_name, op.index = node.id_data.name, node.name, index
    op = layout.operator("yugu_node_studio.target_object", text="Add Object", icon="ADD")
    op.tree_name, op.node_name, op.index = node.id_data.name, node.name, -1


class YUGUS_OT_target_object(bpy.types.Operator):
    bl_idname = "yugu_node_studio.target_object"
    bl_label = "Edit Target Objects"
    bl_options = {"UNDO"}
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    index: bpy.props.IntProperty(default=-1)
    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, "objects"):
            return {"CANCELLED"}
        if self.index < 0:
            node.objects.add()
        elif self.index < len(node.objects):
            node.objects.remove(self.index)
        return {"FINISHED"}


class YUGUS_NODE_armature_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_armature_value"
    bl_label = "Armature"
    bl_icon = "ARMATURE_DATA"
    armature: bpy.props.PointerProperty(name="Armature", type=bpy.types.Object, poll=armature_poll)
    def init(self, context):
        self._init_base(210)
        self.outputs.new(ARMATURES, "Armatures")
    def draw_buttons(self, context, layout):
        layout.prop(self, "armature", text="")


class YUGUS_NODE_objects_value(ObjectSelection, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_objects_value"
    bl_label = "Objects List"
    bl_icon = "OUTLINER_COLLECTION"
    def init(self, context):
        self._init_base(250)
        self.outputs.new(OBJECTS, "Objects")
    def draw_buttons(self, context, layout):
        draw_objects(self, layout)


def character_objects(armature):
    """Reload-only membership scan: descendants and armature-modified objects."""
    found = set()
    for obj in bpy.data.objects:
        if obj == armature:
            continue
        parent = obj.parent
        while parent and parent != armature:
            parent = parent.parent
        if parent == armature or any(mod.type == "ARMATURE" and mod.object == armature for mod in obj.modifiers):
            found.add(obj.name)
    return tuple(sorted(found))


def vrm_armature(node):
    socket = node.inputs.get("Armature")
    if socket and socket.is_linked:
        from .runtime import EvaluationContext
        value = EvaluationContext(node.id_data, bpy.context.scene, 0).input(node, "Armature", Armatures())
        if not isinstance(value, Armatures) or len(value.names) != 1:
            raise ValueError("VRM Data requires exactly one Armature input")
        obj = bpy.data.objects.get(value.names[0])
        if obj is None or obj.type != "ARMATURE":
            raise ValueError("Armature input is unavailable")
        return obj
    return node.armature


CLASSES = (YUGUS_NS_armatures, YUGUS_NS_objects, YUGUS_PG_target_object,
           YUGUS_OT_target_object, YUGUS_NODE_armature_value, YUGUS_NODE_objects_value)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
