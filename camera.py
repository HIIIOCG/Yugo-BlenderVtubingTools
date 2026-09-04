"""Integrated camera presets, dampers, target objects, and keybind controls."""

import math
import time
import uuid

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import scheduler


CAMERA_PRESETS = (
    ("NONE", "None", "Do not calculate this camera"),
    ("PAN_FOLLOW", "Pan Follow", "Keep position and pan toward the target"),
    (
        "AROUND_FOLLOW",
        "Around Follow",
        "Use Pan Follow settings and orbit with the target's world rotation",
    ),
    ("CUSTOM", "Custom", "Use the virtual camera location and rotation directly"),
)

KEYBIND_AFFECTS = (
    (
        "SWITCH_TARGET_SOLO",
        "Switch Target Solo",
        "Switch Solo to one Target Object",
    ),
    (
        "CHANGE_POSITION",
        "Change Position",
        "Move along the camera-view local X, Y, and Z axes",
    ),
)

KEYBIND_KEY_ITEMS = tuple(
    [("NONE", "", "Choose a keyboard key")]
    + [(letter, letter, f"Keyboard {letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    + [
        (identifier, label, f"Keyboard {label}")
        for identifier, label in (
            ("ZERO", "0"),
            ("ONE", "1"),
            ("TWO", "2"),
            ("THREE", "3"),
            ("FOUR", "4"),
            ("FIVE", "5"),
            ("SIX", "6"),
            ("SEVEN", "7"),
            ("EIGHT", "8"),
            ("NINE", "9"),
        )
    ]
    + [
        (f"F{number}", f"F{number}", f"Function key F{number}")
        for number in range(1, 13)
    ]
    + [
        ("SPACE", "Space", "Spacebar"),
        ("TAB", "Tab", "Tab"),
        ("RET", "Enter", "Enter or Return"),
        ("BACK_SPACE", "Backspace", "Backspace"),
        ("UP_ARROW", "Up Arrow", "Up Arrow"),
        ("DOWN_ARROW", "Down Arrow", "Down Arrow"),
        ("LEFT_ARROW", "Left Arrow", "Left Arrow"),
        ("RIGHT_ARROW", "Right Arrow", "Right Arrow"),
        ("PAGE_UP", "Page Up", "Page Up"),
        ("PAGE_DOWN", "Page Down", "Page Down"),
        ("SEMI_COLON", ";", "Semicolon"),
        ("COMMA", ",", "Comma"),
        ("PERIOD", ".", "Period"),
        ("SLASH", "/", "Slash"),
        ("LEFT_BRACKET", "[", "Left Bracket"),
        ("RIGHT_BRACKET", "]", "Right Bracket"),
        ("MINUS", "-", "Minus"),
        ("EQUAL", "=", "Equals"),
    ]
)

AXES = ("x", "y", "z")
EPSILON = 0.00001
MOVE_INPUT_SCALE = 0.1
MOVE_MAX_MULTIPLIER = 10.0
MOVE_RAMP_PER_SECOND = 9.0
MOVE_SAMPLE_RATE = 12.0
MOVE_SAMPLE_INTERVAL = 1.0 / MOVE_SAMPLE_RATE
MOVE_DECAY_PER_SECOND = MOVE_RAMP_PER_SECOND * 9.0
UI_SYNC_INTERVAL = scheduler.SYSTEM_INTERVAL
MOTION_INTERVAL = scheduler.MOTION_INTERVAL
TARGET_DEBUG_INTERVAL = 1.0 / 6.0
TARGET_SAMPLE_HALF_WINDOW = 10
TARGET_SAMPLE_WINDOW = (TARGET_SAMPLE_HALF_WINDOW * 2) + 1
RUNTIME_GENERATION_KEY = "yugo_cam.runtime_generation"
IDLE_UI_GENERATION_KEY = "yugo_cam.idle_ui_generation"


def _clamp(value, minimum=0.0, maximum=1.0):
    return min(max(float(value), minimum), maximum)


def _settling_time(smooth):
    smooth = _clamp(smooth)
    return 0.04 + (1.46 * smooth * smooth)


class ScalarSpring:
    """Stable critically damped scalar matching Yugo Humanoid Mask smoothing."""

    def __init__(self, value=0.0):
        self.reset(value)

    def reset(self, value):
        self.value = float(value)
        self.velocity = 0.0

    def step(self, target, smooth, delta_time):
        target = float(target)
        smooth = _clamp(smooth)
        if smooth <= 0.0 or delta_time <= 0.0:
            self.reset(target)
            return self.value
        delta_time = min(float(delta_time), 0.1)
        omega = 4.0 / _settling_time(smooth)
        offset = self.value - target
        coefficient = self.velocity + (omega * offset)
        decay = math.exp(-omega * delta_time)
        self.value = target + ((offset + coefficient * delta_time) * decay)
        self.velocity = (
            self.velocity - (omega * coefficient * delta_time)
        ) * decay
        return self.value


def active_character_armature(scene):
    """Resolve an active Yugo avatar when available, otherwise any armature."""
    avatars = getattr(scene, "yugo_avatars", None)
    index = int(getattr(scene, "yugo_avatar_index", -1))
    if avatars is not None and 0 <= index < len(avatars):
        armature = getattr(avatars[index], "armature", None)
        if armature is not None and armature.type == "ARMATURE":
            return armature
    legacy = getattr(scene, "yugo_vrm_armature", None)
    if legacy is not None and legacy.type == "ARMATURE":
        return legacy
    return next((obj for obj in scene.objects if obj.type == "ARMATURE"), None)


def camera_poll(_self, candidate):
    return candidate is not None and candidate.type == "CAMERA"


_property_sync_guard = False


def _entry_location(entry):
    return Vector((entry.location_x, entry.location_y, entry.location_z))


def _set_entry_location(entry, value):
    global _property_sync_guard
    _property_sync_guard = True
    try:
        for axis, component in zip(AXES, value):
            property_name = f"location_{axis}"
            component = float(component)
            if abs(float(getattr(entry, property_name)) - component) > EPSILON:
                setattr(entry, property_name, component)
    finally:
        _property_sync_guard = False


def _apply_entry_transform_immediately(entry):
    camera = entry.camera
    if camera is None:
        return
    current_matrix = camera.matrix_world.copy()
    camera.matrix_world = Matrix.LocRotScale(
        _entry_location(entry),
        current_matrix.to_quaternion(),
        current_matrix.to_scale(),
    )
    reset_entry_state(entry, sync_properties=False)


def cam_transform_updated(entry, _context):
    if not _property_sync_guard:
        _apply_entry_transform_immediately(entry)


def _assigned_bone_name(human_bone):
    node = getattr(human_bone, "node", None)
    return str(
        getattr(node, "bone_name", "")
        or getattr(node, "value", "")
        or ""
    )


def _vrm_head_bone_name(armature):
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return ""
    extension = getattr(getattr(armature, "data", None), "vrm_addon_extension", None)
    if extension is None:
        return ""

    version = str(getattr(extension, "spec_version", ""))
    if version.startswith("1"):
        humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
        human_bones = getattr(humanoid, "human_bones", None)
        bone_name = _assigned_bone_name(getattr(human_bones, "head", None))
    else:
        humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
        bone_name = ""
        for human_bone in getattr(humanoid, "human_bones", ()):
            if str(getattr(human_bone, "bone", "")).casefold() == "head":
                bone_name = _assigned_bone_name(human_bone)
                break

    pose_bones = getattr(getattr(armature, "pose", None), "bones", None)
    if pose_bones is None:
        return ""
    if bone_name and bone_name in pose_bones:
        return bone_name

    # Imported VRMs occasionally retain the standard bone name while their
    # metadata is incomplete. Keep this fallback deliberately limited to Head.
    for candidate in pose_bones.keys():
        normalized = "".join(char for char in candidate.casefold() if char.isalnum())
        if normalized == "head":
            return candidate
    return ""


def _first_vrm_head_target(scene):
    if scene is None:
        return None, ""

    candidates = []
    avatars = getattr(scene, "yugo_avatars", None)
    if avatars is not None:
        for avatar in avatars:
            armature = getattr(avatar, "armature", None)
            if armature is not None and armature not in candidates:
                candidates.append(armature)
    for obj in getattr(scene, "objects", ()):
        if getattr(obj, "type", None) == "ARMATURE" and obj not in candidates:
            candidates.append(obj)

    for armature in candidates:
        bone_name = _vrm_head_bone_name(armature)
        if bone_name:
            return armature, bone_name
    return None, ""


PAN_FOLLOW_KEYBINDS = (
    ("UP_ARROW", 0.0, 0.0, -5.0),
    ("DOWN_ARROW", 0.0, 0.0, 5.0),
    ("LEFT_ARROW", -5.0, 0.0, 0.0),
    ("RIGHT_ARROW", 5.0, 0.0, 0.0),
    ("PAGE_UP", 0.0, 3.0, 0.0),
    ("PAGE_DOWN", 0.0, -3.0, 0.0),
)


def _clear_entry_key_runtime(entry):
    state_key = _state_key(entry)
    for key in tuple(_binding_states):
        if key[0] == state_key:
            _binding_states.pop(key, None)
    for key in tuple(_pending_switches):
        if key[0] == state_key:
            _pending_switches.discard(key)


def _apply_follow_preset(entry, context, include_target_rotation=False):
    scene = getattr(entry, "id_data", None)
    if not isinstance(scene, bpy.types.Scene):
        scene = getattr(context, "scene", None)

    for axis in AXES:
        setattr(entry, f"location_smooth_{axis}", 0.7)
    entry.target_smooth = 0.4

    entry.target_objects.clear()
    target = entry.target_objects.add()
    armature, head_bone_name = _first_vrm_head_target(scene)
    if armature is not None:
        target.object = armature
        target.bone_name = head_bone_name
    target.enabled = True
    target.solo = False
    target.position_enabled = True
    target.rotation_enabled = bool(include_target_rotation)
    target.weight = 1.0
    entry.target_object_index = 0

    _clear_entry_key_runtime(entry)
    entry.keybinds.clear()
    for key, amount_x, amount_y, amount_z in PAN_FOLLOW_KEYBINDS:
        binding = entry.keybinds.add()
        binding.runtime_id = uuid.uuid4().hex
        binding.key = key
        binding.mode = "HOLD"
        binding.affects = "CHANGE_POSITION"
        binding.use_shift = False
        binding.use_ctrl = False
        binding.preserve_blender_key = False
        binding.amount_x = amount_x
        binding.amount_y = amount_y
        binding.amount_z = amount_z
    entry.keybind_index = 0


def preset_updated(entry, context):
    if _property_sync_guard:
        return
    if entry.preset == "PAN_FOLLOW":
        _apply_follow_preset(entry, context, include_target_rotation=False)
    elif entry.preset == "AROUND_FOLLOW":
        _apply_follow_preset(entry, context, include_target_rotation=True)
    invalidate_entry_state(entry)


def target_offset_updated(entry, context):
    camera = entry.camera
    if camera is None or camera.type != "CAMERA":
        return
    scene = getattr(context, "scene", None)
    aspect = 1.0
    if scene is not None and scene.render.resolution_y:
        width = scene.render.resolution_x * scene.render.pixel_aspect_x
        height = scene.render.resolution_y * scene.render.pixel_aspect_y
        aspect = max(width / height, EPSILON)
    camera.data.shift_x = float(entry.target_offset_x) - 0.5
    camera.data.shift_y = (float(entry.target_offset_y) - 0.5) / aspect


def _legacy_target_toggle(item, property_name, legacy_names, default):
    if property_name in item:
        return bool(item[property_name])
    legacy_values = [bool(item[name]) for name in legacy_names if name in item]
    return any(legacy_values) if legacy_values else default


def _target_position_enabled_get(item):
    return _legacy_target_toggle(
        item,
        "position_enabled",
        ("position_x", "position_y", "position_z"),
        True,
    )


def _target_position_enabled_set(item, value):
    item["position_enabled"] = bool(value)


def _target_rotation_enabled_get(item):
    return _legacy_target_toggle(
        item,
        "rotation_enabled",
        ("rotation_x", "rotation_y", "rotation_z"),
        False,
    )


def _target_rotation_enabled_set(item, value):
    item["rotation_enabled"] = bool(value)


class YUGO_CAM_PG_target_object(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)
    solo: bpy.props.BoolProperty(name="Solo", default=False, options={"HIDDEN"})
    object: bpy.props.PointerProperty(
        name="Object",
        description="Object or armature used as a camera target",
        type=bpy.types.Object,
    )
    bone_name: bpy.props.StringProperty(
        name="Bone",
        description="Optional armature bone; add more rows for multiple bones",
        default="",
    )
    position_enabled: bpy.props.BoolProperty(
        name="Position",
        description="Use this target's complete world position",
        get=_target_position_enabled_get,
        set=_target_position_enabled_set,
    )
    rotation_enabled: bpy.props.BoolProperty(
        name="Rotation",
        description="Use this target's complete world rotation to orbit the camera",
        get=_target_rotation_enabled_get,
        set=_target_rotation_enabled_set,
    )
    # Retained only so older .blend files can be converted by the getters above.
    position_x: bpy.props.BoolProperty(name="X", default=True, options={"HIDDEN"})
    position_y: bpy.props.BoolProperty(name="Y", default=True, options={"HIDDEN"})
    position_z: bpy.props.BoolProperty(name="Z", default=True, options={"HIDDEN"})
    rotation_x: bpy.props.BoolProperty(name="X", default=False, options={"HIDDEN"})
    rotation_y: bpy.props.BoolProperty(name="Y", default=False, options={"HIDDEN"})
    rotation_z: bpy.props.BoolProperty(name="Z", default=False, options={"HIDDEN"})
    weight: bpy.props.FloatProperty(
        name="Weight",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class YUGO_CAM_PG_keybind(bpy.types.PropertyGroup):
    runtime_id: bpy.props.StringProperty(default="", options={"HIDDEN"})
    key: bpy.props.EnumProperty(name="Key", items=KEYBIND_KEY_ITEMS, default="NONE")
    use_shift: bpy.props.BoolProperty(name="Shift", default=False)
    use_ctrl: bpy.props.BoolProperty(name="Ctrl", default=False)
    preserve_blender_key: bpy.props.BoolProperty(
        name="Keep Blender Key",
        description="Pass this shortcut through so Blender's original key still works",
        default=True,
    )
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            (
                "HOLD",
                "Hold",
                "Ramp movement from one to ten times speed while held, then decay on release",
            ),
            (
                "TRIGGER",
                "Trigger",
                "Toggle movement acceleration or decay on each press",
            ),
        ),
        default="HOLD",
    )
    affects: bpy.props.EnumProperty(
        name="Affects",
        items=KEYBIND_AFFECTS,
        default="CHANGE_POSITION",
    )
    target_slot: bpy.props.IntProperty(
        name="Switch Targets",
        description="One-based Target Object row to make Solo",
        default=1,
        min=1,
    )
    amount_x: bpy.props.FloatProperty(
        name="X",
        description="Camera-view X movement speed",
        default=0.0,
        precision=3,
    )
    amount_y: bpy.props.FloatProperty(
        name="Y",
        description="Camera-view Y movement speed",
        default=0.0,
        precision=3,
    )
    amount_z: bpy.props.FloatProperty(
        name="Z",
        description="Camera-view Z movement speed",
        default=0.0,
        precision=3,
    )


