"""Explicit Studio prop physics through the public feature API.

The original bounded solver and Blender service are unchanged. This adapter owns
the persistent world, action attribution, common scheduler and lifecycle.
"""

import math
import uuid

import bpy

from ... import extensions as api
from ...constants import SOCKET_TRIGGER, TREE_ID
from ...nodes import YUGUS_NodeBase, _YUGUS_SocketBase
from . import service

EVENT = "YUGUS_NS_update_event"
WORLD_SOCKET = "YUGUS_NS_physics_world"
STATE_SOCKET = "YUGUS_NS_physics_state"
_world = None
_controls = {}


class YUGUS_NS_physics_world(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = WORLD_SOCKET
    bl_label = "Physics World"

    def draw(self, context, layout, node, text):
        layout.label(text=text or self.name)

    def draw_color(self, context, node):
        return (.25, .65, .9, 1)


class YUGUS_NS_physics_state(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = STATE_SOCKET
    bl_label = "Physics State"

    def draw(self, context, layout, node, text):
        layout.label(text=text or self.name)

    def draw_color(self, context, node):
        return (.25, .75, .55, 1)


def _ref(value):
    return api.object_ref(value) if value is not None else None


def _resolve(reference, database):
    if not reference:
        return None
    return next((item for item in database if _ref(item) == reference), None)


def _handle(node):
    return {"tree": _ref(node.id_data), "node": str(node.runtime_id)}


def _enabled(node):
    return node is not None and not node.mute and bool(getattr(node, "enabled", True))


def _world_node():
    tree = _resolve(_world["tree"], bpy.data.node_groups) if _world else None
    return next((node for node in tree.nodes if str(getattr(node, "runtime_id", "")) == _world["node"]), None) if tree else None


def release_for_armature(armature):
    """Release props held by configured anchors parented to this rig.

    Called after a ragdoll session successfully captures and leases its rig,
    before the first shared motion update. Unrelated anchors remain held.
    """
    node = _world_node()
    if node is None or not service.active():
        return ()
    related = []
    for anchor in (node.left_anchor, node.right_anchor):
        current = anchor
        while current is not None and current != armature:
            current = current.parent
        if current == armature and anchor not in related:
            related.append(anchor)
    return tuple(name for anchor in related for name in service.release(anchor))


WORLD_NUMBERS = ("radius", "mass", "damping", "angular_damping", "friction", "restitution", "max_speed",
                 "grab_radius", "grab_strength", "grab_damping", "grab_max_force",
                 "recall_strength", "recall_damping", "recall_max_force", "recall_timeout")


def world_configuration(node):
    result = {name: float(getattr(node, name)) for name in WORLD_NUMBERS}
    result.update({"gravity": list(node.gravity), "use_floor": bool(node.use_floor),
                   "floor_height": float(node.floor_height), "recall_hold": bool(node.recall_hold),
                   "recall_orientation": bool(node.recall_orientation),
                   "left_anchor": _ref(node.left_anchor), "right_anchor": _ref(node.right_anchor),
                   "bodies": _ref(node.bodies), "colliders": _ref(node.colliders)})
    for label in ("bodies", "colliders"):
        collection = getattr(node, label)
        result[label + "_members"] = sorted(
            [{"ref": _ref(obj), "radius": float(obj.get("yugo_physics_radius", node.radius)),
              "mass": float(obj.get("yugo_physics_mass", node.mass))}
             for obj in collection.all_objects], key=lambda item: item["ref"]["session_uid"]) if collection else []
    return result


def _world_signature(node):
    # Persistent services outlive their synchronous Start ticket. Watch both
    # directions here, rather than relying on the retired ticket's signature.
    links = [[str(getattr(link.from_node, "runtime_id", "") or link.from_node.name), link.from_socket.identifier,
              str(getattr(link.to_node, "runtime_id", "") or link.to_node.name), link.to_socket.identifier,
              bool(link.is_muted), bool(link.is_valid)]
             for link in node.id_data.links if link.from_node == node or link.to_node == node]
    return {"config": world_configuration(node), "links": sorted(links),
            "upstream": api.configuration_signature(node)}


def _stop(reason="World stopped", *, clear=False):
    global _world
    service.clear() if clear else service.stop()
    _controls.clear()
    if _world:
        api.release_owner(_world["owner"])
        _world["stop_reason"] = str(reason)
        _world["running"] = False
    if clear:
        _world = None


def cleanup(reason):
    # Numeric service state only: safe under RestrictBlend and before RNA dies.
    _stop(str(reason), clear=True)


def recover(reason):
    # Props are user-owned inputs. Reopening never resets or deletes their pose.
    cleanup(reason)


def motion(context, delta_time):
    if _world is None or not _world["running"]:
        return
    try:
        node = _world_node()
        if not _enabled(node):
            _stop("Physics World was deleted or muted", clear=True)
            return
        if _world_signature(node) != _world["signature"]:
            _stop("Physics World configuration or links changed", clear=True)
            return
        if _ref(context.scene) != _world["scene"]:
            _stop("Scene changed", clear=True)
            return
        if context.screen and context.screen.is_animation_playing:
            _stop("Pause the animation timeline before running physics")
            return
        service.update(delta_time)
        if not service.active():
            _stop(service.status())
        elif node.last_summary != service.status():
            node.last_summary = service.status()
    except Exception as error:
        _stop(str(error), clear=True)


def _require_world(context):
    handle = context.input("World", None)
    if not isinstance(handle, dict) or _world is None or handle != {"tree": _world["tree"], "node": _world["node"]}:
        raise ValueError("Connect the running Physics World")
    if not _world["running"] or not service.active():
        raise ValueError(_world.get("stop_reason", "Start the Physics World first"))
    if _ref(context.scene) != _world["scene"]:
        raise ValueError("Physics World belongs to another scene")
    return _world


def _target(node):
    if node.target is None:
        raise ValueError("Choose a physics prop")
    if service.body_state(node.target) is None:
        raise ValueError("Choose a prop in the active Physics World")
    return node.target


def _world_action(context):
    global _world
    node = context.node
    handle = _handle(node)
    if context.input_name == "Start":
        if service.active():
            raise ValueError("One Physics World can run at a time; Stop it first")
        if context.scene is None or context.scene != bpy.context.scene:
            raise ValueError("Start Physics World in the current scene")
        if bpy.context.screen and bpy.context.screen.is_animation_playing:
            raise ValueError("Pause the animation timeline before running physics")
        objects = list(node.bodies.all_objects) if node.bodies else []
        owner = api.node_owner("physics", node, context.scene)
        api.claim_transforms(owner, objects)
        try:
            service.start(context.scene, node)
            _controls.clear()
            _world = {**handle, "scene": _ref(context.scene), "owner": owner,
                      "generation": uuid.uuid4().hex, "signature": _world_signature(node),
                      "running": True, "stop_reason": ""}
        except Exception:
            api.release_owner(owner)
            raise
    else:
        if _world is None or handle != {"tree": _world["tree"], "node": _world["node"]}:
            raise ValueError("This Physics World has no captured home; Start it first")
        if context.input_name == "Stop":
            _stop()
        else:
            objects = [api.resolve_object(reference) for reference in
                       [item["ref"] for item in _world["signature"]["config"]["bodies_members"]]]
            if any(obj is None for obj in objects):
                raise ValueError("A captured physics prop was deleted")
            api.claim_transforms(_world["owner"], objects)
            try:
                service.reset()
                _controls.clear()
            finally:
                if not service.active():
                    api.release_owner(_world["owner"])
    node.last_summary = service.status()
    return api.ActionResult.end()


def command_configuration(node):
    result = {"target": _ref(getattr(node, "target", None))}
    for name in ("linear", "angular", "force"):
        if hasattr(node, name):
            result[name] = list(getattr(node, name))
    for name in ("busy_policy", "clear_force", "hand", "stop_attraction", "radius", "strength", "damping",
                 "max_force", "seconds", "hold", "orient", "settle_time", "tolerance", "speed_tolerance"):
        if hasattr(node, name):
            result[name] = getattr(node, name)
    return result


def _command(context):
    world = _require_world(context)
    node = context.node
    if node.bl_idname == YUGUS_NODE_physics_grab.bl_idname:
        world_node = _world_node()
        anchor = world_node.left_anchor if node.hand == "LEFT" else world_node.right_anchor
        name = service.grab(anchor, node.radius)
        _controls[name] = {"token": uuid.uuid4().hex, "kind": "grab", "pending": False}
    elif node.bl_idname == YUGUS_NODE_physics_release.bl_idname:
        world_node = _world_node()
        anchor = world_node.left_anchor if node.hand == "LEFT" else world_node.right_anchor
        for name in service.release(anchor):
            _controls.pop(name, None)
    else:
        target = _target(node)
        if node.bl_idname == YUGUS_NODE_physics_impulse.bl_idname:
            service.impulse(target, tuple(node.linear), tuple(node.angular))
        elif node.bl_idname == YUGUS_NODE_physics_force.bl_idname:
            service.force(target, (0, 0, 0) if node.clear_force else tuple(node.force))
        else:
            service.cancel_recall(target)
            _controls.pop(target.name, None)
    return api.ActionResult.end()


def _attract_start(context):
    world = _require_world(context)
    node = context.node
    target = _target(node)
    existing = _controls.get(target.name)
    if existing and existing.get("pending"):
        return api.ActionResult.failed("Another attraction action controls this prop")
    token = uuid.uuid4().hex
    service.recall(target, {"strength": node.strength, "damping": node.damping,
                           "max_force": node.max_force, "timeout": node.seconds,
                           "hold": node.hold, "orient": node.orient,
                           "settle_time": node.settle_time, "tolerance": node.tolerance,
                           "speed_tolerance": node.speed_tolerance})
    _controls[target.name] = {"token": token, "kind": "attract", "pending": True}
    return api.ActionResult.running({"generation": world["generation"], "target": _ref(target),
                                     "name": target.name, "token": token})


def _attract_update(context, state):
    if _world is None or state["generation"] != _world["generation"]:
        return api.ActionResult.failed("Physics World was replaced or cleared")
    if not service.active():
        return api.ActionResult.failed(_world.get("stop_reason", "Physics World stopped"))
    target = api.resolve_object(state["target"])
    if target is None:
        return api.ActionResult.failed("Physics prop was deleted")
    control = _controls.get(state["name"])
    if control is None or control["token"] != state["token"]:
        return api.ActionResult.cancelled()
    value = service.body_state(target)
    if value is None:
        return api.ActionResult.failed("Physics prop is unavailable")
    if value["recall_state"] == "settled":
        control["pending"] = False
        return api.ActionResult.end()
    if value["recall_state"] == "timeout":
        return api.ActionResult.failed("Attraction timed out before the prop settled")
    if value["recall_state"] != "seeking":
        return api.ActionResult.cancelled()
    return api.ActionResult.running(state)


def _attract_cancel(context, state, reason):
    if not state or _world is None or state.get("generation") != _world["generation"]:
        return
    control = _controls.get(state["name"])
    if control and control["token"] == state["token"]:
        _controls.pop(state["name"], None)
        # RestrictBlend supplies no scene databases during extension disable.
        # Drop this numerical token now; module cleanup clears the whole solver.
        if getattr(bpy.data, "objects", None) is None:
            return
        target = api.resolve_object(state["target"])
        if target is not None and service.active() and service.body_state(target) is not None:
            service.cancel_recall(target)


def _world_evaluate(context, node):
    from ... import workflow
    return {"World": _handle(node), "Running": float(bool(workflow.running_for(node.id_data, node)))}


def _state_evaluate(context, node):
    handle = context.input(node, "World", None)
    same = isinstance(handle, dict) and _world is not None and handle == {"tree": _world["tree"], "node": _world["node"]}
    state = service.body_state(node.target) if same and node.target is not None else None
    return {"State": state, "Active": float(bool(state and state["active"])),
            "Speed": math.sqrt(sum(value * value for value in state["velocity"])) if state else 0.,
            "Held": float(bool(state and state["grab_anchor"])),
            "Attracting": float(bool(state and state["recall_state"] == "seeking")),
            "Settled": float(bool(state and state["recall_state"] == "settled"))}


class YUGUS_NODE_physics_world(api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_world"
    bl_label = "Physics World"
    bl_icon = "PHYSICS"
    bodies: bpy.props.PointerProperty(name="Dynamic Props", type=bpy.types.Collection)
    colliders: bpy.props.PointerProperty(name="Static Boxes", type=bpy.types.Collection)
    left_anchor: bpy.props.PointerProperty(name="Left Anchor", type=bpy.types.Object)
    right_anchor: bpy.props.PointerProperty(name="Right Anchor", type=bpy.types.Object)
    gravity: bpy.props.FloatVectorProperty(name="Gravity", default=(0, 0, -9.81))
    use_floor: bpy.props.BoolProperty(name="Ground Plane", default=True)
    floor_height: bpy.props.FloatProperty(name="Ground Z", default=0)
    radius: bpy.props.FloatProperty(name="Sphere Radius", default=.28, min=.001)
    mass: bpy.props.FloatProperty(name="Mass", default=1., min=.0001)
    damping: bpy.props.FloatProperty(name="Linear Damping", default=.3, min=0)
    angular_damping: bpy.props.FloatProperty(name="Angular Damping", default=.3, min=0)
    friction: bpy.props.FloatProperty(name="Friction", default=.6, min=0, max=1)
    restitution: bpy.props.FloatProperty(name="Bounce", default=.25, min=0, max=1)
    max_speed: bpy.props.FloatProperty(name="Speed Limit", default=30., min=.01)
    grab_radius: bpy.props.FloatProperty(name="Grab Range", default=1.5, min=0)
    grab_strength: bpy.props.FloatProperty(name="Grab Strength", default=120., min=0)
    grab_damping: bpy.props.FloatProperty(name="Grab Damping", default=22., min=0)
    grab_max_force: bpy.props.FloatProperty(name="Grab Force Limit", default=250., min=0)
    recall_strength: bpy.props.FloatProperty(default=80., min=0)
    recall_damping: bpy.props.FloatProperty(default=18., min=0)
    recall_max_force: bpy.props.FloatProperty(default=200., min=0)
    recall_timeout: bpy.props.FloatProperty(default=8., min=.01)
    recall_hold: bpy.props.BoolProperty(default=True)
    recall_orientation: bpy.props.BoolProperty(default=True)

    def init(self, context):
        self.init_action()
        self.width = 280
        self.inputs["Enter"].name = "Start"
        for name in ("Stop", "Reset"):
            self.inputs.new(EVENT, name).link_limit = 4095
        self.outputs.new(WORLD_SOCKET, "World")

    def draw_buttons(self, context, layout):
        layout.label(text="One world · starts explicitly")
        layout.prop(self, "bodies")
        layout.prop(self, "colliders")
        layout.prop(self, "left_anchor")
        layout.prop(self, "right_anchor")
        layout.prop(self, "gravity")
        layout.prop(self, "use_floor")
        if self.use_floor:
            layout.prop(self, "floor_height")
        for name in ("radius", "mass", "damping", "angular_damping", "friction", "restitution", "max_speed"):
            layout.prop(self, name)
        layout.label(text=self.last_summary)


class _PropAction:
    target: bpy.props.PointerProperty(name="Prop", type=bpy.types.Object)

    def init_prop(self):
        self.init_action()
        self.inputs.new(WORLD_SOCKET, "World")

    def draw_target(self, layout):
        layout.prop(self, "target")
        layout.label(text=self.last_summary)


class YUGUS_NODE_physics_impulse(_PropAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_impulse"
    bl_label = "Physics Impulse"
    linear: bpy.props.FloatVectorProperty(name="Linear Impulse", default=(3, 0, 5))
    angular: bpy.props.FloatVectorProperty(name="Angular Impulse", default=(0, 0, .2))

    def init(self, context):
        self.init_prop()

    def draw_buttons(self, context, layout):
        self.draw_target(layout)
        layout.prop(self, "linear")
        layout.prop(self, "angular")


class YUGUS_NODE_physics_force(_PropAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_force"
    bl_label = "Physics Force"
    force: bpy.props.FloatVectorProperty(name="Force (Newtons)", default=(2, 0, 0))
    clear_force: bpy.props.BoolProperty(name="Clear Force", default=False)

    def init(self, context):
        self.init_prop()

    def draw_buttons(self, context, layout):
        self.draw_target(layout)
        layout.prop(self, "clear_force")
        if not self.clear_force:
            layout.prop(self, "force")


class _HandAction:
    hand: bpy.props.EnumProperty(name="Hand", items=(("LEFT", "Left", "Configured left anchor"),
                                                    ("RIGHT", "Right", "Configured right anchor")))

    def init_hand(self):
        self.init_action()
        self.inputs.new(WORLD_SOCKET, "World")


class YUGUS_NODE_physics_grab(_HandAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_grab"
    bl_label = "Physics Grab"
    radius: bpy.props.FloatProperty(name="Grab Range", default=1.5, min=0)

    def init(self, context):
        self.init_hand()

    def draw_buttons(self, context, layout):
        layout.prop(self, "hand")
        layout.prop(self, "radius")
        layout.label(text="End = nearest free prop attached")
        layout.label(text=self.last_summary)


class YUGUS_NODE_physics_release(_HandAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_release"
    bl_label = "Physics Release"

    def init(self, context):
        self.init_hand()

    def draw_buttons(self, context, layout):
        layout.prop(self, "hand")
        layout.label(text="Keeps linear and angular velocity")
        layout.label(text=self.last_summary)


class YUGUS_NODE_physics_attract(_PropAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_attract"
    bl_label = "Attract Home"
    strength: bpy.props.FloatProperty(name="Attraction", default=80., min=0)
    damping: bpy.props.FloatProperty(name="Damping", default=18., min=0)
    max_force: bpy.props.FloatProperty(name="Force Limit", default=200., min=0)
    seconds: bpy.props.FloatProperty(name="Timeout (simulation seconds)", default=8., min=.01)
    settle_time: bpy.props.FloatProperty(name="Stable Duration", default=.25, min=.001)
    tolerance: bpy.props.FloatProperty(name="Distance / Angle Tolerance", default=.04, min=.000001)
    speed_tolerance: bpy.props.FloatProperty(name="Speed Tolerance", default=.15, min=.000001)
    hold: bpy.props.BoolProperty(name="Maintain Attraction", default=True)
    orient: bpy.props.BoolProperty(name="Restore Orientation", default=True)

    def init(self, context):
        self.init_prop()
        self.busy_policy = "IGNORE"

    def draw_buttons(self, context, layout):
        self.draw_target(layout)
        for name in ("strength", "damping", "max_force", "seconds", "settle_time", "hold", "orient", "busy_policy"):
            layout.prop(self, name)


class YUGUS_NODE_physics_stop_attraction(_PropAction, api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_stop_attraction"
    bl_label = "Stop Attraction"

    def init(self, context):
        self.init_prop()

    def draw_buttons(self, context, layout):
        self.draw_target(layout)


class YUGUS_NODE_physics_state(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_state"
    bl_label = "Physics State"
    target: bpy.props.PointerProperty(name="Prop", type=bpy.types.Object)

    def init(self, context):
        self._init_base(220)
        self.inputs.new(WORLD_SOCKET, "World")
        self.outputs.new(STATE_SOCKET, "State")
        for name in ("Active", "Speed", "Held", "Attracting", "Settled"):
            self.outputs.new(SOCKET_TRIGGER, name)

    def draw_buttons(self, context, layout):
        layout.prop(self, "target")
        layout.label(text="Reads state; never steps physics")


CONTROL_EVENTS = ("Start", "Stop", "Reset", "Impulse", "Grab", "Release", "Attract", "Stop Attraction", "Force", "Clear Force")


class YUGUS_NODE_physics_controls(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_physics_controls"
    bl_label = "Physics Example Controls"

    def init(self, context):
        self._init_base(230)
        for name in CONTROL_EVENTS:
            self.outputs.new(EVENT, name)

    def draw_buttons(self, context, layout):
        for name in CONTROL_EVENTS:
            operator = layout.operator("yugu_node_studio.physics_emit", text=name)
            operator.tree_name, operator.node_name, operator.output_name = self.id_data.name, self.name, name
        layout.label(text="Grab, move Anchor, then Release")


class YUGUS_OT_physics_emit(bpy.types.Operator):
    bl_idname = "yugu_node_studio.physics_emit"
    bl_label = "Trigger Physics Example"
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
    """Add a fresh graph and synthetic props without altering existing content.

    Returned RNA is for the caller's immediate use, never retained by runtime.
    """
    import bmesh
    scene = bpy.context.scene if scene is None else scene
    lab = bpy.data.collections.new("Studio Physics Example")
    scene.collection.children.link(lab)
    bodies = bpy.data.collections.new("Studio Physics Props")
    colliders = bpy.data.collections.new("Studio Physics Boxes")
    lab.children.link(bodies)
    lab.children.link(colliders)
    mesh = bpy.data.meshes.new("Studio Physics Prop Mesh")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=.28)
    bm.to_mesh(mesh)
    bm.free()
    prop = bpy.data.objects.new("Studio Physics Prop", mesh)
    bodies.objects.link(prop)
    prop.location = (-1, 0, .85)
    anchor = bpy.data.objects.new("Studio Physics Left Anchor", None)
    lab.objects.link(anchor)
    anchor.location = prop.location
    anchor.empty_display_type = "SPHERE"
    anchor.empty_display_size = .4
    anchor.show_in_front = True
    floor_mesh = bpy.data.meshes.new("Studio Physics Ground Mesh")
    floor_mesh.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                          [(0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4), (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6)])
    floor = bpy.data.objects.new("Studio Physics Ground", floor_mesh)
    colliders.objects.link(floor)
    floor.location = (0, 0, -.1)
    floor.scale = (4, 3, .1)
    tree = bpy.data.node_groups.new("Studio Physics Example", TREE_ID)
    tree.use_fake_user = True
    controls = tree.nodes.new(YUGUS_NODE_physics_controls.bl_idname)
    controls.location = (-560, 200)
    world = tree.nodes.new(YUGUS_NODE_physics_world.bl_idname)
    world.location = (-260, 200)
    world.bodies, world.colliders, world.left_anchor = bodies, colliders, anchor
    world.use_floor = False
    for event in ("Start", "Stop", "Reset"):
        tree.links.new(controls.outputs[event], world.inputs[event])
    definitions = (("Impulse", YUGUS_NODE_physics_impulse), ("Grab", YUGUS_NODE_physics_grab),
                   ("Release", YUGUS_NODE_physics_release), ("Attract", YUGUS_NODE_physics_attract),
                   ("Stop Attraction", YUGUS_NODE_physics_stop_attraction),
                   ("Force", YUGUS_NODE_physics_force), ("Clear Force", YUGUS_NODE_physics_force))
    for index, (event, cls) in enumerate(definitions):
        node = tree.nodes.new(cls.bl_idname)
        node.location = (80 + 300 * (index % 3), 200 - 470 * (index // 3))
        if hasattr(node, "target"):
            node.target = prop
        if event == "Clear Force":
            node.clear_force = True
        tree.links.new(world.outputs["World"], node.inputs["World"])
        tree.links.new(controls.outputs[event], node.inputs["Enter"])
    state = tree.nodes.new(YUGUS_NODE_physics_state.bl_idname)
    state.target = prop
    state.location = (980, 200)
    tree.links.new(world.outputs["World"], state.inputs["World"])
    bpy.context.view_layer.update()
    if save_path is not None:
        bpy.ops.wm.save_as_mainfile(filepath=str(save_path))
    return {"tree": tree, "world": world, "controls": controls, "prop": prop, "anchor": anchor,
            "collection": lab, "floor": floor, "state": state}


class YUGUS_OT_physics_example(bpy.types.Operator):
    bl_idname = "yugu_node_studio.physics_example"
    bl_label = "Create Studio Physics Example"
    bl_description = "Add prop physics nodes and controls; press Start explicitly"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        example = create_example(context.scene)
        if context.area and context.area.type == "NODE_EDITOR":
            context.space_data.tree_type = TREE_ID
            context.space_data.node_tree = example["tree"]
        self.report({"INFO"}, "Example created. Open Studio Physics Example graph and press Start.")
        return {"FINISHED"}


NODES = (YUGUS_NODE_physics_world, YUGUS_NODE_physics_impulse, YUGUS_NODE_physics_force,
         YUGUS_NODE_physics_grab, YUGUS_NODE_physics_release, YUGUS_NODE_physics_attract,
         YUGUS_NODE_physics_stop_attraction, YUGUS_NODE_physics_state, YUGUS_NODE_physics_controls)


def create_spec():
    actions = {cls.bl_idname: api.ActionSpec(_command, configuration=command_configuration)
               for cls in NODES if cls not in (YUGUS_NODE_physics_world, YUGUS_NODE_physics_attract,
                                              YUGUS_NODE_physics_state, YUGUS_NODE_physics_controls)}
    actions[YUGUS_NODE_physics_world.bl_idname] = api.ActionSpec(
        _world_action, inputs=("Start", "Stop", "Reset"), configuration=world_configuration)
    actions[YUGUS_NODE_physics_attract.bl_idname] = api.ActionSpec(
        _attract_start, _attract_update, _attract_cancel, configuration=command_configuration)
    return api.ModuleSpec("physics", label="Prop Physics",
                          classes=(YUGUS_NS_physics_world, YUGUS_NS_physics_state, *NODES,
                                   YUGUS_OT_physics_emit, YUGUS_OT_physics_example),
                          evaluators={YUGUS_NODE_physics_world.bl_idname: _world_evaluate,
                                      YUGUS_NODE_physics_state.bl_idname: _state_evaluate},
                          actions=actions, menu_entries=tuple((cls.bl_idname, cls.bl_label) for cls in NODES),
                          cards={cls.bl_idname: api.CardSpec(cls.bl_label, "Operate the explicitly started prop world.",
                                                          lambda layout, node: node.draw_buttons(bpy.context, layout))
                                 for cls in NODES if cls.bl_idname in actions and cls != YUGUS_NODE_physics_world},
                          motion=motion, cleanup=cleanup, recover=recover)
