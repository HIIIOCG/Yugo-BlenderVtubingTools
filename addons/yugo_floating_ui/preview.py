"""Explicit, finite native-node preview for the isolated Floating UI feature.

No receiver, app timer, production scheduler, or automatic evaluation is added.
Send creates one event; Step or an explicitly started modal preview animates it.
"""

import time
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import FloatProperty, IntProperty, PointerProperty, StringProperty

from .service import FloatingUIService, cleanup_saved_outputs, load_template


TREE_ID = "YUGOFUI_NT_preview"
SOCKET_ID = "YUGOFUI_NS_event"
SOURCE_ID = "YUGOFUI_N_message"
DISPLAY_ID = "YUGOFUI_N_display"

# This registry is local to the feature preview. It is not a Studio core registry.
_services = {}
_sessions = {}
_registered = False
_cleaning = False


def _pointer(value):
    return value.as_pointer() if value is not None else 0


def _key(node, scene):
    return (_pointer(node.id_data), _pointer(node), _pointer(scene))


def _signature(node):
    return (
        _pointer(node.template),
        _pointer(node.anchor),
        node.template_path,
        node.collection_name,
        node.max_messages,
        node.spacing,
        node.move_duration,
        node.text_object,
        node.icon_object,
        node.avatar_node,
        node.mute,
        tuple(sorted(
            (_pointer(link.from_node), link.from_node.mute, link.is_valid,
             getattr(link, "is_muted", False))
            for link in node.inputs[0].links
        )),
    )


class _Runtime:
    def __init__(self, node, scene, service):
        self.service = service
        self.signature = _signature(node)
        self.clock_offset = 0.0

    def now(self):
        return max(self.service.last_now, time.monotonic() + self.clock_offset)

    def step(self, seconds):
        stamp = self.now() + seconds
        self.clock_offset = stamp - time.monotonic()
        self.service.update(now=stamp)


class _PreviewSession:
    """Timer ownership independent of Blender's short-lived Operator RNA."""

    def __init__(self, context, tree_pointer, scene_pointer, seconds):
        self._tree_pointer = tree_pointer
        self._scene_pointer = scene_pointer
        self._manager = context.window_manager
        self._timer = None
        self._finished = False
        self._deadline = time.monotonic() + seconds
        self._next_update = 0.0
        self._session_id = uuid.uuid4().hex
        try:
            self._timer = self._manager.event_timer_add(1.0 / 30.0, window=context.window)
            _sessions[self._session_id] = self
        except Exception:
            self.finish()
            raise

    def finish(self):
        """Idempotently release the timer even if its operator no longer exists."""
        if self._finished:
            return
        self._finished = True
        timer, manager = self._timer, self._manager
        self._timer = None
        self._manager = None
        try:
            if timer is not None and manager is not None:
                manager.event_timer_remove(timer)
        except (AttributeError, TypeError, ReferenceError, RuntimeError):
            # Window/file teardown may already have invalidated the timer RNA.
            pass
        finally:
            _sessions.pop(self._session_id, None)

    def advance(self, context, event):
        if self._finished:
            return {"CANCELLED"}
        if event.type == "ESC":
            self.finish()
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        # Blender Event has no timer identity. Accept timer events and throttle
        # with our monotonic clock when other modal tools also own timers.
        stamp = time.monotonic()
        if stamp < self._next_update and stamp < self._deadline:
            return {"PASS_THROUGH"}
        self._next_update = stamp + 1.0 / 30.0
        _prune_runtime()
        if self._finished:
            return {"CANCELLED"}
        if _pointer(context.scene) != self._scene_pointer or not any(
            key[0] == self._tree_pointer and key[2] == self._scene_pointer
            for key in _services
        ):
            self.finish()
            return {"CANCELLED"}
        for key, entry in list(_services.items()):
            if key[0] == self._tree_pointer and key[2] == self._scene_pointer:
                entry.service.update(now=entry.now())
        if context.area:
            context.area.tag_redraw()
        if stamp >= self._deadline:
            self.finish()
            return {"FINISHED"}
        return {"PASS_THROUGH"}


