"""Read-only Blender scene adapters for the standalone PhysX worker.

Reader values use world metres. collect() moves them into the controller's
simulation frame; updates() only samples moving boundaries, never mesh data.
"""
import copy
import hashlib
import json
import math
from array import array

import bpy
from mathutils import Matrix, Quaternion, Vector
from bpy.app.handlers import persistent
from . import bridge, data, skeleton

SCHEMA = 'yugo-physx-scene-inputs-v1'
_tracked_meshes = set()
_tracked_modifier_objects = set()
_geometry_dirty = {}
_mesh_signatures = {}


def _uid(block):
    return int(getattr(block,'session_uid',0) or block.as_pointer())


def object_id(obj):
    """Stable during this Blender session, without writing source properties."""
    return 'object:' + str(_uid(obj))


@persistent
def _geometry_changed(_scene,depsgraph):
    for update in depsgraph.updates:
        block = getattr(update.id,'original',update.id)
        if not update.is_updated_geometry:
            continue
        key = _uid(block)
        if isinstance(block,bpy.types.Mesh) and key in _tracked_meshes:
            _geometry_dirty[key] = _geometry_dirty.get(key,0)+1
        elif isinstance(block,bpy.types.Object) and key in _tracked_modifier_objects:
            _geometry_dirty[key] = _geometry_dirty.get(key,0)+1


def install_tracking():
    if _geometry_changed not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_geometry_changed)


def clear_revision_cache():
    _tracked_meshes.clear()
    _tracked_modifier_objects.clear()
    _geometry_dirty.clear()
    _mesh_signatures.clear()


def uninstall_tracking():
    if _geometry_changed in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_geometry_changed)
    clear_revision_cache()


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _scalar(value):
    if isinstance(value,bpy.types.ID):
        return ('ID',_uid(value))
    if isinstance(value,(bool,int,str)) or value is None:
        return value
    if isinstance(value,float):
        return round(value,6)
    if isinstance(value,set):
        return sorted(value)
    try:
        return [_scalar(item) for item in value]
    except TypeError:
        return str(value)


def _modifiers(obj):
    result = []
    for modifier in obj.modifiers:
        settings = []
        for prop in modifier.bl_rna.properties:
            if prop.identifier in {'rna_type','name'} or prop.type == 'COLLECTION' or prop.is_readonly:
                continue
            settings.append((prop.identifier,_scalar(getattr(modifier,prop.identifier))))
        settings.extend((key,_scalar(modifier[key])) for key in sorted(modifier.keys()) if key != '_RNA_UI')
        result.append((modifier.type,settings))
    return result


def _mesh_signature(obj,depsgraph):
    install_tracking()
    mesh_key = _uid(obj.data)
    object_key = _uid(obj)
    _tracked_meshes.add(mesh_key)
    modifiers = _modifiers(obj)
    keys = obj.data.shape_keys
    deformation = bool(modifiers) or keys is not None
    if deformation:
        _tracked_modifier_objects.add(object_key)
    else:
        _tracked_modifier_objects.discard(object_key)
    key_values = [(key.name,key.value,key.mute) for key in keys.key_blocks] if keys else []
    stamp = (mesh_key,_geometry_dirty.get(mesh_key,0),_geometry_dirty.get(object_key,0) if deformation else 0,
             len(obj.data.vertices),len(obj.data.edges),len(obj.data.polygons),_hash([modifiers,key_values]))
    cached = _mesh_signatures.get(object_key)
    if cached and cached[0] == stamp:
        return cached[1]
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = array('f',[0])* (len(mesh.vertices)*3)
        indices = array('i',[0])*len(mesh.loops)
        counts = array('i',[0])*len(mesh.polygons)
        mesh.vertices.foreach_get('co',coords)
        mesh.loops.foreach_get('vertex_index',indices)
        mesh.polygons.foreach_get('loop_total',counts)
        digest = hashlib.sha256(coords.tobytes()+indices.tobytes()+counts.tobytes()).hexdigest()
    finally:
        evaluated.to_mesh_clear()
    _mesh_signatures[object_key] = (stamp,digest)
    return digest


