"""Read-only previews of native scene copies; never assign a source object."""
import hashlib,json
import bpy
from mathutils import Matrix,Quaternion,Vector
from . import data,display,skeleton


def create(scene,owner,spec,binding):
    collection = bpy.data.collections.new('PhysX · Scene copies')
    collection[data.OWNER] = owner
    scene.collection.children.link(collection)
    proxies = {}
    try:
        return reconcile(scene,owner,spec,binding,collection,proxies)
    except Exception:
        display.remove(collection,owner)
        raise


def _remove_proxy(proxy,owner):
    if proxy is None or proxy.get(data.OWNER) != owner:
        return
    mesh = proxy.data
    bpy.data.objects.remove(proxy,do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def reconcile(scene,owner,spec,binding,collection,proxies):
    """Diff the copied previews; retained native bodies retain their previews."""
    from . import physics_inputs
    if collection is not None and collection.get(data.OWNER) != owner:
        raise ValueError('Cannot update a scene preview owned by another node')
    metadata = {record['id']:record for record in spec.get('display_meta',{}).get('objects',[])}
    sources = {physics_inputs._uid(obj):obj for obj in scene.objects if not obj.get(data.OWNER)}
    desired = {}
    for record in spec.get('objects',[]):
        meta = metadata.get(record['id'],{})
        source = sources.get(meta.get('source_uid')) or scene.objects.get(meta.get('source_name',record.get('name','')))
        if source is not None and source.type == 'MESH':
            desired[record['id']] = ('object',record,meta,source)
    for spring in spec.get('springs',[]):
        for link in spring.get('links',[]):
            desired[link['id']] = ('spring',link,{},None)
    for uid in tuple(proxies):
        if uid not in desired:
            _remove_proxy(proxies.pop(uid),owner)
    if not desired:
        if collection is not None and not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
            collection = None
        for mat in tuple(bpy.data.materials):
            if mat.get(data.OWNER) == owner and mat.users == 0:
                bpy.data.materials.remove(mat)
        return collection,proxies
    if collection is None:
        collection = bpy.data.collections.new('PhysX · Scene copies')
        collection[data.OWNER] = owner
        scene.collection.children.link(collection)
    material = next((mat for mat in bpy.data.materials if mat.get(data.OWNER) == owner),None)
    if desired and material is None:
        material = bpy.data.materials.new('PhysX · Scene preview')
        material[data.OWNER] = owner
        material.diffuse_color = (.18,.55,.9,1)
    graph = bpy.context.evaluated_depsgraph_get()
    for uid,(kind,record,meta,source) in desired.items():
        proxy = proxies.get(uid)
        if kind == 'object':
            evaluated = source.evaluated_get(graph)
            geometry = meta.get('preview_geometry') or physics_inputs._mesh_signature(source,graph)
            scale = meta.get('preview_scale',tuple(evaluated.matrix_world.to_scale()))
            label = 'PhysX · '+source.name
            mesh_factory = lambda:bpy.data.meshes.new_from_object(evaluated)
        else:
            length = float(record['length'])/binding['unit_scale']
            radius = max(float(record['radius']),.002)/binding['unit_scale']
            geometry = hashlib.sha256(json.dumps([length,radius]).encode()).hexdigest()
            scale = (1,1,1)
            label = 'PhysX spring · '+record.get('bone',uid)
            mesh_factory = lambda:display.mesh(label,'capsule',[radius,length*.5])
        if proxy is None or proxy.get('physx_preview_geometry') != geometry:
            mesh = mesh_factory()
            mesh[data.OWNER] = owner
            mesh.materials.clear()
            mesh.materials.append(material)
            if proxy is None:
                proxy = bpy.data.objects.new(label,mesh)
                proxy[data.OWNER] = owner
                proxy['physx_scene_id'] = uid
                proxy.hide_select = True
                collection.objects.link(proxy)
                proxies[uid] = proxy
                proxy.matrix_world = matrix(record['pose'],binding) @ Matrix.Diagonal((*scale,1))
            else:
                old_mesh = proxy.data
                proxy.data = mesh
                if old_mesh.users == 0:
                    bpy.data.meshes.remove(old_mesh)
            proxy['physx_preview_geometry'] = geometry
        proxy.name = label
        old_scale = tuple(proxy.get('physx_preview_scale',scale))
        if old_scale != tuple(scale):
            position,rotation,_previous = proxy.matrix_world.decompose()
            proxy.matrix_world = Matrix.LocRotScale(position,rotation,Vector(scale))
        proxy['physx_preview_scale'] = tuple(scale)
        if kind == 'spring':
            proxy['physx_spring_preview'] = True
            proxy['physx_preview_length'] = length
    for mat in tuple(bpy.data.materials):
        if mat.get(data.OWNER) == owner and mat.users == 0:
            bpy.data.materials.remove(mat)
    return collection,proxies


def matrix(pose,binding):
    position = Vector(pose[:3])
    rotation = Quaternion((pose[6],*pose[3:6]))
    value = rotation.to_matrix().to_4x4()
    value.translation = position
    value = skeleton.unflat(binding['placement']) @ value
    value.translation /= binding['unit_scale']
    return value


def sync(proxies,state,binding):
    for record in state.get('objects',[]):
        proxy = proxies.get(record['id'])
        if proxy:
            scale = proxy['physx_preview_scale']
            proxy.matrix_world = matrix(record['pose'],binding) @ Matrix.Diagonal((*scale,1))
    for spring in state.get('springs',[]):
        for link in spring.get('links',[]):
            proxy = proxies.get(link['id'])
            if proxy:
                # Native bone/capsule frames use Y as the bone axis; drawing
                # capsules are authored along Z.
                center = Matrix.Translation((0,proxy['physx_preview_length']*.5,0))
                proxy.matrix_world = matrix(link['pose'],binding) @ center @ Quaternion((1,0,0),-1.5707963267948966).to_matrix().to_4x4()


def spring_motion(spec,state,binding,spring_data=None):
    from . import bridge
    armature_name = spec.get('armature_name',spec.get('display_meta',{}).get('armature_name',''))
    if not armature_name:
        armature_name = next((spring.get('armature_name','') for spring in spec.get('springs',[]) if spring.get('armature_name')), '')
    source = bpy.data.objects.get(armature_name)
    host = bridge.base()
    result = host.flow.BoneTransforms(armature_name,spring_data=spring_data)
    if source is None or source.type != 'ARMATURE':
        return result
    transforms = {}
    bound_bones = set()
    chains = {chain['id']:chain for chain in spec.get('display_meta',{}).get('springs',[])}
    metadata = {link['id']:link for spring in spec.get('display_meta',{}).get('springs',[]) for link in spring.get('links',[])}
    inverse = source.matrix_world.inverted()
    for spring in state.get('springs',[]):
        meta = chains.get(spring['id'],{})
        anchor_correction = None
        source_inverse = inverse
        if meta.get('anchor_body') and spring.get('anchor_pose'):
            # Secondary motion belongs to the same frame as the humanoid
            # packet. Remove the physical body's motion, then express spring
            # deformation around the authored rest anchor. Depending on last
            # frame's applied Head would apply the new Head motion twice.
            rest_anchor = meta['anchor_rest_pose']
            rest_matrix = Quaternion((rest_anchor[6],*rest_anchor[3:6])).to_matrix().to_4x4()
            rest_matrix.translation = Vector(rest_anchor[:3])/binding['unit_scale']
            anchor_correction = rest_matrix @ matrix(spring['anchor_pose'],binding).inverted()
            source_inverse = skeleton.unflat(meta['source_matrix']).inverted()
        for link in spring.get('links',[]):
            name = link.get('bone','')
            if name not in source.data.bones:
                continue
            value = matrix(link['pose'],binding)
            offset = metadata.get(link['id'],{}).get('bone_frame_offset')
            if offset:
                bone_offset = skeleton.unflat(offset)
                bone_offset.translation /= binding['unit_scale']
                value = value @ bone_offset
            if 'head' in link:
                physical = Vector(link['head'])
                value.translation = (skeleton.unflat(binding['placement']) @ physical)/binding['unit_scale']
            if anchor_correction is not None:
                value = anchor_correction @ value
                bound_bones.add(name)
            transforms[name] = source_inverse @ value
    for name,value in transforms.items():
        bone = source.data.bones[name]
        parent = {}
        if bone.parent:
            parent_value = transforms.get(bone.parent.name, bone.parent.matrix_local if name in bound_bones
                                          else source.pose.bones[bone.parent.name].matrix)
            parent = {'parent_matrix':parent_value,'parent_matrix_local':bone.parent.matrix_local}
        basis = bone.convert_local_to_pose(value,bone.matrix_local,invert=True,**parent)
        result.bones[name] = host.data.HumanoidTransform(basis.to_quaternion(),basis.translation.copy())
    return result