def _drop(key):
    entry = _services.pop(key, None)
    if entry is not None:
        entry.service.cleanup()


def _stop_sessions(tree_pointer=None):
    for session in list(_sessions.values()):
        if tree_pointer is None or session._tree_pointer == tree_pointer:
            session.finish()


def _invalidate_node(node):
    if _cleaning:
        return
    try:
        tree_pointer, node_pointer = _pointer(node.id_data), _pointer(node)
    except ReferenceError:
        return
    _stop_sessions(tree_pointer)
    for key in list(_services):
        if key[:2] == (tree_pointer, node_pointer):
            _drop(key)


def _settings_changed(node, _context):
    _invalidate_node(node)


def _prune_runtime():
    """Remove stale/muted/reconfigured output without touching other scene data."""
    if _cleaning or not _services:
        return
    live_nodes = {}
    for tree in bpy.data.node_groups:
        if tree.bl_idname == TREE_ID:
            for node in tree.nodes:
                if node.bl_idname == DISPLAY_ID:
                    live_nodes[(_pointer(tree), _pointer(node))] = node
    live_scenes = {_pointer(scene) for scene in bpy.data.scenes}
    for key, entry in list(_services.items()):
        node = live_nodes.get(key[:2])
        if (
            node is None
            or key[2] not in live_scenes
            or node.mute
            or _signature(node) != entry.signature
            or not entry.service.is_valid()
        ):
            _stop_sessions(key[0])
            _drop(key)


def cleanup_preview():
    """Stop all finite previews and release only services owned by this module."""
    global _cleaning
    _cleaning = True
    try:
        _stop_sessions()
        for key in list(_services):
            _drop(key)
    finally:
        _cleaning = False


def _entry(node, scene):
    _prune_runtime()
    if node.mute:
        raise ValueError("Display node is muted")
    if node.template is None:
        raise ValueError("Choose a template collection or import a preset first")
    key = _key(node, scene)
    if key not in _services:
        service = FloatingUIService(
            scene,
            node.template,
            anchor=node.anchor,
            max_messages=node.max_messages,
            spacing=node.spacing,
            move_duration=node.move_duration,
            text_object=node.text_object,
            icon_object=node.icon_object,
            avatar_node=node.avatar_node,
        )
        _services[key] = _Runtime(node, scene, service)
    return _services[key]


def get_service(node, scene):
    """Return this display's scene-specific service, creating it when requested."""
    return _entry(node, scene).service


def send_message(source, scene):
    """Snapshot one source event and submit it once to each linked active display."""
    _prune_runtime()
    if source.mute:
        raise ValueError("Message source is muted")
    if not source.text.strip():
        raise ValueError("Enter message text before sending")
    targets = {}
    for link in source.outputs[0].links:
        if (
            link.to_node.bl_idname == DISPLAY_ID
            and not link.to_node.mute
            and link.is_valid
            and not getattr(link, "is_muted", False)
        ):
            targets[_pointer(link.to_node)] = link.to_node
    if not targets:
        raise ValueError("Link the event output to an active Floating UI Display")
    payload = {
        "id": uuid.uuid4().hex,
        "text": source.text,
        "author": source.author,
        "avatar_path": bpy.path.abspath(source.avatar_path) if source.avatar_path else "",
    }
    # Resolve template bindings for all targets before submitting this event.
    # Each service still owns validation and rollback of its individual clone.
    entries = [_entry(node, scene) for node in targets.values()]
    for entry in entries:
        entry.service.push(dict(payload), now=entry.now())
    return payload["id"], len(entries)


@persistent
def _reset_before_change(*_args):
    cleanup_preview()


@persistent
def _recover_after_change(*_args):
    cleanup_preview()
    cleanup_saved_outputs()


@persistent
def _check_after_update(*_args):
    _prune_runtime()


def _node_button(layout, operator_id, node, text):
    op = layout.operator(operator_id, text=text)
    op.tree_name = node.id_data.name
    op.node_name = node.name
    return op


