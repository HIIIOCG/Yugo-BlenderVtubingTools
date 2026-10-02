"""Shared identifiers for Yugu's VRM Humanoid motion graph."""

TREE_ID = "YUGUS_NT_humanoid_motion"

SOCKET_MOTION = "YUGUS_NS_humanoid_motion"
SOCKET_MOTION_MASK = "YUGUS_NS_humanoid_mask"
SOCKET_TRIGGER = "YUGUS_NS_trigger"
SOCKET_NATIVE_POSE = "YUGUS_NS_native_pose"
SOCKET_EXPRESSIONS = "YUGUS_NS_expressions"
SOCKET_NATIVE_EXPRESSIONS = "YUGUS_NS_native_expressions"
SOCKET_BONE_MAP = "YUGUS_NS_bone_map"
SOCKET_EXPRESSION_MAP = "YUGUS_NS_expression_map"
SOCKET_SPRING_DATA = "YUGUS_NS_spring_data"
SOCKET_META = "YUGUS_NS_meta"

# Canonical names follow the VRM 1.0 Humanoid specification. Motion sources
# always use these names, never Blender pose-bone names.
VRM1_HUMANOID_PROPERTIES = (
    ("hips", "hips"),
    ("spine", "spine"),
    ("chest", "chest"),
    ("upperChest", "upper_chest"),
    ("neck", "neck"),
    ("head", "head"),
    ("leftEye", "left_eye"),
    ("rightEye", "right_eye"),
    ("jaw", "jaw"),
    ("leftUpperLeg", "left_upper_leg"),
    ("leftLowerLeg", "left_lower_leg"),
    ("leftFoot", "left_foot"),
    ("leftToes", "left_toes"),
    ("rightUpperLeg", "right_upper_leg"),
    ("rightLowerLeg", "right_lower_leg"),
    ("rightFoot", "right_foot"),
    ("rightToes", "right_toes"),
    ("leftShoulder", "left_shoulder"),
    ("leftUpperArm", "left_upper_arm"),
    ("leftLowerArm", "left_lower_arm"),
    ("leftHand", "left_hand"),
    ("rightShoulder", "right_shoulder"),
    ("rightUpperArm", "right_upper_arm"),
    ("rightLowerArm", "right_lower_arm"),
    ("rightHand", "right_hand"),
    ("leftThumbMetacarpal", "left_thumb_metacarpal"),
    ("leftThumbProximal", "left_thumb_proximal"),
    ("leftThumbDistal", "left_thumb_distal"),
    ("leftIndexProximal", "left_index_proximal"),
    ("leftIndexIntermediate", "left_index_intermediate"),
    ("leftIndexDistal", "left_index_distal"),
    ("leftMiddleProximal", "left_middle_proximal"),
    ("leftMiddleIntermediate", "left_middle_intermediate"),
    ("leftMiddleDistal", "left_middle_distal"),
    ("leftRingProximal", "left_ring_proximal"),
    ("leftRingIntermediate", "left_ring_intermediate"),
    ("leftRingDistal", "left_ring_distal"),
    ("leftLittleProximal", "left_little_proximal"),
    ("leftLittleIntermediate", "left_little_intermediate"),
    ("leftLittleDistal", "left_little_distal"),
    ("rightThumbMetacarpal", "right_thumb_metacarpal"),
    ("rightThumbProximal", "right_thumb_proximal"),
    ("rightThumbDistal", "right_thumb_distal"),
    ("rightIndexProximal", "right_index_proximal"),
    ("rightIndexIntermediate", "right_index_intermediate"),
    ("rightIndexDistal", "right_index_distal"),
    ("rightMiddleProximal", "right_middle_proximal"),
    ("rightMiddleIntermediate", "right_middle_intermediate"),
    ("rightMiddleDistal", "right_middle_distal"),
    ("rightRingProximal", "right_ring_proximal"),
    ("rightRingIntermediate", "right_ring_intermediate"),
    ("rightRingDistal", "right_ring_distal"),
    ("rightLittleProximal", "right_little_proximal"),
    ("rightLittleIntermediate", "right_little_intermediate"),
    ("rightLittleDistal", "right_little_distal"),
)