def _axis_value_property(label, update=cam_transform_updated, subtype="NONE"):
    return bpy.props.FloatProperty(
        name=label,
        default=0.0,
        precision=4,
        subtype=subtype,
        update=update,
    )


def _axis_smooth_property(label):
    return bpy.props.FloatProperty(
        name=label,
        description="Spring-Damper smoothing; 0 is immediate and 1 is slowest",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class YUGO_CAM_PG_entry(bpy.types.PropertyGroup):
    camera: bpy.props.PointerProperty(name="Camera", type=bpy.types.Object, poll=camera_poll)
    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Camera calculation preset",
        items=CAMERA_PRESETS,
        default="NONE",
        update=preset_updated,
    )

    location_x: _axis_value_property("X")
    location_y: _axis_value_property("Y")
    location_z: _axis_value_property("Z")
    location_smooth_x: _axis_smooth_property("Smooth X")
    location_smooth_y: _axis_smooth_property("Smooth Y")
    location_smooth_z: _axis_smooth_property("Smooth Z")

    # Legacy storage retained for older .blend files; no longer exposed or evaluated.
    rotation_x: bpy.props.FloatProperty(name="X", options={"HIDDEN"})
    rotation_y: bpy.props.FloatProperty(name="Y", options={"HIDDEN"})
    rotation_z: bpy.props.FloatProperty(name="Z", options={"HIDDEN"})

    target_objects_expanded: bpy.props.BoolProperty(
        name="Target Objects",
        default=False,
        options={"HIDDEN"},
    )
    target_objects: bpy.props.CollectionProperty(type=YUGO_CAM_PG_target_object)
    target_object_index: bpy.props.IntProperty(name="Active Target", default=-1, min=-1)
    target_offset_x: bpy.props.FloatProperty(
        name="X",
        description="Normalized horizontal framing shift; 0.5 is centered",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=target_offset_updated,
    )
    target_offset_y: bpy.props.FloatProperty(
        name="Y",
        description="Normalized vertical framing shift; 0.5 is centered",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=target_offset_updated,
    )
    target_smooth: bpy.props.FloatProperty(
        name="Target Smooth",
        description=(
            "Spring-Damper smoothing from the virtual camera position to the "
            "target-relative final position"
        ),
        default=0.15,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    debug_target_data: bpy.props.BoolProperty(
        name="Print Target Data (6 Hz)",
        description=(
            "Print raw Target Object/Bone positions, blended and smoothed target "
            "positions, source change count, and camera output to the console"
        ),
        default=False,
        options={"SKIP_SAVE"},
    )
    use_lock_distance: bpy.props.BoolProperty(
        name="Lock Distance",
        description="Keep the camera at the configured distance from its target",
        default=False,
    )
    lock_distance: bpy.props.FloatProperty(
        name="Distance",
        description="Distance kept between the camera and its target",
        default=5.0,
        min=0.01,
        soft_max=50.0,
        subtype="DISTANCE",
    )
    keybinds: bpy.props.CollectionProperty(type=YUGO_CAM_PG_keybind)
    keybind_index: bpy.props.IntProperty(name="Active Keybind", default=-1, min=-1)


class YUGO_CAM_PG_settings(bpy.types.PropertyGroup):
    listener_running: bpy.props.BoolProperty(
        name="Running", default=False, options={"HIDDEN", "SKIP_SAVE"}
    )
    last_error: bpy.props.StringProperty(
        default="", options={"HIDDEN", "SKIP_SAVE"}
    )


def _camera_world_transform(camera):
    matrix = camera.matrix_world.copy()
    return matrix.translation.copy(), matrix.to_quaternion().to_euler("XYZ"), matrix


def _initialize_entry_from_camera(entry, camera):
    location, _rotation, _matrix = _camera_world_transform(camera)
    entry.camera = camera
    _set_entry_location(entry, location)
    target_offset_updated(entry, bpy.context)


def sync_scene_cameras(scene):
    cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
    valid = {obj.as_pointer() for obj in cameras}
    entries = scene.yugo_cam_entries
    for index in range(len(entries) - 1, -1, -1):
        camera = entries[index].camera
        if camera is None or camera.as_pointer() not in valid:
            entries.remove(index)
    known = {entry.camera.as_pointer() for entry in entries if entry.camera is not None}
    for camera in cameras:
        if camera.as_pointer() not in known:
            _initialize_entry_from_camera(entries.add(), camera)
    active_index = min(scene.yugo_cam_index, len(entries) - 1)
    if entries and active_index < 0:
        active_index = 0
    if scene.yugo_cam_index != active_index:
        scene.yugo_cam_index = active_index


_ui_sync_times = {}
_camera_list_signatures = {}


def _available_scenes():
    return getattr(bpy.data, "scenes", ())


def _camera_list_signature(scene):
    return (
        len(scene.objects),
        len(getattr(bpy.data, "cameras", ())),
        len(scene.yugo_cam_entries),
    )


def maybe_sync_scene_cameras(scene, now=None, force=False):
    """Refresh camera-list/UI data at no more than two updates per second."""
    if scene is None or not hasattr(scene, "yugo_cam_entries"):
        return False
    # While running, reserve Blender's main thread for the 60 FPS motion path.
    # Explicit Refresh/Set Camera operations can still request a forced sync.
    if not force and _runtime_tracking_active(scene):
        return False
    if not force and _runtime_hold_motion_busy(scene):
        return False
    now = time.monotonic() if now is None else float(now)
    key = scene.as_pointer()
    if not force and now - _ui_sync_times.get(key, -UI_SYNC_INTERVAL) < UI_SYNC_INTERVAL:
        return False
    signature = _camera_list_signature(scene)
    if not force and _camera_list_signatures.get(key) == signature:
        _ui_sync_times[key] = now
        return False
    sync_scene_cameras(scene)
    _camera_list_signatures[key] = _camera_list_signature(scene)
    _ui_sync_times[key] = now
    return True


def active_cam_entry(scene):
    index = scene.yugo_cam_index
    if 0 <= index < len(scene.yugo_cam_entries):
        return scene.yugo_cam_entries[index]
    return None


def active_target(entry):
    index = entry.target_object_index
    if 0 <= index < len(entry.target_objects):
        return entry.target_objects[index]
    return None


def active_keybind(entry):
    index = entry.keybind_index
    if 0 <= index < len(entry.keybinds):
        return entry.keybinds[index]
    return None


def _target_matrix(item):
    obj = item.object
    if obj is None:
        return None
    if obj.type == "ARMATURE" and item.bone_name:
        pose_bone = obj.pose.bones.get(item.bone_name) if obj.pose else None
        if pose_bone is not None:
            return obj.matrix_world @ pose_bone.matrix
    return obj.matrix_world.copy()


def _selected_target_items(entry, forced_slots=()):
    items = [item for item in entry.target_objects if item.enabled and item.object]
    if forced_slots:
        forced = [
            entry.target_objects[index]
            for index in forced_slots
            if 0 <= index < len(entry.target_objects)
            and entry.target_objects[index].enabled
            and entry.target_objects[index].object
        ]
        if forced:
            return forced
    solos = [item for item in items if item.solo]
    return solos or items


def _rotation_follow_signature(entry, forced_slots=()):
    return tuple(
        (
            item.as_pointer(),
            item.object.as_pointer(),
            item.bone_name,
        )
        for item in _selected_target_items(entry, forced_slots)
        if item.rotation_enabled
    )


def _combined_target(scene, entry, forced_slots=()):
    items = _selected_target_items(entry, forced_slots)
    records = []
    for item in items:
        matrix = _target_matrix(item)
        weight = max(float(item.weight), 0.0)
        if matrix is not None and weight > EPSILON:
            records.append(
                (
                    item,
                    matrix.translation.copy(),
                    matrix.to_quaternion().normalized(),
                    weight,
                )
            )

    if not records:
        return None

    total = sum(record[3] for record in records)
    aim = Vector((0.0, 0.0, 0.0))
    for _item, position, _rotation, weight in records:
        aim += position * (weight / total)

    position_records = [
        (position, weight)
        for item, position, _rotation, weight in records
        if item.position_enabled
    ]
    target_position = aim.copy()
    if position_records:
        position_total = sum(weight for _position, weight in position_records)
        target_position = sum(
            (position * weight for position, weight in position_records),
            Vector((0.0, 0.0, 0.0)),
        ) / position_total

    rotation_records = [
        (position, rotation, weight)
        for item, position, rotation, weight in records
        if item.rotation_enabled
    ]
    target_rotation = Quaternion()
    rotation_target_position = aim.copy()
    if rotation_records:
        rotation_total = sum(
            weight for _position, _rotation, weight in rotation_records
        )
        rotation_target_position = sum(
            (
                position * weight
                for position, _rotation, weight in rotation_records
            ),
            Vector((0.0, 0.0, 0.0)),
        ) / rotation_total
        reference = rotation_records[0][1]
        components = [0.0, 0.0, 0.0, 0.0]
        for _position, rotation, weight in rotation_records:
            aligned = rotation.copy()
            if reference.dot(aligned) < 0.0:
                aligned.negate()
            for index, component in enumerate(aligned):
                components[index] += component * (weight / rotation_total)
        magnitude = math.sqrt(sum(component * component for component in components))
        if magnitude > EPSILON:
            target_rotation = Quaternion(
                tuple(component / magnitude for component in components)
            )
    position_mask = (bool(position_records),) * 3
    rotation_mask = (bool(rotation_records),) * 3
    return (
        target_position,
        position_mask,
        target_rotation,
        rotation_mask,
        rotation_target_position,
    )


class EntryState:
    def __init__(self, entry):
        location, rotation, _matrix = _camera_world_transform(entry.camera)
        self.virtual_location = _entry_location(entry)
        self.location_springs = [ScalarSpring(value) for value in location]
        self.target_follow_springs = [
            ScalarSpring(value) for value in self.virtual_location
        ]
        self.solo_target_springs = [ScalarSpring(0.0) for _axis in AXES]
        self.solo_target_position = None
        self.solo_transition_active = False
        self.target_position_samples = []
        self.target_sample_signature = None
        self.target_mean_position = None
        self.target_anchor = None
        self.camera_location_anchor = self.virtual_location.copy()
        self.rotation_follow_signature = None
        self.rotation_follow_offset = None
        self.rotation_follow_target_quaternion = None
        self.last_written_location = location.copy()
        self.last_written_quaternion = _matrix.to_quaternion()
        self.preset = entry.preset
        self.move_multipliers = {}
        self.move_sample_accumulators = {}
        self.debug_elapsed = 0.0
        self.debug_tick_count = 0
        self.debug_raw_change_count = 0
        self.debug_last_raw_target = None
        self.debug_manual_skip_count = 0
        self.debug_cooldown_skip_count = 0
        self.manual_until = 0.0

    def begin_solo_transition(self):
        if self.solo_target_position is None:
            return
        for spring, value in zip(
            self.solo_target_springs,
            self.solo_target_position,
        ):
            spring.reset(value)
        self.solo_transition_active = True

    def reset_anchors(self):
        self.target_anchor = None
        self.camera_location_anchor = self.virtual_location.copy()
        self.rotation_follow_signature = None
        self.rotation_follow_offset = None
        self.rotation_follow_target_quaternion = None
        for spring, value in zip(self.target_follow_springs, self.virtual_location):
            spring.reset(value)


_states = {}
_binding_states = {}
_pending_switches = set()
_target_debug_snapshots = {}
_owner = None
_motion_timer = None
_system_timer = None
_runtime_scene = None
_runtime_generation = 0
_idle_ui_timer = None
_idle_ui_generation = 0
_transform_active = False
_transform_camera = None
_viewport_navigating = False
_navigation_camera = None


def _state_key(entry):
    return entry.camera.as_pointer() if entry.camera is not None else 0


def invalidate_entry_state(entry):
    _states.pop(_state_key(entry), None)


def reset_entry_state(entry, sync_properties=True):
    camera = entry.camera
    if camera is None:
        return None
    location, rotation, _matrix = _camera_world_transform(camera)
    if sync_properties:
        _set_entry_location(entry, location)
    state = EntryState(entry)
    state.virtual_location = location.copy()
    state.location_springs = [ScalarSpring(value) for value in location]
    state.last_written_location = location.copy()
    state.last_written_quaternion = _matrix.to_quaternion()
    _states[_state_key(entry)] = state
    return state


def _entry_state(entry):
    key = _state_key(entry)
    state = _states.get(key)
    if state is None or state.preset != entry.preset:
        state = reset_entry_state(entry, sync_properties=False)
        state.preset = entry.preset
    return state


def _rotation_difference(a, b):
    return abs(a.rotation_difference(b).angle)


def _sync_manual_transform(entry, state, location, rotation, sync_location=True, sync_rotation=True):
    if sync_location:
        state.virtual_location = location.copy()
        for spring, value in zip(state.location_springs, location):
            spring.reset(value)
        state.last_written_location = location.copy()
    if sync_rotation:
        quaternion = Euler(Vector(rotation), "XYZ").to_quaternion()
        state.last_written_quaternion = quaternion.copy()
    state.reset_anchors()


def _binding_key(entry, binding, index):
    identifier = binding.runtime_id or str(index)
    return _state_key(entry), identifier


def _binding_active(entry, binding, index):
    return bool(_binding_states.get(_binding_key(entry, binding, index), False))


def _runtime_hold_motion_busy(scene):
    """Keep 2 FPS RNA/list work away from active or decaying Hold movement."""
    if scene is None or not hasattr(scene, "yugo_cam_entries"):
        return False
    for entry in scene.yugo_cam_entries:
        state = _states.get(_state_key(entry))
        for index, binding in enumerate(entry.keybinds):
            if binding.mode != "HOLD" or binding.affects == "SWITCH_TARGET_SOLO":
                continue
            key = _binding_key(entry, binding, index)
            if _binding_states.get(key, False):
                return True
            if state is not None and state.move_multipliers.get(key, 0.0) > EPSILON:
                return True
    return False


def _runtime_tracking_active(scene):
    return _owner is not None and _runtime_scene == scene


def _process_key_event(scene, event):
    if event.value not in {"PRESS", "RELEASE"}:
        return False, False
    matched = False
    consume = False
    for entry in scene.yugo_cam_entries:
        if entry.preset == "NONE" or entry.camera is None:
            continue
        for index, binding in enumerate(entry.keybinds):
            if binding.key == "NONE" or event.type != binding.key:
                continue
            if bool(event.shift) != bool(binding.use_shift):
                continue
            if bool(event.ctrl) != bool(binding.use_ctrl):
                continue
            key = _binding_key(entry, binding, index)
            if binding.affects == "SWITCH_TARGET_SOLO":
                if event.value == "PRESS" and not getattr(event, "is_repeat", False):
                    _pending_switches.add(key)
            elif binding.mode == "HOLD":
                _binding_states[key] = event.value == "PRESS"
            elif event.value == "PRESS" and not getattr(event, "is_repeat", False):
                _binding_states[key] = not _binding_states.get(key, False)
            matched = True
            consume = consume or not binding.preserve_blender_key
    return matched, consume


def _set_target_solo(entry, slot):
    target_index = int(slot) - 1
    if not 0 <= target_index < len(entry.target_objects):
        return False
    for index, target in enumerate(entry.target_objects):
        target.solo = index == target_index
    entry.target_object_index = target_index
    return True


def _apply_pending_target_switches(entry, state):
    for index, binding in enumerate(entry.keybinds):
        key = _binding_key(entry, binding, index)
        if key in _pending_switches:
            _pending_switches.discard(key)
            state.begin_solo_transition()
            if _set_target_solo(entry, binding.target_slot):
                state.reset_anchors()


def _apply_keybind_motion(entry, state, delta_time):
    delta_time = min(max(float(delta_time), 0.0), 0.1)
    changed = False
    valid_keys = set()
    for index, binding in enumerate(entry.keybinds):
        key = _binding_key(entry, binding, index)
        valid_keys.add(key)
        if binding.affects == "SWITCH_TARGET_SOLO":
            state.move_multipliers.pop(key, None)
            state.move_sample_accumulators.pop(key, None)
            continue

        multiplier = float(state.move_multipliers.get(key, 0.0))
        active = _binding_active(entry, binding, index)
        if active and multiplier <= EPSILON:
            # Start at normal speed immediately. Further acceleration is sampled
            # at 12 Hz while camera position itself remains integrated at 60 Hz.
            multiplier = 1.0
            state.move_sample_accumulators[key] = 0.0

        if active:
            sample_time = (
                float(state.move_sample_accumulators.get(key, 0.0)) + delta_time
            )
            sample_count = int(sample_time / MOVE_SAMPLE_INTERVAL)
            if sample_count:
                sample_time -= sample_count * MOVE_SAMPLE_INTERVAL
                sample_duration = sample_count * MOVE_SAMPLE_INTERVAL
                multiplier = min(
                    MOVE_MAX_MULTIPLIER,
                    multiplier + (MOVE_RAMP_PER_SECOND * sample_duration),
                )
            state.move_sample_accumulators[key] = sample_time
        else:
            # Decay every 60 FPS motion tick. Sampling this at 12 Hz creates
            # large velocity steps, especially with the stronger release decay.
            multiplier = max(
                0.0,
                multiplier - (MOVE_DECAY_PER_SECOND * delta_time),
            )
            state.move_sample_accumulators.pop(key, None)

        if multiplier <= EPSILON or delta_time <= 0.0:
            state.move_multipliers.pop(key, None)
            state.move_sample_accumulators.pop(key, None)
            continue

        state.move_multipliers[key] = multiplier
        local_distance = (
            Vector((binding.amount_x, binding.amount_y, binding.amount_z))
            * MOVE_INPUT_SCALE
            * multiplier
            * delta_time
        )
        world_distance = state.last_written_quaternion @ local_distance
        if (
            state.rotation_follow_offset is not None
            and state.rotation_follow_target_quaternion is not None
        ):
            state.rotation_follow_offset += (
                state.rotation_follow_target_quaternion.inverted()
                @ world_distance
            )
        else:
            state.virtual_location += world_distance
        changed = True

    for key in tuple(state.move_multipliers):
        if key not in valid_keys:
            state.move_multipliers.pop(key, None)
            state.move_sample_accumulators.pop(key, None)
    for key in tuple(state.move_sample_accumulators):
        if key not in valid_keys:
            state.move_sample_accumulators.pop(key, None)
    return changed


def _solo_transition_target(state, target, smooth, delta_time):
    target = Vector(target)
    if state.solo_target_position is None:
        for spring, value in zip(state.solo_target_springs, target):
            spring.reset(value)
        state.solo_target_position = target.copy()
        return target
    output = Vector(
        spring.step(value, smooth, delta_time)
        for spring, value in zip(state.solo_target_springs, target)
    )
    state.solo_target_position = output.copy()
    if (
        (output - target).length <= EPSILON
        and max(abs(spring.velocity) for spring in state.solo_target_springs)
        <= EPSILON
    ):
        state.solo_transition_active = False
    return output


def _position_sample_signature(entry):
    signature = []
    for item in _selected_target_items(entry):
        obj = item.object
        if obj is None or not item.position_enabled:
            continue
        signature.append(
            (
                obj.as_pointer(),
                str(item.bone_name),
                round(max(float(item.weight), 0.0), 6),
            )
        )
    return tuple(signature)


def _clear_target_position_samples(state, reset_virtual_target=False):
    state.target_position_samples.clear()
    state.target_sample_signature = None
    state.target_mean_position = None
    if reset_virtual_target:
        state.solo_target_position = None
        state.solo_transition_active = False


def _cache_target_position_sample(state, raw_target, signature):
    """Return the centered 10-before/10-after mean with ten ticks of latency."""
    if raw_target is None or not signature:
        _clear_target_position_samples(state, reset_virtual_target=True)
        return None

    raw_target = Vector(raw_target)
    if signature != state.target_sample_signature or not state.target_position_samples:
        # Prime the complete window with the first point. This gives a valid
        # target immediately; after twenty live samples the window is entirely
        # composed of measured 60 FPS data.
        state.target_position_samples = [
            raw_target.copy() for _index in range(TARGET_SAMPLE_WINDOW)
        ]
        state.target_sample_signature = signature
    else:
        state.target_position_samples.append(raw_target.copy())
        del state.target_position_samples[:-TARGET_SAMPLE_WINDOW]

    total = Vector((0.0, 0.0, 0.0))
    for position in state.target_position_samples:
        total += position
    state.target_mean_position = total / len(state.target_position_samples)
    return state.target_mean_position.copy()


def _format_debug_vector(value):
    if value is None:
        return "None"
    return "({:.6f}, {:.6f}, {:.6f})".format(*value)


def _print_target_debug(
    scene,
    entry,
    state,
    raw_target,
    mean_target,
    smooth_target,
    camera_location,
):
    frame = float(scene.frame_current) + float(scene.frame_subframe)
    summary = (
        "[YUGO_CAM_TARGET] "
        f'frame={frame:.4f} cam="{entry.camera.name}" preset={entry.preset} '
        f"ticks={state.debug_tick_count} raw_changes={state.debug_raw_change_count} "
        f"manual_skips={state.debug_manual_skip_count} "
        f"cooldown_skips={state.debug_cooldown_skip_count} "
        f"raw={_format_debug_vector(raw_target)} "
        f"mean21={_format_debug_vector(mean_target)} "
        f"smoothed={_format_debug_vector(smooth_target)} "
        f"camera={_format_debug_vector(camera_location)}"
    )
    console_lines = [summary]
    panel_lines = [
        f"Frame {frame:.4f} | Ticks {state.debug_tick_count} | Raw Changes {state.debug_raw_change_count}",
        f"Manual Skips {state.debug_manual_skip_count} | Cooldown Skips {state.debug_cooldown_skip_count}",
        f"Raw: {_format_debug_vector(raw_target)}",
        f"Mean 21: {_format_debug_vector(mean_target)}",
        f"Smoothed: {_format_debug_vector(smooth_target)}",
        f"Camera: {_format_debug_vector(camera_location)}",
    ]
    for index, item in enumerate(_selected_target_items(entry), start=1):
        matrix = _target_matrix(item)
        position = matrix.translation if matrix is not None else None
        object_name = item.object.name if item.object is not None else "None"
        target_name = (
            f"{object_name}:{item.bone_name}" if item.bone_name else object_name
        )
        row_line = (
            "[YUGO_CAM_TARGET_ROW] "
            f'index={index} target="{target_name}" '
            f"position={_format_debug_vector(position)} "
            f"P={int(item.position_enabled)} R={int(item.rotation_enabled)} "
            f"solo={int(item.solo)} weight={float(item.weight):.4f}"
        )
        console_lines.append(row_line)
        panel_lines.extend(
            (
                f'{index}. {target_name} | P {int(item.position_enabled)} R {int(item.rotation_enabled)}',
                f"   Pos: {_format_debug_vector(position)} | Weight {float(item.weight):.4f}",
            )
        )
    _target_debug_snapshots[_state_key(entry)] = {
        "console": tuple(console_lines),
        "panel": tuple(panel_lines),
    }
    for line in console_lines:
        print(line, flush=True)
    state.debug_elapsed = 0.0
    state.debug_tick_count = 0
    state.debug_raw_change_count = 0
    state.debug_manual_skip_count = 0
    state.debug_cooldown_skip_count = 0


def _update_target_debug(
    scene,
    entry,
    state,
    raw_target,
    mean_target,
    smooth_target,
    camera_location,
    delta_time,
):
    if not entry.debug_target_data:
        _target_debug_snapshots.pop(_state_key(entry), None)
        state.debug_elapsed = 0.0
        state.debug_tick_count = 0
        state.debug_raw_change_count = 0
        state.debug_last_raw_target = None
        state.debug_manual_skip_count = 0
        state.debug_cooldown_skip_count = 0
        return

    state.debug_elapsed += max(float(delta_time), 0.0)
    state.debug_tick_count += 1
    if raw_target is None:
        changed = state.debug_last_raw_target is not None
        state.debug_last_raw_target = None
    else:
        raw_target = Vector(raw_target)
        changed = (
            state.debug_last_raw_target is None
            or (raw_target - state.debug_last_raw_target).length > EPSILON
        )
        state.debug_last_raw_target = raw_target.copy()
    if changed:
        state.debug_raw_change_count += 1
    if state.debug_elapsed + EPSILON >= TARGET_DEBUG_INTERVAL:
        _print_target_debug(
            scene,
            entry,
            state,
            raw_target,
            mean_target,
            smooth_target,
            camera_location,
        )


def _apply_entry(scene, entry, delta_time, now):
    camera = entry.camera
    if camera is None:
        return
    state = _entry_state(entry)

    # Sample Target Object/Bone positions once on every 60 FPS runtime tick,
    # including ticks where a camera drag or its cooldown temporarily owns the
    # camera. Only the final virtual-target damper and Look At are deferred
    # until after camera location has been fully calculated.
    sample = None
    target = None
    position_mask = (False, False, False)
    target_rotation = Quaternion()
    rotation_mask = (False, False, False)
    rotation_target = None
    raw_track_target = None
    target_sample_signature = None
    mean_track_target = None
    if entry.preset != "NONE":
        _apply_pending_target_switches(entry, state)
        sample = _combined_target(scene, entry)
    if sample is not None:
        (
            target,
            position_mask,
            target_rotation,
            rotation_mask,
            rotation_target,
        ) = sample
        if any(position_mask):
            raw_track_target = target.copy()
            target_sample_signature = _position_sample_signature(entry)
        else:
            _clear_target_position_samples(state, reset_virtual_target=True)
    else:
        _clear_target_position_samples(state, reset_virtual_target=True)

    location, rotation, current_matrix = _camera_world_transform(camera)
    location_changed = (location - state.last_written_location).length > EPSILON
    current_quaternion = current_matrix.to_quaternion()
    rotation_changed = (
        _rotation_difference(current_quaternion, state.last_written_quaternion)
        > EPSILON
    )
    position_target_active = raw_track_target is not None

    manual_interaction = (
        (_transform_active and camera == _transform_camera)
        or (_viewport_navigating and camera == _navigation_camera)
    )
    # An enabled Position target owns Camera rotation through the final Look At.
    # Blender can slightly alter a written world matrix because of parenting,
    # scale decomposition, or constraints; treating that readback difference as
    # a manual edit caused a 0.2 second tracking pause on every false detection.
    external_rotation_changed = rotation_changed and not position_target_active
    if manual_interaction or location_changed or external_rotation_changed:
        if raw_track_target is not None:
            _cache_target_position_sample(
                state,
                raw_track_target,
                target_sample_signature,
            )
        if entry.debug_target_data:
            state.debug_manual_skip_count += 1
        _sync_manual_transform(
            entry,
            state,
            location,
            rotation,
            sync_location=manual_interaction or location_changed,
            sync_rotation=manual_interaction or external_rotation_changed,
        )
        state.manual_until = now + 0.2
        return

    if now < state.manual_until:
        if raw_track_target is not None:
            _cache_target_position_sample(
                state,
                raw_track_target,
                target_sample_signature,
            )
        if entry.debug_target_data:
            state.debug_cooldown_skip_count += 1
        return

    if entry.preset == "NONE":
        return

    rotation_follow_active = False
    target_quaternion = None
    track_target = None

    if sample is not None:
        rotation_signature = _rotation_follow_signature(entry)
        if rotation_signature != state.rotation_follow_signature:
            state.reset_anchors()
            state.rotation_follow_signature = rotation_signature

        if state.target_anchor is None:
            state.target_anchor = target.copy()
            state.camera_location_anchor = state.virtual_location.copy()

        rotation_follow_active = bool(rotation_signature) and any(rotation_mask)
        if rotation_follow_active:
            target_quaternion = target_rotation.copy()
            if state.rotation_follow_offset is None:
                # Capture the camera offset in the target's local rotation space.
                state.rotation_follow_offset = (
                    target_quaternion.inverted()
                    @ (state.virtual_location - rotation_target)
                )
                for spring, value in zip(
                    state.target_follow_springs,
                    state.virtual_location,
                ):
                    spring.reset(value)
            state.rotation_follow_target_quaternion = target_quaternion.copy()
        else:
            state.rotation_follow_offset = None
            state.rotation_follow_target_quaternion = None
    else:
        if state.rotation_follow_signature:
            state.reset_anchors()
        state.rotation_follow_signature = ()

    # Apply movement only after Rotation Follow has captured its local offset.
    _apply_keybind_motion(entry, state, delta_time)
    desired_location = state.virtual_location.copy()
    desired_quaternion = state.last_written_quaternion.copy()

    if sample is not None:
        if rotation_follow_active:
            final_facing_location = rotation_target + (
                target_quaternion @ state.rotation_follow_offset
            )
            state.virtual_location = Vector(
                spring.step(value, entry.target_smooth, delta_time)
                for spring, value in zip(
                    state.target_follow_springs,
                    final_facing_location,
                )
            )
            desired_location = state.virtual_location.copy()

        if entry.use_lock_distance:
            offset = desired_location - target
            if offset.length_squared <= EPSILON:
                offset = Vector((0.0, -1.0, 0.0))
            desired_location = target + offset.normalized() * float(entry.lock_distance)

    location_smooth = (
        entry.location_smooth_x,
        entry.location_smooth_y,
        entry.location_smooth_z,
    )
    output_location = Vector(
        spring.step(value, smooth, delta_time)
        for spring, value, smooth in zip(state.location_springs, desired_location, location_smooth)
    )

    # Target Position is deliberately the final motion stage. The virtual
    # target damper follows the 21-sample centered mean using Target Smooth,
    # then Camera rotation is forcibly rebuilt from the completed camera
    # location toward that virtual target.
    if raw_track_target is not None:
        mean_track_target = _cache_target_position_sample(
            state,
            raw_track_target,
            target_sample_signature,
        )
        track_target = _solo_transition_target(
            state,
            mean_track_target,
            entry.target_smooth,
            delta_time,
        )
        direction = track_target - output_location
        if direction.length_squared > EPSILON:
            desired_quaternion = direction.to_track_quat("-Z", "Y")
    output_quaternion = desired_quaternion

    _update_target_debug(
        scene,
        entry,
        state,
        raw_track_target,
        mean_track_target,
        track_target,
        output_location,
        delta_time,
    )

    wrote_matrix = False
    if (
        (output_location - location).length > EPSILON
        or _rotation_difference(output_quaternion, current_quaternion) > EPSILON
    ):
        camera.matrix_world = Matrix.LocRotScale(
            output_location,
            output_quaternion,
            current_matrix.to_scale(),
        )
        wrote_matrix = True
    if wrote_matrix:
        # Cache Blender's accepted matrix, not the requested decomposition.
        # Parent transforms and constraints can make these differ slightly.
        written_matrix = camera.matrix_world.copy()
        state.last_written_location = written_matrix.translation.copy()
        state.last_written_quaternion = written_matrix.to_quaternion()
    else:
        state.last_written_location = location.copy()
        state.last_written_quaternion = current_quaternion.copy()


def tick(scene, delta_time):
    now = time.monotonic()
    valid_keys = set()
    for entry in scene.yugo_cam_entries:
        if entry.camera is not None:
            valid_keys.add(_state_key(entry))
            _apply_entry(scene, entry, delta_time, now)
    for key in tuple(_states):
        if key not in valid_keys:
            _states.pop(key, None)


def _sync_running_cameras_from_blender(scene):
    for entry in scene.yugo_cam_entries:
        if entry.camera is not None:
            state = _entry_state(entry)
            location, rotation, _matrix = _camera_world_transform(entry.camera)
            _sync_manual_transform(entry, state, location, rotation)
            state.manual_until = 0.0


def _sync_camera_from_blender(scene, camera):
    if camera is None:
        return
    entry = next(
        (item for item in scene.yugo_cam_entries if item.camera == camera),
        None,
    )
    if entry is None:
        return
    state = _entry_state(entry)
    location, rotation, _matrix = _camera_world_transform(camera)
    _sync_manual_transform(entry, state, location, rotation)
    state.manual_until = 0.0


def _runtime_error(scene, error, generation):
    global _owner, _motion_timer, _system_timer, _runtime_scene
    if scene is not None and hasattr(scene, "yugo_cam_manager"):
        scene.yugo_cam_manager.last_error = str(error)
        scene.yugo_cam_manager.listener_running = False
    print(f"Yugo Cam runtime stopped: {error}")
    _owner = None
    _motion_timer = None
    _system_timer = None
    _runtime_scene = None
    _start_idle_ui_timer()


def _make_runtime_timer(interval, generation, callback, maximum_delta):
    last_time = time.monotonic() - interval
    next_deadline = last_time

    def timer():
        nonlocal last_time, next_deadline
        if (
            _owner is None
            or bpy.app.driver_namespace.get(RUNTIME_GENERATION_KEY) != generation
        ):
            return None
        scene = _runtime_scene
        if scene is None:
            return None
        now = time.monotonic()
        delta_time = min(max(now - last_time, 0.0), maximum_delta)
        if delta_time <= 0.0:
            delta_time = interval
        last_time = now
        try:
            callback(scene, delta_time)
            if scene.yugo_cam_manager.last_error:
                scene.yugo_cam_manager.last_error = ""
        except Exception as error:
            _runtime_error(scene, error, generation)
            return None

        # Keep a stable deadline. Callback execution time is not added to the
        # next interval, and missed cycles are skipped rather than replayed.
        next_deadline += interval
        after = time.monotonic()
        if next_deadline <= after:
            next_deadline = after + interval
        return max(0.001, next_deadline - after)

    return timer


def _motion_update(scene, delta_time):
    tick(scene, delta_time)


def _sync_runtime_ui(scene):
    """Publish runtime vectors to RNA properties only on the 2 Hz UI path."""
    if scene is None or not hasattr(scene, "yugo_cam_entries"):
        return
    for entry in scene.yugo_cam_entries:
        if entry.camera is None:
            continue
        state = _states.get(_state_key(entry))
        if state is None:
            continue
        if (_entry_location(entry) - state.virtual_location).length > EPSILON:
            _set_entry_location(entry, state.virtual_location)


def _system_update(scene, _delta_time):
    if _runtime_hold_motion_busy(scene):
        return
    maybe_sync_scene_cameras(scene)
    _sync_runtime_ui(scene)


def _scheduler_motion_update(_context, delta_time):
    if _owner is None or _runtime_scene is None:
        return
    scene = _runtime_scene
    try:
        _motion_update(scene, delta_time)
        if scene.yugo_cam_manager.last_error:
            scene.yugo_cam_manager.last_error = ""
    except Exception as error:
        _runtime_error(scene, error, _runtime_generation)


def _scheduler_system_update(_context, delta_time):
    # Match the standalone behavior: camera-list RNA publication is paused
    # while live tracking owns the 60 Hz path.
    if _owner is not None:
        return
    changed = False
    now = time.monotonic()
    for scene in _available_scenes():
        try:
            changed = maybe_sync_scene_cameras(scene, now) or changed
            _sync_runtime_ui(scene)
        except (AttributeError, ReferenceError, RuntimeError) as error:
            print(f"Yugo Cam UI sync skipped: {error}")
    if changed:
        _tag_cam_manager_redraw()


def _tag_cam_manager_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        screen = getattr(window, "screen", None)
        for area in getattr(screen, "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _stop_idle_ui_timer():
    global _idle_ui_timer, _idle_ui_generation
    _idle_ui_generation = int(
        bpy.app.driver_namespace.get(IDLE_UI_GENERATION_KEY, 0)
    ) + 1
    bpy.app.driver_namespace[IDLE_UI_GENERATION_KEY] = _idle_ui_generation
    timer = _idle_ui_timer
    _idle_ui_timer = None
    if timer is None:
        return
    try:
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        pass


def _start_idle_ui_timer():
    """Compatibility shim: the shared Yugo 2 Hz scheduler owns this work."""
    global _idle_ui_timer, _idle_ui_generation
    _stop_idle_ui_timer()
    _idle_ui_timer = None


def _start_runtime_timers(scene):
    global _motion_timer, _system_timer, _runtime_scene, _runtime_generation
    _stop_idle_ui_timer()
    _runtime_generation = int(
        bpy.app.driver_namespace.get(RUNTIME_GENERATION_KEY, 0)
    ) + 1
    bpy.app.driver_namespace[RUNTIME_GENERATION_KEY] = _runtime_generation
    _runtime_scene = scene
    # Motion and UI work are registered once with Yugo's central schedulers.
    _motion_timer = None
    _system_timer = None


def stop_listener(restart_idle=True, publish_ui=True):
    global _owner, _motion_timer, _system_timer, _runtime_scene
    global _runtime_generation, _transform_active, _transform_camera
    global _viewport_navigating, _navigation_camera
    owner = _owner
    if publish_ui:
        _sync_runtime_ui(_runtime_scene)
    _owner = None
    bpy.app.driver_namespace[RUNTIME_GENERATION_KEY] = _runtime_generation + 1
    _transform_active = False
    _transform_camera = None
    _viewport_navigating = False
    _navigation_camera = None
    for timer in (_motion_timer, _system_timer):
        if timer is None:
            continue
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except (AttributeError, ReferenceError, RuntimeError, ValueError):
            pass
    _motion_timer = None
    _system_timer = None
    _runtime_scene = None
    _states.clear()
    _binding_states.clear()
    _pending_switches.clear()
    if publish_ui:
        for scene in _available_scenes():
            if hasattr(scene, "yugo_cam_manager"):
                scene.yugo_cam_manager.listener_running = False
    if restart_idle:
        _start_idle_ui_timer()
    return owner


class YUGO_CAM_OT_set_scene_camera(bpy.types.Operator):
    bl_idname = "yugo_cam.set_scene_camera"
    bl_label = "Set Scene Camera"
    bl_description = "Use this camera as Blender's active scene camera"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        maybe_sync_scene_cameras(context.scene, force=True)
        if not 0 <= self.index < len(context.scene.yugo_cam_entries):
            return {"CANCELLED"}
        camera = context.scene.yugo_cam_entries[self.index].camera
        if camera is None:
            return {"CANCELLED"}
        context.scene.camera = camera
        context.scene.yugo_cam_index = self.index
        return {"FINISHED"}


class YUGO_CAM_OT_refresh(bpy.types.Operator):
    bl_idname = "yugo_cam.refresh"
    bl_label = "Refresh Cameras"

    def execute(self, context):
        maybe_sync_scene_cameras(context.scene, force=True)
        return {"FINISHED"}


class YUGO_CAM_OT_copy_target_debug(bpy.types.Operator):
    bl_idname = "yugo_cam.copy_target_debug"
    bl_label = "Copy Debug Snapshot"
    bl_description = "Copy the latest six-Hz Target diagnostic snapshot"

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        snapshot = (
            _target_debug_snapshots.get(_state_key(entry))
            if entry is not None and entry.camera is not None
            else None
        )
        if not snapshot:
            self.report({"WARNING"}, "No Target diagnostic sample is available yet")
            return {"CANCELLED"}
        context.window_manager.clipboard = "\n".join(snapshot["console"])
        self.report({"INFO"}, "Target diagnostic snapshot copied")
        return {"FINISHED"}


class YUGO_CAM_OT_target_add(bpy.types.Operator):
    bl_idname = "yugo_cam.target_add"
    bl_label = "Add Target Object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        if entry is None:
            return {"CANCELLED"}
        target = entry.target_objects.add()
        if context.active_object is not None and context.active_object != entry.camera:
            target.object = context.active_object
        entry.target_object_index = len(entry.target_objects) - 1
        invalidate_entry_state(entry)
        return {"FINISHED"}


class YUGO_CAM_OT_target_remove(bpy.types.Operator):
    bl_idname = "yugo_cam.target_remove"
    bl_label = "Remove Target Object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        target = active_target(entry) if entry is not None else None
        if target is None:
            return {"CANCELLED"}
        index = entry.target_object_index
        entry.target_objects.remove(index)
        entry.target_object_index = min(index, len(entry.target_objects) - 1)
        invalidate_entry_state(entry)
        return {"FINISHED"}


class YUGO_CAM_OT_target_move(bpy.types.Operator):
    bl_idname = "yugo_cam.target_move"
    bl_label = "Move Target Object"
    bl_options = {"REGISTER", "UNDO"}
    direction: bpy.props.EnumProperty(items=(("UP", "Up", "Move up"), ("DOWN", "Down", "Move down")))

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        if entry is None:
            return {"CANCELLED"}
        index = entry.target_object_index
        destination = index - 1 if self.direction == "UP" else index + 1
        if not 0 <= destination < len(entry.target_objects):
            return {"CANCELLED"}
        entry.target_objects.move(index, destination)
        entry.target_object_index = destination
        return {"FINISHED"}


class YUGO_CAM_OT_target_solo(bpy.types.Operator):
    bl_idname = "yugo_cam.target_solo"
    bl_label = "Solo Target Object"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        if entry is None or not 0 <= self.index < len(entry.target_objects):
            return {"CANCELLED"}
        state = _states.get(_state_key(entry))
        if state is not None:
            state.begin_solo_transition()
        enabled = not entry.target_objects[self.index].solo
        for index, target in enumerate(entry.target_objects):
            target.solo = enabled and index == self.index
        entry.target_object_index = self.index
        if state is not None:
            state.reset_anchors()
        return {"FINISHED"}


class YUGO_CAM_OT_keybind_add(bpy.types.Operator):
    bl_idname = "yugo_cam.keybind_add"
    bl_label = "Add Camera Keybind"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        if entry is None:
            return {"CANCELLED"}
        binding = entry.keybinds.add()
        binding.runtime_id = uuid.uuid4().hex
        entry.keybind_index = len(entry.keybinds) - 1
        return {"FINISHED"}


class YUGO_CAM_OT_keybind_remove(bpy.types.Operator):
    bl_idname = "yugo_cam.keybind_remove"
    bl_label = "Remove Camera Keybind"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        binding = active_keybind(entry) if entry is not None else None
        if binding is None:
            return {"CANCELLED"}
        index = entry.keybind_index
        entry.keybinds.remove(index)
        entry.keybind_index = min(index, len(entry.keybinds) - 1)
        return {"FINISHED"}


class YUGO_CAM_OT_keybind_move(bpy.types.Operator):
    bl_idname = "yugo_cam.keybind_move"
    bl_label = "Move Camera Keybind"
    bl_options = {"REGISTER", "UNDO"}
    direction: bpy.props.EnumProperty(items=(("UP", "Up", "Move up"), ("DOWN", "Down", "Move down")))

    def execute(self, context):
        entry = active_cam_entry(context.scene)
        if entry is None:
            return {"CANCELLED"}
        index = entry.keybind_index
        destination = index - 1 if self.direction == "UP" else index + 1
        if not 0 <= destination < len(entry.keybinds):
            return {"CANCELLED"}
        entry.keybinds.move(index, destination)
        entry.keybind_index = destination
        return {"FINISHED"}


class YUGO_CAM_OT_listener_toggle(bpy.types.Operator):
    bl_idname = "yugo_cam.listener_toggle"
    bl_label = "Start Cam Manager"
    bl_description = "Start or stop camera presets and keyboard controls"

    def execute(self, context):
        global _owner
        if _owner is not None:
            stop_listener()
            self.report({"INFO"}, "Cam Manager stopped")
            return {"FINISHED"}
        maybe_sync_scene_cameras(context.scene, force=True)
        if not any(entry.preset != "NONE" for entry in context.scene.yugo_cam_entries):
            self.report({"WARNING"}, "Set at least one camera Preset first")
            return {"CANCELLED"}
        _owner = self
        _start_runtime_timers(context.scene)
        context.window_manager.modal_handler_add(self)
        context.scene.yugo_cam_manager.listener_running = True
        context.scene.yugo_cam_manager.last_error = ""
        _sync_running_cameras_from_blender(context.scene)
        self.report({"INFO"}, "Cam Manager started")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _transform_active, _transform_camera
        global _viewport_navigating, _navigation_camera
        if _owner is not self:
            return {"CANCELLED"}
        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                _viewport_navigating = True
                space = getattr(context, "space_data", None)
                region_3d = getattr(space, "region_3d", None)
                _navigation_camera = (
                    context.scene.camera
                    if getattr(region_3d, "view_perspective", "") == "CAMERA"
                    else None
                )
            elif event.value == "RELEASE":
                _sync_camera_from_blender(context.scene, _navigation_camera)
                _viewport_navigating = False
                _navigation_camera = None
            return {"PASS_THROUGH"}
        if event.type in {
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "WHEELINMOUSE",
            "WHEELOUTMOUSE",
            "TRACKPADPAN",
            "TRACKPADZOOM",
            "MOUSEROTATE",
        } or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE":
            area = getattr(context, "area", None)
            region = getattr(context, "region", None)
            active_object = getattr(context, "active_object", None)
            if (
                event.value == "PRESS"
                and getattr(area, "type", "") == "VIEW_3D"
                and getattr(region, "type", "") == "WINDOW"
                and active_object is not None
                and active_object.type == "CAMERA"
            ):
                _transform_active = True
                _transform_camera = active_object
            elif event.value == "RELEASE":
                _sync_camera_from_blender(context.scene, _transform_camera)
                _transform_active = False
                _transform_camera = None
                _viewport_navigating = False
                _navigation_camera = None
        elif (
            event.type in {"G", "R"}
            and event.value == "PRESS"
            and context.active_object is not None
            and context.active_object.type == "CAMERA"
        ):
            _transform_active = True
            _transform_camera = context.active_object
        elif event.type in {"RET", "NUMPAD_ENTER", "ESC", "RIGHTMOUSE"}:
            if event.value == "PRESS" and _transform_active:
                _sync_camera_from_blender(context.scene, _transform_camera)
                _transform_active = False
                _transform_camera = None
            if event.value == "PRESS":
                _viewport_navigating = False
                _navigation_camera = None
        matched, consume = _process_key_event(context.scene, event)
        if matched and consume:
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, _context):
        if _owner is self:
            stop_listener()


class YUGO_CAM_UL_cameras(bpy.types.UIList):
    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_prop, index=0, _flt=0):
        row = layout.row(align=True)
        camera = item.camera
        if camera is not None:
            content = row.split(factor=0.55, align=True)
            content.prop(
                camera,
                "name",
                text="",
                icon="OUTLINER_OB_CAMERA",
                emboss=False,
            )
            focal = content.row(align=True)
            if camera.data is not None:
                focal.prop(camera.data, "lens", text="Focal")
        else:
            row.label(text="Missing Camera", icon="ERROR")
        if camera is not None and context.scene.camera == camera:
            row.label(text="Active", icon="RADIOBUT_ON")
        else:
            button = row.operator("yugo_cam.set_scene_camera", text="", icon="RADIOBUT_OFF", emboss=False)
            button.index = index


