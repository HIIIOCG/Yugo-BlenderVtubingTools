"""Finite, explicitly started preview; service owns no timer or workflow state."""

import time
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, PointerProperty, StringProperty

from . import service
from .solver import Settings

TREE_ID = "YUGORAG_NT_preview"
SOURCE_ID = "YUGORAG_N_pose_source"
RAGDOLL_ID = "YUGORAG_N_ragdoll"
APPLY_ID = "YUGORAG_N_apply"
TRIGGER_ID = "YUGORAG_N_hz"
_sessions = {}
_playing = {}
_registered = False
_cleaning = False
_checking = False


def _ptr(value):
    return value.as_pointer() if value is not None else 0


def _key(tree, apply):
    return (_ptr(tree), _ptr(apply))


def _input(node, name, expected, required=True):
    links = list(node.inputs[name].links)
    if not links and not required:
        return None
    if len(links) != 1 or not links[0].is_valid or getattr(links[0], "is_muted", False):
        raise ValueError("Connect exactly one valid " + name + " input")
    parent = links[0].from_node
    if parent.bl_idname != expected or parent.mute:
        raise ValueError("Invalid or muted " + name + " source")
    if links[0].from_socket.bl_idname != node.inputs[name].bl_idname or links[0].from_socket.name != name:
        raise ValueError("Connect the " + name + " output with the matching socket type")
    return parent


def _graph(tree, apply):
    if tree.bl_idname != TREE_ID or apply.bl_idname != APPLY_ID or apply.mute:
        raise ValueError("Choose an enabled Apply Ragdoll node")
    motion = _input(apply, "Motion", RAGDOLL_ID)
    source = _input(motion, "Pose", SOURCE_ID)
    if source.armature is None:
        raise ValueError("Choose an Armature in Pose Source")
    trigger = _input(apply, "Tick", TRIGGER_ID, required=False)
    return source, motion, trigger


def _signature(tree, apply):
    source, motion, trigger = _graph(tree, apply)
    return (_ptr(source), _ptr(motion), _ptr(source.armature), source.bone_names,
            motion.ground_z, motion.damping, motion.min_hold, motion.settle_speed,
            motion.settle_time, motion.timeout, motion.recovery_duration,
            motion.auto_recover, _ptr(trigger), trigger.hz if trigger else apply.hz,
            apply.preview_seconds)


def _settings(motion):
    return Settings(ground_z=motion.ground_z, damping=motion.damping,
                    min_hold=motion.min_hold, settle_speed=motion.settle_speed,
                    settle_time=motion.settle_time, timeout=motion.timeout,
                    recovery_duration=motion.recovery_duration,
                    auto_recover=motion.auto_recover)


def _show(apply, snapshot):
    apply.status = snapshot.get("status", "STOPPED")
    apply.message = snapshot.get("error", "") or ""
    apply.elapsed = snapshot.get("elapsed", 0.0)
    apply.speed = snapshot.get("speed", 0.0)
    apply.grounded = snapshot.get("contact", False)
    return snapshot


def start(tree, apply):
    key = _key(tree, apply)
    if key in _sessions:
        return read(tree, apply)
    source, motion, _ = _graph(tree, apply)
    signature = _signature(tree, apply)
    names = [v.strip() for v in source.bone_names.split(",") if v.strip()] or None
    session_id = service.start(source.armature, bone_names=names,
                               settings=_settings(motion), owner="motion-preview:" + apply.runtime_id)
    try:
        if signature != _signature(tree, apply):
            raise ValueError("Graph changed while capturing the initial pose")
    except Exception:
        service.stop(session_id)
        raise
    _sessions[key] = {"id": session_id, "signature": signature}
    return _show(apply, service.read(session_id))


def _session(tree, apply):
    record = _sessions.get(_key(tree, apply))
    if record is None:
        raise ValueError("Start Apply Ragdoll first")
    try:
        if record["signature"] != _signature(tree, apply):
            raise ValueError("Graph or settings changed; start a fresh preview")
    except Exception:
        stop(tree, apply)
        raise
    return record["id"]