def _resolve_node(operator, expected_id=None):
    tree = bpy.data.node_groups.get(operator.tree_name)
    node = tree.nodes.get(operator.node_name) if tree and tree.bl_idname == TREE_ID else None
    if node is None or (expected_id and node.bl_idname != expected_id):
        raise ValueError("The preview node no longer exists")
    return node


def _context_tree(context):
    space = context.space_data
    return space.edit_tree if space and space.type == "NODE_EDITOR" else None


class YUGOFUI_NT_preview(bpy.types.NodeTree):
    bl_idname = TREE_ID
    bl_label = "Floating UI Preview"
    bl_icon = "NODETREE"

    @classmethod
    def valid_socket_type(cls, idname):
        return idname == SOCKET_ID

    def update(self):
        _prune_runtime()


class YUGOFUI_NS_event(bpy.types.NodeSocket):
    bl_idname = SOCKET_ID
    bl_label = "Message Event"

    def draw(self, _context, layout, _node, text):
        layout.label(text=text)

    def draw_color(self, _context, _node):
        return (0.85, 0.45, 0.12, 1.0)


class _PreviewNode:
    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE_ID


class YUGOFUI_N_message(_PreviewNode, bpy.types.Node):
    bl_idname = SOURCE_ID
    bl_label = "Message Source"
    bl_icon = "OUTLINER_OB_FONT"

    text: StringProperty(name="Message", default="Hello, Floating UI!")
    author: StringProperty(name="Author", default="Viewer")
    avatar_path: StringProperty(name="Avatar File", subtype="FILE_PATH")

    def init(self, _context):
        self.width = 300
        self.outputs.new(SOCKET_ID, "Message Event")

    def update(self):
        _prune_runtime()

    def free(self):
        try:
            links = tuple(self.outputs[0].links)
        except ReferenceError:
            return
        for link in links:
            if link.to_node.bl_idname == DISPLAY_ID:
                _invalidate_node(link.to_node)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "text")
        layout.prop(self, "author")
        layout.prop(self, "avatar_path")
        row = layout.row()
        row.enabled = not self.mute
        _node_button(row, "yugofui.send", self, "Send Once")


class YUGOFUI_N_display(_PreviewNode, bpy.types.Node):
    bl_idname = DISPLAY_ID
    bl_label = "Floating UI Display"
    bl_icon = "OUTLINER_COLLECTION"

    template: PointerProperty(
        name="Template", type=bpy.types.Collection, update=_settings_changed
    )
    template_path: StringProperty(
        name="Preset .blend", subtype="FILE_PATH", update=_settings_changed
    )
    collection_name: StringProperty(
        name="Collection", default="Floating UI", update=_settings_changed
    )
    anchor: PointerProperty(
        name="Anchor", type=bpy.types.Object, update=_settings_changed
    )
    spacing: FloatProperty(
        name="Spacing", default=1.0, min=0.001, update=_settings_changed
    )
    max_messages: IntProperty(
        name="Maximum Messages", default=8, min=1, max=200, update=_settings_changed
    )
    move_duration: FloatProperty(
        name="Move Duration", default=0.25, min=0.0, max=60.0,
        subtype="TIME", update=_settings_changed
    )
    text_object: StringProperty(name="Message Object", update=_settings_changed)
    icon_object: StringProperty(name="Icon Object", update=_settings_changed)
    avatar_node: StringProperty(name="Avatar Texture Node", update=_settings_changed)

    def init(self, _context):
        self.width = 340
        self.inputs.new(SOCKET_ID, "Message Event")

    def update(self):
        _prune_runtime()

    def free(self):
        _invalidate_node(self)

    def draw_buttons(self, context, layout):
        layout.prop(self, "template")
        column = layout.column(align=True)
        column.prop(self, "template_path")
        column.prop(self, "collection_name")
        _node_button(column, "yugofui.import_template", self, "Import Template")
        layout.prop(self, "anchor")
        layout.prop(self, "spacing")
        layout.prop(self, "max_messages")
        layout.prop(self, "move_duration")
        column = layout.column(align=True)
        column.label(text="Optional template bindings")
        column.prop(self, "text_object")
        column.prop(self, "icon_object")
        column.prop(self, "avatar_node")
        entry = _services.get(_key(self, context.scene))
        count = len(entry.service.instances) if entry else 0
        layout.label(text=f"Live Messages: {count}")
        _node_button(layout, "yugofui.clear", self, "Clear Generated Messages")