def _finite(values, name='Scene input'):
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError(name + ' contains a non-finite value')
    return result


def _number(value, name, minimum=None, maximum=None):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _units(scene):
    value = float(scene.unit_settings.scale_length)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Scene unit scale must be positive')
    return value


def _pose(matrix, units=1):
    q = matrix.to_quaternion().normalized()
    return _finite((*((matrix.translation) * units), q.x, q.y, q.z, q.w))


def _matrix(pose):
    matrix = Quaternion((pose[6], *pose[3:6])).to_matrix().to_4x4()
    matrix.translation = Vector(pose[:3])
    return matrix


def _frame(head, tail, orientation=None):
    direction = Vector(tail) - Vector(head)
    if direction.length < 1e-7:
        raise ValueError('Spring segment is too short')
    source = Quaternion(orientation).normalized() if orientation is not None else Quaternion()
    q = (source @ Vector((0, 1, 0))).rotation_difference(direction.normalized()) @ source
    matrix = q.to_matrix().to_4x4()
    matrix.translation = Vector(head)
    return matrix


def _depsgraph(ctx):
    return getattr(ctx, 'depsgraph', None) or bpy.context.evaluated_depsgraph_get()


def _object_pose(obj, center, units, depsgraph=None):
    source = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    matrix = source.matrix_world.copy()
    matrix.translation = source.matrix_world @ Vector(center)
    return _pose(matrix, units)


def _rigid_object(obj, units, depsgraph):
    rigid = obj.rigid_body
    evaluated = obj.evaluated_get(depsgraph)
    world = evaluated.matrix_world.copy()
    # Passive source boundaries retain infinite mass and can follow authored
    # animation without the worker assigning any source transform.
    mode = 'KINEMATIC' if rigid.type == 'PASSIVE' or rigid.kinematic else 'DYNAMIC'
    shape = {'CONVEX_HULL': 'CONVEX'}.get(rigid.collision_shape, rigid.collision_shape)
    if shape not in {'BOX', 'SPHERE', 'CAPSULE', 'CYLINDER', 'CONE', 'CONVEX', 'MESH'}:
        raise ValueError(f'{obj.name}: unsupported rigid-body shape {shape}')
    if shape == 'MESH' and mode == 'DYNAMIC':
        raise ValueError(f'{obj.name}: dynamic concave Mesh collisions are unsupported; choose Convex Hull')
    scale = world.to_scale()
    if world.to_3x3().determinant() <= 0 or min(scale) <= 0:
        raise ValueError(f'{obj.name}: apply negative or zero object scale before Play')
    basis = world.to_3x3().normalized()
    if any(abs(basis.col[i].dot(basis.col[j])) > 1e-5 for i in range(3) for j in range(i)):
        raise ValueError(f'{obj.name}: apply object transform shear before Play')
    bbox = [Vector(v) for v in evaluated.bound_box]
    low = Vector(tuple(min(v[i] for v in bbox) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in bbox) for i in range(3)))
    center = (low + high) / 2
    size = Vector(tuple((high[i] - low[i]) * scale[i] * units for i in range(3)))
    radius = max(size.x, size.y) / 2
    record = {'id': object_id(obj), 'name': obj.name, 'mode': mode,
        'source_type': rigid.type, 'source_kinematic': bool(rigid.kinematic), 'shape': shape,
        'size': _finite(size), 'radius': float(max(size)/2 if shape == 'SPHERE' else radius),
        'height': float(max(0, size.z - 2*radius) if shape == 'CAPSULE' else size.z),
        'axis': 'Z', 'mass': float(rigid.mass), 'friction': float(rigid.friction),
        'restitution': float(rigid.restitution), 'linear_damping': float(rigid.linear_damping),
        'angular_damping': float(rigid.angular_damping),
        'use_margin': bool(rigid.use_margin), 'margin': float(rigid.collision_margin)*units,
        'collision_groups': [bool(v) for v in rigid.collision_collections],
        'use_deactivation': bool(rigid.use_deactivation),
        'start_deactivated': bool(rigid.use_start_deactivated),
        'deactivate_linear_velocity': float(rigid.deactivate_linear_velocity)*units,
        'deactivate_angular_velocity': float(rigid.deactivate_angular_velocity),
        'lock_location': [False]*3, 'lock_rotation': [False]*3}
    if shape in {'CONVEX', 'MESH'}:
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            if len(mesh.vertices) > 100000 or len(mesh.loop_triangles) > 200000:
                raise ValueError(f'{obj.name}: collision mesh is too large; use a simpler collider')
            record['vertices'] = [_finite(tuple(v.co[i]*scale[i]*units for i in range(3))) for v in mesh.vertices]
            record['faces'] = [list(triangle.vertices) for triangle in mesh.loop_triangles]
        finally:
            evaluated.to_mesh_clear()
        center = Vector((0, 0, 0))
    record['collider_center'] = _finite(tuple(center[i]*scale[i]*units for i in range(3)))
    record['pose'] = _pose(world, units)
    return record, {'id': record['id'], 'source_name': obj.name, 'source_uid': _uid(obj),
                    'local_center': [0, 0, 0], 'shape': shape,
                    'preview_geometry': _mesh_signature(obj,depsgraph),
                    'preview_scale': _finite(scale)}


