"""Opt-in, engine-neutral Spring time series. Recording never applies a pose."""
from dataclasses import asdict
from datetime import datetime, timezone
import copy
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import bpy
from bpy_extras.io_utils import ExportHelper
from mathutils import Quaternion, Vector
from .physics_inputs import SPRING_REFERENCE_MASS

SCHEMA = 'yugo.spring-comparison'
SCHEMA_VERSION = 1
SOCKET = 'YUGUS_NS_simulation_data'
OUTPUT = 'Simulation Data'
LEGACY = 'YUGUS_NODE_spring_simulation'
MUJOCO = 'YUGUS_NODE_physics_solver'
MAX_BONE_SAMPLES = 250000
MAX_CAPTURE_BYTES = 256 * 1024 * 1024
_records = {}
_latest = {}


def _uid(node):
    return str(node.runtime_id)


def _node(record):
    tree = bpy.data.node_groups.get(record['tree'])
    node = tree.nodes.get(record['node']) if tree else None
    return node if node and _uid(node) == record['uid'] else None


def _matrix_m(matrix, units):
    result = matrix.copy()
    result.translation *= units
    return tuple(tuple(row) for row in result)


def _scene_frame(scene):
    return float(scene.frame_current) + float(scene.frame_subframe)


def _metadata(node, scene, armature, settings, hz, engine):
    units = float(scene.unit_settings.scale_length)
    rest = [dict(name=b.name, parent=b.parent.name if b.parent else '',
                 matrix_armature_m=_matrix_m(b.matrix_local, units), length_m=b.length*units,
                 inherit_rotation=b.use_inherit_rotation, inherit_scale=b.inherit_scale)
            for b in armature.data.bones]
    fingerprint = hashlib.sha256(json.dumps(rest, sort_keys=True).encode()).hexdigest()
    bindings=[]
    for collider in settings.get('colliders', ()):
        name=collider.get('object_name', collider.get('object', ''))
        if not name:continue
        obj=scene.objects.get(name)
        bindings.append(dict(name=name,available=obj is not None,
            parent=obj.parent.name if obj and obj.parent else '',
            parent_type=obj.parent_type if obj else '',parent_bone=obj.parent_bone if obj else '',
            in_view_layer=(name in bpy.context.view_layer.objects) if scene==bpy.context.scene else None,
            collections=[c.name for c in obj.users_collection] if obj else []))
    warnings=[f"Collider {b['name']} is outside the current View Layer; its object transform may be frozen"
              for b in bindings if b['in_view_layer'] is False]
    return dict(engine=engine, blender_version=bpy.app.version_string, addon_version='0.10.0',
                scene=scene.name, armature=armature.name, rest_signature=fingerprint,
                rest_bones=rest, unit_scale=units, armature_scale=tuple(armature.matrix_world.to_scale()),
                coordinates=dict(positions='metres', velocity='metres/second', quaternion='w,x,y,z',
                                 angular_velocity='radians/second', local_rotation='Blender matrix_basis',
                                 armature_position='armature object axes, in metres'),
                physics_hz=hz, physics_settings=settings,
                collider_bindings=bindings,input_warnings=warnings,
                boundary_policy='interpolated between input samples',
                presentation_latency_s=1/hz,
                graph_output_policy=('interpolated tail-direction frames with animated Spring attachments'
                                     if engine=='MUJOCO' else 'interpolated SpringBone frames'),
                sample_kind='solved physics state before presentation interpolation and frame projection',
                fps=float(scene.render.fps)/scene.render.fps_base)


def _acquire_official(record, armature, data):
    from . import flow, runtime
    spring = runtime._official_spring_bone(armature, data)
    if spring is None:
        return
    existing = flow._official.setdefault(armature.name, dict(enabled=bool(spring.enable_animation), data=data, owners=set()))
    existing['owners'].add(record['lease'])
    if spring.enable_animation:
        spring.enable_animation = False


