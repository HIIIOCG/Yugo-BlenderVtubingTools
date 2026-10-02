"""Resolve VMC HumanBodyBones names through VRM Add-on metadata."""

from __future__ import annotations


VMC_TO_VRM1_PROPERTY = {
    "Hips": "hips",
    "Spine": "spine",
    "Chest": "chest",
    "UpperChest": "upper_chest",
    "Neck": "neck",
    "Head": "head",
    "LeftEye": "left_eye",
    "RightEye": "right_eye",
    "Jaw": "jaw",
    "LeftShoulder": "left_shoulder",
    "LeftUpperArm": "left_upper_arm",
    "LeftLowerArm": "left_lower_arm",
    "LeftHand": "left_hand",
    "RightShoulder": "right_shoulder",
    "RightUpperArm": "right_upper_arm",
    "RightLowerArm": "right_lower_arm",
    "RightHand": "right_hand",
    "LeftUpperLeg": "left_upper_leg",
    "LeftLowerLeg": "left_lower_leg",
    "LeftFoot": "left_foot",
    "LeftToes": "left_toes",
    "RightUpperLeg": "right_upper_leg",
    "RightLowerLeg": "right_lower_leg",
    "RightFoot": "right_foot",
    "RightToes": "right_toes",
}


def _add_fingers() -> None:
    for side in ("Left", "Right"):
        prop_side = side.lower()
        # VMC uses Unity HumanBodyBones names. VRM 1.0 inserted a thumb
        # metacarpal, so Unity's proximal/intermediate slots map as below.
        VMC_TO_VRM1_PROPERTY[f"{side}ThumbProximal"] = f"{prop_side}_thumb_metacarpal"
        VMC_TO_VRM1_PROPERTY[f"{side}ThumbIntermediate"] = f"{prop_side}_thumb_proximal"
        VMC_TO_VRM1_PROPERTY[f"{side}ThumbDistal"] = f"{prop_side}_thumb_distal"

        for finger in ("Index", "Middle", "Ring", "Little"):
            prop_finger = finger.lower()
            for segment in ("Proximal", "Intermediate", "Distal"):
                VMC_TO_VRM1_PROPERTY[f"{side}{finger}{segment}"] = (
                    f"{prop_side}_{prop_finger}_{segment.lower()}"
                )


_add_fingers()


def _lower_camel(name: str) -> str:
    return name[:1].lower() + name[1:]


VRM0_NAME_TO_VMC = {
    _lower_camel(vmc_name).casefold(): vmc_name
    for vmc_name in VMC_TO_VRM1_PROPERTY
}


def get_vrm_extension(armature):
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return None
    return getattr(getattr(armature, "data", None), "vrm_addon_extension", None)


def is_vrm_armature(armature) -> bool:
    return get_vrm_extension(armature) is not None


def _assigned_bone_name(human_bone) -> str:
    node = getattr(human_bone, "node", None)
    if node is None:
        return ""
    return str(
        getattr(node, "bone_name", "")
        or getattr(node, "value", "")
        or ""
    )


def _vrm1_mapping(extension) -> dict[str, str]:
    humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
    human_bones = getattr(humanoid, "human_bones", None)
    if human_bones is None:
        return {}

    result = {}
    for vmc_name, property_name in VMC_TO_VRM1_PROPERTY.items():
        bone_name = _assigned_bone_name(getattr(human_bones, property_name, None))
        if bone_name:
            result[vmc_name] = bone_name
    return result


def _vrm0_mapping(extension) -> dict[str, str]:
    humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
    result = {}
    for human_bone in getattr(humanoid, "human_bones", ()):
        vrm_name = str(getattr(human_bone, "bone", "") or "")
        vmc_name = VRM0_NAME_TO_VMC.get(vrm_name.casefold())
        bone_name = _assigned_bone_name(human_bone)
        if vmc_name and bone_name:
            result[vmc_name] = bone_name
    return result


def build_vrm_humanoid_map(armature) -> dict[str, str]:
    """Return case-folded VMC bone name -> Blender pose bone name."""

    extension = get_vrm_extension(armature)
    if extension is None:
        return {}

    version = str(getattr(extension, "spec_version", ""))
    metadata_mapping = (
        _vrm1_mapping(extension) if version.startswith("1") else _vrm0_mapping(extension)
    )

    pose_bones = getattr(getattr(armature, "pose", None), "bones", None)
    if pose_bones is None:
        return {}

    available_names = set(pose_bones.keys())
    result = {
        vmc_name.casefold(): bone_name
        for vmc_name, bone_name in metadata_mapping.items()
        if bone_name in available_names
    }

    # Some imported VRMs have incomplete metadata while retaining standard bone
    # names. Use a conservative normalized-name fallback for those bones only.
    normalized_available = {}
    for bone_name in available_names:
        key = "".join(char for char in bone_name.casefold() if char.isalnum())
        normalized_available.setdefault(key, bone_name)

    for vmc_name in VMC_TO_VRM1_PROPERTY:
        key = vmc_name.casefold()
        if key in result:
            continue
        normalized = "".join(char for char in key if char.isalnum())
        bone_name = normalized_available.get(normalized)
        if bone_name:
            result[key] = bone_name

    return result


def mask_property_for_vmc_name(vmc_name: str) -> str | None:
    """Return the Yugo Humanoid Mask property for a VMC humanoid bone."""

    name = vmc_name.casefold()
    central = {
        "hips": "hips",
        "spine": "spine",
        "chest": "chest",
        "upperchest": "chest",
        "neck": "neck",
        "head": "head",
        "lefteye": "head",
        "righteye": "head",
        "jaw": "head",
    }
    if name in central:
        return central[name]

    if name.startswith("left"):
        side, part = "left", name[4:]
    elif name.startswith("right"):
        side, part = "right", name[5:]
    else:
        return None

    if part in {"upperleg", "lowerleg", "foot", "toes"}:
        region = "leg"
    elif part == "shoulder":
        region = "shoulder"
    elif part in {"upperarm", "lowerarm"}:
        region = "arm"
    elif part == "hand":
        region = "hand"
    elif any(
        finger in part
        for finger in ("thumb", "index", "middle", "ring", "little")
    ):
        region = "finger"
    else:
        return None
    return f"{side}_{region}"


def build_blender_bone_region_map(armature) -> dict[str, str]:
    """Return Blender pose bone name -> Yugo Humanoid Mask property."""

    result = {}
    for vmc_name, bone_name in build_vrm_humanoid_map(armature).items():
        region = mask_property_for_vmc_name(vmc_name)
        if region:
            result.setdefault(bone_name, region)
    return result
