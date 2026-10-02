"""Studio declarations and lifecycle around the unchanged PR #12 service."""

from dataclasses import dataclass
import time
import uuid

import bpy

from ... import extensions, flow
from ...constants import TREE_ID
from ...nodes import YUGUS_NodeBase
from .model import Message
from .service import FloatingUIService, OWNER, ROLE, load_template

MODULE_ID = "floating_ui"
SOURCE_ID = "YUGUS_NODE_message_source"
DISPLAY_ID = "YUGUS_NODE_floating_ui_display"
STUDIO_TAG = "yugus_floating_ui_generated"
_services = {}
_retiring = False


def _data_available():
    return hasattr(bpy.data, "objects") and hasattr(bpy.data, "node_groups")


def _identity(block):
    return int(block.session_uid) if block is not None else 0


def configuration(node):
    return {
        "template": _identity(node.template), "anchor": _identity(node.anchor),
        "template_path": node.template_path, "collection_name": node.collection_name,
        "spacing": node.spacing, "max_messages": node.max_messages,
        "move_duration": node.move_duration, "text_object": node.text_object,
        "icon_object": node.icon_object, "avatar_node": node.avatar_node,
    }


def _signature(node):
    # Persistent presentation continues after insertion completes. Reconcile its
    # configuration and all wires even when there is no live action ticket.
    wires = []
    for socket in (*node.inputs, *node.outputs):
        for link in socket.links:
            wires.append((
                link.from_node.runtime_id if hasattr(link.from_node, "runtime_id") else link.from_node.name,
                link.from_socket.identifier, bool(link.from_node.mute),
                link.to_node.runtime_id if hasattr(link.to_node, "runtime_id") else link.to_node.name,
                link.to_socket.identifier, bool(link.to_node.mute),
                bool(link.is_valid), bool(getattr(link, "is_muted", False)),
            ))
    return extensions.configuration_signature(node), bool(node.mute), sorted(wires)


@dataclass
class DisplayRuntime:
    owner: str
    tree_uid: int
    node_id: str
    scene_uid: int
    signature: object
    service: FloatingUIService


def _generated_objects(service):
    return (service.panel_root, *(obj for item in service.instances.values()
             for obj in (item.root, *item.objects.values())))


def _mark_generated(service):
    """Add a Studio-only recovery tag; PR #12's generic owner tag stays intact."""
    blocks = [service.output, service.panel_root]
    for item in service.instances.values():
        blocks.extend((item.root, *item.objects.values(), *item.owned))
        pending = [item.collection]
        while pending:
            collection = pending.pop()
            blocks.append(collection)
            pending.extend(collection.children)
    for block in blocks:
        block[STUDIO_TAG] = service.owner
    for image in bpy.data.images:
        if image.get(OWNER) == service.owner:
            image[STUDIO_TAG] = service.owner


def _drop(owner):
    global _retiring
    entry = _services.pop(owner, None)
    extensions.release_owner(owner)
    if entry is None or not _data_available():
        return
    previous = _retiring
    _retiring = True
    try:
        entry.service.cleanup()
    finally:
        _retiring = previous


def _invalidate_node(node):
    if _retiring:
        return
    try:
        tree_uid, node_id = _identity(node.id_data), node.runtime_id
    except ReferenceError:
        return
    for owner, entry in tuple(_services.items()):
        if entry.tree_uid == tree_uid and entry.node_id == node_id:
            _drop(owner)


def _settings_changed(node, _context):
    extensions.cancel_node(node, "Floating UI settings changed")
    _invalidate_node(node)


def _reconcile():
    if _retiring or not _services or not _data_available():
        return
    nodes = {(_identity(tree), node.runtime_id): node
             for tree in bpy.data.node_groups if tree.bl_idname == TREE_ID
             for node in tree.nodes if node.bl_idname == DISPLAY_ID}
    scenes = {_identity(scene) for scene in bpy.data.scenes}
    for owner, entry in tuple(_services.items()):
        node = nodes.get((entry.tree_uid, entry.node_id))
        try:
            invalid = (node is None or entry.scene_uid not in scenes or node.mute
                       or _signature(node) != entry.signature or not entry.service.is_valid())
        except ReferenceError:
            invalid = True
        if invalid:
            _drop(owner)


