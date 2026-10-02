"""Non-destructive Blender 5.1 Action sampling for Motion Layers."""

from __future__ import annotations

from bpy_extras import anim_utils
from mathutils import Euler, Quaternion, Vector

from .motion_processing import QuaternionSpring, VectorSpring
from .state import ensure_runtime_id
from .vmc.mapping import build_blender_bone_region_map


_rotation_springs = {}
_location_springs = {}


def _action_slot(action, armature):
    object_slots = tuple(
        slot
        for slot in action.slots
        if slot.target_id_type in {"OBJECT", "UNSPECIFIED"}
    )
    for slot in object_slots:
        if slot.name_display == armature.name:
            return slot
    return object_slots[0] if object_slots else None


def _action_fcurves(action, armature):
    slot = _action_slot(action, armature)
    channelbag = anim_utils.action_get_channelbag_for_slot(action, slot)
    return tuple(channelbag.fcurves) if channelbag is not None else ()


def _evaluated_channels(action, armature, frame):
    channels = {}
    for fcurve in _action_fcurves(action, armature):
        if fcurve.mute:
            continue
        channels[(fcurve.data_path, fcurve.array_index)] = fcurve.evaluate(frame)
    return channels


def _sample_frame(action, frame):
    if not action.use_cyclic:
        return frame
    frame_start, frame_end = (float(value) for value in action.frame_range)
    duration = frame_end - frame_start
    if duration <= 1.0e-8:
        return frame_start
    return frame_start + ((frame - frame_start) % duration)


def _values(channels, data_path, size, defaults):
    found = False
    result = list(defaults)
    for index in range(size):
        key = (data_path, index)
        if key not in channels:
            continue
        result[index] = float(channels[key])
        found = True
    return tuple(result) if found else None


def _sample_rotation(channels, pose_bone):
    quaternion = _values(
        channels,
        pose_bone.path_from_id("rotation_quaternion"),
        4,
        (1.0, 0.0, 0.0, 0.0),
    )
    if quaternion is not None:
        result = Quaternion(quaternion)
    else:
        euler = _values(
            channels,
            pose_bone.path_from_id("rotation_euler"),
            3,
            (0.0, 0.0, 0.0),
        )
        if euler is not None:
            order = pose_bone.rotation_mode
            if order in {"QUATERNION", "AXIS_ANGLE"}:
                order = "XYZ"
            result = Euler(euler, order).to_quaternion()
        else:
            axis_angle = _values(
                channels,
                pose_bone.path_from_id("rotation_axis_angle"),
                4,
                (0.0, 0.0, 1.0, 0.0),
            )
            if axis_angle is None:
                return None
            angle, axis_x, axis_y, axis_z = axis_angle
            axis = Vector((axis_x, axis_y, axis_z))
            result = (
                Quaternion(axis.normalized(), angle)
                if axis.length > 1.0e-8
                else Quaternion()
            )
    if result.magnitude < 1.0e-8:
        return Quaternion()
    result.normalize()
    return result


def _sample_location(channels, owner):
    values = _values(
        channels,
        owner.path_from_id("location"),
        3,
        (0.0, 0.0, 0.0),
    )
    return Vector(values) if values is not None else None


def _mask_smooth(layer, region):
    try:
        mask = getattr(layer, region)
        return float(mask.smooth)
    except (AttributeError, ReferenceError):
        return 0.0


def sample_layer(layer, armature, frame, delta_time):
    """Sample an Action without assigning it to the avatar's animation data."""

    action = layer.action
    if action is None:
        return {"bones": {}, "root": None}
    channels = _evaluated_channels(
        action, armature, _sample_frame(action, frame)
    )
    if not channels:
        return {"bones": {}, "root": None}

    layer_key = ensure_runtime_id(layer)
    action_key = action.as_pointer()
    regions = build_blender_bone_region_map(armature)
    bones = {}
    for bone_name, region in regions.items():
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        rotation = _sample_rotation(channels, pose_bone)
        location = _sample_location(channels, pose_bone)
        if rotation is None and location is None:
            continue
        smooth = _mask_smooth(layer, region)
        if rotation is not None:
            spring = _rotation_springs.setdefault(
                (layer_key, action_key, bone_name),
                QuaternionSpring(),
            )
            rotation = spring.step(rotation, smooth, delta_time)
        if location is not None:
            spring = _location_springs.setdefault(
                (layer_key, action_key, bone_name),
                VectorSpring(),
            )
            location = spring.step(location, smooth, delta_time)
        bones[bone_name] = {
            "region": region,
            "rotation": rotation,
            "location": location,
        }

    root_rotation = _sample_rotation(channels, armature)
    root_location = _sample_location(channels, armature)
    root = None
    if root_rotation is not None or root_location is not None:
        smooth = _mask_smooth(layer, "root")
        if root_rotation is not None:
            spring = _rotation_springs.setdefault(
                (layer_key, action_key, "__root__"), QuaternionSpring()
            )
            root_rotation = spring.step(root_rotation, smooth, delta_time)
        if root_location is not None:
            spring = _location_springs.setdefault(
                (layer_key, action_key, "__root__"), VectorSpring()
            )
            root_location = spring.step(root_location, smooth, delta_time)
        root = {
            "region": "root",
            "rotation": root_rotation,
            "location": root_location,
        }
    return {"bones": bones, "root": root}


def clear_layer(layer):
    layer_key = ensure_runtime_id(layer)
    if not layer_key:
        return
    for storage in (_rotation_springs, _location_springs):
        for key in tuple(storage):
            if key[0] == layer_key:
                storage.pop(key, None)


def clear_all():
    _rotation_springs.clear()
    _location_springs.clear()
