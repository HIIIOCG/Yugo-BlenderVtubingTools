"""Dependency-free access to VRM Add-on metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpringBoneStats:
    springs: int = 0
    joints: int = 0
    colliders: int = 0


def get_vrm_extension(armature_object):
    if armature_object is None or getattr(armature_object, "type", None) != "ARMATURE":
        return None
    return getattr(getattr(armature_object, "data", None), "vrm_addon_extension", None)


def is_vrm_armature(armature_object) -> bool:
    return get_vrm_extension(armature_object) is not None


def vrm_version(extension) -> str:
    return str(getattr(extension, "spec_version", "") or "")


def is_vrm1(extension) -> bool:
    checker = getattr(extension, "is_vrm1", None)
    if callable(checker):
        try:
            return bool(checker())
        except (AttributeError, ReferenceError, TypeError):
            pass
    return vrm_version(extension).startswith("1")


def is_vrm0(extension) -> bool:
    checker = getattr(extension, "is_vrm0", None)
    if callable(checker):
        try:
            return bool(checker())
        except (AttributeError, ReferenceError, TypeError):
            pass
    return vrm_version(extension).startswith("0")


def spring_bone_data(extension):
    return getattr(extension, "spring_bone1", None)


def vrm0_secondary_animation(extension):
    return getattr(getattr(extension, "vrm0", None), "secondary_animation", None)


def spring_bone_stats(extension) -> SpringBoneStats:
    if is_vrm0(extension):
        secondary_animation = vrm0_secondary_animation(extension)
        if secondary_animation is None:
            return SpringBoneStats()
        bone_groups = tuple(getattr(secondary_animation, "bone_groups", ()) or ())
        return SpringBoneStats(
            springs=len(bone_groups),
            joints=sum(
                len(getattr(bone_group, "bones", ()) or ())
                for bone_group in bone_groups
            ),
            colliders=sum(
                len(getattr(collider_group, "colliders", ()) or ())
                for collider_group in (
                    getattr(secondary_animation, "collider_groups", ()) or ()
                )
            ),
        )
    spring_bone = spring_bone_data(extension)
    if spring_bone is None:
        return SpringBoneStats()
    springs = tuple(getattr(spring_bone, "springs", ()) or ())
    return SpringBoneStats(
        springs=len(springs),
        joints=sum(len(getattr(spring, "joints", ()) or ()) for spring in springs),
        colliders=len(getattr(spring_bone, "colliders", ()) or ()),
    )


def spring_joint_bone_names(extension) -> tuple[str, ...]:
    names = []
    seen = set()
    spring_bone = spring_bone_data(extension)
    for spring in getattr(spring_bone, "springs", ()) or ():
        for joint in getattr(spring, "joints", ()) or ():
            name = str(getattr(getattr(joint, "node", None), "bone_name", "") or "")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return tuple(names)