def get_service(node, scene):
    """Return an existing scene-specific display without creating or mutating it."""
    owner = extensions.node_owner(MODULE_ID, node, scene)
    entry = _services.get(owner)
    return entry.service if entry is not None else None


def _entry(node, scene):
    _reconcile()
    owner = extensions.node_owner(MODULE_ID, node, scene)
    entry = _services.get(owner)
    if entry is None:
        if node.mute or node.template is None:
            raise ValueError("Choose an active display and a template Collection")
        service = FloatingUIService(
            scene, node.template, anchor=node.anchor, spacing=node.spacing,
            max_messages=node.max_messages, move_duration=node.move_duration,
            text_object=node.text_object, icon_object=node.icon_object,
            avatar_node=node.avatar_node,
        )
        entry = DisplayRuntime(owner, _identity(node.id_data), node.runtime_id,
                               _identity(scene), _signature(node), service)
        _services[owner] = entry
        try:
            _mark_generated(service)
            extensions.claim_transforms(owner, _generated_objects(service))
        except Exception:
            _drop(owner)
            raise
    return entry


def start(context):
    node = context.node
    if context.input_name == "Clear":
        _drop(extensions.node_owner(MODULE_ID, node, context.scene))
        return extensions.ActionResult.end()
    # Validate before creating any generated output. The service performs its own
    # validation too, preserving the original immutable snapshot contract.
    Message.from_payload(context.payload)
    entry = _entry(node, context.scene)
    try:
        entry.service.push(context.payload, now=max(entry.service.last_now, context.now))
        _mark_generated(entry.service)
        extensions.claim_transforms(entry.owner, _generated_objects(entry.service))
    except Exception:
        # The original service rolls back an invalid new row. Retain a valid
        # existing queue so a bad avatar file cannot erase earlier messages.
        if not entry.service.instances or not entry.service.is_valid():
            _drop(entry.owner)
        raise
    return extensions.ActionResult.end()


def motion(_context, _delta_time):
    _reconcile()
    now = time.monotonic()
    for owner, entry in tuple(_services.items()):
        try:
            # Claim before writing; eviction/manual deletion is pruned by core.
            extensions.claim_transforms(owner, _generated_objects(entry.service))
            entry.service.update(now=max(entry.service.last_now, now))
        except (ValueError, RuntimeError, ReferenceError):
            _drop(owner)


def cleanup(_reason):
    for owner in tuple(_services):
        _drop(owner)


