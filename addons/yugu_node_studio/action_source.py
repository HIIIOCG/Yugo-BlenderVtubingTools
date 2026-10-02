"""Read Blender Actions and emit canonical VRM Humanoid motion."""

from __future__ import annotations

import bpy
from bpy_extras import anim_utils
from mathutils import Euler, Quaternion, Vector

from .data import BoneMapData, HumanoidMotion, HumanoidTransform


_fcurve_cache = {}


def _slot(action, armature):
    slots = tuple(
        slot
        for slot in getattr(action, "slots", ())
        if slot.target_id_type in {"OBJECT", "UNSPECIFIED"}
    )
    for slot in slots:
        if slot.name_display == armature.name:
            return slot
    return slots[0] if slots else None


def _fcurves(action, armature):
    key = (action.as_pointer(), armature.as_pointer())
    cached = _fcurve_cache.get(key)
    if cached is not None:
        return cached
    slot = _slot(action, armature)
    try:
        channelbag = anim_utils.action_get_channelbag_for_slot(action, slot)
        curves = tuple(channelbag.fcurves) if channelbag is not None else ()
    except (AttributeError, RuntimeError, TypeError):
        curves = tuple(getattr(action, "fcurves", ()))
    _fcurve_cache[key] = curves
    return curves


def _channels(action, armature, frame):
    return {
        (curve.data_path, curve.array_index): curve.evaluate(frame)
        for curve in _fcurves(action, armature)
        if not curve.mute
    }


def _values(channels, data_path, size, defaults):
    result = list(defaults)
    found = False
    for index in range(size):
        key = (data_path, index)
        if key in channels:
            result[index] = float(channels[key])
            found = True
    return tuple(result) if found else None


def _rotation(channels, owner):
    quaternion = _values(
        channels,
        owner.path_from_id("rotation_quaternion"),
        4,
        (1.0, 0.0, 0.0, 0.0),
    )
    if quaternion is not None:
        result = Quaternion(quaternion)
    else:
        euler = _values(
            channels,
            owner.path_from_id("rotation_euler"),
            3,
            (0.0, 0.0, 0.0),
        )
        if euler is not None:
            order = owner.rotation_mode
            if order in {"QUATERNION", "AXIS_ANGLE"}:
                order = "XYZ"
            result = Euler(euler, order).to_quaternion()
        else:
            axis_angle = _values(
                channels,
                owner.path_from_id("rotation_axis_angle"),
                4,
                (0.0, 0.0, 1.0, 0.0),
            )
            if axis_angle is None:
                return None
            angle, x, y, z = axis_angle
            axis = Vector((x, y, z))
            result = Quaternion(axis.normalized(), angle) if axis.length else Quaternion()
    if result.magnitude < 1.0e-8:
        return Quaternion()
    result.normalize()
    return result


def _location(channels, owner):
    values = _values(
        channels, owner.path_from_id("location"), 3, (0.0, 0.0, 0.0)
    )
    return Vector(values) if values is not None else None


def _sample_frame(action, frame, speed, offset, cyclic):
    frame = float(frame) * float(speed) + float(offset)
    if not cyclic:
        return frame
    start, end = (float(value) for value in action.frame_range)
    duration = end - start
    return start if duration <= 1.0e-8 else start + ((frame - start) % duration)


def sample_action(
    action,
    bone_map: BoneMapData | None,
    frame,
    speed=1.0,
    offset=0.0,
    cyclic=True,
):
    if action is None or bone_map is None:
        return HumanoidMotion()
    armature = bpy.data.objects.get(bone_map.armature_name)
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        return HumanoidMotion()
    channels = _channels(
        action,
        armature,
        _sample_frame(action, frame, speed, offset, cyclic),
    )
    motion = HumanoidMotion()
    for humanoid, bone_name in bone_map.by_humanoid.items():
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        local_rotation = _rotation(channels, pose_bone)
        local_location = _location(channels, pose_bone)
        if local_rotation is None and local_location is None:
            continue
        rest = pose_bone.bone.matrix_local.to_3x3().normalized()
        rest_rotation = rest.to_quaternion()
        canonical_rotation = None
        if local_rotation is not None:
            canonical_rotation = rest_rotation @ local_rotation @ rest_rotation.inverted()
            canonical_rotation.normalize()
        canonical_location = rest @ local_location if local_location is not None else None
        motion.bones[humanoid] = HumanoidTransform(
            canonical_rotation, canonical_location
        )
    root_rotation = _rotation(channels, armature)
    root_location = _location(channels, armature)
    if root_rotation is not None or root_location is not None:
        motion.root = HumanoidTransform(root_rotation, root_location)
    return motion


def clear_cache():
    _fcurve_cache.clear()
