"""Yugo canonical deltas <-> articulated body poses; never sample a driven pose."""
import math
from mathutils import Matrix, Quaternion, Vector
from . import bridge, skeleton


def validate(reference):
    host = bridge.base()
    if reference is None:
        return host.data.HumanoidMotion()
    if not isinstance(reference, host.data.HumanoidMotion):
        raise ValueError("Reference Motion requires Yugo Humanoid Motion")
    for item in [*reference.bones.values(), reference.root]:
        if item is None:
            continue
        for attr in ("rotation", "location"):
            value = getattr(item, attr, None)
            if value is not None and not all(math.isfinite(float(x)) for x in value):
                raise ValueError("Reference Motion contains non-finite values")
        if item.rotation is not None and item.rotation.magnitude < 1e-6:
            raise ValueError("Reference Motion contains a zero quaternion")
    return reference.copy()


def virtual_pose(source, binding, reference):
    matrices = {}
    canonical = {name: h for h, name in binding["mapping"].items()}

    def visit(bone):
        if bone.name in matrices:
            return matrices[bone.name]
        item = reference.bones.get(canonical.get(bone.name))
        basis = Matrix.Identity(4)
        if item:
            rest = bone.matrix_local.to_3x3().normalized()
            q = rest.to_quaternion()
            if item.rotation is not None:
                basis = (q.inverted() @ item.rotation.normalized() @ q).to_matrix().to_4x4()
            if item.location is not None:
                basis.translation = rest.inverted() @ item.location
        parent = {"parent_matrix": visit(bone.parent), "parent_matrix_local": bone.parent.matrix_local} if bone.parent else {}
        matrices[bone.name] = bone.convert_local_to_pose(basis, bone.matrix_local, **parent)
        return matrices[bone.name]

    for bone in source.data.bones:
        visit(bone)
    return matrices


def reference_state(source, binding, reference):
    import numpy as np
    from . import model, tracker_math as tm
    smpl = model.Skeleton()
    if binding.get('source_mode') == 'BUILTIN':
        placement = skeleton.unflat(binding['placement']).to_quaternion()
        local = np.tile([1.,0,0,0],(24,1))
        for index,name in enumerate(smpl.names):
            item = reference.bones.get(binding['body_to_humanoid'].get(name))
            if item and item.rotation is not None:
                local[index] = tuple(placement.inverted() @ item.rotation.normalized() @ placement)
        root_pos = Vector((0,0,.95))
        hips = reference.bones.get('hips')
        if hips and hips.location is not None:
            root_pos += (placement.inverted() @ hips.location)*binding['unit_scale']
        root_rotation = Quaternion(local[0])
        if reference.root:
            if reference.root.location is not None:
                root_pos += (placement.inverted() @ reference.root.location)*binding['unit_scale']
            if reference.root.rotation is not None:
                root_rotation = placement.inverted() @ reference.root.rotation.normalized() @ placement @ root_rotation
        p,q = smpl.fk(tuple(root_pos),tuple(root_rotation),local)
        return {'positions':p.tolist(),'rotations':q.tolist()}
    poses = virtual_pose(source,binding,reference)
    source_world = skeleton.unflat(binding['source_matrix'])
    placement = skeleton.unflat(binding['placement']).to_quaternion()
    rotations = []
    for i,name in enumerate(smpl.names):
        h = binding['body_to_humanoid'].get(name)
        if h:
            bone = source.data.bones[binding['mapping'][h]]
            target = (source_world @ poses[bone.name]).to_quaternion()
            rest = (source_world @ bone.matrix_local).to_quaternion()
            rotations.append(placement.inverted() @ target @ rest.inverted() @ placement)
        else:
            rotations.append(rotations[smpl.parents[i]].copy() if i else Quaternion())
    q = np.asarray([tuple(r.normalized()) for r in rotations])
    local = np.tile([1.,0,0,0],(24,1))
    local[1:] = tm.multiply(tm.conjugate(q[smpl.parents[1:]]),q[1:])
    root_pos = Vector((0,0,.95))
    hips = reference.bones.get('hips')
    if hips and hips.location is not None:
        delta = placement.inverted() @ (source_world.to_3x3() @ hips.location)
        root_pos += delta*binding['unit_scale']
    # Root is a delta in the host's Humanoid Motion contract.
    if reference.root and reference.root.location is not None:
        root_pos += (placement.inverted() @ reference.root.location)*binding['unit_scale']
    if reference.root and reference.root.rotation is not None:
        q[0] = tuple(placement.inverted() @ reference.root.rotation @ placement @ Quaternion(q[0]))
    p,q = smpl.fk(tuple(root_pos),q[0],local)
    return {'positions':p.tolist(),'rotations':q.tolist()}