class YUGO_CAM_UL_targets(bpy.types.UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop, index=0, _flt=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="", icon="HIDE_OFF" if item.enabled else "HIDE_ON", emboss=False)
        solo = row.operator("yugo_cam.target_solo", text="", icon="LAYER_ACTIVE" if item.solo else "LAYER_USED", emboss=False)
        solo.index = index
        content = row.split(factor=0.304, align=True)
        object_cell = content.row(align=True)
        object_cell.prop(item, "object", text="")
        remaining = content.row(align=True)
        position_split = remaining.split(factor=1.0 / 3.0, align=True)
        position = position_split.row(align=True)
        position.alignment = "CENTER"
        position.prop(item, "position_enabled", text="")
        rotation_and_weight = position_split.row(align=True)
        rotation_split = rotation_and_weight.split(factor=0.50, align=True)
        rotation = rotation_split.row(align=True)
        rotation.alignment = "CENTER"
        rotation.prop(item, "rotation_enabled", text="")
        rotation_split.prop(item, "weight", text="", slider=True)


class YUGO_CAM_UL_keybinds(bpy.types.UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop, index=0, _flt=0):
        row = layout.row(align=True)
        row.label(text=f"Keybind_{index + 1:03d}")
        row.prop(item, "key", text="")
        row.prop(item, "mode", text="")
        row.prop(item, "affects", text="")