def _validate_shared_source(nodes, scene):
    """Same-armature comparisons must not feed either Spring output into it."""
    from . import flow, runtime
    bindings = {}
    for node in nodes:
        context = runtime.EvaluationContext(node.id_data, scene, 0)
        if node.bl_idname == LEGACY:
            data = context.input(node, 'Spring Data', None)
            name = getattr(data, 'armature_name', '')
        else:
            description = context.input(node, 'Physics Description', None)
            name = description.springs[0].armature_name if getattr(description, 'springs', ()) else ''
        if not name:
            raise ValueError(f'{node.name}: connect loaded Spring input')
        bindings.setdefault(name, set()).add(_uid(node))
    for name, sources in bindings.items():
        if len(sources)<2:
            continue
        for tree in bpy.data.node_groups:
            if tree.bl_idname != 'YUGUS_NT_humanoid_motion':
                continue
            for apply in tree.nodes:
                if apply.bl_idname != 'YUGUS_NODE_apply_rotation' or not apply.enabled or apply.mute:
                    continue
                if flow._upstream_ids(apply) & sources:
                    raise ValueError('Same-armature comparison: disconnect Spring outputs from Apply; recording runs both solvers without Apply. Use separate model copies for two previews.')


def start(nodes, *, scene=None, now=None):
    """Reset a group at one shared clock origin. No bone channels are written."""
    from . import flow, physics, runtime
    from .data import SpringData
    scene = scene or bpy.context.scene
    nodes = tuple(nodes)
    from .availability import require_available
    for node in nodes:
        require_available(node)
    if not nodes or any(n.bl_idname != MUJOCO or n.mute for n in nodes):
        raise ValueError('Select enabled YugoPhysics Solver nodes')
    _validate_shared_source(nodes, scene)
    # Validate authored inputs before replacing a previous recording.
    descriptions = {}
    for node in nodes:
        if node.bl_idname == MUJOCO and not node.enabled:
            raise ValueError('Enable YugoPhysics Solver before recording')
        context = runtime.EvaluationContext(node.id_data, scene, 0)
        key = 'Spring Data' if node.bl_idname==LEGACY else 'Physics Description'
        descriptions[_uid(node)] = context.input(node, key, None)
    origin = None if now is None else float(now)
    if origin is not None and not math.isfinite(origin):
        raise ValueError('Recording timestamp must be finite')
    group_id = uuid.uuid4().hex
    duration = float(nodes[0].trace_duration)
    sample_hz = min(float(nodes[0].trace_sample_hz), *(60 if n.bl_idname==LEGACY else n.physics_hz for n in nodes))
    started = []
    try:
        # Retire/build native worlds before initializing the legacy boundary:
        # their Stop may release an old Apply lease and restore a pose.
        for node in nodes:
            if node.bl_idname==MUJOCO:
                physics.action(node, 'BUILD', scene=scene)
        if origin is None:
            origin = time.monotonic()
        for node in nodes:
            uid = _uid(node)
            finish(uid, 'Replaced by a new recording')
            data = descriptions[uid]
            if node.bl_idname==LEGACY:
                flow.reload_spring(node)
                flow.spring_value(node, data, origin, 0)
                state = flow._springs[uid]
                if not state['solver'].output_bone_names:
                    raise ValueError('No Spring bones to record')
                state['solver'].trace_enabled = True
                armature = bpy.data.objects.get(data.armature_name)
                metadata = _metadata(node,scene,armature,asdict(data),60,'LEGACY')
            else:
                state = physics._worlds[uid]
                data = state['description']
                state['status'], state['last'] = 'Playing', origin
                state['trace_origin'] = origin
                armature = bpy.data.objects.get(data.springs[0].armature_name)
                metadata = _metadata(node,scene,armature,asdict(data),state['hz'],'MUJOCO')
                metadata['engine_version'] = state['world'].engine.__version__
                context = runtime.EvaluationContext(node.id_data, scene, 0)
                upstream = flow._upstream_ids(node)
                metadata['source_spring_data'] = [dict(node=n.name, mass_kg=SPRING_REFERENCE_MASS, stiffness_scale=1.0,
                    spring_data=asdict(context.input(n, 'Spring Data', None)))
                    for n in node.id_data.nodes if n.bl_idname==physics.SPRING and _uid(n) in upstream]
                data = SpringData('1.0', (), (), (), armature.name)
            record = dict(uid=uid,tree=node.id_data.name,node=node.name,scene=scene.name,
                          session_id=uuid.uuid4().hex,comparison_id=group_id,origin=origin,
                          created_utc=datetime.now(timezone.utc).isoformat(),metadata=metadata,
                          duration=duration,sample_hz=sample_hz,
                          active=True,reason='',frames=[],next_sample=1/sample_hz,previous=None,bytes=0,
                          lease='spring-trace:'+uid,limit=max(1, MAX_BONE_SAMPLES//max(1,len(state['solver'].output_bone_names) if node.bl_idname==LEGACY else len(state['world']._spring_ids))))
            _records[uid] = record
            _latest.pop(uid,None)
            started.append(uid)
            _acquire_official(record,armature,data)
            node.trace_recording=True
            node.trace_summary='Recording · 0 samples'
            if node.bl_idname==MUJOCO:
                physics._publish(state)
    except Exception:
        for uid in started:
            finish(uid,'Recording initialization failed')
        raise
    return group_id


def finish(uid, reason='Stopped'):
    from . import flow
    uid = str(uid)
    record = _records.get(uid)
    if not record or not record['active']:
        return
    record['active'], record['reason'] = False, str(reason)
    flow._release_official(record['lease'])
    node = _node(record)
    state = flow._springs.get(uid)
    if state:
        socket = node.outputs.get(OUTPUT) if node else None
        state['solver'].trace_enabled = bool(socket and socket.is_linked)
    if node:
        node.trace_recording=False
        node.trace_summary=f"{reason} · {len(record['frames'])} samples"


def active(uid):
    record = _records.get(str(uid))
    return bool(record and record['active'])


def legacy_ids():
    return {uid for uid,r in _records.items() if r['active'] and r['metadata']['engine']=='LEGACY'}


def interested(node):
    socket = getattr(node, 'outputs', {}).get(OUTPUT)
    return active(_uid(node)) or bool(socket and socket.is_linked)


def latest(node):
    return copy.deepcopy(_latest.get(_uid(node)))


def output(node):
    """Large diagnostic packets are copied only for connected data consumers."""
    socket = node.outputs.get(OUTPUT)
    return latest(node) if socket and socket.is_linked else None


def _due(node, simulation_s):
    record = _records.get(_uid(node))
    if not record or not record['active']:
        socket = getattr(node, 'outputs', {}).get(OUTPUT)
        previous = _latest.get(_uid(node))
        interval = 1 / max(1, float(getattr(node, 'trace_sample_hz', 60)))
        return bool(socket and socket.is_linked and (previous is None or simulation_s < previous['simulation_time_s']
                    or simulation_s-previous['simulation_time_s']>=interval-1e-9))
    if simulation_s > record['duration'] + 1e-9:
        finish(_uid(node),'Duration reached')
        return False
    return simulation_s >= record['next_sample']-1e-9


def _record(node, scene, snapshot):
    uid=_uid(node)
    record=_records.get(uid)
    previous=record.get('previous') if record and record['active'] else _latest.get(uid)
    dt=snapshot['simulation_time_s']-previous['simulation_time_s'] if previous else 0
    for name,bone in snapshot['bones'].items():
        old=previous['bones'].get(name) if previous else None
        bone['tail_velocity_world_m_s']=None
        bone['angular_velocity_local_rad_s']=None
        if old and dt>0:
            bone['tail_velocity_world_m_s']=tuple((Vector(bone['tail_world_m'])-Vector(old['tail_world_m']))/dt)
            delta=Quaternion(old['local_rotation_wxyz']).rotation_difference(Quaternion(bone['local_rotation_wxyz']))
            if delta.w<0:delta.negate()
            bone['angular_velocity_local_rad_s']=tuple(delta.axis*(delta.angle/dt))
    snapshot.update(scene_frame_at_capture=_scene_frame(scene),
                    scene_time_at_capture_s=_scene_frame(scene)/(scene.render.fps/scene.render.fps_base),
                    measured_at_monotonic_s=time.monotonic())
    if record and record['active']:
        snapshot['sample_target_s'] = record['next_sample']
    encoded=json.dumps(snapshot,allow_nan=False,separators=(',',':'))
    snapshot=json.loads(encoded)
    _latest[uid]=snapshot
    if record and record['active']:
        size=len(encoded.encode('utf-8'))
        if sum(r['bytes'] for r in _records.values())+size>MAX_CAPTURE_BYTES:
            for key in tuple(_records):finish(key,'Capture memory limit reached; export and clear recordings')
            return
        # The first sampled velocity is undefined rather than a fabricated zero.
        record['frames'].append(snapshot)
        record['bytes']+=size
        record['previous']=snapshot
        record['next_sample']=(math.floor((snapshot['simulation_time_s']+1e-9)*record['sample_hz'])+1)/record['sample_hz']
        count=len(record['frames'])
        if count%max(1,int(record['sample_hz']/5))==0 or count==1:
            node.trace_summary=f'Recording · {count} samples'
        if count>=record['limit']:
            finish(uid,'Capture limit reached')
        elif snapshot['simulation_time_s']>=record['duration']-1e-9:
            finish(uid,'Duration reached')


def capture_legacy(node, state, boundary, step, timestamp, step_ms):
    elapsed=step/60
    if not _due(node,elapsed):return
    scene=bpy.context.scene
    armature=bpy.data.objects.get(state['data'].armature_name)
    units=float(scene.unit_settings.scale_length)
    inverse=boundary.armature_world.inverted()
    bones={}
    for name,raw in state['solver'].trace_bones.items():
        head,tail=Vector(raw['head_world']),Vector(raw['tail_world'])
        bones[name]=dict(parent_bone=raw['parent_bone'],local_rotation_wxyz=raw['local_rotation_wxyz'],
                         world_rotation_wxyz=raw['world_rotation_wxyz'],head_world_m=tuple(head*units),tail_world_m=tuple(tail*units),
                         head_armature_m=tuple((inverse@head)*units),tail_armature_m=tuple((inverse@tail)*units),
                         radius_m=raw['radius_world']*units)
    from .spring_solver import SphereCollider,CapsuleCollider,PlaneCollider
    colliders=[]
    for index,c in enumerate(boundary.colliders):
        source=state['solver'].colliders[index]
        identity=dict(index=index,bone_name=getattr(source,'bone_name',''),object_name=getattr(source,'object_name',''))
        if isinstance(c,SphereCollider):colliders.append(dict(**identity,shape='SPHERE',center_world_m=tuple(c.center*units),radius_m=c.radius*units,inside=c.inside))
        elif isinstance(c,CapsuleCollider):colliders.append(dict(**identity,shape='CAPSULE',head_world_m=tuple(c.head*units),tail_world_m=tuple(c.tail*units),radius_m=c.radius*units,inside=c.inside))
        elif isinstance(c,PlaneCollider):colliders.append(dict(**identity,shape='PLANE',point_world_m=tuple(c.point*units),normal_world=tuple(c.normal)))
    boundary_data=dict(armature_world_m=_matrix_m(boundary.armature_world,units),
                       parents={n:_matrix_m(boundary.armature_world@m,units) for n,m in boundary.parent_matrices.items()},
                       centers_world_m={n:tuple(v*units) for n,v in boundary.centers.items()},
                       colliders=colliders,sampled_monotonic_s=timestamp)
    _record(node,scene,dict(engine='LEGACY',simulation_step=step,simulation_time_s=elapsed,
             physics_timestamp_monotonic_s=timestamp,physics_dt_s=1/60,bones=bones,boundary=boundary_data,
             solver_step_ms=step_ms,contacts=None,warnings=[]))


def capture_mujoco(node, state, now, step_ms):
    world=state['world']
    elapsed=world.tick*world.timestep
    if not _due(node,elapsed):return
    scene=bpy.data.scenes[state['scene']]
    name=world.description.springs[0].armature_name
    units=world.description.unit_scale
    inverse=world._armature_world[name].inverted()
    # Diagnostics keep the native solved rotation and never commit the display
    # frame's roll continuity or interpolate physical state.
    packet=world.bone_transforms(raw=True)
    bones={}
    for chain in world.description.springs:
        for joint in chain.joints:
            body=world._spring_ids[(name,joint.bone_name)]
            head=Vector(tuple(world.data.xpos[body]));rotation=Quaternion(tuple(world.data.xquat[body]))
            tail=head+rotation@Vector(joint.tail)
            jid=int(world.model.body_jntadr[body]);adr=int(world.model.jnt_dofadr[jid])
            bone=dict(parent_bone=joint.parent_bone or chain.parent_bone,
                      local_rotation_wxyz=tuple(packet.bones[joint.bone_name].rotation),world_rotation_wxyz=tuple(rotation),
                      head_world_m=tuple(head),tail_world_m=tuple(tail),
                      head_armature_m=tuple((inverse@(head/units))*units),tail_armature_m=tuple((inverse@(tail/units))*units),radius_m=joint.radius,
                      native=dict(joint_velocity_rad_s=tuple(world.data.qvel[adr:adr+3]),
                                  joint_passive_torque_nm=tuple(world.data.qfrc_passive[adr:adr+3]),
                                  joint_constraint_torque_nm=tuple(world.data.qfrc_constraint[adr:adr+3]),
                                  external_force_world_n=tuple(world.data.xfrc_applied[body,:3])))
            bones[joint.bone_name]=bone
    contacts=[]
    for index in range(world.data.ncon):
        c=world.data.contact[index]
        contacts.append(dict(geom1=int(c.geom1),geom2=int(c.geom2),distance_m=float(c.dist),position_world_m=tuple(c.pos)))
    colliders=[]
    for c in world.description.colliders:
        if c.collider_id in world._collider_names:
            matrix=world._world_matrix(world._body_id(world._collider_names[c.collider_id]))
        else:
            matrix=world._world_matrix(world._spring_ids[(c.armature_name,c.bone_name)])
        colliders.append(dict(id=c.collider_id,shape=c.shape,radius_m=c.radius,bone_name=c.bone_name,object_name=c.object_name))
        # Offsets are SI while the helper matrix has Blender-unit translation.
        head=tuple(matrix.translation*units+matrix.to_quaternion()@Vector(c.offset))
        if c.shape=='SPHERE':colliders[-1]['center_world_m']=head
        else:
            colliders[-1]['head_world_m']=head
            colliders[-1]['tail_world_m']=tuple(matrix.translation*units+matrix.to_quaternion()@Vector(c.tail))
    boundary_data=dict(armature_world_m=_matrix_m(world._armature_world[name],units),
                       parents={n:_matrix_m(world._armature_world[a]@m,units) for (a,n),m in world._parent_poses.items()},
                       colliders=colliders,sampled_monotonic_s=now)
    origin=state.get('trace_origin',now-elapsed)
    _record(node,scene,dict(engine='MUJOCO',simulation_step=world.tick,simulation_time_s=elapsed,
            physics_timestamp_monotonic_s=origin+elapsed,physics_dt_s=world.timestep,
            bones=bones,boundary=boundary_data,solver_step_ms=step_ms,contacts=contacts,
            warnings=[int(w.number) for w in world.data.warning]))


def tick_legacy(now):
    from . import flow
    for uid in tuple(legacy_ids()):
        record=_records[uid];node=_node(record);scene=bpy.data.scenes.get(record['scene'])
        if node is None or scene is None or node.mute or scene!=bpy.context.scene:
            finish(uid,'Source unavailable');continue
        try:
            context=flow.PullContext(node.id_data,scene,now,0)
            context.evaluate(node)
        except Exception as error:
            finish(uid,str(error))


def clear():
    for uid in tuple(_records):finish(uid,'Cleared')
    _records.clear();_latest.clear()


def stop_all(reason='Stopped'):
    for uid in tuple(_records):finish(uid, reason)


def forget(uid):
    """A removed node must release its writer lease and retained recording."""
    finish(uid, 'Node removed')
    _records.pop(str(uid), None)
    _latest.pop(str(uid), None)


def document(nodes=None):
    uids={_uid(n) for n in nodes} if nodes is not None else set(_records)
    traces=[]
    for uid,record in _records.items():
        if uid not in uids:continue
        traces.append({k:record[k] for k in ('session_id','comparison_id','tree','node','created_utc','origin','metadata','duration','sample_hz','active','reason','frames')})
    if not traces or not any(t['frames'] for t in traces):
        raise ValueError('Record simulation data before exporting')
    return dict(schema=SCHEMA,schema_version=SCHEMA_VERSION,
                comparison_notes=['Match bones by name and physical timestamps; do not match row numbers.',
                                  'Scene frame labels describe capture time, not an interpolated physics input.',
                                  'World positions include model placement; use armature positions for translated duplicates.',
                                  'Velocities are differences between captured solver states; the first sample has null velocity.',
                                  'Legacy contact impulses are unavailable; contacts=null does not mean no collision.',
                                  'Recording adds measurement overhead; solver_step_ms excludes JSON/recording work.'],traces=traces)


def export(path,nodes=None):
    payload=document(nodes)
    destination=Path(bpy.path.abspath(str(path)))
    if destination.suffix.lower()!='.json':destination=destination.with_suffix('.json')
    with destination.open('w',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    return str(destination)


def draw_controls(layout,node):
    box=layout.box()
    box.label(text='Simulation Recording')
    box.prop(node,'trace_duration',text='Seconds')
    box.prop(node,'trace_sample_hz',text='Record Hz')
    row=box.row(align=True)
    for operation,label in [('STOP' if active(_uid(node)) else 'START','Stop Record' if active(_uid(node)) else 'Record')]:
        op=row.operator('yugu_node_studio.spring_trace_action',text=label)
        op.tree_name,op.node_name,op.operation=node.id_data.name,node.name,operation
    op=box.operator('yugu_node_studio.spring_trace_export',text='Export Recording JSON',icon='EXPORT')
    op.tree_name,op.node_name=node.id_data.name,node.name
    op=box.operator('yugu_node_studio.spring_trace_action',text='Clear Recording',icon='X')
    op.tree_name,op.node_name,op.operation=node.id_data.name,node.name,'CLEAR'
    box.label(text=node.trace_summary)
    record=_records.get(_uid(node))
    if record and record['metadata'].get('input_warnings'):
        box.label(text='Collider sources excluded from View Layer',icon='ERROR')


class YUGUS_OT_spring_trace_action(bpy.types.Operator):
    bl_idname='yugu_node_studio.spring_trace_action'
    bl_label='Record YugoPhysics Simulation'
    tree_name:bpy.props.StringProperty()
    node_name:bpy.props.StringProperty()
    operation:bpy.props.EnumProperty(items=[(s,s.title(),'') for s in ('START','STOP','CLEAR')])
    def execute(self,context):
        tree=bpy.data.node_groups.get(self.tree_name)
        node=tree.nodes.get(self.node_name) if tree else None
        if node is None:return {'CANCELLED'}
        try:
            if self.operation in {'STOP','CLEAR'}:
                record=_records.get(_uid(node))
                group=record['comparison_id'] if record else None
                for uid,r in tuple(_records.items()):
                    if uid==_uid(node) or (group and r['comparison_id']==group):
                        if self.operation=='CLEAR':forget(uid)
                        else:finish(uid)
                if self.operation=='CLEAR':node.trace_summary='No recording'
            else:
                start([node],scene=context.scene)
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        return {'FINISHED'}


class YUGUS_OT_spring_trace_export(bpy.types.Operator,ExportHelper):
    bl_idname='yugu_node_studio.spring_trace_export'
    bl_label='Export Spring Recording JSON'
    filename_ext='.json'
    filter_glob:bpy.props.StringProperty(default='*.json',options={'HIDDEN'})
    tree_name:bpy.props.StringProperty()
    node_name:bpy.props.StringProperty()
    def invoke(self,context,event):
        self.filepath='spring-comparison.json'
        return ExportHelper.invoke(self,context,event)
    def execute(self,context):
        tree=bpy.data.node_groups.get(self.tree_name)
        node=tree.nodes.get(self.node_name) if tree else None
        record=_records.get(_uid(node)) if node else None
        nodes=[n for n in tree.nodes if _uid(n) in _records and (not record or _records[_uid(n)]['comparison_id']==record['comparison_id'])] if tree else None
        try:path=export(self.filepath,nodes)
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        self.report({'INFO'},f'Exported {path}')
        return {'FINISHED'}


CLASSES=(YUGUS_OT_spring_trace_action,YUGUS_OT_spring_trace_export)