def _rigid_sources(ctx,node):
    socket = node.inputs.get('Objects')
    if socket and socket.is_linked:
        values = ctx.input(node, 'Objects', None)
        host = bridge.base()
        if not isinstance(values, host.targets.Objects):
            raise ValueError('Objects must come from an Objects List output')
        objects = [bpy.data.objects.get(name) for name in dict.fromkeys(values.names)]
    else:
        objects = sorted(ctx.scene.objects, key=lambda obj: obj.name)
    return objects


def _eligible_rigid(ctx,obj):
    rigid = getattr(obj,'rigid_body',None) if obj is not None else None
    return (obj is not None and ctx.scene.objects.get(obj.name) is obj and obj.type == 'MESH'
            and rigid is not None and rigid.enabled and not obj.get(data.OWNER))


def _rigid_bodies(ctx, node):
    units = _units(ctx.scene)
    records, metadata, warnings = [], [], []
    depsgraph = _depsgraph(ctx)
    for obj in _rigid_sources(ctx,node):
        if obj is None:
            warnings.append('An Objects List target no longer exists')
            continue
        if ctx.scene.objects.get(obj.name) is not obj:
            warnings.append(f'{obj.name}: Objects List target is outside the current scene')
            continue
        rigid = getattr(obj, 'rigid_body', None)
        if not _eligible_rigid(ctx,obj):
            continue
        record, meta = _rigid_object(obj, units, depsgraph)
        if record['collision_groups'] != [True] + [False]*(len(record['collision_groups'])-1):
            warnings.append(f'{obj.name}: Blender collision collections are not used; connected PhysX bodies interact')
        if record['use_deactivation'] or record['start_deactivated']:
            warnings.append(f'{obj.name}: Blender deactivation settings are not transferred')
        records.append(record)
        metadata.append(meta)
    return data.RigidBodiesInput(tuple(records), tuple(metadata), tuple(warnings))


def _bone_world(armature, name, rest=False):
    bone = armature.data.bones.get(name) if rest else armature.pose.bones.get(name)
    if bone is None:
        raise ValueError(f'{armature.name}: spring bone {name!r} does not exist')
    return armature.matrix_world @ (bone.matrix_local if rest else bone.matrix)