def read(tree, apply):
    """Value sampling is read-only: no simulation tick and no hidden start."""
    return _show(apply, service.read(_session(tree, apply)))


def step(tree, apply, dt):
    return _show(apply, service.update(_session(tree, apply), dt))


def _apply_for_motion(tree, motion):
    matches = [n for n in tree.nodes if n.bl_idname == APPLY_ID and
               any(l.from_node == motion for l in n.inputs["Motion"].links)]
    if len(matches) != 1:
        raise ValueError("Connect Ragdoll Motion to exactly one Apply")
    return matches[0]


def hit(tree, motion):
    apply = _apply_for_motion(tree, motion)
    start(tree, apply)
    if not motion.impulse_bone.strip():
        raise ValueError("Choose the bone to receive the impulse")
    return _show(apply, service.hit(_session(tree, apply), motion.impulse_bone.strip(), tuple(motion.impulse)))


def recover(tree, apply):
    return _show(apply, service.recover(_session(tree, apply)))


def pause(tree, apply):
    _playing.pop(_key(tree, apply), None)
    _remove_timer_if_idle()


def stop(tree, apply, restore=True):
    pause(tree, apply)
    record = _sessions.pop(_key(tree, apply), None)
    if record is not None:
        result = service.stop(record["id"], restore=restore)
        _show(apply, result)
    else:
        apply.status = "STOPPED"
    return {"status": apply.status, "error": apply.message}


def play(tree, apply):
    start(tree, apply)
    now = time.monotonic()
    _playing[_key(tree, apply)] = {"last": now, "until": now + apply.preview_seconds, "next": now}
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.0, persistent=False)


def _cancel_key(key):
    record = _sessions.pop(key, None)
    _playing.pop(key, None)
    if record:
        result = service.stop(record["id"])
        _, apply = _locate(key)
        if apply is not None:
            _show(apply, result)
    _remove_timer_if_idle()


def _uses_node(key, record, node):
    signature = record["signature"]
    return key[1] == _ptr(node) or _ptr(node) in (signature[0], signature[1], signature[12])


def _locate(key):
    # Extension register/unregister can run under Blender's RestrictBlend.
    # Teardown still retires clocks and service sessions when RNA is absent.
    for tree in getattr(bpy.data, "node_groups", ()):
        if _ptr(tree) == key[0] and tree.bl_idname == TREE_ID:
            for node in tree.nodes:
                if _ptr(node) == key[1] and node.bl_idname == APPLY_ID:
                    return tree, node
    return None, None


def _timer():
    now = time.monotonic()
    interval = .05
    for key, clock in list(_playing.items()):
        tree, apply = _locate(key)
        if tree is None:
            record = _sessions.pop(key, None)
            if record:
                service.stop(record["id"])
            _playing.pop(key, None)
            continue
        try:
            source, motion, trigger = _graph(tree, apply)
            hz = float(trigger.hz if trigger else apply.hz)
            interval = min(interval, 1.0 / hz)
            if now >= clock["until"]:
                pause(tree, apply)
                continue
            if now < clock["next"]:
                continue
            snapshot = step(tree, apply, now - clock["last"])
            clock["last"] = now
            clock["next"] = now + 1.0 / hz
            if snapshot["status"] in {"FAILED", "CANCELLED", "STOPPED"}:
                pause(tree, apply)
        except Exception as exc:
            try:
                stop(tree, apply)
                apply.status = "FAILED"
                apply.message = str(exc)
            except (ReferenceError, RuntimeError):
                _playing.pop(key, None)
    return interval if _playing else None


def _remove_timer_if_idle():
    if not _playing and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)


def cleanup():
    global _cleaning
    if _cleaning:
        return
    _cleaning = True
    try:
        _playing.clear()
        _remove_timer_if_idle()
        for key, record in list(_sessions.items()):
            tree, node = _locate(key)
            result = service.stop(record["id"])
            if node is not None:
                _show(node, result)
        _sessions.clear()
        service.cleanup()
    finally:
        _cleaning = False