def _draw_axis_damper(layout, entry, label, value_property, smooth_property):
    row = layout.row(align=True)
    row.label(text=label)
    row.prop(entry, value_property, text="")
    row.prop(entry, smooth_property, text="Smooth", slider=True)


class YUGO_CAM_PT_manager(bpy.types.Panel):
    bl_idname = "YUGO_CAM_PT_manager"
    bl_label = ""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, _context):
        self.layout.label(text="Cam Manager", icon="CAMERA_DATA")

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        running = scene.yugo_cam_manager.listener_running
        start = layout.row()
        start.scale_y = 1.25
        start.operator(
            "yugo_cam.listener_toggle",
            text="Stop" if running else "Start",
            icon="PAUSE" if running else "PLAY",
        )

        camera_row = layout.row()
        camera_row.template_list(
            "YUGO_CAM_UL_cameras", "", scene, "yugo_cam_entries", scene, "yugo_cam_index", rows=3
        )
        camera_row.column(align=True).operator("yugo_cam.refresh", text="", icon="FILE_REFRESH")
        if not scene.yugo_cam_entries:
            has_camera = any(obj.type == "CAMERA" for obj in scene.objects)
            layout.label(
                text=(
                    "Camera list is updating..."
                    if has_camera
                    else "Add a camera to this scene to begin"
                ),
                icon="INFO",
            )
            return
        entry = active_cam_entry(scene)
        if entry is None or entry.camera is None:
            return

        layout.separator()
        layout.label(text="Additional Properties")
        layout.prop(entry, "preset", text="Preset")

        settings = layout.box()
        settings.label(text="Cam Location")
        for axis in AXES:
            _draw_axis_damper(
                settings,
                entry,
                axis.upper(),
                f"location_{axis}",
                f"location_smooth_{axis}",
            )

        targets = layout.box()
        target_header = targets.row(align=True)
        target_header.prop(
            entry,
            "target_objects_expanded",
            text="",
            icon="TRIA_DOWN" if entry.target_objects_expanded else "TRIA_RIGHT",
            emboss=False,
        )
        target_header.label(text="Target Objects")
        if entry.target_objects_expanded:
            columns = targets.row(align=True)
            columns.label(text="")
            columns.label(text="")
            header_content = columns.split(factor=0.304, align=True)
            header_content.label(text="Name")
            header_remaining = header_content.row(align=True)
            header_position = header_remaining.split(factor=1.0 / 3.0, align=True)
            header_position.label(text="Position")
            header_rotation_weight = header_position.row(align=True)
            header_rotation = header_rotation_weight.split(factor=0.50, align=True)
            header_rotation.label(text="Rotation")
            header_rotation.label(text="Weight")
            target_row = targets.row()
            target_row.template_list(
                "YUGO_CAM_UL_targets",
                "",
                entry,
                "target_objects",
                entry,
                "target_object_index",
                rows=3,
            )
            target_controls = target_row.column(align=True)
            target_controls.operator("yugo_cam.target_add", text="", icon="ADD")
            target_controls.operator("yugo_cam.target_remove", text="", icon="REMOVE")
            target_controls.separator()
            up = target_controls.operator("yugo_cam.target_move", text="", icon="TRIA_UP")
            up.direction = "UP"
            down = target_controls.operator("yugo_cam.target_move", text="", icon="TRIA_DOWN")
            down.direction = "DOWN"
            target = active_target(entry)
            if target is not None and target.object is not None and target.object.type == "ARMATURE":
                targets.prop_search(target, "bone_name", target.object.data, "bones", text="Bone")

        offset = layout.row(align=True)
        offset.label(text="Target Offset XY")
        offset.prop(entry, "target_offset_x", text="X", slider=True)
        offset.prop(entry, "target_offset_y", text="Y", slider=True)
        layout.prop(entry, "target_smooth", text="Target Smooth", slider=True)
        layout.prop(entry, "debug_target_data")
        if entry.debug_target_data:
            debug_box = layout.box()
            debug_box.label(text="Target Debug (6 Hz)", icon="CONSOLE")
            snapshot = _target_debug_snapshots.get(_state_key(entry))
            if snapshot:
                for line in snapshot["panel"]:
                    debug_box.label(text=line)
                debug_box.operator("yugo_cam.copy_target_debug", icon="COPYDOWN")
            else:
                debug_box.label(text="Waiting for the next sample...", icon="TIME")
        lock = layout.row(align=True)
        lock.prop(entry, "use_lock_distance", text="Lock Distance")
        distance = lock.row(align=True)
        distance.enabled = entry.use_lock_distance
        distance.prop(entry, "lock_distance", text="")

        key_box = layout.box()
        key_box.label(text="Keybinds")
        columns = key_box.row(align=True)
        columns.label(text="Name")
        columns.label(text="Key")
        columns.label(text="Mode")
        columns.label(text="Affects")
        key_row = key_box.row()
        key_row.template_list(
            "YUGO_CAM_UL_keybinds", "", entry, "keybinds", entry, "keybind_index", rows=3
        )
        key_controls = key_row.column(align=True)
        key_controls.operator("yugo_cam.keybind_add", text="", icon="ADD")
        key_controls.operator("yugo_cam.keybind_remove", text="", icon="REMOVE")
        key_controls.separator()
        up = key_controls.operator("yugo_cam.keybind_move", text="", icon="TRIA_UP")
        up.direction = "UP"
        down = key_controls.operator("yugo_cam.keybind_move", text="", icon="TRIA_DOWN")
        down.direction = "DOWN"

        binding = active_keybind(entry)
        if binding is not None:
            modifiers = key_box.row(align=True)
            modifiers.prop(binding, "use_shift", toggle=True)
            modifiers.prop(binding, "use_ctrl", toggle=True)
            modifiers.prop(binding, "preserve_blender_key")
            if binding.affects == "SWITCH_TARGET_SOLO":
                key_box.prop(binding, "target_slot", text="Switch Targets")
            else:
                amount = key_box.row(align=True)
                amount.label(text="Move Amount")
                amount.prop(binding, "amount_x", text="X")
                amount.prop(binding, "amount_y", text="Y")
                amount.prop(binding, "amount_z", text="Z")

        if scene.yugo_cam_manager.last_error:
            warning = layout.row()
            warning.alert = True
            warning.label(text=scene.yugo_cam_manager.last_error, icon="ERROR")


