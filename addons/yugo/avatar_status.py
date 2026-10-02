"""Read-only validation of VRM humanoid and ARKit expression bindings."""

import re

from .vrm import get_vrm_extension


VRM1_HUMANOID_BONES = (
    ("hips", "hips"),
    ("spine", "spine"),
    ("chest", "chest"),
    ("neck", "neck"),
    ("head", "head"),
    ("left_eye", "leftEye"),
    ("right_eye", "rightEye"),
    ("left_upper_leg", "leftUpperLeg"),
    ("left_lower_leg", "leftLowerLeg"),
    ("left_foot", "leftFoot"),
    ("left_toes", "leftToes"),
    ("right_upper_leg", "rightUpperLeg"),
    ("right_lower_leg", "rightLowerLeg"),
    ("right_foot", "rightFoot"),
    ("right_toes", "rightToes"),
    ("left_shoulder", "leftShoulder"),
    ("left_upper_arm", "leftUpperArm"),
    ("left_lower_arm", "leftLowerArm"),
    ("left_hand", "leftHand"),
    ("right_shoulder", "rightShoulder"),
    ("right_upper_arm", "rightUpperArm"),
    ("right_lower_arm", "rightLowerArm"),
    ("right_hand", "rightHand"),
    ("left_thumb_metacarpal", "leftThumbMetacarpal"),
    ("left_thumb_proximal", "leftThumbProximal"),
    ("left_thumb_distal", "leftThumbDistal"),
    ("left_index_proximal", "leftIndexProximal"),
    ("left_index_intermediate", "leftIndexIntermediate"),
    ("left_index_distal", "leftIndexDistal"),
    ("left_middle_proximal", "leftMiddleProximal"),
    ("left_middle_intermediate", "leftMiddleIntermediate"),
    ("left_middle_distal", "leftMiddleDistal"),
    ("left_ring_proximal", "leftRingProximal"),
    ("left_ring_intermediate", "leftRingIntermediate"),
    ("left_ring_distal", "leftRingDistal"),
    ("left_little_proximal", "leftLittleProximal"),
    ("left_little_intermediate", "leftLittleIntermediate"),
    ("left_little_distal", "leftLittleDistal"),
    ("right_thumb_metacarpal", "rightThumbMetacarpal"),
    ("right_thumb_proximal", "rightThumbProximal"),
    ("right_thumb_distal", "rightThumbDistal"),
    ("right_index_proximal", "rightIndexProximal"),
    ("right_index_intermediate", "rightIndexIntermediate"),
    ("right_index_distal", "rightIndexDistal"),
    ("right_middle_proximal", "rightMiddleProximal"),
    ("right_middle_intermediate", "rightMiddleIntermediate"),
    ("right_middle_distal", "rightMiddleDistal"),
    ("right_ring_proximal", "rightRingProximal"),
    ("right_ring_intermediate", "rightRingIntermediate"),
    ("right_ring_distal", "rightRingDistal"),
    ("right_little_proximal", "rightLittleProximal"),
    ("right_little_intermediate", "rightLittleIntermediate"),
    ("right_little_distal", "rightLittleDistal"),
)

VRM0_HUMANOID_BONES = tuple(
    display_name
    for _property_name, display_name in VRM1_HUMANOID_BONES
    if "Thumb" not in display_name
) + (
    "leftThumbProximal",
    "leftThumbIntermediate",
    "leftThumbDistal",
    "rightThumbProximal",
    "rightThumbIntermediate",
    "rightThumbDistal",
)

ARKIT_BLENDSHAPES = (
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyeWideLeft",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
)


def get_vrm_version(armature_object):
    extension = get_vrm_extension(armature_object)
    if extension is None:
        return None
    version = str(getattr(extension, "spec_version", ""))
    if version == "1.0":
        return "VRM 1.0"
    if version == "0.0":
        return "VRM 0.x"
    return version or "Unknown"


def _bone_name(human_bone):
    node = getattr(human_bone, "node", None)
    return str(getattr(node, "bone_name", "") or "")


def get_missing_humanoid_bones(armature_object):
    extension = get_vrm_extension(armature_object)
    if extension is None:
        return []

    version = str(getattr(extension, "spec_version", ""))
    if version == "1.0":
        humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
        human_bones = getattr(humanoid, "human_bones", None)
        return [
            display_name
            for property_name, display_name in VRM1_HUMANOID_BONES
            if not _bone_name(getattr(human_bones, property_name, None))
        ]

    humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
    assignments = {}
    for human_bone in getattr(humanoid, "human_bones", ()):
        assignments[str(getattr(human_bone, "bone", ""))] = _bone_name(human_bone)
    return [name for name in VRM0_HUMANOID_BONES if not assignments.get(name)]


def _normalized_expression_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _valid_vrm1_expression(expression):
    for bind in getattr(expression, "morph_target_binds", ()):
        node = getattr(bind, "node", None)
        if getattr(node, "mesh_object_name", "") and getattr(bind, "index", ""):
            return True
    return False


def _valid_vrm0_expression(expression):
    for bind in getattr(expression, "binds", ()):
        mesh = getattr(bind, "mesh", None)
        if getattr(mesh, "mesh_object_name", "") and getattr(bind, "index", ""):
            return True
    return False


def get_arkit_bind_status(armature_object):
    extension = get_vrm_extension(armature_object)
    required = {_normalized_expression_name(name): name for name in ARKIT_BLENDSHAPES}
    if extension is None:
        return 0, len(required), list(ARKIT_BLENDSHAPES)

    bound_names = set()
    version = str(getattr(extension, "spec_version", ""))
    if version == "1.0":
        expressions = getattr(getattr(extension, "vrm1", None), "expressions", None)
        for expression in getattr(expressions, "custom", ()):
            if _valid_vrm1_expression(expression):
                bound_names.add(
                    _normalized_expression_name(getattr(expression, "custom_name", ""))
                )
    else:
        master = getattr(getattr(extension, "vrm0", None), "blend_shape_master", None)
        for expression in getattr(master, "blend_shape_groups", ()):
            if _valid_vrm0_expression(expression):
                bound_names.add(_normalized_expression_name(getattr(expression, "name", "")))

    missing = [display_name for key, display_name in required.items() if key not in bound_names]
    return len(required) - len(missing), len(required), missing
