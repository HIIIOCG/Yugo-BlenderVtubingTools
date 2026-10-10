import hashlib, json, math
from functools import lru_cache
from mathutils import Matrix, Quaternion, Vector
from . import model

BODY_MAP = {"Pelvis": "hips", "Torso": "spine", "Spine": "chest", "Chest": "upperChest",
            "Neck": "neck", "Head": "head"}
for side, prefix in (("left", "L_"), ("right", "R_")):
    for body, humanoid in (("Hip", "UpperLeg"), ("Knee", "LowerLeg"), ("Ankle", "Foot"),
                           ("Toe", "Toes"), ("Thorax", "Shoulder"), ("Shoulder", "UpperArm"),
                           ("Elbow", "LowerArm"), ("Wrist", "Hand")):
        BODY_MAP[prefix + body] = side + humanoid
    # SMPL's terminal Hand body remains unbound; Wrist drives the VRM hand.
    BODY_MAP[prefix + "Hand"] = None
REQUIRED = ("hips", "head", "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm",
            "rightLowerArm", "rightHand", "leftUpperLeg", "leftLowerLeg", "leftFoot",
            "rightUpperLeg", "rightLowerLeg", "rightFoot")
BUILTIN_BASIS = 'BLENDER_FORWARD_MINUS_Y'
BUILTIN_BASIS_VERSION = 2


def numbers(values):
    return " ".join(format(float(v), ".15g") for v in values)


def flat(matrix):
    return [float(v) for row in matrix for v in row]


def unflat(values):
    return Matrix([values[i:i+4] for i in range(0, 16, 4)])


def matrix(position, rotation, units=1):
    m = Matrix([rotation[i:i+3] for i in range(0, 9, 3)]).to_4x4()
    m.translation = Vector(position) / units
    return m


def signature(source, mapping, units, mode, offset):
    content = {"source": source.name, "mapping": mapping, "matrix": flat(source.matrix_world),
               "asset": "smpl-24-v1", "units": float(units), "mode": mode, "offset": list(offset),
               "bones": [(b.name, b.parent.name if b.parent else "", flat(b.matrix_local),
                          list(b.tail_local)) for b in source.data.bones]}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def capture(source, mapping, units):
    if not math.isfinite(units) or units <= 0:
        raise ValueError("Scene unit scale must be positive")
    missing = [h for h in REQUIRED if not mapping.get(h) or mapping[h] not in source.data.bones]
    if missing:
        raise ValueError("Bone Map missing: " + ", ".join(missing))
    used = [name for name in mapping.values() if name in source.data.bones]
    if len(used) != len(set(used)):
        raise ValueError("Bone Map has duplicate bone assignments")
    linear = source.matrix_world.to_3x3()
    scales = [linear.col[i].length for i in range(3)]
    if linear.determinant() <= 0 or min(scales) < 1e-7 or max(scales) - min(scales) > max(scales)*1e-4:
        raise ValueError("Apply non-uniform or negative Armature scale before Setup")
    basis = linear.normalized()
    if any(abs(basis.col[i].dot(basis.col[j])) > 1e-5 for i in range(3) for j in range(i)):
        raise ValueError("Armature transform contains shear")
    points = {}
    for h, name in mapping.items():
        bone = source.data.bones.get(name)
        if bone:
            a = source.matrix_world @ bone.head_local * units
            b = source.matrix_world @ bone.tail_local * units
            if not all(math.isfinite(v) for v in (*a, *b)):
                raise ValueError("Non-finite bone position: " + h)
            if (b-a).length < 1e-6:
                raise ValueError("Zero length bone: " + h)
            points[h] = (a, b)
    return points


def build(source, mapping, units, offset):
    points = capture(source, mapping, units)
    smpl = model.Skeleton()
    left = (points['leftUpperArm'][0]-points['rightUpperArm'][0]).normalized()
    up = points['head'][0]-points['hips'][0]
    up -= left*up.dot(left)
    if up.length < 1e-5:
        raise ValueError('Cannot determine character orientation')
    up.normalize()
    forward = left.cross(up).normalized()
    # Physics stays Z-up; placement only changes yaw and horizontal translation.
    yaw = math.atan2(forward.y, forward.x)
    placement = Quaternion((0,0,1), yaw).to_matrix().to_4x4()
    origin = points['hips'][0]+Vector(offset)*units
    placement.translation = Vector((origin.x, origin.y, float(offset[2])*units))
    pos, rot = smpl.fk()
    matrices = world_matrices(pos, rot, {'placement': flat(placement), 'unit_scale': units})
    binding = {
        'source_mode': 'BONE_MAP',
        'mapping': mapping, 'source_matrix': flat(source.matrix_world),
        'unit_scale': units, 'offset': list(offset), 'placement': flat(placement),
        'body_to_humanoid': {b:h for b,h in BODY_MAP.items() if h in points},
        'rest_body_matrices': {b:flat(matrices[b]) for b in smpl.names},
        'source_signature': signature(source, mapping, units, 'TEMPLATE', offset),
    }
    return binding, matrices