CLASSES = (
    YUGO_CAM_PG_target_object,
    YUGO_CAM_PG_keybind,
    YUGO_CAM_PG_entry,
    YUGO_CAM_PG_settings,
    YUGO_CAM_OT_set_scene_camera,
    YUGO_CAM_OT_refresh,
    YUGO_CAM_OT_copy_target_debug,
    YUGO_CAM_OT_target_add,
    YUGO_CAM_OT_target_remove,
    YUGO_CAM_OT_target_move,
    YUGO_CAM_OT_target_solo,
    YUGO_CAM_OT_keybind_add,
    YUGO_CAM_OT_keybind_remove,
    YUGO_CAM_OT_keybind_move,
    YUGO_CAM_OT_listener_toggle,
    YUGO_CAM_UL_cameras,
    YUGO_CAM_UL_targets,
    YUGO_CAM_UL_keybinds,
    YUGO_CAM_PT_manager,
)


SCENE_PROPERTY_NAMES = (
    "yugo_cam_manager",
    "yugo_cam_index",
    "yugo_cam_entries",
)


def _remove_cam_handlers():
    # Remove handlers left by versions older than 0.7.0. Current versions use
    # only one 60 FPS Blender app timer plus modal input events while running.
    handler_specs = (
        (bpy.app.handlers.load_post, "_cam_load_post"),
        (bpy.app.handlers.depsgraph_update_post, "_cam_depsgraph_update_post"),
    )
    for handlers, function_name in handler_specs:
        for handler in tuple(handlers):
            module_name = str(getattr(handler, "__module__", ""))
            if (
                getattr(handler, "__name__", "") == function_name
                and module_name.endswith("yugo_cam.addon")
            ):
                handlers.remove(handler)