def recover(_reason):
    if not _data_available():
        return
    # Do not call PR #12's broad cleanup_saved_outputs: a standalone preview may
    # coexist with Studio. Only Studio-tagged generated data belongs here.
    for obj in tuple(bpy.data.objects):
        if obj.get(STUDIO_TAG) and obj.get(STUDIO_TAG) == obj.get(OWNER):
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        if collection.get(STUDIO_TAG) and collection.get(STUDIO_TAG) == collection.get(OWNER):
            bpy.data.collections.remove(collection)
    for database in (bpy.data.curves, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        # A geometry datablock can keep copied materials alive until removed.
        for block in tuple(database):
            if (block.get(STUDIO_TAG) and block.get(STUDIO_TAG) == block.get(OWNER)
                    and block.users == 0):
                database.remove(block)


def _node_button(layout, operator_id, node, label):
    operator = layout.operator(operator_id, text=label)
    operator.tree_name, operator.node_name = node.id_data.name, node.name


class YUGUS_NODE_message_source(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = SOURCE_ID
    bl_label = "Message Source"
    bl_icon = "OUTLINER_DATA_FONT"
    text: bpy.props.StringProperty(name="Message", default="Hello, Studio!")
    author: bpy.props.StringProperty(name="Author", default="Viewer")
    avatar_path: bpy.props.StringProperty(name="Avatar File", subtype="FILE_PATH")
    last_summary: bpy.props.StringProperty(default="Send one event", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(280)
        self.outputs.new(flow.EVENT, "Message")

    def draw_buttons(self, _context, layout):
        for name in ("text", "author", "avatar_path"):
            layout.prop(self, name)
        _node_button(layout, "yugu_node_studio.floating_ui_send", self, "Send Once")
        layout.label(text=self.last_summary)


class YUGUS_NODE_floating_ui_display(extensions.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = DISPLAY_ID
    bl_label = "Floating UI Display"
    bl_icon = "OUTLINER_COLLECTION"
    template: bpy.props.PointerProperty(name="Template", type=bpy.types.Collection, update=_settings_changed)
    anchor: bpy.props.PointerProperty(name="Anchor", type=bpy.types.Object, update=_settings_changed)
    template_path: bpy.props.StringProperty(name="Preset .blend", subtype="FILE_PATH", update=_settings_changed)
    collection_name: bpy.props.StringProperty(name="Collection", default="Floating UI", update=_settings_changed)
    spacing: bpy.props.FloatProperty(name="Spacing", default=1.0, min=0.001, update=_settings_changed)
    max_messages: bpy.props.IntProperty(name="Maximum Messages", default=8, min=1, max=200, update=_settings_changed)
    move_duration: bpy.props.FloatProperty(name="Move Duration", default=.25, min=0, max=60, subtype="TIME", update=_settings_changed)
    text_object: bpy.props.StringProperty(name="Message Object", update=_settings_changed)
    icon_object: bpy.props.StringProperty(name="Icon Object", update=_settings_changed)
    avatar_node: bpy.props.StringProperty(name="Avatar Texture Node", update=_settings_changed)

    def init(self, _context):
        self.init_action()
        self.width = 320
        self.inputs.new(flow.EVENT, "Clear").link_limit = 4095

    def free(self):
        extensions.cancel_node(self, "Floating UI display deleted")
        _invalidate_node(self)
        super().free()

    def draw_buttons(self, context, layout):
        draw_display(layout, self)
        service = get_service(self, context.scene)
        layout.label(text=f"Live Messages: {len(service.instances) if service else 0}")
        _node_button(layout, "yugu_node_studio.floating_ui_clear", self, "Clear Generated Messages")
        layout.label(text=self.last_summary)


def draw_display(layout, node):
    for name in ("template", "anchor", "spacing", "max_messages", "move_duration"):
        layout.prop(node, name)
    column = layout.column(align=True)
    for name in ("template_path", "collection_name"):
        column.prop(node, name)
    _node_button(column, "yugu_node_studio.floating_ui_import", node, "Import Template")
    for name in ("text_object", "icon_object", "avatar_node"):
        column.prop(node, name)


def send_message(source, scene, now=None):
    if source.mute:
        raise ValueError("Message source is muted")
    if not source.text.strip():
        raise ValueError("Enter message text before sending")
    payload = {
        "id": uuid.uuid4().hex, "text": source.text, "author": source.author,
        "avatar_path": bpy.path.abspath(source.avatar_path) if source.avatar_path else "",
    }
    run_id = flow.emit_event(source.id_data, source, "Message", payload, now=now)
    if run_id is None:
        raise ValueError("Studio is suspended; send again after the file operation")
    source.last_summary = "Sent one message event"
    return run_id


class _NodeOperator:
    def resolve(self, expected):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree is not None and tree.bl_idname == TREE_ID else None
        if node is None or node.bl_idname != expected:
            raise ValueError("The Studio node no longer exists")
        return node


class YUGUS_OT_floating_ui_send(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.floating_ui_send"
    bl_label = "Send Studio Floating UI Message Once"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            send_message(self.resolve(SOURCE_ID), context.scene)
        except (ValueError, TypeError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_floating_ui_clear(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.floating_ui_clear"
    bl_label = "Clear Studio Floating UI Messages"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            node = self.resolve(DISPLAY_ID)
            _drop(extensions.node_owner(MODULE_ID, node, context.scene))
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_floating_ui_import(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.floating_ui_import"
    bl_label = "Import Studio Floating UI Template"
    bl_options = {"REGISTER", "UNDO"}
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        try:
            node = self.resolve(DISPLAY_ID)
            node.template = load_template(node.template_path, node.collection_name)
        except (ValueError, RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def make_example_template(name="Floating UI Template"):
    """Build an editable original layout with a generated placeholder avatar."""
    collection = bpy.data.collections.new(name)
    parts = {}

    def material(label, color):
        mat = bpy.data.materials.new(label)
        mat.diffuse_color = (*color, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*color, 1)
        shader.inputs["Emission Color"].default_value = (*color, 1)
        shader.inputs["Emission Strength"].default_value = 1
        return mat

    def rectangle(label, width, height, position, mat):
        mesh = bpy.data.meshes.new(label)
        mesh.from_pydata([(-width/2, -height/2, 0), (width/2, -height/2, 0),
                          (width/2, height/2, 0), (-width/2, height/2, 0)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new()
        for item, coords in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
            item.uv = coords
        mesh.materials.append(mat)
        obj = bpy.data.objects.new(label, mesh)
        collection.objects.link(obj)
        obj.location = position
        return obj

    navy = material("Floating UI Panel", (.03, .06, .1))
    white = material("Floating UI Text", (.88, .94, .98))
    cyan = material("Floating UI Accent", (.2, .8, .85))
    parts["panel"] = rectangle("Message Backplate", 5.4, 1.1, (0, 0, 0), navy)
    parts["icon"] = rectangle("Viewer Icon", .7, .7, (-2.1, 0, .01), cyan)
    parts["icon"][ROLE] = "icon"
    avatar_material = cyan.copy()
    parts["icon"].data.materials[0] = avatar_material
    image = bpy.data.images.new("Floating UI Placeholder Avatar", width=8, height=8)
    image.pixels = [channel for y in range(8) for x in range(8)
                    for channel in ((.2, .8, .85, 1) if (x-3.5)**2+(y-3.5)**2 < 10 else (.06, .15, .2, 1))]
    texture = avatar_material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.name, texture.label, texture.image = "Avatar", "floating_ui_avatar", image
    shader = avatar_material.node_tree.nodes.get("Principled BSDF")
    avatar_material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    avatar_material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Emission Color"])
    for role, label, body, location, size in (
        ("author", "Viewer Name", "VIEWER", (-1.55, .18, .02), .19),
        ("message", "Message Text", "Your message appears here", (-1.55, -.15, .02), .24),
    ):
        data = bpy.data.curves.new(label, "FONT")
        data.body, data.size = body, size
        data.materials.append(cyan if role == "author" else white)
        obj = bpy.data.objects.new(label, data)
        obj[ROLE] = role
        collection.objects.link(obj)
        obj.location = location
        parts[role] = obj
    return collection, parts


def create_example(context):
    tree = bpy.data.node_groups.new("Studio Floating UI Example", TREE_ID)
    tree.use_fake_user = True
    template, _parts = make_example_template()
    anchor = bpy.data.objects.new("Floating UI Anchor", None)
    context.scene.collection.objects.link(anchor)
    source = tree.nodes.new(SOURCE_ID)
    display = tree.nodes.new(DISPLAY_ID)
    source.location, display.location = (-360, 0), (0, 0)
    display.template, display.anchor, display.spacing = template, anchor, 1.3
    tree.links.new(source.outputs["Message"], display.inputs["Enter"])
    context.scene.yugu_node_studio_tree = tree
    if context.area is not None and context.area.type == "NODE_EDITOR":
        context.space_data.tree_type = TREE_ID
        context.space_data.node_tree = tree
    return tree, source, display


class YUGUS_OT_floating_ui_example(bpy.types.Operator):
    bl_idname = "yugu_node_studio.floating_ui_example"
    bl_label = "Create Studio Floating UI Example"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        create_example(context)
        return {"FINISHED"}


def create_spec():
    return extensions.ModuleSpec(
        module_id=MODULE_ID, label="Floating UI",
        classes=(YUGUS_NODE_message_source, YUGUS_NODE_floating_ui_display,
                 YUGUS_OT_floating_ui_send, YUGUS_OT_floating_ui_clear,
                 YUGUS_OT_floating_ui_import, YUGUS_OT_floating_ui_example),
        actions={DISPLAY_ID: extensions.ActionSpec(start, inputs=("Enter", "Clear"), configuration=configuration)},
        menu_entries=((SOURCE_ID, "Message Source"), (DISPLAY_ID, "Floating UI Display")),
        cards={DISPLAY_ID: extensions.CardSpec("Floating UI Display", "Insert one message; the queue keeps animating.", draw_display)},
        motion=motion, cleanup=cleanup, recover=recover,
    )