@persistent
def _cleanup_handler(_unused):
    cleanup()


@persistent
def _check_handler(_scene, _depsgraph):
    global _checking
    if _checking or _cleaning or service.is_writing():
        return
    _checking = True
    try:
        for key, record in list(_sessions.items()):
            tree, apply = _locate(key)
            if apply is None:
                service.stop(record["id"])
                _sessions.pop(key, None)
                _playing.pop(key, None)
                continue
            try:
                snapshot = read(tree, apply)
                if snapshot["status"] in {"FAILED", "CANCELLED"}:
                    _sessions.pop(key, None)
                    _playing.pop(key, None)
            except (ValueError, ReferenceError, RuntimeError) as exc:
                stop(tree, apply)
                apply.status = "FAILED"
                apply.message = str(exc)
        _remove_timer_if_idle()
    finally:
        _checking = False


def _armature_poll(_self, obj):
    return obj.type == "ARMATURE"


def _config_changed(node, _context):
    tree = node.id_data
    for key in list(_sessions):
        if key[0] == _ptr(tree) and _uses_node(key, _sessions[key], node):
            found_tree, apply = _locate(key)
            if apply is not None:
                stop(found_tree, apply)


class RagdollTree(bpy.types.NodeTree):
    bl_idname = TREE_ID
    bl_label = "Ragdoll Motion Preview"
    bl_icon = "PHYSICS"

    def update(self):
        # Link mutations revoke the affected writer immediately.
        for key in list(_sessions):
            if key[0] != _ptr(self):
                continue
            tree, node = _locate(key)
            if node is None:
                _cancel_key(key)
                continue
            try:
                _session(tree, node)
            except (ValueError, ReferenceError, RuntimeError):
                pass


class PoseSocket(bpy.types.NodeSocket):
    bl_idname = "YUGORAG_NS_pose"
    bl_label = "Native Pose"

    def draw(self, _context, layout, _node, text):
        layout.label(text=text)

    def draw_color(self, _context, _node):
        return (.55, .55, .55, 1)


class TickSocket(bpy.types.NodeSocket):
    bl_idname = "YUGORAG_NS_tick"
    bl_label = "Tick"

    def draw(self, _context, layout, _node, text):
        layout.label(text=text)

    def draw_color(self, _context, _node):
        return (.85, .12, .1, 1)


class StatusSocket(PoseSocket):
    bl_idname = "YUGORAG_NS_status"
    bl_label = "Ragdoll Status"

    def draw_color(self, _context, _node):
        return (.3, .7, .45, 1)


class _Node:
    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE_ID

    def free(self):
        for key, record in list(_sessions.items()):
            if key[0] == _ptr(self.id_data) and _uses_node(key, record, self):
                _cancel_key(key)


class PoseSource(_Node, bpy.types.Node):
    bl_idname = SOURCE_ID
    bl_label = "Pose Source"
    armature: PointerProperty(type=bpy.types.Object, poll=_armature_poll, update=_config_changed)
    bone_names: StringProperty(name="Bones", description="Optional comma-separated bone names; empty uses VRM humanoid or deform bones", update=_config_changed)

    def init(self, _context):
        self.outputs.new(PoseSocket.bl_idname, "Pose")
        self.width = 220

    def draw_buttons(self, _context, layout):
        layout.prop(self, "armature")
        layout.prop(self, "bone_names")
        layout.label(text="Initial pose / recovery target")


HZ_ITEMS = tuple((str(v), str(v) + " Hz", "Real-time fixed substeps") for v in (8, 12, 24, 30, 60, 120))


class HzTrigger(_Node, bpy.types.Node):
    bl_idname = TRIGGER_ID
    bl_label = "Hz Trigger (Preview)"
    hz: EnumProperty(name="Rate", items=HZ_ITEMS, default="60", update=_config_changed)

    def init(self, _context):
        self.outputs.new(TickSocket.bl_idname, "Tick")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "hz")