def _stop_legacy_extension_runtime():
    """Stop timers from the former standalone yugo_cam Extension."""

    import sys

    legacy = sys.modules.get("yugo_cam.addon")
    if legacy is None or legacy is sys.modules.get(__name__):
        return
    try:
        legacy._stop_idle_ui_timer()
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        pass
    try:
        legacy.stop_listener(restart_idle=False)
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        pass
    print(
        "Yugo: stopped the standalone Yugo Cam runtime; disable or uninstall "
        "the old yugo_cam Extension after installing the integrated version"
    )


def _remove_scene_properties():
    for property_name in SCENE_PROPERTY_NAMES:
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)


def _unregister_cam_classes():
    """Remove current or stale same-name classes after an Extension reload."""
    for cls in reversed(CLASSES):
        registered = cls if getattr(cls, "is_registered", False) else None
        if registered is None:
            for base_type in (
                bpy.types.PropertyGroup,
                bpy.types.Operator,
                bpy.types.UIList,
                bpy.types.Panel,
            ):
                registered = base_type.bl_rna_get_subclass_py(cls.__name__, None)
                if registered is not None:
                    break
        if registered is None:
            continue
        try:
            bpy.utils.unregister_class(registered)
        except (RuntimeError, TypeError, ValueError):
            pass