def _spring_pairs(armature, spring, version):
    joints = tuple(spring.get('joints', ()))
    if not version.startswith('0'):
        # Match VRM 1's ancestor-ordered segments; the final joint is an endpoint.
        from_host = bridge.base().spring_solver._sorted_chains(armature.pose.bones, joints)
        return [[(joint, bone.name, following.name, None)
                 for (joint, bone), (_next, following) in zip(chain, chain[1:])]
                for chain in from_host if len(chain) > 1]
    result, seen = [], set()
    for entry in joints:
        root = armature.data.bones.get(str(entry.get('bone', '')))
        if root is None:
            raise ValueError(f'{armature.name}: missing VRM 0 spring root {entry.get("bone", "")}')
        pairs = []
        def visit(bone, parent):
            if bone.name in seen:
                return
            seen.add(bone.name)
            pairs.append((spring, bone.name, None, parent))
            for child in sorted(bone.children, key=lambda b: b.name):
                visit(child, bone.name)
        visit(root, None)
        if pairs:
            result.append(pairs)
    return result


def _spring_bones(ctx, node):
    values = ctx.input(node, 'Spring Data', None)
    host = bridge.base()
    if values is None:
        return data.SpringBonesInput()
    if not isinstance(values, host.data.SpringData):
        raise ValueError('Spring Data must come from the VRM Data node')
    armature = bpy.data.objects.get(values.armature_name)
    if armature is None or armature.type != 'ARMATURE':
        raise ValueError('Spring Data source Armature is missing')
    units = _units(ctx.scene)
    scale = max(abs(v) for v in armature.matrix_world.to_scale()) * units
    chains, metadata, warnings = [], [], []
    used_collider_ids = set()
    groups = {str(group.get('uuid', '')): group for group in values.collider_groups}
    for spring_index, spring in enumerate(values.springs):
        for group_id in spring.get('collider_groups', ()):
            group = groups.get(str(group_id))
            if group:
                used_collider_ids.update(str(v) for v in group.get('colliders', ()))
        for chain_index, pairs in enumerate(_spring_pairs(armature, spring, values.version)):
            chain_id = f'spring:{armature.name}:{spring_index}:{chain_index}'
            chain = {'id': chain_id, 'name': str(spring.get('name', 'Spring')),
                     'armature_name': armature.name, 'links': []}
            meta = {'id': chain_id, 'armature_name': armature.name, 'links': []}
            link_ids = {bone: chain_id + ':' + bone for _params, bone, _next, _parent in pairs}
            for params, bone_name, following, parent in pairs:
                bone = armature.pose.bones[bone_name]
                rest_bone = armature.data.bones[bone_name]
                rest_head = armature.matrix_world @ rest_bone.head_local * units
                rest_tail = (armature.matrix_world @ armature.data.bones[following].head_local * units if following
                             else armature.matrix_world @ rest_bone.tail_local * units)
                # Applying a native result can introduce small joint position
                # drift. Physical dimensions remain authored rest dimensions,
                # so reading the output cannot change the scene topology.
                length = (rest_tail - rest_head).length
                if length < 1e-7:
                    warnings.append(f'{bone_name}: skipped zero-length rest spring segment')
                    continue
                head = armature.matrix_world @ bone.head * units
                tail = (armature.matrix_world @ armature.pose.bones[following].head * units if following
                        else armature.matrix_world @ bone.tail * units)
                if (tail - head).length < 1e-7:
                    tail = head + _bone_world(armature, bone_name).to_quaternion() @ Vector((0, length, 0))
                gravity = Vector(params.get('gravity_dir', (0, 0, -1)))
                if gravity.length > 1e-8:
                    gravity.normalize()
                gravity *= _number(params.get('gravity_power', 0), bone_name + ' gravity')
                link = {'id': link_ids[bone_name], 'bone': bone_name,
                        'pose': _pose(_frame(head, tail, _bone_world(armature, bone_name).to_quaternion())),
                        'length': float(length), 'radius': _number(params.get('hit_radius', .01), bone_name + ' radius', 0)*scale,
                        'mass': max(.001, length*.1),
                        'drag': _number(params.get('drag_force', .5), bone_name + ' drag', 0, 1), 'gravity': _finite(gravity)}
                link['radius'] = max(.0001, link['radius'])
                link['stiffness'] = _number(params.get('stiffness', 1), bone_name + ' stiffness', 0)
                if values.version.startswith('0'):
                    link['parent_id'] = link_ids.get(parent)
                chain['links'].append(link)
                meta['links'].append({'id': link['id'], 'bone': bone_name,
                    'parent_bone': bone.parent.name if bone.parent else '',
                    'bone_rest': skeleton.flat(bone.bone.matrix_local),
                    'segment_pose': link['pose'], 'length': float(length),
                    'segment_rest_pose': _pose(_frame(rest_head, rest_tail, _bone_world(armature, bone_name, True).to_quaternion())),
                    'bone_frame_offset': skeleton.flat(_matrix(link['pose']).inverted() @
                        _matrix(_pose(_bone_world(armature, bone_name), units)))})
            if not chain['links']:
                continue
            root_name = chain['links'][0]['bone']
            root = armature.pose.bones[root_name]
            chain['anchor_pose'] = chain['links'][0]['pose']
            meta.update({'root_bone': root_name, 'parent_bone': root.parent.name if root.parent else '',
                'source_matrix': skeleton.flat(armature.matrix_world),
                'anchor_rest_pose': meta['links'][0]['segment_rest_pose']})
            # Follow source-parent motion without following a downstream Apply
            # node's simulated root rotation back into the anchor.
            boundary = _bone_world(armature, root.parent.name) if root.parent else armature.matrix_world
            meta['boundary_bone'] = root.parent.name if root.parent else ''
            meta['boundary_local_pose'] = _pose(_matrix(_pose(boundary, units)).inverted() @ _matrix(chain['anchor_pose']))
            chains.append(chain)
            metadata.append(meta)
    collider_records = []
    for item in values.colliders:
        uuid = str(item.get('uuid', ''))
        if uuid not in used_collider_ids:
            continue
        shape = str(item.get('shape', 'SPHERE')).upper()
        if bool(item.get('inside', False)) or 'PLANE' in shape:
            warnings.append(f'{uuid}: skipped unsupported VRM collider {"inside " if item.get("inside") else ""}{shape}')
            continue
        collider_id = f'collider:{armature.name}:{uuid}'
        if item.get('object'):
            obj = bpy.data.objects.get(str(item['object']))
            if obj is None:
                warnings.append(f'{uuid}: collider object is missing')
                continue
            pose = _pose(obj.matrix_world, units)
            radius = obj.empty_display_size*max(abs(v) for v in obj.matrix_world.to_scale())*units
            bone_name = str(item.get('bone', ''))
            collider_meta = {'id': collider_id, 'source_name': obj.name, 'local_center': [0, 0, 0],
                             'armature_name': armature.name, 'bone': bone_name}
            if bone_name in armature.data.bones:
                rest = _matrix(_pose(_bone_world(armature, bone_name, True), units))
                current = _matrix(_pose(_bone_world(armature, bone_name), units))
                collider_meta['anchor_rest_pose'] = _pose(rest @ current.inverted() @ _matrix(pose))
            height = 0
            shape = 'SPHERE'
        else:
            bone_name = str(item.get('bone', ''))
            matrix = _bone_world(armature, bone_name)
            rest_matrix = _bone_world(armature, bone_name, True)
            a = matrix @ Vector(item.get('offset', (0, 0, 0))) * units
            b = matrix @ Vector(item.get('tail', item.get('offset', (0, 0, 0)))) * units
            rest_a = rest_matrix @ Vector(item.get('offset', (0, 0, 0))) * units
            rest_b = rest_matrix @ Vector(item.get('tail', item.get('offset', (0, 0, 0)))) * units
            # Native collider geometry is fixed while its owning bone moves.
            # Current pose scale/rotation must not rebuild the scene on resume.
            height = (rest_b-rest_a).length if 'CAPSULE' in shape else 0
            if height > 1e-7:
                direction = b-a if (b-a).length > 1e-7 else rest_b-rest_a
                pose = _pose(_frame((a+b)/2, (a+b)/2 + direction))
            else:
                pose = _pose(Matrix.Translation(a))
            radius = _number(item.get('radius', 0), uuid + ' radius', 0)*max(abs(v) for v in rest_matrix.to_scale())*units
            collider_meta = {'id': collider_id, 'armature_name': armature.name, 'bone': bone_name,
                'offset': list(item.get('offset', (0, 0, 0))), 'tail': list(item.get('tail', item.get('offset', (0, 0, 0)))),
                'shape': 'CAPSULE' if height > 1e-7 else 'SPHERE'}
            collider_meta['anchor_rest_pose'] = _pose(_frame((rest_a+rest_b)/2, (rest_a+rest_b)/2 + (rest_b-rest_a))
                if height > 1e-7 else Matrix.Translation(rest_a))
            shape = collider_meta['shape']
        collider_records.append({'id': collider_id, 'name': uuid, 'mode': 'KINEMATIC', 'shape': shape,
            'pose': pose, 'axis': 'Y', 'radius': max(.0001, radius), 'height': height,
            'size': [2*radius, height+2*radius, 2*radius], 'mass': 1,
            'friction': .5, 'restitution': 0, 'linear_damping': 0, 'angular_damping': 0,
            'spring_collider': True, 'source_meta': collider_meta})
    return data.SpringBonesInput(tuple(chains), tuple(collider_records), tuple(metadata), armature.name, tuple(warnings), values)