class _NodeOperator:
    def resolve(self, expected_id):
        return _resolve_node(self, expected_id)


class YUGOFUI_OT_send(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugofui.send"
    bl_label = "Send Floating UI Message Once"

    tree_name: StringProperty(options={"HIDDEN"})
    node_name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            _event_id, count = send_message(self.resolve(SOURCE_ID), context.scene)
        except (TypeError, ValueError, RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Sent one message to {count} display(s)")
        return {"FINISHED"}


class YUGOFUI_OT_import_template(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugofui.import_template"
    bl_label = "Import Floating UI Template Collection"

    tree_name: StringProperty(options={"HIDDEN"})
    node_name: StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        try:
            node = self.resolve(DISPLAY_ID)
            if not node.template_path or not node.collection_name:
                raise ValueError("Set a preset .blend path and its collection name")
            node.template = load_template(
                bpy.path.abspath(node.template_path), node.collection_name
            )
        except (TypeError, ValueError, RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Imported template datablock; it remains unlinked")
        return {"FINISHED"}


class YUGOFUI_OT_clear(_NodeOperator, bpy.types.Operator):
    bl_idname = "yugofui.clear"
    bl_label = "Clear Generated Floating UI Messages"

    tree_name: StringProperty(options={"HIDDEN"})
    node_name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            node = self.resolve(DISPLAY_ID)
            _prune_runtime()
            entry = _services.get(_key(node, context.scene))
            if entry:
                entry.service.clear()
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGOFUI_OT_step(bpy.types.Operator):
    bl_idname = "yugofui.step"
    bl_label = "Step Floating UI Preview"

    seconds: FloatProperty(name="Seconds", default=0.25, min=0.001, max=60.0)

    def execute(self, context):
        tree = _context_tree(context)
        if tree is None or tree.bl_idname != TREE_ID:
            return {"CANCELLED"}
        _stop_sessions(_pointer(tree))
        _prune_runtime()
        for key, entry in list(_services.items()):
            if key[0] == _pointer(tree) and key[2] == _pointer(context.scene):
                entry.step(self.seconds)
        return {"FINISHED"}


class YUGOFUI_OT_play(bpy.types.Operator):
    bl_idname = "yugofui.play"
    bl_label = "Play Finite Floating UI Preview"

    seconds: FloatProperty(name="Preview Seconds", default=3.0, min=0.1, max=30.0)

    def execute(self, context):
        if bpy.app.background:
            self.report({"ERROR"}, "Play requires an interactive Blender window; use Step in background tests")
            return {"CANCELLED"}
        tree = _context_tree(context)
        if tree is None or tree.bl_idname != TREE_ID:
            return {"CANCELLED"}
        _prune_runtime()
        tree_pointer = _pointer(tree)
        scene_pointer = _pointer(context.scene)
        _stop_sessions(tree_pointer)
        matching = [
            entry for key, entry in _services.items()
            if key[0] == tree_pointer and key[2] == scene_pointer
        ]
        if not matching:
            self.report({"INFO"}, "Send a message before playing the preview")
            return {"CANCELLED"}
        stamp = time.monotonic()
        for entry in matching:
            entry.clock_offset = max(entry.clock_offset, entry.service.last_now - stamp)
        self._session = None
        try:
            self._session = _PreviewSession(context, tree_pointer, scene_pointer, self.seconds)
            context.window_manager.modal_handler_add(self)
        except Exception as error:
            self.finish()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def finish(self):
        session = getattr(self, "_session", None)
        if session is not None:
            session.finish()

    def modal(self, context, event):
        session = getattr(self, "_session", None)
        if session is None:
            return {"CANCELLED"}
        try:
            return session.advance(context, event)
        except Exception as error:
            session.finish()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def cancel(self, _context):
        self.finish()


class YUGOFUI_OT_stop(bpy.types.Operator):
    bl_idname = "yugofui.stop"
    bl_label = "Stop Floating UI Preview"

    def execute(self, context):
        tree = _context_tree(context)
        if tree and tree.bl_idname == TREE_ID:
            _stop_sessions(_pointer(tree))
        else:
            _stop_sessions()
        return {"FINISHED"}


class YUGOFUI_OT_new_tree(bpy.types.Operator):
    bl_idname = "yugofui.new_tree"
    bl_label = "Create Floating UI Preview Graph"

    def execute(self, context):
        if context.space_data.type != "NODE_EDITOR":
            return {"CANCELLED"}
        tree = bpy.data.node_groups.new("Floating UI Preview", TREE_ID)
        source = tree.nodes.new(SOURCE_ID)
        display = tree.nodes.new(DISPLAY_ID)
        source.location = (-360, 0)
        display.location = (0, 0)
        tree.links.new(source.outputs[0], display.inputs[0])
        context.space_data.tree_type = TREE_ID
        context.space_data.node_tree = tree
        return {"FINISHED"}


class YUGOFUI_MT_add(bpy.types.Menu):
    bl_idname = "YUGOFUI_MT_add"
    bl_label = "Floating UI"

    def draw(self, _context):
        for type_id, label in ((SOURCE_ID, "Message Source"), (DISPLAY_ID, "Floating UI Display")):
            op = self.layout.operator("node.add_node", text=label)
            op.type = type_id
            op.use_transform = True


def _draw_add_menu(self, context):
    if context.space_data.tree_type == TREE_ID:
        self.layout.menu(YUGOFUI_MT_add.bl_idname)


class YUGOFUI_PT_preview(bpy.types.Panel):
    bl_label = "Floating UI Preview"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Floating UI"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == TREE_ID

    def draw(self, context):
        layout = self.layout
        layout.operator("yugofui.new_tree", text="Create Preview Graph")
        tree = _context_tree(context)
        if tree is None:
            return
        layout.label(text="Send once, then Step or Play.")
        row = layout.row(align=True)
        row.operator("yugofui.step", text="Step 0.25 s")
        row.operator("yugofui.play", text="Play 3 s")
        layout.operator("yugofui.stop", text="Stop Preview")
        layout.label(text="Development harness; no chat receiver.")


_CLASSES = (
    YUGOFUI_NT_preview,
    YUGOFUI_NS_event,
    YUGOFUI_N_message,
    YUGOFUI_N_display,
    YUGOFUI_OT_send,
    YUGOFUI_OT_import_template,
    YUGOFUI_OT_clear,
    YUGOFUI_OT_step,
    YUGOFUI_OT_play,
    YUGOFUI_OT_stop,
    YUGOFUI_OT_new_tree,
    YUGOFUI_MT_add,
    YUGOFUI_PT_preview,
)

_HANDLERS = (
    (bpy.app.handlers.load_pre, _reset_before_change),
    (bpy.app.handlers.load_post, _recover_after_change),
    (bpy.app.handlers.undo_pre, _reset_before_change),
    (bpy.app.handlers.undo_post, _recover_after_change),
    (bpy.app.handlers.redo_pre, _reset_before_change),
    (bpy.app.handlers.redo_post, _recover_after_change),
    (bpy.app.handlers.depsgraph_update_post, _check_after_update),
)


def register():
    global _registered
    if _registered:
        return
    registered = []
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.NODE_MT_add.append(_draw_add_menu)
        for handlers, callback in _HANDLERS:
            if callback not in handlers:
                handlers.append(callback)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise
    _registered = True


def unregister():
    global _registered
    cleanup_preview()
    cleanup_saved_outputs()
    if not _registered:
        return
    for handlers, callback in _HANDLERS:
        if callback in handlers:
            handlers.remove(callback)
    bpy.types.NODE_MT_add.remove(_draw_add_menu)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _registered = False