def register():
    # Blender can reload an updated Extension without clearing its old Python
    # subclasses first. Clean only Yugo Cam's exact names so registration is
    # idempotent and never collides with the main Yugo Extension.
    _stop_legacy_extension_runtime()
    stop_listener(restart_idle=False)
    _stop_idle_ui_timer()
    _remove_cam_handlers()
    _ui_sync_times.clear()
    _camera_list_signatures.clear()
    _target_debug_snapshots.clear()
    _remove_scene_properties()
    _unregister_cam_classes()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.yugo_cam_entries = bpy.props.CollectionProperty(type=YUGO_CAM_PG_entry)
    bpy.types.Scene.yugo_cam_index = bpy.props.IntProperty(name="Active Camera", default=-1, min=-1)
    bpy.types.Scene.yugo_cam_manager = bpy.props.PointerProperty(type=YUGO_CAM_PG_settings)
    scheduler.add_motion("camera", _scheduler_motion_update, priority=1200)
    scheduler.add_system("camera", _scheduler_system_update, priority=300)


def unregister():
    scheduler.remove_motion("camera")
    scheduler.remove_system("camera")
    _stop_idle_ui_timer()
    stop_listener(restart_idle=False)
    _remove_cam_handlers()
    _ui_sync_times.clear()
    _remove_scene_properties()
    _unregister_cam_classes()