def evaluate(ctx, node):
    if node.bl_idname == data.RIGID_BODIES:
        packet = _rigid_bodies(ctx, node)
        name = 'Rigid Bodies'
        node.status = f'{len(packet.objects)} rigid bodies · copies on Play'
    elif node.bl_idname == data.SPRINGS:
        packet = _spring_bones(ctx, node)
        name = 'Spring Bones'
        node.status = (f'{len(packet.springs)} spring chains · {sum(len(s["links"]) for s in packet.springs)} segments'
                       if packet.armature_name else 'Connect VRM Data Spring Data')
    else:
        raise ValueError('Unknown PhysX input reader')
    if packet.warnings:
        node.status += '\n' + '\n'.join(packet.warnings)
    return bridge.outputs(node, **{name: packet})


def _connected(ctx, node, name, kind):
    socket = node.inputs.get(name)
    if socket is None or not socket.is_linked:
        return kind()
    value = ctx.input(node, name, None)
    if not isinstance(value, kind):
        raise ValueError(f'{name} requires its PhysX adapter node')
    return value


def _reader(node,name,kind):
    socket = node.inputs.get(name)
    if socket is None or not socket.is_linked:
        return None
    source = socket.links[0].from_node
    return source if source.bl_idname == kind else False


def revision(ctx,node,binding):
    """Poll at about 4 Hz; mesh bytes are read only after source geometry dirties.

    The current data ports remain demand-driven. Known adapter nodes are read
    directly here so this check never extracts collision meshes unnecessarily.
    """
    install_tracking()
    content = {'units':_units(ctx.scene),'placement':binding['placement'],'rigid':None,'springs':None}
    reader = _reader(node,'Rigid Bodies',data.RIGID_BODIES)
    if reader is False:
        packet = _connected(ctx,node,'Rigid Bodies',data.RigidBodiesInput)
        stable = copy.deepcopy(list(packet.objects))
        for record in stable:
            record.pop('pose',None)
        content['rigid'] = stable
    elif reader is not None:
        depsgraph = _depsgraph(ctx)
        content['rigid'] = []
        fields = ('type','kinematic','mass','collision_shape','friction','restitution',
                  'linear_damping','angular_damping','use_margin','collision_margin',
                  'use_deactivation','use_start_deactivated','deactivate_linear_velocity',
                  'deactivate_angular_velocity','collision_collections')
        for obj in _rigid_sources(ctx,reader):
            if not _eligible_rigid(ctx,obj):
                continue
            evaluated = obj.evaluated_get(depsgraph)
            content['rigid'].append({'id':object_id(obj),'name':obj.name,'mesh':_mesh_signature(obj,depsgraph),
                'scale':_scalar(evaluated.matrix_world.to_scale()),
                'settings':[(key,_scalar(getattr(obj.rigid_body,key))) for key in fields]})
        content['rigid'].sort(key=lambda value:value['id'])
    reader = _reader(node,'Spring Bones',data.SPRINGS)
    if reader is False:
        packet = _connected(ctx,node,'Spring Bones',data.SpringBonesInput)
        stable = {'objects':copy.deepcopy(list(packet.colliders)),'springs':copy.deepcopy(list(packet.springs))}
        content['springs'] = _topology(stable)
    elif reader is not None:
        spring_data = ctx.input(reader,'Spring Data',None)
        if spring_data is not None:
            armature = bpy.data.objects.get(spring_data.armature_name)
            bones = [] if armature is None else [(bone.name,bone.parent.name if bone.parent else '',
                skeleton.flat(bone.matrix_local),list(bone.tail_local)) for bone in armature.data.bones]
            colliders = []
            for collider in spring_data.colliders:
                obj = bpy.data.objects.get(collider.get('object',''))
                if obj is not None:
                    colliders.append((_uid(obj),obj.name,obj.empty_display_size,_scalar(obj.matrix_world.to_scale())))
            content['springs'] = {'version':spring_data.version,'armature':spring_data.armature_name,
                'data':[spring_data.springs,spring_data.colliders,spring_data.collider_groups],
                'rest':bones,'world':skeleton.flat(armature.matrix_world) if armature else [],'objects':colliders}
    return _hash(content)