def _button(layout, node, action, text, icon="NONE"):
    op = layout.operator("yugo_ragdoll.preview_action", text=text, icon=icon)
    op.tree_name = node.id_data.name
    op.node_name = node.name
    op.action = action


class RagdollMotion(_Node, bpy.types.Node):
    bl_idname = RAGDOLL_ID
    bl_label = "Ragdoll Motion"
    ground_z: FloatProperty(name="Ground Z", default=0, update=_config_changed)
    damping: FloatProperty(name="Damping", default=2.0, min=0, max=30, update=_config_changed)
    min_hold: FloatProperty(name="Minimum wait", default=1, min=0, max=60, subtype="TIME", update=_config_changed)
    settle_speed: FloatProperty(name="Stable speed", default=.18, min=.001, max=5, update=_config_changed)
    settle_time: FloatProperty(name="Stable duration", default=.4, min=0, max=10, subtype="TIME", update=_config_changed)
    timeout: FloatProperty(name="Timeout", default=15, min=.1, max=120, subtype="TIME", update=_config_changed)
    recovery_duration: FloatProperty(name="Recovery blend", default=1.5, min=.01, max=30, subtype="TIME", update=_config_changed)
    auto_recover: BoolProperty(name="Recover when grounded and stable", default=True, update=_config_changed)
    impulse_bone: StringProperty(name="Hit bone", default="hips")
    impulse: FloatVectorProperty(name="Impulse", size=3, default=(3, 0, 4), description="World-space impulse in kg·m/s")

    def init(self, _context):
        self.inputs.new(PoseSocket.bl_idname, "Pose")
        self.outputs.new(PoseSocket.bl_idname, "Motion")
        self.outputs.new(StatusSocket.bl_idname, "Status")
        self.width = 270

    def draw_buttons(self, _context, layout):
        for prop in ("ground_z", "damping", "auto_recover", "min_hold", "settle_speed", "settle_time", "timeout", "recovery_duration", "impulse_bone", "impulse"):
            layout.prop(self, prop)
        _button(layout, self, "HIT", "Apply / Add Impulse", "FORCE_FORCE")


class ApplyRagdoll(_Node, bpy.types.Node):
    bl_idname = APPLY_ID
    bl_label = "Apply Ragdoll"
    runtime_id: StringProperty(options={"HIDDEN"})
    status: StringProperty(default="STOPPED", options={"SKIP_SAVE"})
    message: StringProperty(options={"SKIP_SAVE"})
    elapsed: FloatProperty(options={"SKIP_SAVE"})
    speed: FloatProperty(options={"SKIP_SAVE"})
    grounded: BoolProperty(options={"SKIP_SAVE"})
    hz: EnumProperty(name="Fallback rate", items=HZ_ITEMS, default="60", update=_config_changed)
    preview_seconds: FloatProperty(name="Preview seconds", default=30, min=.1, max=120, update=_config_changed)

    def init(self, _context):
        self.inputs.new(TickSocket.bl_idname, "Tick")
        self.inputs.new(PoseSocket.bl_idname, "Motion")
        self.runtime_id = uuid.uuid4().hex
        self.width = 240

    def copy(self, _node):
        self.runtime_id = uuid.uuid4().hex

    def draw_buttons(self, _context, layout):
        layout.label(text="State: " + self.status)
        layout.label(text=f"{self.elapsed:.2f} s  |  Speed {self.speed:.2f} m/s")
        layout.label(text="Ground contact" if self.grounded else "Airborne")
        if self.message:
            layout.label(text=self.message[:70], icon="ERROR")
        layout.prop(self, "preview_seconds")
        row = layout.row(align=True)
        _button(row, self, "START", "Start")
        _button(row, self, "STEP", "Step")
        row = layout.row(align=True)
        _button(row, self, "PLAY", "Play", "PLAY")
        _button(row, self, "PAUSE", "Pause", "PAUSE")
        _button(layout, self, "RECOVER", "Recover to Source Pose")
        _button(layout, self, "STOP", "Stop / Restore", "LOOP_BACK")


