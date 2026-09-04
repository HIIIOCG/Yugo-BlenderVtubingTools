"""Central Replace mixer for all Yugo bone motion sources."""

from __future__ import annotations

import time

import bpy
from mathutils import Quaternion, Vector

from . import blender_action, scheduler
from .state import ensure_runtime_id, iter_avatars, layers_for_armature


APPLY_INTERVAL = scheduler.MOTION_INTERVAL
ROTATION_DOT_EPSILON = 1.0e-10
LOCATION_EPSILON_SQUARED = 1.0e-12
POST_APPLY_CALLBACKS_KEY = "yugo.motion_mixer.post_apply_callbacks"
MIXER_ACTIVE_KEY = "yugo.motion_mixer.active"

_live_poses = {}
_last_driven_bones = {}
_last_root_armatures = set()
_armatures = {}
_last_apply_time = 0.0
_undo_in_progress = False
_post_apply_registry = {}
_post_callback_error_times = {}


def _ensure_post_apply_registry():
    namespace = bpy.app.driver_namespace
    exposed = namespace.get(POST_APPLY_CALLBACKS_KEY)
    if isinstance(exposed, dict) and exposed is not _post_apply_registry:
        # Preserve callbacks installed through the legacy public namespace,
        # including the standalone Spring preview integration.
        _post_apply_registry.update(exposed)
    namespace[POST_APPLY_CALLBACKS_KEY] = _post_apply_registry
    namespace[MIXER_ACTIVE_KEY] = True


def _post_apply_callbacks(delta_time=APPLY_INTERVAL):
    for name, callback in tuple(_post_apply_registry.items()):
        if not callable(callback):
            _post_apply_registry.pop(name, None)
            continue
        try:
            callback(bpy.context, delta_time)
        except Exception as error:
            now = time.monotonic()
            if now - _post_callback_error_times.get(name, 0.0) >= 2.0:
                _post_callback_error_times[name] = now
                print(f'Yugo post-mix callback "{name}" will retry: {error}')


def submit_layer_pose(layer, armature, bones=None, root=None):
    try:
        layer_key = ensure_runtime_id(layer)
        armature_key = armature.as_pointer()
    except (AttributeError, ReferenceError):
        return
    if not layer_key:
        return
    _live_poses[layer_key] = {
        "armature_key": armature_key,
        "bones": dict(bones or {}),
        "root": root,
    }
    _armatures[armature_key] = armature


def clear_layer(layer):
    layer_key = ensure_runtime_id(layer)
    if not layer_key:
        return
    _live_poses.pop(layer_key, None)
    blender_action.clear_layer(layer)


def _layer_active(layers, layer):
    try:
        if not layer.enabled:
            return False
        any_solo = any(item.solo for item in layers)
        return not any_solo or layer.solo
    except ReferenceError:
        return False


def _influence(layer, region):
    try:
        mask = getattr(layer, region)
        if not mask.enabled:
            return 0.0
        return min(max(float(layer.weight) * float(mask.weight), 0.0), 1.0)
    except (AttributeError, ReferenceError):
        return 0.0


def _replace_rotation(current, target, influence):
    if target is None:
        return current
    target = target.copy()
    target.normalize()
    if current.dot(target) < 0.0:
        target.negate()
    result = current.slerp(target, influence)
    result.normalize()
    return result


def _replace_location(current, target, influence):
    return current if target is None else current.lerp(target, influence)


def _assign_quaternion(target, value) -> bool:
    value = value.copy()
    value.normalize()
    mode_changed = target.rotation_mode != "QUATERNION"
    if mode_changed:
        target.rotation_mode = "QUATERNION"
    current = target.rotation_quaternion.copy()
    current.normalize()
    if 1.0 - abs(current.dot(value)) <= ROTATION_DOT_EPSILON:
        return mode_changed
    target.rotation_quaternion = value
    return True


def _assign_location(target, value) -> bool:
    if (target.location - value).length_squared <= LOCATION_EPSILON_SQUARED:
        return False
    target.location = value
    return True


def _layer_pose(layer, armature, frame, delta_time):
    if layer.source_type == "BASE_ACTION":
        return blender_action.sample_layer(layer, armature, frame, delta_time)
    layer_key = ensure_runtime_id(layer)
    if not layer_key:
        return None
    live = _live_poses.get(layer_key)
    if live is None or live["armature_key"] != armature.as_pointer():
        return None
    return live