def _native(pose, inverse):
    return _pose(inverse @ _matrix(pose))


def _humanoid_mapping(armature, binding):
    source_mapping = binding.get('mapping', {})
    if source_mapping and all(name in armature.data.bones for name in source_mapping.values()):
        return {bone: humanoid for humanoid, bone in source_mapping.items()}
    try:
        return bridge.base().vrm_cache.build_snapshot(armature).bone_map.by_blender
    except (AttributeError, ValueError):
        return {}


def _body_binding(armature, bone_name, source_pose, binding, inverse):
    mapping = _humanoid_mapping(armature, binding)
    humanoid_body = {human: body for body, human in skeleton.BODY_MAP.items() if human}
    bone = armature.data.bones.get(bone_name)
    while bone is not None:
        body = humanoid_body.get(mapping.get(bone.name))
        if body and body in binding['rest_body_matrices']:
            units = binding['unit_scale']
            ancestor = _matrix(_pose(_bone_world(armature, bone.name, True), units))
            source_anchor = _matrix(source_pose)
            body_world = skeleton.unflat(binding['rest_body_matrices'][body])
            body_world.translation *= units
            # SMPL body frames and VRM bone frames have different rest axes.
            # Preserve authored world orientation and the ancestor-to-anchor
            # offset, then express that result in the SMPL body's own frame.
            desired_world = source_anchor.copy()
            desired_world.translation = body_world.translation + source_anchor.translation - ancestor.translation
            local = body_world.inverted() @ desired_world
            desired = inverse @ desired_world
            return {'smpl_body': body, 'local_pose': _pose(local)}, _pose(desired)
        bone = bone.parent
    return None