def builtin_placement(units,offset):
    if not math.isfinite(units) or units <= 0:
        raise ValueError('Scene unit scale must be positive')
    if len(offset) != 3 or not all(math.isfinite(float(value)) for value in offset):
        raise ValueError('Skeleton placement must be finite')
    # The actual asset has left +Y and toe/forward +X. Blender VRM rest
    # geometry has left +X and forward -Y; this converts both at once.
    placement = Quaternion((0,0,1),-math.pi/2).to_matrix().to_4x4()
    placement.translation = Vector(offset)*units
    return placement


def builtin_signature(units,offset):
    content = {'asset':'smpl-24-v1','mode':'BUILTIN','canonical_basis':BUILTIN_BASIS,
               'basis_version':BUILTIN_BASIS_VERSION,'units':float(units),'offset':[float(v) for v in offset],
               'placement':flat(builtin_placement(units,offset))}
    return hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=32)
def _builtin_expected(units,offset):
    placement = tuple(flat(builtin_placement(units,offset)))
    smpl = model.Skeleton()
    expected = world_matrices(*smpl.fk(),{'placement':placement,'unit_scale':units})
    rest = tuple((name,tuple(flat(expected[name]))) for name in smpl.names)
    return builtin_signature(units,offset),placement,rest


def builtin_binding_current(binding,units,offset):
    """Reject saved axes/rest data that would rotate the preview or input twice."""
    try:
        expected_signature,expected_placement,expected_rest = _builtin_expected(float(units),tuple(float(v) for v in offset))
        if (binding.get('source_mode') != 'BUILTIN' or binding.get('canonical_basis') != BUILTIN_BASIS
                or binding.get('basis_version') != BUILTIN_BASIS_VERSION
                or binding.get('source_signature') != expected_signature
                or binding.get('mapping') != {}
                or binding.get('body_to_humanoid') != {b:h for b,h in BODY_MAP.items() if h is not None}):
            return False
        if len(binding['placement']) != 16 or any(not math.isclose(float(a),b,rel_tol=1e-6,abs_tol=1e-6)
                for a,b in zip(binding['placement'],expected_placement)):
            return False
        if not math.isclose(float(binding['unit_scale']),float(units),rel_tol=1e-9):
            return False
        rest = binding['rest_body_matrices']
        return set(rest) == {name for name,_ in expected_rest} and all(len(rest[name]) == 16 and all(
            math.isclose(float(a),b,rel_tol=1e-6,abs_tol=1e-5)
            for a,b in zip(rest[name],values)) for name,values in expected_rest)
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def build_builtin(units,offset):
    fingerprint = builtin_signature(units,offset)
    smpl = model.Skeleton()
    placement = builtin_placement(units,offset)
    binding = {'source_mode':'BUILTIN','canonical_basis':BUILTIN_BASIS,'basis_version':BUILTIN_BASIS_VERSION,
        'mapping':{},'source_matrix':flat(Matrix.Identity(4)),
        'unit_scale':units,'offset':list(offset),'placement':flat(placement),
        'body_to_humanoid':{body:human for body,human in BODY_MAP.items() if human is not None},
        'source_signature':fingerprint}
    matrices = world_matrices(*smpl.fk(),binding)
    binding['rest_body_matrices'] = {body:flat(matrices[body]) for body in smpl.names}
    return binding,matrices


def world_matrices(pos, rot, binding):
    placement = unflat(binding['placement'])
    result = {}
    for name, p, q in zip(model.Skeleton().names, pos, rot):
        m = Quaternion(q).to_matrix().to_4x4()
        m.translation = Vector(p)
        m = placement @ m
        m.translation /= binding['unit_scale']
        result[name] = m
    return result