def apply_scene(scene, armature, delta_time=APPLY_INTERVAL):
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        return 0
    frame = float(scene.frame_current) + float(scene.frame_subframe)
    bone_values = {}
    root_rotation = Quaternion()
    root_location = Vector((0.0, 0.0, 0.0))
    has_root = False

    layers = layers_for_armature(scene, armature)
    for layer in reversed(tuple(layers)):
        if not _layer_active(layers, layer):
            continue
        pose = _layer_pose(layer, armature, frame, delta_time)
        if not pose:
            continue
        for bone_name, contribution in pose.get("bones", {}).items():
            region = contribution["region"]
            influence = _influence(layer, region)
            if influence <= 0.0:
                continue
            current = bone_values.setdefault(
                bone_name,
                {
                    "rotation": Quaternion(),
                    "location": Vector((0.0, 0.0, 0.0)),
                    "has_rotation": False,
                    "has_location": False,
                },
            )
            rotation = contribution.get("rotation")
            location = contribution.get("location")
            if rotation is not None:
                current["rotation"] = _replace_rotation(
                    current["rotation"], rotation, influence
                )
                current["has_rotation"] = True
            if location is not None:
                current["location"] = _replace_location(
                    current["location"], location, influence
                )
                current["has_location"] = True

        root = pose.get("root")
        if root:
            influence = _influence(layer, root.get("region", "root"))
            if influence > 0.0:
                if root.get("rotation") is not None:
                    root_rotation = _replace_rotation(
                        root_rotation, root["rotation"], influence
                    )
                    has_root = True
                if root.get("location") is not None:
                    root_location = _replace_location(
                        root_location, root["location"], influence
                    )
                    has_root = True

    armature_key = armature.as_pointer()
    _armatures[armature_key] = armature
    previously_driven = _last_driven_bones.get(armature_key, set())
    currently_driven = set(bone_values)
    changed = 0
    for bone_name in previously_driven | currently_driven:
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        value = bone_values.get(bone_name)
        changed += int(
            _assign_quaternion(
                pose_bone,
                value["rotation"]
                if value is not None and value["has_rotation"]
                else Quaternion(),
            )
        )
        changed += int(
            _assign_location(
                pose_bone,
                value["location"]
                if value is not None and value["has_location"]
                else Vector((0.0, 0.0, 0.0)),
            )
        )
    _last_driven_bones[armature_key] = currently_driven

    if has_root or armature_key in _last_root_armatures:
        changed += int(
            _assign_quaternion(
                armature,
                root_rotation if has_root else Quaternion(),
            )
        )
        changed += int(
            _assign_location(
                armature,
                root_location if has_root else Vector((0.0, 0.0, 0.0)),
            )
        )
    if has_root:
        _last_root_armatures.add(armature_key)
    else:
        _last_root_armatures.discard(armature_key)
    return changed


def _apply_timer():
    global _last_apply_time
    _ensure_post_apply_registry()
    if _undo_in_progress:
        return APPLY_INTERVAL
    now = time.monotonic()
    delta_time = (
        min(max(now - _last_apply_time, 0.0), 0.1)
        if _last_apply_time
        else APPLY_INTERVAL
    )
    _last_apply_time = now
    for scene in bpy.data.scenes:
        applied_armatures = set()
        for avatar in iter_avatars(scene):
            armature = getattr(avatar, "armature", None)
            if armature is None:
                continue
            try:
                armature_key = armature.as_pointer()
            except ReferenceError:
                continue
            if armature_key in applied_armatures:
                continue
            applied_armatures.add(armature_key)
            apply_scene(scene, armature, delta_time)
    # Spring Bone and other secondary motion must see the final mixed pose and
    # must not race Yugo with an unrelated application timer.
    _post_apply_callbacks(delta_time)
    return APPLY_INTERVAL


def _motion_update(_context, _delta_time):
    _apply_timer()


def prepare_undo():
    global _undo_in_progress
    _undo_in_progress = True
    _live_poses.clear()
    _armatures.clear()


def finish_undo():
    global _undo_in_progress, _last_apply_time
    _live_poses.clear()
    _last_driven_bones.clear()
    _last_root_armatures.clear()
    _armatures.clear()
    blender_action.clear_all()
    _last_apply_time = 0.0
    _undo_in_progress = False


def clear_all():
    global _last_apply_time, _undo_in_progress
    for armature_key, armature in tuple(_armatures.items()):
        try:
            if armature_key in _last_root_armatures:
                armature.rotation_mode = "QUATERNION"
                armature.rotation_quaternion = Quaternion()
                armature.location = Vector((0.0, 0.0, 0.0))
        except ReferenceError:
            continue
        for bone_name in _last_driven_bones.get(armature_key, set()):
            try:
                pose_bone = armature.pose.bones.get(bone_name)
                if pose_bone is not None:
                    pose_bone.rotation_mode = "QUATERNION"
                    pose_bone.rotation_quaternion = Quaternion()
                    pose_bone.location = Vector((0.0, 0.0, 0.0))
            except ReferenceError:
                break
    _live_poses.clear()
    _last_driven_bones.clear()
    _last_root_armatures.clear()
    _armatures.clear()
    blender_action.clear_all()
    _last_apply_time = 0.0
    _undo_in_progress = False


def discard_all():
    """Drop runtime references without touching RNA that Blender is replacing."""

    global _last_apply_time, _undo_in_progress
    _live_poses.clear()
    _last_driven_bones.clear()
    _last_root_armatures.clear()
    _armatures.clear()
    blender_action.clear_all()
    _last_apply_time = 0.0
    _undo_in_progress = False


def register():
    _post_apply_registry.clear()
    _post_callback_error_times.clear()
    _ensure_post_apply_registry()
    scheduler.add_motion("motion_mixer", _motion_update, priority=1000)


def unregister():
    scheduler.remove_motion("motion_mixer")
    bpy.app.driver_namespace.pop(MIXER_ACTIVE_KEY, None)
    bpy.app.driver_namespace.pop(POST_APPLY_CALLBACKS_KEY, None)
    _post_apply_registry.clear()
    _post_callback_error_times.clear()
    clear_all()
