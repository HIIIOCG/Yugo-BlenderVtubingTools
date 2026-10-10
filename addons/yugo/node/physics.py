"""Live MuJoCo sessions, explicit transform ownership and global status."""
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
import json
import time
import uuid
import bpy
import numpy as np
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase
from .constants import TREE_ID
from .physics_data import PhysicsDescription, ObjectTransforms
from . import flow, targets

DESCRIPTION = 'YUGUS_NS_physics_description'
SOLVER = 'YUGUS_NODE_physics_solver'
RIGID = 'YUGUS_NODE_rigid_to_physics'
SPRING = 'YUGUS_NODE_spring_to_physics'
_worlds = {}
_stopped = set()
_failed_models = {}
_model_checks = {}
MODEL_CHECK_INTERVAL = 0.5
_rigid_owners = {}
_bone_applies = {}
FLAGS_MARKER = '_yugo_node_physics_lease_v1'
BONES_MARKER = '_yugo_node_bone_physics_leases_v1'


@dataclass(frozen=True)
class _Boundary:
    """One evaluated pose sample; integration never reads Blender per substep."""
    positions: object
    rotations: object
    armatures: dict
    parents: dict
    colliders: dict

    @classmethod
    def sample(cls, world):
        return cls(world.data.mocap_pos.copy(), world.data.mocap_quat.copy(),
                   {key: value.copy() for key, value in world._armature_world.items()},
                   {key: world._armature_world[key[0]] @ value
                    for key, value in world._parent_poses.items()},
                   {key: value.copy() for key, value in world._collider_matrices.items()})

    def same(self, other):
        return (np.array_equal(self.positions, other.positions)
                and np.array_equal(self.rotations, other.rotations)
                and self.armatures == other.armatures
                and self.parents == other.parents and self.colliders == other.colliders)

    def interpolate(self, other, factor):
        if factor <= 0:
            return self
        if factor >= 1:
            return other
        # Vectorized shortest-arc SLERP keeps the work proportional to moving
        # boundaries, rather than the number of simulated Spring bones.
        start, end = self.rotations, other.rotations
        dot = np.sum(start * end, axis=1)
        end = np.where((dot < 0)[:, None], -end, end)
        dot = np.clip(np.abs(dot), 0, 1)
        angle = np.arccos(dot)
        sine = np.sin(angle)
        regular = sine > 1e-8
        denominator = np.where(regular, sine, 1)
        first = np.where(regular, np.sin((1-factor)*angle)/denominator, 1-factor)
        second = np.where(regular, np.sin(factor*angle)/denominator, factor)
        rotations = first[:, None]*start + second[:, None]*end
        if len(rotations):
            rotations /= np.linalg.norm(rotations, axis=1)[:, None]
        def matrices(before, after):
            return {key: value.lerp(after[key], factor) for key, value in before.items()}
        return _Boundary(self.positions + factor*(other.positions-self.positions), rotations,
                         matrices(self.armatures, other.armatures),
                         matrices(self.parents, other.parents),
                         matrices(self.colliders, other.colliders))

    def apply(self, world):
        world.data.mocap_pos[:] = self.positions
        world.data.mocap_quat[:] = self.rotations
        world._armature_world = dict(self.armatures)
        # Interpolate parent frames in world space, then express them in the
        # sampled armature frame. Trace and transform conversion use the exact
        # same parent frame as the attachment mocap body.
        world._parent_poses = self.parent_frames()
        world._collider_matrices = dict(self.colliders)

    def parent_frames(self):
        inverse = {key: value.inverted() for key, value in self.armatures.items()}
        return {key: inverse[key[0]] @ value for key, value in self.parents.items()}


def _boundary_clock_start(state, scene, now):
    """Begin/resume an active clock without integrating time spent paused."""
    world = state['world']
    samples = state['boundaries']
    if samples:
        shift = now - state['boundary_clock']
        state['boundaries'] = samples = deque((stamp + shift, sample) for stamp, sample in samples)
        state['trace_origin'] += shift
    else:
        state['trace_origin'] = now - (world.tick / state['hz'] + state['debt'])
    world.update_boundaries(scene)
    sample = _Boundary.sample(world)
    state['observed_boundary'] = sample
    if not samples:
        samples.append((now-state['debt'], sample))
    samples.append((now, sample))
    state['boundary_clock'] = now
    state['last'] = now


def _advance_boundaries(state, count, observer=None):
    """Integrate known pose history at physics time, preserving catch-up debt."""
    world, samples = state['world'], state['boundaries']
    if count:
        state['presentation_enabled'] = True
    if world.tick == 0:
        # update_boundaries prepares a tick-zero preview at the newest pose.
        # Integration starts at the clock's original pose; the newest sample
        # must not teleport the dynamic attachment ahead of the first substep.
        initial = samples[0][1]
        for mocap_name, anchor_name in world._anchor_names.values():
            body = world._body_id(anchor_name)
            joint = int(world.model.body_jntadr[body])
            address = int(world.model.jnt_qposadr[joint])
            mocap = world._mocap_ids[mocap_name]
            world.data.qpos[address:address+3] = initial.positions[mocap]
            world.data.qpos[address+3:address+7] = initial.rotations[mocap]
    finish = world.tick + count
    origin, hz = state['trace_origin'], state['hz']
    while world.tick < finish:
        timestamp = origin + (world.tick + 1) / hz
        while len(samples) > 1 and samples[1][0] <= timestamp + 1e-10:
            samples.popleft()
        before_time, before = samples[0]
        after_time, after = samples[1] if len(samples) > 1 else (None, None)
        if after is None or before.same(after):
            batch = finish - world.tick
            if after is not None:
                batch = min(batch, max(1, int((after_time-origin)*hz + 1e-7)-world.tick))
            before.apply(world)
            if observer is None:
                world.step(batch, forward=False)
            else:
                world.step(batch, observer=lambda item, duration: observer(
                    item, origin+item.tick/hz, duration))
        else:
            factor = min(max((timestamp-before_time)/(after_time-before_time), 0), 1)
            sampled = before.interpolate(after, factor)
            sampled.apply(world)
            if observer is None:
                world.step(1, forward=False)
            else:
                world.step(1, observer=lambda item, duration: observer(item, timestamp, duration))

    if count and observer is None and not world._damped_bodies:
        world.forward()