HUMANOID_BONES = tuple(name for name, _property_name in VRM1_HUMANOID_PROPERTIES)
PROPERTY_BY_HUMANOID = dict(VRM1_HUMANOID_PROPERTIES)

VMC_TO_HUMANOID = {name[:1].upper() + name[1:]: name for name in HUMANOID_BONES}

# Unity/VRM 0.x has no thumb metacarpal slot. Its Proximal and Intermediate
# names correspond to the first two useful VRM 1.0 thumb joints.
for side in ("Left", "Right"):
    lower = side.lower()
    VMC_TO_HUMANOID[f"{side}ThumbProximal"] = f"{lower}ThumbMetacarpal"
    VMC_TO_HUMANOID[f"{side}ThumbIntermediate"] = f"{lower}ThumbProximal"
    VMC_TO_HUMANOID[f"{side}ThumbDistal"] = f"{lower}ThumbDistal"

HUMANOID_BY_CASEFOLD = {
    incoming.casefold(): humanoid for incoming, humanoid in VMC_TO_HUMANOID.items()
}
HUMANOID_BY_CASEFOLD.update({name.casefold(): name for name in HUMANOID_BONES})

CENTRAL_REGIONS = ("root", "hips", "spine", "chest", "neck", "head")
BILATERAL_REGIONS = (
    "left_leg",
    "right_leg",
    "left_shoulder",
    "right_shoulder",
    "left_arm",
    "right_arm",
    "left_hand",
    "right_hand",
    "left_finger",
    "right_finger",
)
MASK_REGIONS = CENTRAL_REGIONS + BILATERAL_REGIONS


def humanoid_region(name: str) -> str:
    """Return the coarse mask region for a canonical Humanoid name."""

    if name in {"hips", "spine", "chest", "upperChest", "neck", "head"}:
        return "chest" if name == "upperChest" else name
    if name in {"leftEye", "rightEye", "jaw"}:
        return "head"
    if name.startswith("left"):
        side, part = "left", name[4:]
    elif name.startswith("right"):
        side, part = "right", name[5:]
    else:
        return "root"
    if part in {"UpperLeg", "LowerLeg", "Foot", "Toes"}:
        group = "leg"
    elif part == "Shoulder":
        group = "shoulder"
    elif part in {"UpperArm", "LowerArm"}:
        group = "arm"
    elif part == "Hand":
        group = "hand"
    else:
        group = "finger"
    return f"{side}_{group}"


MIRROR_BONE = {}
for _name in HUMANOID_BONES:
    if _name.startswith("left"):
        MIRROR_BONE[_name] = "right" + _name[4:]
    elif _name.startswith("right"):
        MIRROR_BONE[_name] = "left" + _name[5:]
    else:
        MIRROR_BONE[_name] = _name


EXPRESSION_PRESETS = (
    ("happy", "happy"),
    ("angry", "angry"),
    ("sad", "sad"),
    ("relaxed", "relaxed"),
    ("surprised", "surprised"),
    ("aa", "aa"),
    ("ih", "ih"),
    ("ou", "ou"),
    ("ee", "ee"),
    ("oh", "oh"),
    ("blink", "blink"),
    ("blink_left", "blinkLeft"),
    ("blink_right", "blinkRight"),
    ("look_up", "lookUp"),
    ("look_down", "lookDown"),
    ("look_left", "lookLeft"),
    ("look_right", "lookRight"),
    ("neutral", "neutral"),
)


# Canonical ARKit blendshape names. iFacialMocap sends the bilateral channels
# with ``_L``/``_R`` suffixes, while VRM avatars commonly store the same names
# with ``Left``/``Right`` suffixes. The iFacial source normalizes both forms to
# this list before it exposes an ExpressionFrame.
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


def normalize_name(value: object) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())