def _anchor(chain, meta, binding, inverse):
    armature = bpy.data.objects.get(meta['armature_name'])
    if armature is None:
        return
    attached = _body_binding(armature, meta['parent_bone'], meta['anchor_rest_pose'], binding, inverse)
    if attached:
        anchor, desired = attached
        source_rest = _matrix(_native(meta['anchor_rest_pose'], inverse))
        delta = _matrix(desired) @ source_rest.inverted()
        chain['anchor'] = anchor
        chain['anchor_pose'] = desired
        for link in chain['links']:
            link['pose'] = _pose(delta @ _matrix(link['pose']))
        meta['anchor_body'] = anchor['smpl_body']


def _topology(spec):
    """Boundary poses can change while collision geometry remains valid."""
    stable = copy.deepcopy({key: spec[key] for key in ('objects', 'springs')})
    for obj in stable['objects']:
        obj.pop('pose', None)
        obj.pop('source_meta', None)
    for chain in stable['springs']:
        chain.pop('anchor_pose', None)
        for link in chain['links']:
            link.pop('pose', None)
    return hashlib.sha256(json.dumps(stable, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def collect(ctx, node, binding):
    rigid = _connected(ctx, node, 'Rigid Bodies', data.RigidBodiesInput)
    springs = _connected(ctx, node, 'Spring Bones', data.SpringBonesInput)
    inverse = skeleton.unflat(binding['placement']).inverted()
    spec = {'schema': SCHEMA, 'objects': copy.deepcopy(list(rigid.objects) + list(springs.colliders)),
        'springs': copy.deepcopy(list(springs.springs)), 'warnings': list(rigid.warnings) + list(springs.warnings),
        'armature_name': springs.armature_name,
        'display_meta': {'objects': copy.deepcopy(list(rigid.display_meta)),
                         'springs': copy.deepcopy(list(springs.display_meta)),
                         'armature_name': springs.armature_name}}
    for obj in spec['objects']:
        obj['pose'] = _native(obj['pose'], inverse)
        meta = obj.get('source_meta', {})
        if meta.get('anchor_rest_pose'):
            armature = bpy.data.objects.get(meta.get('armature_name', ''))
            attached = (_body_binding(armature, meta['bone'], meta['anchor_rest_pose'], binding, inverse)
                        if armature is not None else None)
            if attached:
                obj['anchor'], obj['pose'] = attached
    for chain, meta in zip(spec['springs'], spec['display_meta']['springs']):
        chain['anchor_pose'] = _native(chain['anchor_pose'], inverse)
        for link in chain['links']:
            link['pose'] = _native(link['pose'], inverse)
            link['gravity'] = _finite(inverse.to_3x3() @ Vector(link['gravity']))
        _anchor(chain, meta, binding, inverse)
    spec['topology_hash'] = _topology(spec)
    # This also rejects non-finite source settings before spawning a worker.
    json.dumps(spec, allow_nan=False, separators=(',', ':'))
    return spec


def updates(ctx, node, binding, spec):
    inverse = skeleton.unflat(binding['placement']).inverted()
    units = binding['unit_scale']
    result = {'objects': [], 'anchors': []}
    depsgraph = _depsgraph(ctx)
    metadata = {record['id']: record for record in spec['display_meta']['objects']}
    for record in spec['objects']:
        if record['mode'] != 'KINEMATIC' or record.get('anchor'):
            continue
        meta = record.get('source_meta', metadata.get(record['id'], {}))
        if meta.get('source_name'):
            obj = bpy.data.objects.get(meta['source_name'])
            if obj is None:
                continue
            pose = _object_pose(obj, meta.get('local_center', (0, 0, 0)), units,
                                None if record.get('spring_collider') else depsgraph)
        elif meta.get('armature_name'):
            armature = bpy.data.objects.get(meta['armature_name'])
            if armature is None or meta['bone'] not in armature.pose.bones:
                continue
            matrix = _bone_world(armature, meta['bone'])
            a = matrix @ Vector(meta['offset']) * units
            b = matrix @ Vector(meta['tail']) * units
            pose = _pose(_frame((a+b)/2, (a+b)/2 + (b-a)) if meta['shape'] == 'CAPSULE' and (b-a).length > 1e-7
                         else Matrix.Translation(a))
        else:
            continue
        result['objects'].append({'id': record['id'], 'pose': _native(pose, inverse)})
    for meta in spec['display_meta']['springs']:
        if meta.get('anchor_body'):
            continue  # The worker follows the simulated SMPL body each substep.
        armature = bpy.data.objects.get(meta['armature_name'])
        if armature is None:
            continue
        boundary = _bone_world(armature, meta['boundary_bone']) if meta['boundary_bone'] else armature.matrix_world
        pose = _pose(_matrix(_pose(boundary, units)) @ _matrix(meta['boundary_local_pose']))
        result['anchors'].append({'id': meta['id'], 'pose': _native(pose, inverse)})
    json.dumps(result, allow_nan=False, separators=(',', ':'))
    return result