def output(matrices_by_body, binding, source):
    host = bridge.base()
    result = host.data.HumanoidMotion()
    if binding.get('source_mode') == 'BUILTIN':
        from . import model
        smpl = model.Skeleton()
        placement = skeleton.unflat(binding['placement'])
        canonical = placement.to_quaternion()
        orientation = canonical.inverted()
        rotations = [orientation @ matrices_by_body[name].to_quaternion() for name in smpl.names]
        for index,name in enumerate(smpl.names):
            human = binding['body_to_humanoid'].get(name)
            if not human:
                continue
            rotation = rotations[index] if index == 0 else rotations[smpl.parents[index]].inverted() @ rotations[index]
            rotation = canonical @ rotation @ canonical.inverted()
            rotation.normalize()
            location = None
            if index == 0:
                physical_position = placement.inverted() @ (matrices_by_body[name].translation*binding['unit_scale'])
                location = canonical @ ((physical_position-Vector((0,0,.95)))/binding['unit_scale'])
            result.bones[human] = host.data.HumanoidTransform(rotation,location)
        return result
    source_world = skeleton.unflat(binding["source_matrix"])
    inverse = source_world.inverted()
    offset = Vector(binding["offset"])
    rotations = {}
    hip_position = None
    for body_name, h in binding["body_to_humanoid"].items():
        bone_name = binding["mapping"][h]
        rest_source = source_world @ source.data.bones[bone_name].matrix_local
        rest_source.translation += offset
        actual = matrices_by_body[body_name]
        rest_body = skeleton.unflat(binding["rest_body_matrices"][body_name])
        world = actual @ rest_body.inverted() @ rest_source
        world.translation -= offset
        local = inverse @ world
        rotations[bone_name] = local.to_quaternion()
        if h == 'hips':
            hip_position = local.translation.copy()

    matrices = {}

    def visit(bone):
        if bone.name not in matrices:
            parent = {"parent_matrix": visit(bone.parent), "parent_matrix_local": bone.parent.matrix_local} if bone.parent else {}
            rest_pose = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local, **parent)
            if bone.name in rotations:
                target = rotations[bone.name].to_matrix().to_4x4()
                # Retarget orientations onto the source hierarchy. SMPL child
                # positions encode its own lengths and joint solver drift;
                # they cannot be translations on a connected VRM skeleton.
                target.translation = hip_position if bone.name == binding['mapping']['hips'] else rest_pose.translation
                matrices[bone.name] = target
            else:
                matrices[bone.name] = rest_pose
        return matrices[bone.name]

    for body_name, h in binding["body_to_humanoid"].items():
        bone = source.data.bones[binding["mapping"][h]]
        parent = {"parent_matrix": visit(bone.parent), "parent_matrix_local": bone.parent.matrix_local} if bone.parent else {}
        basis = bone.convert_local_to_pose(visit(bone), bone.matrix_local, invert=True, **parent)
        rest = bone.matrix_local.to_3x3().normalized()
        q = rest.to_quaternion()
        rotation = q @ basis.to_quaternion() @ q.inverted()
        rotation.normalize()
        result.bones[h] = host.data.HumanoidTransform(rotation, rest @ basis.translation if h == 'hips' else None)
    return result
