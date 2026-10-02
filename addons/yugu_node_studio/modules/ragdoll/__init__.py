"""Articulated native pose output through Studio's public actions and scheduler."""

from copy import deepcopy
from dataclasses import dataclass
import uuid

import bpy

from ... import extensions as api
from ...constants import TREE_ID, SOCKET_TRIGGER
from ...nodes import YUGUS_NodeBase, _YUGUS_SocketBase
from . import service

EVENT = "YUGUS_NS_update_event"
POSE = "YUGUS_NS_ragdoll_pose"
MOTION = "YUGUS_NS_ragdoll_motion"
STATUS = "YUGUS_NS_ragdoll_status"
_records = {}


@dataclass(frozen=True)
class PoseValue:
    """World-space native endpoints; distinct from core local-transform poses."""
    armature: dict
    source: dict
    bone_names: object
    pose: dict


@dataclass(frozen=True)
class MotionValue:
    source: PoseValue
    motion: dict
    settings: dict
    hit_bone: str
    impulse: tuple


class YUGUS_NS_ragdoll_pose(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = POSE
    bl_label = "Native Ragdoll Pose"

    def draw(self, context, layout, node, text):
        layout.label(text=text or self.name)

    def draw_color(self, context, node):
        return (.7, .5, .85, 1)


class YUGUS_NS_ragdoll_motion(YUGUS_NS_ragdoll_pose):
    bl_idname = MOTION
    bl_label = "Ragdoll Motion"


class YUGUS_NS_ragdoll_status(YUGUS_NS_ragdoll_pose):
    bl_idname = STATUS
    bl_label = "Ragdoll Status"

    def draw_color(self, context, node):
        return (.3, .75, .5, 1)


def _ref(value):
    return api.object_ref(value) if value is not None else None


def _handle(node):
    return {"tree": _ref(node.id_data), "node": str(node.runtime_id)}


def _node(handle):
    tree = next((item for item in getattr(bpy.data, "node_groups", ()) if _ref(item) == handle["tree"]), None)
    return next((item for item in tree.nodes if str(getattr(item, "runtime_id", "")) == handle["node"]), None) if tree else None


def _enabled(node):
    return node is not None and not node.mute and bool(getattr(node, "enabled", True))


def _key(node, scene):
    return api.node_owner("ragdoll", node, scene)


def _sample_pose(rig, names=None):
    if rig is None or rig.type != "ARMATURE":
        raise ValueError("Choose an Armature in Pose Source")
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    names = [bone.name for bone in rig.data.bones if bone.use_deform] if names is None else names
    return {name: {"head": list(rig.matrix_world @ evaluated.pose.bones[name].head),
                   "tail": list(rig.matrix_world @ evaluated.pose.bones[name].tail)} for name in names}


SETTINGS = ("ground_z", "damping", "min_hold", "settle_speed", "settle_time", "timeout", "recovery_duration", "auto_recover")


def _settings(node):
    return {name: getattr(node, name) for name in SETTINGS}


def configuration(node):
    """Snapshot upstream source/settings, while allowing future Hit parameters."""
    pending, visited, result = [node], set(), []
    while pending:
        current = pending.pop()
        if current.as_pointer() in visited:
            continue
        visited.add(current.as_pointer())
        if current.bl_idname == YUGUS_NODE_ragdoll_pose_source.bl_idname:
            result.append({"source": _handle(current), "armature": _ref(current.armature),
                           "data": _ref(current.armature.data) if current.armature else None,
                           "bone_names": current.bone_names})
        elif current.bl_idname == YUGUS_NODE_ragdoll_motion.bl_idname:
            result.append({"motion": _handle(current), "settings": _settings(current)})
        for socket in current.inputs:
            pending.extend(link.from_node for link in socket.links)
    return {"graph": sorted(result, key=lambda item: str(item)), "busy_policy": node.busy_policy}


def _signature(node):
    return {"upstream": api.configuration_signature(node),
            "outgoing": sorted([[link.from_socket.identifier,
                                 str(getattr(link.to_node, "runtime_id", "") or link.to_node.name),
                                 link.to_socket.identifier, bool(link.is_muted), bool(link.is_valid)]
                                for link in node.id_data.links if link.from_node == node])}


def _retire(record, reason, *, failed=False, restore=True):
    if record["active"]:
        rig = api.resolve_object(record["rig"]) if getattr(bpy.data, "objects", None) is not None else None
        safe_restore = restore and rig is not None and _ref(rig.data) == record["data"]
        record["active"] = False
        record["recover_token"] = None
        try:
            snapshot = service.stop(record["session"], restore=safe_restore)
            record["snapshot"] = deepcopy(snapshot)
            if safe_restore:
                names = [name for name in record["source_pose"] if name in rig.pose.bones]
                record["snapshot"]["pose"] = _sample_pose(rig, names)
            record["snapshot"]["status"] = "FAILED" if failed else "CANCELLED"
            record["snapshot"]["error"] = str(reason) if failed else ""
        finally:
            api.release_owner(record["owner"])
    api.release_owner(record["owner"])


def cleanup(reason):
    # stop(restore=False) has no RNA access. Retire recorded sessions before
    # service.cleanup(), which otherwise attempts restricted pose restoration.
    for record in tuple(_records.values()):
        _retire(record, reason, restore=getattr(bpy.data, "objects", None) is not None)
    _records.clear()
    service.cleanup()


def recover(reason):
    cleanup(reason)


def _show(node, snapshot):
    node.status = snapshot["status"]
    node.message = snapshot.get("error", "")
    node.elapsed = snapshot.get("elapsed", 0.)
    node.speed = snapshot.get("speed", 0.)
    node.grounded = snapshot.get("contact", False)


def motion(context, delta_time):
    for record in tuple(_records.values()):
        if not record["active"]:
            if _node(record["apply"]) is None:
                _records.pop(record["owner"], None)
            continue
        try:
            node = _node(record["apply"])
            if not _enabled(node) or _ref(context.scene) != record["scene"]:
                _retire(record, "Ragdoll endpoint or scene changed")
                continue
            if _signature(node) != record["signature"]:
                _retire(record, "Ragdoll configuration or topology changed")
                _show(node, record["snapshot"])
                continue
            rig = api.resolve_object(record["rig"])
            if rig is None or _ref(rig.data) != record["data"]:
                _retire(record, "The armature was deleted or replaced", failed=True, restore=False)
                continue
            if context.screen and context.screen.is_animation_playing:
                _retire(record, "Pause the animation timeline before running ragdoll", failed=True)
                continue
            # COMPLETED still reserves its pose, but read does not advance time.
            snapshot = service.read(record["session"]) if record["snapshot"]["status"] == "COMPLETED" else service.update(record["session"], delta_time)
            record["snapshot"] = deepcopy(snapshot)
            if snapshot["status"] in {"FAILED", "CANCELLED"}:
                _retire(record, snapshot.get("error", "Ragdoll stopped"), failed=snapshot["status"] == "FAILED")
            _show(node, record["snapshot"])
        except Exception as error:
            _retire(record, str(error), failed=True)


def _source_evaluate(context, node):
    if node.armature is None:
        return {"Pose": None}
    handle = _handle(node)
    active = next((record for record in _records.values() if record["active"] and record["source"] == handle
                   and record["rig"] == _ref(node.armature)), None)
    names = tuple(value.strip() for value in node.bone_names.split(",") if value.strip()) or None
    pose = deepcopy(active["source_pose"]) if active else _sample_pose(node.armature, names)
    return {"Pose": PoseValue(_ref(node.armature), handle, names, pose)}


def _motion_evaluate(context, node):
    source = context.input(node, "Pose", None)
    if not isinstance(source, PoseValue):
        raise ValueError("Connect Pose Source to Ragdoll Motion")
    descriptor = MotionValue(source, _handle(node), _settings(node), node.impulse_bone, tuple(node.impulse))
    candidates = [item for item in _records.values() if item["motion"] == descriptor.motion
                  and item["rig"] == source.armature and _node(item["apply"]) is not None]
    record = next((item for item in reversed(candidates) if item["active"]),
                  candidates[-1] if candidates else None)
    snapshot = deepcopy(record["snapshot"]) if record else _empty_snapshot(source.pose)
    return {"Motion": descriptor, "Pose": PoseValue(source.armature, source.source, source.bone_names, snapshot["pose"]),
            "Status": snapshot, "Speed": snapshot["speed"], "Grounded": float(snapshot["contact"]),
            "Completed": float(snapshot["status"] == "COMPLETED")}


def _apply_evaluate(context, node):
    from ... import workflow
    record = _records.get(_key(node, context.scene))
    snapshot = deepcopy(record["snapshot"]) if record else _empty_snapshot()
    return {"Pose": PoseValue(deepcopy(record["rig"]), deepcopy(record["source"]), None, snapshot["pose"]) if record else None,
            "Status": snapshot, "Running": float(bool(workflow.running_for(node.id_data, node))),
            "Speed": snapshot["speed"], "Grounded": float(snapshot["contact"]),
            "Completed": float(snapshot["status"] == "COMPLETED")}


def _empty_snapshot(pose=None):
    return {"session_id": "", "status": "STOPPED", "pose": deepcopy(pose or {}), "elapsed": 0.,
            "speed": 0., "contact": False, "error": ""}


def get_status(node, scene=None):
    """Read a defensive numerical snapshot without starting or advancing time."""
    scene = bpy.context.scene if scene is None else scene
    record = _records.get(_key(node, scene))
    return deepcopy(record["snapshot"]) if record else _empty_snapshot()


def _action_start(context):
    node = context.node
    owner = _key(node, context.scene)
    record = _records.get(owner)
    if context.input_name == "Stop":
        if record:
            _retire(record, "Stopped")
            _show(node, record["snapshot"])
        return api.ActionResult.end()
    descriptor = context.input("Motion", None)
    if not isinstance(descriptor, MotionValue):
        raise ValueError("Connect Ragdoll Motion to Apply Ragdoll")
    if context.input_name == "Enter":
        if record and record["active"]:
            return api.ActionResult.failed("Ragdoll already owns this armature; use Hit or Stop")
        if bpy.context.screen and bpy.context.screen.is_animation_playing:
            raise ValueError("Pause the animation timeline before running ragdoll")
        rig = api.resolve_object(descriptor.source.armature)
        if rig is None:
            raise ValueError("Pose Source armature was deleted")
        api.claim_transforms(owner, [rig])
        session_id, new_record = None, None
        try:
            session_id = service.start(rig, descriptor.source.bone_names, descriptor.settings, owner=owner)
            snapshot = service.read(session_id)
            new_record = {"owner": owner, "apply": _handle(node), "source": descriptor.source.source,
                      "motion": descriptor.motion, "rig": _ref(rig), "data": _ref(rig.data),
                      "scene": _ref(context.scene), "session": session_id, "generation": uuid.uuid4().hex,
                      "source_pose": deepcopy(snapshot["pose"]), "snapshot": deepcopy(snapshot),
                      "active": True, "hit_token": uuid.uuid4().hex, "recover_token": None,
                      "signature": _signature(node)}
            record = new_record
            _records[owner] = record
            from ..physics import release_for_armature
            release_for_armature(rig)
        except Exception:
            if new_record and new_record.get("active"):
                _retire(new_record, "Start failed")
            elif session_id is not None:
                service.stop(session_id, restore=False)
            api.release_owner(owner)
            raise
        _show(node, snapshot)
        return api.ActionResult.running({"owner": owner, "generation": record["generation"], "kind": "session"})
    if record is None or not record["active"]:
        raise ValueError("Enter Apply Ragdoll before Hit or Recover")
    if _signature(node) != record["signature"]:
        _retire(record, "Ragdoll graph changed")
        return api.ActionResult.cancelled()
    if context.input_name == "Hit":
        record["snapshot"] = service.hit(record["session"], descriptor.hit_bone.strip(), descriptor.impulse)
        if record["snapshot"]["status"] == "FAILED":
            error = record["snapshot"].get("error", "Ragdoll hit failed")
            _retire(record, error, failed=True)
            return api.ActionResult.failed(error)
        record["hit_token"] = uuid.uuid4().hex
        record["recover_token"] = None
        _show(node, record["snapshot"])
        return api.ActionResult.end()
    if record["recover_token"] is not None:
        return api.ActionResult.failed("This ragdoll already has a recovery invocation")
    token = uuid.uuid4().hex
    record["snapshot"] = service.recover(record["session"], record["source_pose"])
    if record["snapshot"]["status"] == "FAILED":
        error = record["snapshot"].get("error", "Ragdoll recovery failed")
        _retire(record, error, failed=True)
        return api.ActionResult.failed(error)
    record["recover_token"] = token
    _show(node, record["snapshot"])
    return api.ActionResult.running({"owner": owner, "generation": record["generation"],
                                     "kind": "recover", "token": token, "hit": record["hit_token"]})


def _action_update(context, state):
    record = _records.get(state["owner"])
    if record is None or record["generation"] != state["generation"]:
        return api.ActionResult.cancelled()
    if record["snapshot"]["status"] == "FAILED":
        return api.ActionResult.failed(record["snapshot"].get("error", "Ragdoll failed"))
    if not record["active"]:
        return api.ActionResult.cancelled()
    if state["kind"] == "recover" and (record["recover_token"] != state["token"] or record["hit_token"] != state["hit"]):
        return api.ActionResult.cancelled()
    if record["snapshot"]["status"] == "COMPLETED":
        if state["kind"] == "recover":
            record["recover_token"] = None
        return api.ActionResult.end()
    return api.ActionResult.running(state)


def _action_cancel(context, state, reason):
    if not state:
        return
    record = _records.get(state["owner"])
    if record is None or record["generation"] != state["generation"] or not record["active"]:
        return
    if state["kind"] == "session":
        _retire(record, reason)
    elif record["recover_token"] == state["token"] and record["hit_token"] == state["hit"]:
        # A zero hit exits recovery without replacing pose or velocities. The
        # original service exposes this restart operation, with no new solver.
        record["recover_token"] = None
        if getattr(bpy.data, "objects", None) is not None:
            bone = next(iter(record["source_pose"]))
            record["snapshot"] = service.hit(record["session"], bone, (0, 0, 0))
            record["hit_token"] = uuid.uuid4().hex


def _armature_poll(self, obj):
    return obj.type == "ARMATURE"


class YUGUS_NODE_ragdoll_pose_source(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_ragdoll_pose_source"
    bl_label = "Pose Source"
    armature: bpy.props.PointerProperty(name="Armature", type=bpy.types.Object, poll=_armature_poll)
    bone_names: bpy.props.StringProperty(name="Bones", description="Optional comma-separated names; empty uses native VRM mapping or deform bones")

    def init(self, context):
        self._init_base(250)
        self.outputs.new(POSE, "Pose")

    def draw_buttons(self, context, layout):
        layout.prop(self, "armature")
        layout.prop(self, "bone_names")
        layout.label(text="Captured source / recovery pose")


class YUGUS_NODE_ragdoll_motion(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_ragdoll_motion"
    bl_label = "Ragdoll Motion"
    ground_z: bpy.props.FloatProperty(name="Ground Z", default=0)
    damping: bpy.props.FloatProperty(name="Damping", default=2., min=0, max=30)
    min_hold: bpy.props.FloatProperty(name="Minimum Wait", default=1., min=0)
    settle_speed: bpy.props.FloatProperty(name="Stable Speed", default=.18, min=.001)
    settle_time: bpy.props.FloatProperty(name="Stable Duration", default=.4, min=0)
    timeout: bpy.props.FloatProperty(name="Recovery Timeout", default=15., min=.1)
    recovery_duration: bpy.props.FloatProperty(name="Recovery Blend", default=1.5, min=.01)
    auto_recover: bpy.props.BoolProperty(name="Recover when Grounded and Stable", default=True)
    impulse_bone: bpy.props.StringProperty(name="Hit Bone", default="hips")
    impulse: bpy.props.FloatVectorProperty(name="World Impulse", default=(1, 0, 2))

    def init(self, context):
        self._init_base(270)
        self.inputs.new(POSE, "Pose")
        self.outputs.new(MOTION, "Motion")
        self.outputs.new(POSE, "Pose")
        self.outputs.new(STATUS, "Status")
        for name in ("Speed", "Grounded", "Completed"):
            self.outputs.new(SOCKET_TRIGGER, name)

    def draw_buttons(self, context, layout):
        for name in SETTINGS + ("impulse_bone", "impulse"):
            layout.prop(self, name)


class YUGUS_NODE_apply_ragdoll(api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_apply_ragdoll"
    bl_label = "Apply Ragdoll"
    status: bpy.props.StringProperty(default="STOPPED", options={"SKIP_SAVE"})
    message: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})
    elapsed: bpy.props.FloatProperty(options={"SKIP_SAVE"})
    speed: bpy.props.FloatProperty(options={"SKIP_SAVE"})
    grounded: bpy.props.BoolProperty(options={"SKIP_SAVE"})

    def init(self, context):
        self.init_action()
        for name in ("Hit", "Recover", "Stop"):
            self.inputs.new(EVENT, name).link_limit = 4095
        self.inputs.new(MOTION, "Motion")
        self.outputs.new(POSE, "Pose")
        self.outputs.new(STATUS, "Status")
        for name in ("Speed", "Grounded", "Completed"):
            self.outputs.new(SOCKET_TRIGGER, name)

    def draw_buttons(self, context, layout):
        snapshot = get_status(self, context.scene)
        layout.label(text=snapshot["status"])
        layout.label(text=f"{snapshot['elapsed']:.2f} s · {snapshot['speed']:.2f} m/s")
        layout.label(text="Ground contact" if snapshot["contact"] else "Airborne")
        if snapshot["error"]:
            layout.label(text=snapshot["error"][:80], icon="ERROR")
        layout.label(text="Enter starts · Stop restores")
        layout.label(text="End = recovery completed")
        layout.label(text=self.last_summary)


class YUGUS_NODE_ragdoll_controls(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_ragdoll_controls"
    bl_label = "Ragdoll Example Controls"

    def init(self, context):
        self._init_base(220)
        for name in ("Enter", "Hit", "Recover", "Stop", "Cancel"):
            self.outputs.new(EVENT, name)

    def draw_buttons(self, context, layout):
        for name in ("Enter", "Hit", "Recover", "Stop", "Cancel"):
            operator = layout.operator("yugu_node_studio.ragdoll_emit", text="Start" if name == "Enter" else name)
            operator.tree_name, operator.node_name, operator.output_name = self.id_data.name, self.name, name


class YUGUS_OT_ragdoll_emit(bpy.types.Operator):
    bl_idname = "yugu_node_studio.ragdoll_emit"
    bl_label = "Trigger Ragdoll Example"
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    output_name: bpy.props.StringProperty()

    def execute(self, context):
        from ... import flow, workflow
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {"CANCELLED"}
        run_id = flow.emit_event(tree, node, self.output_name)
        result = workflow.status(run_id)
        if result and result["state"] == "failed":
            self.report({"WARNING"}, result["error"])
            return {"CANCELLED"}
        return {"FINISHED"}


def create_example(scene=None, save_path=None):
    from .example import make_armature
    scene = bpy.context.scene if scene is None else scene
    if scene != bpy.context.scene:
        raise ValueError("Create the ragdoll example in the current scene")
    rig = make_armature("Studio Ragdoll Example Rig")
    tree = bpy.data.node_groups.new("Studio Ragdoll Example", TREE_ID)
    tree.use_fake_user = True
    source = tree.nodes.new(YUGUS_NODE_ragdoll_pose_source.bl_idname)
    source.armature = rig
    source.location = (-700, 0)
    motion_node = tree.nodes.new(YUGUS_NODE_ragdoll_motion.bl_idname)
    motion_node.location = (-380, 0)
    apply = tree.nodes.new(YUGUS_NODE_apply_ragdoll.bl_idname)
    apply.location = (-20, 0)
    controls = tree.nodes.new(YUGUS_NODE_ragdoll_controls.bl_idname)
    controls.location = (-700, 280)
    tree.links.new(source.outputs["Pose"], motion_node.inputs["Pose"])
    tree.links.new(motion_node.outputs["Motion"], apply.inputs["Motion"])
    for name in ("Enter", "Hit", "Recover", "Stop", "Cancel"):
        tree.links.new(controls.outputs[name], apply.inputs[name])
    if save_path is not None:
        bpy.ops.wm.save_as_mainfile(filepath=str(save_path))
    return {"tree": tree, "rig": rig, "motion": motion_node, "controls": controls, "apply": apply, "source": source}


class YUGUS_OT_ragdoll_example(bpy.types.Operator):
    bl_idname = "yugu_node_studio.ragdoll_example"
    bl_label = "Create Studio Ragdoll Example"
    bl_description = "Add an original 21-bone rig and explicit ragdoll controls"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        example = create_example(context.scene)
        if context.area and context.area.type == "NODE_EDITOR":
            context.space_data.tree_type = TREE_ID
            context.space_data.node_tree = example["tree"]
        self.report({"INFO"}, "Example created. Open Studio Ragdoll Example graph and press Start.")
        return {"FINISHED"}


def create_spec():
    nodes = (YUGUS_NODE_ragdoll_pose_source, YUGUS_NODE_ragdoll_motion, YUGUS_NODE_apply_ragdoll, YUGUS_NODE_ragdoll_controls)
    return api.ModuleSpec("ragdoll", label="Ragdoll Motion", dependencies=("physics",),
                          classes=(YUGUS_NS_ragdoll_pose, YUGUS_NS_ragdoll_motion, YUGUS_NS_ragdoll_status,
                                   *nodes, YUGUS_OT_ragdoll_emit, YUGUS_OT_ragdoll_example),
                          evaluators={YUGUS_NODE_ragdoll_pose_source.bl_idname: _source_evaluate,
                                      YUGUS_NODE_ragdoll_motion.bl_idname: _motion_evaluate,
                                      YUGUS_NODE_apply_ragdoll.bl_idname: _apply_evaluate},
                          actions={YUGUS_NODE_apply_ragdoll.bl_idname: api.ActionSpec(
                              _action_start, _action_update, _action_cancel,
                              inputs=("Enter", "Hit", "Recover", "Stop"), configuration=configuration,
                              policy_inputs=("Enter",))},
                          menu_entries=tuple((cls.bl_idname, cls.bl_label) for cls in nodes),
                          cards={YUGUS_NODE_apply_ragdoll.bl_idname: api.CardSpec(
                              "Apply Ragdoll", "Start a ragdoll and wait for completed recovery.",
                              lambda layout, node: node.draw_buttons(bpy.context, layout))},
                          motion=motion, cleanup=cleanup, recover=recover)