class PreviewAction(bpy.types.Operator):
    bl_idname = "yugo_ragdoll.preview_action"
    bl_label = "Ragdoll Preview Action"
    tree_name: StringProperty()
    node_name: StringProperty()
    action: StringProperty()

    def execute(self, _context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            self.report({"ERROR"}, "Preview node no longer exists")
            return {"CANCELLED"}
        try:
            if self.action == "HIT":
                hit(tree, node)
            elif self.action == "START":
                start(tree, node)
            elif self.action == "STEP":
                _, _, trigger = _graph(tree, node)
                step(tree, node, 1 / float(trigger.hz if trigger else node.hz))
            elif self.action == "PLAY":
                play(tree, node)
            elif self.action == "PAUSE":
                pause(tree, node)
            elif self.action == "RECOVER":
                recover(tree, node)
            elif self.action == "STOP":
                stop(tree, node)
            else:
                raise ValueError("Unknown preview operation")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            if node.bl_idname == APPLY_ID:
                node.message = str(exc)
            return {"CANCELLED"}
        return {"FINISHED"}


class AddNodeMenu(bpy.types.Menu):
    bl_idname = "YUGORAG_MT_nodes"
    bl_label = "Ragdoll Preview Nodes"

    def draw(self, _context):
        for cls in (PoseSource, RagdollMotion, HzTrigger, ApplyRagdoll):
            op = self.layout.operator("node.add_node", text=cls.bl_label)
            op.type = cls.bl_idname
            op.use_transform = True


class NewPreview(bpy.types.Operator):
    bl_idname = "yugo_ragdoll.new_preview"
    bl_label = "Create Ragdoll Motion Preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = bpy.data.node_groups.new("Ragdoll Motion Preview", TREE_ID)
        source = tree.nodes.new(SOURCE_ID)
        motion = tree.nodes.new(RAGDOLL_ID)
        trigger = tree.nodes.new(TRIGGER_ID)
        apply = tree.nodes.new(APPLY_ID)
        source.location = (-600, 0)
        motion.location = (-300, 0)
        trigger.location = (-300, 200)
        apply.location = (50, 0)
        tree.links.new(source.outputs["Pose"], motion.inputs["Pose"])
        tree.links.new(motion.outputs["Motion"], apply.inputs["Motion"])
        tree.links.new(trigger.outputs["Tick"], apply.inputs["Tick"])
        if context.object and context.object.type == "ARMATURE":
            source.armature = context.object
            names = [b.name for b in context.object.data.bones if b.use_deform]
            if names:
                motion.impulse_bone = names[0]
        tree.use_fake_user = True
        if context.area and context.area.type == "NODE_EDITOR":
            context.space_data.tree_type = TREE_ID
            context.space_data.node_tree = tree
            context.space_data.pin = True
        self.report({"INFO"}, "Choose Armature in Pose Source, then Play in Apply Ragdoll")
        return {"FINISHED"}


def _menu(self, context):
    if getattr(context.space_data, "tree_type", None) == TREE_ID:
        self.layout.menu(AddNodeMenu.bl_idname)


CLASSES = (RagdollTree, PoseSocket, TickSocket, StatusSocket, PoseSource, HzTrigger, RagdollMotion, ApplyRagdoll, PreviewAction, AddNodeMenu, NewPreview)


def register():
    global _registered
    if _registered:
        return
    registered = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.NODE_MT_add.append(_menu)
        for handlers in (bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre, bpy.app.handlers.load_pre, bpy.app.handlers.save_pre):
            if _cleanup_handler not in handlers:
                handlers.append(_cleanup_handler)
        bpy.app.handlers.depsgraph_update_post.append(_check_handler)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise
    _registered = True


def unregister():
    global _registered
    cleanup()
    if not _registered:
        return
    for handlers in (bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre, bpy.app.handlers.load_pre, bpy.app.handlers.save_pre):
        if _cleanup_handler in handlers:
            handlers.remove(_cleanup_handler)
    if _check_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_check_handler)
    bpy.types.NODE_MT_add.remove(_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _registered = False
