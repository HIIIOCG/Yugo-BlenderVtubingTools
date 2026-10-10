import math
import bpy
from mathutils import Matrix, Quaternion, Vector
from .data import OWNER
from . import skeleton, model

def remove(collection, owner):
    if collection is None or collection.get(OWNER) != owner:
        return
    for obj in tuple(collection.objects):
        if obj.get(OWNER) != owner:
            continue
        block = obj.data
        materials = tuple(block.materials) if hasattr(block, "materials") else ()
        bpy.data.objects.remove(obj, do_unlink=True)
        if block and block.users == 0:
            if isinstance(block, bpy.types.Mesh):
                bpy.data.meshes.remove(block)
            elif isinstance(block, bpy.types.Armature):
                bpy.data.armatures.remove(block)
        for mat in materials:
            if mat and mat.users == 0 and mat.get(OWNER) == owner:
                bpy.data.materials.remove(mat)
    if not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def mesh(name, kind, size):
    block = bpy.data.meshes.new(name)
    if kind == 'box':
        x, y, z = size
        v = [(a*x, b*y, c*z) for a,b,c in ((-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1), (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1))]
        faces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    elif kind in ('capsule', 'sphere'):
        radius = size[0]
        half = size[1] if kind == 'capsule' else 0
        rings = [(radius*math.cos(t), -half+radius*math.sin(t)) for t in [-math.pi/2 + i*math.pi/12 for i in range(1,7)]]
        if half:
            rings.append((radius, half))
        rings += [(radius*math.cos(t), half+radius*math.sin(t)) for t in [i*math.pi/12 for i in range(1,6)]]
        sides = 12
        v = [(0,0,-half-radius)]
        for r,z in rings:
            v += [(r*math.cos(2*math.pi*i/sides),r*math.sin(2*math.pi*i/sides),z) for i in range(sides)]
        top = len(v)
        v.append((0,0,half+radius))
        faces = [(0,1+(i+1)%sides,1+i) for i in range(sides)]
        for row in range(len(rings)-1):
            a,b = 1+row*sides,1+(row+1)*sides
            faces += [(a+i,a+(i+1)%sides,b+(i+1)%sides,b+i) for i in range(sides)]
        a = 1+(len(rings)-1)*sides
        faces += [(a+i,a+(i+1)%sides,top) for i in range(sides)]
    else:
        bpy.data.meshes.remove(block)
        raise ValueError("Unsupported SMPL-X display geometry")
    block.from_pydata(v, [], faces)
    if kind != 'box':
        for face in block.polygons:
            face.use_smooth = True
    return block


def create(scene, owner, matrices, units):
    if bpy.context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish Edit Mode before Setup')
    previous_mode = bpy.context.mode
    selected = tuple(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    if previous_mode == 'POSE':
        bpy.ops.object.mode_set(mode='OBJECT')
    collection = bpy.data.collections.new('PhysX · SMPL')
    collection[OWNER] = owner
    scene.collection.children.link(collection)
    try:
        arm = bpy.data.armatures.new('PhysX · SMPL')
        rig = bpy.data.objects.new(arm.name, arm)
        rig[OWNER] = owner
        collection.objects.link(rig)
        rig.show_in_front = True
        arm.display_type = 'STICK'
        for obj in selected:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        smpl = model.Skeleton()
        for i,name in enumerate(smpl.names):
            bone = arm.edit_bones.new(name)
            bone.head = matrices[name].translation
            bone.tail = bone.head+Vector((0,.08/units,0))
            bone.matrix = matrices[name]
            bone.length = .08/units
            if smpl.parents[i] >= 0:
                bone.parent = arm.edit_bones[smpl.names[smpl.parents[i]]]
        bpy.ops.object.mode_set(mode='OBJECT')
        mat = bpy.data.materials.new('PhysX · Turquoise')
        mat[OWNER] = owner
        mat.diffuse_color = (.025,.52,.47,1)
        for i, (body, geom) in enumerate(smpl.geoms):
            kind = geom.get('type', 'sphere')
            size = list(map(float, geom.get('size', '.05').split()))
            local = Quaternion(tuple(map(float, geom.get('quat','1 0 0 0').split()))).to_matrix().to_4x4()
            local.translation = Vector(tuple(map(float, geom.get('pos','0 0 0').split()))) / units
            if 'fromto' in geom:
                ends = list(map(float,geom['fromto'].split()))
                a,b = Vector(ends[:3]),Vector(ends[3:])
                local = Vector((0,0,1)).rotation_difference(b-a).to_matrix().to_4x4()
                local.translation = (a+b)*.5/units
                size = [size[0], (b-a).length*.5]
            block = mesh('PhysX collider '+str(i),kind,[v/units for v in size])
            obj = bpy.data.objects.new(block.name,block)
            obj[OWNER] = owner
            obj['physx_body'] = smpl.names[body]
            obj['physx_local'] = skeleton.flat(local)
            collection.objects.link(obj)
            block.materials.append(mat)
            obj.matrix_world = matrices[smpl.names[body]] @ local
            obj.hide_select = True
        return collection,rig
    except Exception:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        remove(collection,owner)
        raise
    finally:
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
        if previous_mode == 'POSE' and active:
            bpy.ops.object.mode_set(mode='POSE')


def sync(node, matrices):
    if node.rig is None or node.collection is None:
        raise ValueError('Generated rig removed; Setup again')
    for bone in node.rig.pose.bones:
        parent = {'parent_matrix':matrices[bone.parent.name],
                  'parent_matrix_local':bone.parent.bone.matrix_local} if bone.parent else {}
        bone.matrix_basis = bone.bone.convert_local_to_pose(matrices[bone.name],bone.bone.matrix_local,invert=True,**parent)
    for obj in node.collection.objects:
        if obj.get(OWNER) == node.runtime_id and obj.get('physx_body') in matrices:
            obj.matrix_world = matrices[obj['physx_body']] @ skeleton.unflat(obj['physx_local'])