class YUGUS_NS_physics_description(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = DESCRIPTION
    bl_label = 'Physics Description'
    def draw_color(self, context, node):
        return (0.16, 0.7, 0.55, 1)


class YUGUS_NODE_rigid_to_physics(targets.ObjectSelection, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = RIGID
    bl_label = 'Rigid Body to YugoPhysics'
    bl_icon = 'RIGID_BODY'
    def init(self, context):
        self._init_base(255)
        self.inputs.new(targets.OBJECTS, 'Objects')
        self.outputs.new(DESCRIPTION, 'Physics Description')
    def draw_buttons(self, context, layout):
        layout.label(text='Active / Passive from Blender')


def _collision_preview_updated(node, context):
    from . import spring_preview
    spring_preview.refresh(node, scene=getattr(context, 'scene', None))


class YUGUS_NODE_spring_to_physics(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = SPRING
    bl_label = 'Spring to YugoPhysics'
    bl_icon = 'BONE_DATA'
    show_collision_spheres: bpy.props.BoolProperty(
        name='Preview Collision Spheres',
        description='Show VRM Spring joint collision radii in the viewport',
        default=False, update=_collision_preview_updated)
    def init(self, context):
        self._init_base(255)
        self.inputs.new('YUGUS_NS_spring_data', 'Spring Data')
        self.outputs.new(DESCRIPTION, 'Physics Description')
    def draw_buttons(self, context, layout):
        layout.label(text='MuJoCo spring conversion')
        layout.prop(self, 'show_collision_spheres')
    def free(self):
        from . import spring_preview
        from .physics_inputs import clear_spring_description_cache
        clear_spring_description_cache(str(self.runtime_id))
        spring_preview.release(str(self.runtime_id))
        YUGUS_NodeBase.free(self)


def _button(layout, node, operation, text=None, icon='NONE'):
    op = layout.operator('yugu_node_studio.physics_action', text=operation.title() if text is None else text, icon=icon)
    op.tree_name, op.node_name, op.operation = node.id_data.name, node.name, operation


def ensure_solver_sockets(node):
    """Migrate controls in place so retained event and data links survive."""
    for name in ('Play',):
        socket = node.inputs.get(name)
        if socket is not None:
            node.inputs.remove(socket)
    socket = node.outputs.get('Updated')
    if socket is not None:
        node.outputs.remove(socket)
    for index, (kind, name) in enumerate(((DESCRIPTION, 'Physics Description'),
                                          (flow.EVENT, 'Reset'), (flow.EVENT, 'Stop'))):
        socket = node.inputs.get(name)
        if socket is None:
            socket = node.inputs.new(kind, name)
        current = list(node.inputs).index(socket)
        if current != index:
            node.inputs.move(current, index)
    for kind, name in (('YUGUS_NS_object_transforms', 'Object Transforms'),
                       (flow.TRANSFORMS, 'Bone Transforms'),
                       ('YUGUS_NS_simulation_data', 'Simulation Data')):
        if node.outputs.get(name) is None:
            node.outputs.new(kind, name)


class YUGUS_NODE_physics_solver(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = SOLVER
    bl_label = 'YugoPhysics Solver'
    bl_icon = 'PHYSICS'
    enabled: bpy.props.BoolProperty(name='Enabled', default=True)
    physics_hz: bpy.props.IntProperty(name='Physics Hz', default=240, min=30, max=2000)
    state_status: bpy.props.StringProperty(default='Waiting', options={'SKIP_SAVE'})
    last_summary: bpy.props.StringProperty(default='Connect Physics Description', options={'SKIP_SAVE'})
    trace_duration: bpy.props.FloatProperty(name='Recording Duration', default=10, min=0.1, max=120, unit='TIME')
    trace_sample_hz: bpy.props.IntProperty(name='Record Hz', default=60, min=1, max=240)
    trace_recording: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    trace_summary: bpy.props.StringProperty(default='No recording', options={'SKIP_SAVE'})
    def init(self, context):
        self._init_base(255)
        ensure_solver_sockets(self)
    def draw_buttons(self, context, layout):
        layout.prop(self, 'enabled')
        layout.label(text=self.state_status)
        state = _worlds.get(str(self.runtime_id))
        if self.state_status == 'Error' or (state and state.get('input_error')):
            layout.label(text=self.last_summary)
    def free(self):
        uid = str(self.runtime_id)
        stop(uid)
        _stopped.discard(uid)
        _failed_models.pop(uid, None)
        _model_checks.pop(uid, None)
        YUGUS_NodeBase.free(self)


class YUGUS_OT_physics_action(bpy.types.Operator):
    bl_idname = 'yugu_node_studio.physics_action'
    bl_label = 'Control Yugo Physics'
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    operation: bpy.props.EnumProperty(items=[(key, key.title(), '') for key in ('BUILD', 'PLAY', 'PAUSE', 'STOP', 'RESET', 'STEP')])
    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != SOLVER:
            return {'CANCELLED'}
        try:
            action(node, self.operation, scene=context.scene)
        except Exception as error:
            node.state_status = 'Error'
            node.last_summary = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def authored_settings():
    _refresh_rigid_owners()
    return {record['object'].name: dict(record['original']) for record in _rigid_owners.values()}


def _refresh_rigid_owners():
    """Names are write targets, not the identity of a live leased object."""
    current_owners = {}
    for record in tuple(_rigid_owners.values()):
        try:
            current = record['object'].name
        except ReferenceError:
            continue
        current_owners[current] = record
    _rigid_owners.clear()
    _rigid_owners.update(current_owners)


def _node(state):
    tree = bpy.data.node_groups.get(state['tree'])
    node = tree.nodes.get(state['node']) if tree else None
    return node if node and str(node.runtime_id) == state['uid'] and node.bl_idname == SOLVER else None


def _output_packet(state, port):
    """Project a requested port once per publication, sharing its display pose."""
    outputs = state['_outputs']
    if port in outputs:
        return outputs[port]
    world = state['world']
    pose = state.get('_presentation_pose')
    if pose is None:
        interpolate = state['_interpolate']
        pose = world.presentation_data(state['presentation_alpha']) if interpolate else world.data
        state['_presentation_pose'] = pose
    if port == 'Object Transforms':
        packet = replace(world.object_transforms(pose_data=pose), world_id=state['id'],
                         revision=state['description'].revision, tick=state['steps'])
        state['objects'] = packet
    else:
        boundary = state.get('observed_boundary', state['initial_boundary'])
        packet = world.bone_transforms(pose_data=pose, armature_world=boundary.armatures,
                                      parent_poses=boundary.parent_frames(),
                                      presentation_time=state['presentation_time'], commit=True)
        packet.physics_world_id = state['id']
        packet.physics_revision = state['description'].revision
        packet.physics_tick = state['steps']
        if packet.armature_name:
            from .data import SpringData
            packet.spring_data = SpringData('1.0', (), (), (), packet.armature_name)
        state['bones'] = packet
    outputs[port] = packet
    return packet


class _SolverOutputs(Mapping):
    """Port lookup requests only the projection that a consumer actually reads."""
    def __init__(self, node):
        self.uid = str(node.runtime_id)
        self.node = node
        self.aliases = {key: socket.name for socket in node.outputs
                        for key in (socket.identifier, socket.name)}

    def __getitem__(self, key):
        port = self.aliases[key]
        state = _worlds.get(self.uid)
        if port in {'Object Transforms', 'Bone Transforms'}:
            if state is None:
                return ObjectTransforms() if port == 'Object Transforms' else flow.BoneTransforms()
            return _output_packet(state, port)
        from . import spring_trace
        try:
            return spring_trace.output(_node(state) if state else self.node)
        except (AttributeError, ReferenceError):
            return None

    def __iter__(self):
        return iter(self.aliases)

    def __len__(self):
        return len(self.aliases)


def _publish(state):
    world = state['world']
    # Projection is needed only for connected transform ports or an explicit
    # read. Connected bone output commits its continuity gauge every motion
    # cycle, even when the downstream consumer reads less often.
    interpolate = state['status'] in {'Playing', 'Paused'} and world.tick > 0 and state.get('presentation_enabled', True)
    alpha = min(max(state['debt']*state['hz'], 0.0), 1.0) if interpolate else 1.0
    state['_interpolate'] = interpolate
    state['presentation_alpha'] = alpha
    state['presentation_time'] = max(0, world.tick-1+alpha)/state['hz'] if interpolate else world.tick/state['hz']
    state['_outputs'] = {}
    state['_presentation_pose'] = None
    state.pop('objects', None)
    state.pop('bones', None)
    node = _node(state)
    if node:
        for port in ('Object Transforms', 'Bone Transforms'):
            socket = node.outputs.get(port)
            if socket and socket.is_linked:
                _output_packet(state, port)
        desc = state['description']
        changed = node.state_status != state['status']
        if changed:
            node.state_status = state['status']
        elapsed = state['steps'] / state['hz']
        if changed or elapsed < state.get('summary_time', -1) or elapsed - state.get('summary_time', -1) >= MODEL_CHECK_INTERVAL:
            node.last_summary = state.get('input_error') or f"{len(desc.bodies)} bodies · {len(desc.springs)} springs · {elapsed:.2f} s"
            state['summary_time'] = elapsed


def _description(node, scene):
    from .runtime import EvaluationContext
    description = EvaluationContext(node.id_data, scene, 0).input(node, 'Physics Description', None)
    if description is not None and not isinstance(description, PhysicsDescription):
        raise ValueError('Connect Physics Description')
    return description


def _gravity(description, scene):
    return tuple(value * description.unit_scale for value in scene.gravity) if scene.use_gravity else (0, 0, 0)


def _stable_geometry(description, previous):
    """Ignore float32 matrix-decomposition noise, preserving authored changes.

    Blender can return scale 1 +/- 1e-7 when only an object's rotation changes.
    Stabilize derived geometry against the current world; masses, friction,
    damping and bindings still compare exactly through the model revision.
    """
    if description.revision == previous.revision:
        return description
    def value(current, old):
        if isinstance(current, tuple):
            return old if len(current) == len(old) and all(
                math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-12)
                for a, b in zip(current, old)) else current
        return old if math.isclose(current, old, rel_tol=1e-6, abs_tol=1e-12) else current
    old_bodies = {body.body_id: body for body in previous.bodies}
    bodies = tuple(replace(body, **{name: value(getattr(body, name), getattr(old_bodies[body.body_id], name))
                                  for name in ('size', 'geom_position', 'object_scale')})
                   if body.body_id in old_bodies else body for body in description.bodies)
    old_springs = {chain.chain_id: chain for chain in previous.springs}
    springs = tuple(replace(chain, armature_scale=value(chain.armature_scale, old_springs[chain.chain_id].armature_scale))
                    if chain.chain_id in old_springs else chain for chain in description.springs)
    old_colliders = {collider.collider_id: collider for collider in previous.colliders}
    colliders = tuple(replace(collider, radius=value(collider.radius, old_colliders[collider.collider_id].radius),
                              source_scale=value(collider.source_scale, old_colliders[collider.collider_id].source_scale))
                      if collider.collider_id in old_colliders else collider for collider in description.colliders)
    return replace(description, bodies=bodies, springs=springs, colliders=colliders)


def _build(node, scene, *, description=None, previous=None, now=None):
    from .physics_backend import World
    description = _description(node, scene) if description is None else description
    if not isinstance(description, PhysicsDescription):
        raise ValueError('Connect Physics Description')
    if not description.bodies and not description.springs:
        raise ValueError('Physics Description is empty')
    if previous and description.unit_scale == previous['description'].unit_scale:
        # Keep each surviving body's birth pose for Reset. Reading a body after
        # Apply must not silently turn its current simulated pose into a reset.
        births = {body.body_id: body for body in previous['description'].bodies}
        description = replace(description, bodies=tuple(
            replace(body, position=births[body.body_id].position,
                    quaternion=births[body.body_id].quaternion)
            if body.motion == 'DYNAMIC' and body.body_id in births
            and births[body.body_id].motion == 'DYNAMIC' else body
            for body in description.bodies))
    # Compile and validate before retiring the current world or changing Blender.
    gravity = _gravity(description, scene)
    world = World(description, timestep=1 / node.physics_hz, gravity=gravity)
    try:
        world.update_boundaries(scene)
        if previous:
            world.transfer_state_from(previous['world'])
    except Exception:
        world.close()
        raise
    uid = str(node.runtime_id)
    if previous:
        state = dict(previous)
        state.update(world=world, description=description, hz=node.physics_hz,
                     tree=node.id_data.name, node=node.name, scene=scene.name,
                     status='Playing', boundaries=deque(), last=None,
                     initial_boundary=_Boundary.sample(world), boundary_clock=None,
                     summary_time=-1, input_error=None)
        if node.physics_hz != previous['hz']:
            world.tick = state['steps'] = round(world.data.time * node.physics_hz)
        clock_now = time.monotonic() if now is None else now
        if previous['status'] == 'Playing' and previous['last'] is not None:
            state['debt'] += max(0.0, clock_now - previous['last'])
        # A topology change invalidates old mocap array dimensions. Begin a new
        # boundary history at the existing physics time, never at tick zero.
        try:
            _boundary_clock_start(state, scene, clock_now)
        except Exception:
            world.close()
            raise
    else:
        stop(uid)
        state = {'uid': uid, 'id': uuid.uuid4().hex, 'tree': node.id_data.name,
                 'node': node.name, 'scene': scene.name, 'description': description,
                 'world': world, 'hz': node.physics_hz, 'status': 'Ready',
                 'steps': 0, 'last': None, 'debt': 0.0,
                 'boundaries': deque(), 'initial_boundary': _Boundary.sample(world),
                 'boundary_clock': None, 'presentation_enabled': True}
    state['observed_boundary'] = state['initial_boundary']
    try:
        _publish(state)
    except Exception:
        world.close()
        raise
    _worlds[uid] = state
    if previous:
        previous['world'].close()
    return state


def _synchronize(node, scene, now):
    """Audit authored inputs; compile only a changed model, never a moving pose."""
    uid = str(node.runtime_id)
    state = _worlds.get(uid)
    if state:
        state['tree'], state['node'] = node.id_data.name, node.name
    try:
        description = _description(node, scene)
        if description is None or (not description.bodies and not description.springs):
            stop(uid)
            _failed_models.pop(uid, None)
            node.state_status, node.last_summary = 'Waiting', 'Connect Physics Description'
            return None
        if state and description.unit_scale == state['description'].unit_scale:
            description = _stable_geometry(description, state['description'])
        key = (description.revision, node.physics_hz, _gravity(description, scene))
        current = ((state['description'].revision, state['hz'], state['world'].gravity)
                   if state else None)
        if current == key:
            if state.pop('input_error', None):
                state['summary_time'] = -1
            _failed_models.pop(uid, None)
        else:
            failure = _failed_models.get(uid)
            if failure and failure[0] == key:
                raise ValueError(failure[1])
            try:
                state = _build(node, scene, description=description, previous=state, now=now)
            except Exception as error:
                _failed_models[uid] = (key, str(error))
                raise
            _failed_models.pop(uid, None)
        if state['status'] == 'Ready':
            state['status'], state['last'] = 'Playing', None
            _publish(state)
        return state
    except Exception as error:
        if state:
            state['input_error'] = str(error)
            state['summary_time'] = -1
            node.last_summary = str(error)
        else:
            node.state_status, node.last_summary = 'Error', str(error)
        return state


def action(node, operation, *, scene=None):
    scene = scene or bpy.context.scene
    uid = str(node.runtime_id)
    operation = operation.upper()
    if operation == 'STOP':
        _stopped.add(uid)
        _failed_models.pop(uid, None)
        stop(uid)
        node.state_status, node.last_summary = 'Stopped', 'Blender transforms and rigid body settings restored'
        return
    if node.mute or not node.enabled:
        raise ValueError('Enable YugoPhysics Solver')
    state = _worlds.get(uid)
    if operation in {'BUILD', 'PLAY', 'RESET'}:
        _model_checks.pop(uid, None)
        _stopped.discard(uid)
        _failed_models.pop(uid, None)
    if operation == 'BUILD':
        _build(node, scene)
        return
    if operation == 'RESET':
        state = _synchronize(node, scene, time.monotonic())
        if state is None:
            return
    if state is None:
        if operation == 'PAUSE':
            return
        state = _build(node, scene)
    if state['hz'] != node.physics_hz:
        raise ValueError('Physics Hz changed; Build the world again')
    if operation == 'PAUSE':
        from . import spring_trace
        spring_trace.finish(uid, 'Physics paused')
        state['status'], state['last'] = 'Paused', None
    elif operation == 'PLAY':
        state['status'], state['last'] = 'Playing', None
        _publish(state)
    elif operation == 'RESET':
        from . import spring_trace
        spring_trace.finish(uid, 'Physics reset')
        state['world'].reset()
        state['steps'], state['debt'], state['last'] = 0, 0.0, None
        state['boundaries'].clear()
        state['initial_boundary'] = _Boundary.sample(state['world'])
        state['observed_boundary'] = state['initial_boundary']
        state['presentation_enabled'] = True
        state['boundary_clock'] = None
        state['status'] = 'Playing'
    elif operation == 'STEP':
        from . import spring_trace
        spring_trace.finish(uid, 'Manual physics step')
        state['status'], state['last'] = 'Paused', None
        state['world'].update_boundaries(scene)
        state['world'].step(1)
        state['steps'] += 1
        state['boundaries'].clear()
        state['initial_boundary'] = _Boundary.sample(state['world'])
        state['observed_boundary'] = state['initial_boundary']
        state['presentation_enabled'] = False
        state['boundary_clock'] = None
        _publish(state)
    else:
        raise ValueError(f'Unknown physics operation: {operation}')
    _publish(state)


def has_world(world_id):
    return any(state['id'] == world_id and state['status'] in {'Playing', 'Paused'} for state in _worlds.values())


def _session(world_id, revision, tick):
    state = next((value for value in _worlds.values() if value['id'] == world_id), None)
    if not state or state['status'] not in {'Playing', 'Paused'}:
        raise ValueError('Physics solver is not running')
    if revision != state['description'].revision or tick != state['steps']:
        raise ValueError('Physics output is stale; read the current solver result')
    return state


def authorize_bones(packet):
    if packet.physics_world_id:
        _session(packet.physics_world_id, packet.physics_revision, packet.physics_tick)


def _read_marker(obj, key, default=None):
    if key not in obj:
        return default
    value = json.loads(obj[key])
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError(f'Invalid Yugo recovery metadata: {obj.name}')
    return value


def sync_bone_apply(node, packet, originals):
    uid = str(node.runtime_id)
    record = _bone_applies.get(uid)
    if (record and record['world'] == packet.physics_world_id and originals
            and record.get('originals') == originals
            and record['tree'] == node.id_data.name and record['node'] == node.name
            and all(bpy.data.objects.get(name) == obj and BONES_MARKER in obj
                    for name, obj in record['objects'].items())):
        # Recovery metadata changes with ownership, not every physics tick.
        # Rewriting armature ID properties here dirties Blender's dependency
        # graph and needlessly repeats JSON serialization at playback speed.
        return
    release_bone_apply(uid)
    if not packet.physics_world_id or not originals:
        return
    by_object = {}
    for (kind, object_name, name), original in originals.items():
        if kind not in {'bone', 'bone_location', 'object', 'object_location'}:
            continue
        if kind in {'bone', 'object'}:
            mode, quaternion, euler, axis = original
            original = (mode, tuple(quaternion), tuple(euler), tuple(axis))
        else:
            original = tuple(original)
        by_object.setdefault(object_name, []).append((kind, name, original))
    refs = {}
    for object_name, channels in by_object.items():
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f'Physics Apply target is unavailable: {object_name}')
        marker = _read_marker(obj, BONES_MARKER, {'schema': 1, 'owners': {}})
        official = flow._official.get(object_name)
        marker['owners'][uid] = {'world': packet.physics_world_id, 'channels': channels,
                                'official_enabled': official['enabled'] if official else None}
        obj[BONES_MARKER] = json.dumps(marker, separators=(',', ':'))
        refs[object_name] = obj
    _bone_applies[uid] = {'world': packet.physics_world_id, 'objects': refs,
                         'tree': node.id_data.name, 'node': node.name, 'originals': dict(originals)}


def resolve_bone_object(name):
    for record in _bone_applies.values():
        obj = record['objects'].get(name)
        if obj:
            try:
                obj.name
                return obj
            except ReferenceError:
                pass
    return None


def release_bone_apply(uid):
    record = _bone_applies.pop(str(uid), None)
    if not record:
        return
    for obj in record['objects'].values():
        try:
            marker = _read_marker(obj, BONES_MARKER)
            if marker:
                marker['owners'].pop(str(uid), None)
                if marker['owners']:
                    obj[BONES_MARKER] = json.dumps(marker, separators=(',', ':'))
                else:
                    del obj[BONES_MARKER]
        except ReferenceError:
            pass


def _recovery_vector(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f'Expected a {size}-component recovery vector')
    if any(type(item) not in {int, float} for item in value):
        raise ValueError('Recovery vectors must contain finite numbers')
    try:
        value = tuple(float(item) for item in value)
    except OverflowError as error:
        raise ValueError('Recovery vectors must contain finite numbers') from error
    # Blender's pose channels store single-precision floats. A finite JSON
    # double outside that range would still become infinity when assigned.
    limit = float(np.finfo(np.float32).max)
    if any(not math.isfinite(item) or abs(item) > limit for item in value):
        raise ValueError('Recovery vectors must contain finite numbers within Blender float range')
    return value


def _recovery_plan(obj, key, marker):
    """Validate every channel before exposing any of its values to Blender RNA."""
    if type(marker.get('schema')) is not int or marker['schema'] != 1:
        raise ValueError('Unsupported recovery schema')
    if not obj.is_editable:
        raise ValueError('Recovery object is read-only')
    if key == FLAGS_MARKER:
        original = marker.get('original')
        if (not isinstance(original, dict) or original.get('type') not in {'ACTIVE', 'PASSIVE'}
                or type(original.get('enabled')) is not bool
                or type(original.get('kinematic')) is not bool):
            raise ValueError('Invalid rigid body recovery flags')
        if obj.rigid_body is None:
            raise ValueError('Rigid Body settings are unavailable')
        return [(obj.rigid_body, [(name, original[name]) for name in ('type', 'kinematic', 'enabled')])]
    owners = marker.get('owners')
    if not isinstance(owners, dict):
        raise ValueError('Recovery owners must be a mapping')
    plan, seen, official_values = [], set(), set()
    rotation_modes = {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
    for uid, record in owners.items():
        if not isinstance(uid, str) or not uid or not isinstance(record, dict):
            raise ValueError('Invalid recovery owner')
        if not isinstance(record.get('world'), str) or not record['world']:
            raise ValueError('Recovery owner must name its physics world')
        if 'official_enabled' not in record or (record['official_enabled'] is not None
                and type(record['official_enabled']) is not bool):
            raise ValueError('Invalid official Spring recovery flag')
        if record['official_enabled'] is not None:
            official_values.add(record['official_enabled'])
        if not isinstance(record.get('channels'), list):
            raise ValueError('Recovery channels must be a list')
        for channel in record['channels']:
            if not isinstance(channel, list) or len(channel) != 3:
                raise ValueError('Invalid recovery channel record')
            kind, name, original = channel
            if not isinstance(kind, str) or kind not in {'bone', 'bone_location', 'object', 'object_location'}:
                raise ValueError('Invalid recovery channel kind')
            if not isinstance(name, str) or (not name if kind.startswith('bone') else bool(name)):
                raise ValueError('Invalid recovery channel name')
            if (kind, name) in seen:
                raise ValueError('Duplicate recovery channel ownership')
            seen.add((kind, name))
            target = obj
            if kind.startswith('bone'):
                target = obj.pose.bones.get(name) if obj.type == 'ARMATURE' else None
                if target is None:
                    raise ValueError(f'Recovery bone is unavailable: {name}')
            if kind in {'bone', 'object'}:
                if (not isinstance(original, list) or len(original) != 4
                        or not isinstance(original[0], str) or original[0] not in rotation_modes):
                    raise ValueError('Invalid recovery rotation mode or record')
                values = [('rotation_mode', original[0]),
                          ('rotation_quaternion', _recovery_vector(original[1], 4)),
                          ('rotation_euler', _recovery_vector(original[2], 3)),
                          ('rotation_axis_angle', _recovery_vector(original[3], 4))]
            else:
                values = [('location', _recovery_vector(original, 3))]
            plan.append((target, values))
    if official_values:
        if len(official_values) != 1 or obj.type != 'ARMATURE':
            raise ValueError('Conflicting or misplaced official Spring recovery flags')
        from .data import SpringData
        spring = flow.runtime._official_spring_bone(obj, SpringData('1.0', (), (), (), obj.name))
        if spring is None:
            raise ValueError('Official Spring settings are unavailable')
        plan.append((spring, [('enable_animation', official_values.pop())]))
    return plan


def _apply_recovery_plan(obj, key, plan):
    before = []
    for target, values in plan:
        snapshot = []
        for name, _value in values:
            if target.is_property_readonly(name):
                raise ValueError(f'Recovery property is read-only: {name}')
            value = getattr(target, name)
            snapshot.append((name, value if isinstance(value, (str, bool)) else tuple(value)))
        before.append((target, snapshot))
    try:
        for target, values in plan:
            for name, value in values:
                setattr(target, name, value)
        del obj[key]
    except Exception:
        for target, values in reversed(before):
            try:
                for name, value in values:
                    setattr(target, name, value)
            except Exception as error:
                print(f'Yugo physics recovery rollback failed for "{obj.name}" ({key}): {error}')
        raise


def recover_saved_leases():
    """Recover independent markers; retain failed metadata and return successes."""
    recovered = 0
    for obj in tuple(bpy.data.objects):
        for key in (FLAGS_MARKER, BONES_MARKER):
            try:
                if key not in obj:
                    continue
                marker = _read_marker(obj, key)
                plan = _recovery_plan(obj, key, marker)
                _apply_recovery_plan(obj, key, plan)
                recovered += 1
            except Exception as error:
                print(f'Yugo physics recovery kept metadata for "{obj.name}" ({key}): {error}')
    return recovered


def authorize_apply(packet, node):
    """Lease only explicitly applied dynamic bodies, after sink preflight."""
    _refresh_rigid_owners()
    if not packet.world_id:
        for record in packet.records:
            obj = bpy.data.objects.get(record.object_name)
            rb = obj.rigid_body if obj else None
            if rb and (record.object_name in _rigid_owners or (rb.enabled and rb.type == 'ACTIVE' and not rb.kinematic)):
                raise ValueError(f'Object is controlled by rigid body physics: {record.object_name}')
        release_apply(str(node.runtime_id))
        return
    state = _session(packet.world_id, packet.revision, packet.tick)
    bodies = {body.object_name: body for body in state['description'].bodies if body.motion == 'DYNAMIC'}
    uid = str(node.runtime_id)
    for item in packet.records:
        if item.object_name not in bodies:
            raise ValueError(f'Object is outside this physics world: {item.object_name}')
        owner = _rigid_owners.get(item.object_name)
        if owner and (owner['world'] != state['id'] or owner['apply'] != uid):
            raise ValueError(f'Rigid body already has another owner: {item.object_name}')
    selected = {item.object_name for item in packet.records}
    for name, owner in tuple(_rigid_owners.items()):
        if owner['apply'] == uid and name not in selected:
            _restore_rigid(name, owner)
    acquired = []
    try:
        for name in selected:
            if name in _rigid_owners:
                continue
            obj = bpy.data.objects.get(name)
            rb = obj.rigid_body if obj else None
            if rb is None:
                raise ValueError(f'Rigid Body settings were removed: {name}; Build again')
            inherited = _read_marker(obj, FLAGS_MARKER)
            original = (dict(inherited['original']) if inherited else
                        {'enabled': rb.enabled, 'kinematic': rb.kinematic, 'type': rb.type})
            obj[FLAGS_MARKER] = json.dumps({'schema': 1, 'original': original}, separators=(',', ':'))
            _rigid_owners[name] = {'original': original, 'world': state['id'], 'apply': uid, 'object': obj}
            acquired.append(name)
            rb.kinematic, rb.enabled = True, False
    except Exception:
        for name in acquired:
            _restore_rigid(name, _rigid_owners[name])
        raise


def _restore_rigid(name, owner):
    obj = owner['object']
    try:
        if obj and obj.rigid_body:
            obj.rigid_body.type = owner['original']['type']
            obj.rigid_body.kinematic = owner['original']['kinematic']
            obj.rigid_body.enabled = owner['original']['enabled']
        if obj and FLAGS_MARKER in obj:
            del obj[FLAGS_MARKER]
    except ReferenceError:
        pass
    _rigid_owners.pop(name, None)


def release_apply(uid):
    for name, owner in tuple(_rigid_owners.items()):
        if owner['apply'] == uid:
            _restore_rigid(name, owner)


def stop(uid):
    from . import spring_trace
    spring_trace.finish(uid, 'Physics stopped')
    state = _worlds.pop(str(uid), None)
    if state is None:
        return
    from . import object_transforms
    object_transforms.release_world(state['id'])
    for apply_uid, record in tuple(_bone_applies.items()):
        if record['world'] == state['id']:
            flow.release(apply_uid)
    state['world'].close()
    node = _node(state)
    if node:
        node.state_status = 'Stopped'


def _graph_signature(node):
    """Inspect only the description's wiring, excluding downstream consumers."""
    pending, visited, nodes, links = [node], set(), [], []
    while pending:
        current = pending.pop()
        identity = current.as_pointer()
        if identity in visited:
            continue
        visited.add(identity)
        nodes.append((identity, current.bl_idname, bool(current.mute),
                      getattr(current, 'data_type', None), getattr(current, 'enabled', None)))
        sockets = ((current.inputs.get('Physics Description'),) if current is node else current.inputs)
        for socket in sockets:
            if socket is None:
                continue
            for link in socket.links:
                links.append((link.from_node.as_pointer(), link.from_socket.identifier,
                              identity, link.to_socket.identifier,
                              bool(link.is_valid), bool(link.is_muted)))
                pending.append(link.from_node)
    return tuple(nodes), tuple(links)


def _model_signature(node, scene, graph):
    return (graph, node.physics_hz, float(scene.unit_settings.scale_length),
            bool(scene.use_gravity), tuple(scene.gravity))


def _check_model(node, scene, now, signature):
    _model_checks[str(node.runtime_id)] = (signature, now)
    return _synchronize(node, scene, now)


def tick(now=None):
    from . import object_transforms, spring_trace
    now = time.monotonic() if now is None else float(now)
    if not math.isfinite(now):
        raise ValueError('Physics timestamp must be finite')
    live = set()
    for tree in tuple(bpy.data.node_groups):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tuple(tree.nodes):
            if node.bl_idname != SOLVER:
                continue
            uid = str(node.runtime_id)
            live.add(uid)
            state = _worlds.get(uid)
            scene = bpy.data.scenes.get(state['scene']) if state else bpy.context.scene
            if node.mute or not node.enabled or scene is None:
                stop(uid)
                _stopped.discard(uid)
                _failed_models.pop(uid, None)
                _model_checks.pop(uid, None)
                if node.state_status != 'Disabled':
                    node.state_status = 'Disabled'
                continue
            if uid in _stopped:
                continue
            if state:
                state['tree'], state['node'] = tree.name, node.name
            socket = node.inputs.get('Physics Description')
            if socket is None or not socket.is_linked:
                stop(uid)
                _failed_models.pop(uid, None)
                _model_checks.pop(uid, None)
                if node.state_status != 'Waiting':
                    node.state_status, node.last_summary = 'Waiting', 'Connect Physics Description'
                continue
            signature = _model_signature(node, scene, _graph_signature(node))
            checked = _model_checks.get(uid)
            if (checked is None or checked[0] != signature or now < checked[1]
                    or now-checked[1] >= MODEL_CHECK_INTERVAL-1e-10):
                _check_model(node, scene, now, signature)
    for uid in set(_worlds) - live:
        stop(uid)
    _stopped.intersection_update(live)
    for registry in (_failed_models, _model_checks):
        for uid in set(registry) - live:
            registry.pop(uid, None)
    object_transforms.maintenance()
    for apply_uid, record in tuple(_bone_applies.items()):
        tree = bpy.data.node_groups.get(record['tree'])
        node = tree.nodes.get(record['node']) if tree else None
        valid = node and str(node.runtime_id) == apply_uid and apply_uid in flow.live_apply_ids(tree)
        if valid:
            try:
                from .runtime import EvaluationContext
                packet = EvaluationContext(tree, bpy.context.scene, 0).input(node, 'Bone Transform', None)
                valid = isinstance(packet, flow.BoneTransforms) and packet.physics_world_id == record['world']
            except Exception:
                valid = False
        if not valid:
            flow.release(apply_uid)
    for uid, state in tuple(_worlds.items()):
        node = _node(state)
        scene = bpy.data.scenes.get(state['scene'])
        if node is None or scene is None or node.mute or not node.enabled:
            stop(uid)
            continue
        if state['status'] != 'Playing':
            continue
        try:
            if node.physics_hz != state['hz']:
                raise ValueError('Physics Hz changed; Build the world again')
            if state['last'] is None:
                _boundary_clock_start(state, scene, now)
                continue
            if now < state['last'] or (now == state['last'] and not state['debt']):
                continue
            elapsed = max(0.0, now - state['last'])
            if not state['boundaries']:
                # Record Both supplies its shared origin after Build, without
                # running the ordinary first clock tick. Keep that build pose
                # as the initial sample, rather than backdating today's pose.
                state['boundaries'].append((state['last'], state['initial_boundary']))
                state.setdefault('trace_origin', state['last']-state['world'].tick/state['hz'])
            state['last'] = now
            state['boundary_clock'] = now
            state['debt'] += elapsed
            from .physics_backend import BoundaryModelChanged
            try:
                state['world'].update_boundaries(scene, forward=False)
            except BoundaryModelChanged:
                # A real boundary scale edit needs matching native geometry
                # before it can be sampled. Audit this exceptional change now
                # and keep the surviving world's state and existing time debt.
                signature = _model_signature(node, scene, _graph_signature(node))
                checked = _model_checks.get(uid)
                if (state.get('input_error') and checked and checked[0] == signature
                        and 0 <= now-checked[1] < MODEL_CHECK_INTERVAL-1e-10):
                    # An incompatible edit cannot be sampled using the old
                    # geometry. Retry failed descriptions at the normal rate,
                    # including when this tick's scheduled audit already failed.
                    continue
                state = _check_model(node, scene, now, signature)
                if state is None or state.get('input_error'):
                    continue
                state['world'].update_boundaries(scene, forward=False)
            observed = _Boundary.sample(state['world'])
            state['observed_boundary'] = observed
            state['boundaries'].append((now, observed))
            count = min(240, int((state['debt'] + 1e-10) * state['hz']))
            if not count:
                if state['world'].tick:
                    _publish(state)
                continue
            if state['description'].springs and spring_trace.interested(node):
                _advance_boundaries(state, count, observer=lambda world, stamp, step_ms:
                                    spring_trace.capture_mujoco(node, state, stamp, step_ms))
            else:
                _advance_boundaries(state, count)
            state['steps'] += count
            state['debt'] = max(0.0, state['debt'] - count / state['hz'])
            _publish(state)
        except Exception as error:
            stop(uid)
            node.state_status, node.last_summary = 'Error', str(error)


def evaluate(context, node):
    from .physics_inputs import describe_rigid, cached_describe_spring
    from .runtime import _output
    if node.bl_idname == RIGID:
        selected = context.input(node, 'Objects', targets.selected_objects(node))
        if not isinstance(selected, targets.Objects):
            raise ValueError('Connect Objects List')
        return _output(node, 'Physics Description', describe_rigid(selected, context.scene, authored_settings=authored_settings()))
    if node.bl_idname == SPRING:
        value = context.input(node, 'Spring Data', None)
        return _output(node, 'Physics Description', cached_describe_spring(
            value, context.scene, cache_key=str(node.runtime_id)))
    if node.bl_idname == SOLVER:
        return _SolverOutputs(node)
    return None


def clear():
    from .physics_inputs import clear_spring_description_cache
    clear_spring_description_cache()
    from . import spring_preview
    spring_preview.clear()
    for uid in tuple(_worlds):
        stop(uid)
    for name, owner in tuple(_rigid_owners.items()):
        _restore_rigid(name, owner)
    _stopped.clear()
    _failed_models.clear()
    _model_checks.clear()


def register_runtime():
    from . import scheduler, spring_preview
    # Native motion Apply precedes boundary sampling; MuJoCo publishes afterward.
    scheduler.add_motion('physics', lambda context, delta: tick(), priority=120)
    scheduler.add_motion('spring_collision_preview', spring_preview.tick, priority=130)


def unregister_runtime():
    from . import scheduler
    scheduler.remove_motion('physics')
    scheduler.remove_motion('spring_collision_preview')
    clear()


def _draw_global(layout):
    found = False
    layout.label(text='MuJoCo · default engine')
    for tree in bpy.data.node_groups:
        if tree.bl_idname != TREE_ID:
            continue
        for node in tree.nodes:
            if node.bl_idname != SOLVER:
                continue
            found = True
            box = layout.box()
            box.label(text=f'{tree.name} / {node.name}')
            box.label(text=node.state_status)
            box.label(text=node.last_summary)
            state = _worlds.get(str(node.runtime_id))
            if state:
                world = state['world']
                box.label(text=f"MuJoCo {world.engine.__version__} · CPU · {state['hz']} Hz")
                box.label(text=f"{world.data.ncon} contacts · {len(world._spring_ids)} spring bones")
                for body in state['description'].bodies:
                    box.label(text=f"{body.object_name} · {body.motion.title()}", icon='OBJECT_DATA')
                for name in dict.fromkeys(chain.armature_name for chain in state['description'].springs):
                    box.label(text=f"{name} · Spring", icon='ARMATURE_DATA')
                count = sum(owner['world'] == state['id'] for owner in _rigid_owners.values())
                box.label(text=f'{count} objects applied')
            row = box.row(align=True)
            _button(row, node, 'RESET', icon='FILE_REFRESH')
            _button(row, node, 'STOP', icon='SNAP_FACE')
    if not found:
        layout.label(text='Add a YugoPhysics Solver')


class YUGUS_PT_physics(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Yugo'
    bl_label = 'Yugo Physics'
    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, 'space_data', None), 'tree_type', '') == TREE_ID
    def draw(self, context):
        _draw_global(self.layout)


class YUGUS_PT_physics_view3d(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Yugo'
    bl_label = 'Yugo Physics'
    def draw(self, context):
        _draw_global(self.layout)


CLASSES = (YUGUS_NS_physics_description, YUGUS_NODE_rigid_to_physics,
           YUGUS_NODE_spring_to_physics, YUGUS_NODE_physics_solver,
           YUGUS_OT_physics_action, YUGUS_PT_physics, YUGUS_PT_physics_view3d)
