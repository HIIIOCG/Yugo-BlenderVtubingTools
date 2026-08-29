import re

import bpy


SOURCE_TYPES = (
    ("NONE", "None", "Select a source type for this empty Motion Layer"),
    ("VMC", "VMC Receiver", "Receive body and expression motion over VMC/OSC"),
    (
        "IFACIALMOCAP",
        "iFacial Mocap",
        "Receive head rotation and facial tracking directly from the mobile app",
    ),
    (
        "KEYBINDS",
        "Keybinds",
        "Drive VRM expressions with configurable keyboard shortcuts",
    ),
    (
        "SPRING_BONE",
        "VRM Physics",
        "Evaluate VRM Spring Bone physics after the final Motion Layer mix",
    ),
    ("BASE_ACTION", "Blender Action", "Use a Blender Action as a motion source"),
)

SOURCE_UNAVAILABLE_TYPES = (
    ("Rokoko", "Coming soon"),
    ("Leap Motion", "Coming soon"),
    ("MeddiaPipe", "Coming soon"),
    ("SteamVR", "Coming soon"),
    ("Mocopi", "Coming soon"),
)

SOURCE_TYPE_LABELS = {
    identifier: label
    for identifier, label, _description in SOURCE_TYPES
}

SOURCE_DEFAULT_COLORS = {
    "VMC": "ORANGE",
    "IFACIALMOCAP": "GREEN",
    "BASE_ACTION": "BLUE",
    "KEYBINDS": "PURPLE",
    "SPRING_BONE": "PINK",
}

SOURCE_DEFAULT_NAMES = {
    "VMC": "VMC Receiver",
    "IFACIALMOCAP": "iFacial Mocap",
    "KEYBINDS": "Keybinds",
    "SPRING_BONE": "VRM Physics",
    "BASE_ACTION": "Blender Action",
}

DEFAULT_LAYER_NAME_PATTERN = re.compile(r"^MotionLayer(?:[ _])(\d+)$")


def motion_layer_source_updated(layer, _context):
    try:
        if layer.source_type == "NONE":
            return
        if not layer.source_initialized:
            default_name = DEFAULT_LAYER_NAME_PATTERN.fullmatch(layer.name.strip())
            if default_name is not None:
                number = int(default_name.group(1))
                layer.name = (
                    f"{SOURCE_DEFAULT_NAMES.get(layer.source_type, 'MotionLayer')} "
                    f"{number:02d}"
                )
            if layer.color_tag == "GRAY":
                layer.color_tag = SOURCE_DEFAULT_COLORS.get(
                    layer.source_type, "GRAY"
                )
            if layer.source_type == "IFACIALMOCAP":
                layer.port = "49983"
            layer.source_initialized = True
    except (AttributeError, ReferenceError, TypeError, ValueError):
        pass

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

_keybind_expression_enum_cache = []


def keybind_expression_items(_self, context):
    from .keybinds.vrm import iter_expressions
    from .state import active_armature, find_avatar_for_keybind

    global _keybind_expression_enum_cache
    scene = getattr(context, "scene", None)
    avatar = find_avatar_for_keybind(scene, _self) if scene is not None else None
    armature = (
        getattr(avatar, "armature", None)
        if avatar is not None
        else active_armature(scene) if scene is not None else None
    )
    records = iter_expressions(armature)
    if records:
        _keybind_expression_enum_cache = [
            (record.identifier, record.name, f"Drive {record.name}")
            for record in records
        ]
    else:
        _keybind_expression_enum_cache = [
            ("NONE", "No bound expressions", "Select a VRM avatar with expressions")
        ]
    return _keybind_expression_enum_cache

IFACIAL_DATA_MODES = (
    ("RAW", "Raw Data", "Send unprocessed received data to the Motion Layer"),
    (
        "PREDICT",
        "Predict Data",
        "Prediction stage output; currently identical to Raw Data",
    ),
    (
        "SMOOTH",
        "Smooth Data",
        "Apply each mask's Spring-Damper Smooth to Predict Data",
    ),
)

VMC_DATA_MODES = (
    ("RAW", "Raw Data", "Send received VMC transforms directly to the Motion Layer"),
    (
        "SMOOTH",
        "Smooth Data",
        "Apply each Humanoid Mask's Spring-Damper Smooth directly to Raw Data",
    ),
)


def spring_show_hit_radius_updated(layer, context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    try:
        from .springbone import runtime

        runtime.rebuild_layer_visualization(scene, layer)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass

LAYER_COLOR_TAGS = (
    ("RED", "", "Red layer label", "STRIP_COLOR_01", 0),
    ("ORANGE", "", "Orange layer label", "STRIP_COLOR_02", 1),
    ("YELLOW", "", "Yellow layer label", "STRIP_COLOR_03", 2),
    ("GREEN", "", "Green layer label", "STRIP_COLOR_04", 3),
    ("BLUE", "", "Blue layer label", "STRIP_COLOR_05", 4),
    ("PURPLE", "", "Purple layer label", "STRIP_COLOR_06", 5),
    ("PINK", "", "Pink layer label", "STRIP_COLOR_07", 6),
    ("BROWN", "", "Brown layer label", "STRIP_COLOR_08", 7),
    ("GRAY", "", "Gray layer label", "STRIP_COLOR_09", 8),
)


class YUGO_PG_mask_part(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Enable",
        description="Allow this layer to affect this humanoid region",
        default=True,
    )
    weight: bpy.props.FloatProperty(
        name="Weight",
        description="Influence of this layer on this humanoid region",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    smooth: bpy.props.FloatProperty(
        name="Smooth",
        description="Spring-Damper smoothing for this layer's virtual bone value",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class YUGO_PG_blendshape_mask_item(bpy.types.PropertyGroup):
    expression_name: bpy.props.StringProperty(name="Expression", default="")
    normalized_name: bpy.props.StringProperty(default="", options={"HIDDEN"})
    is_arkit: bpy.props.BoolProperty(default=False, options={"HIDDEN"})
    raw_value: bpy.props.FloatProperty(
        name="Data Raw",
        description="Raw value received from the motion source",
        default=0.0,
        options={"SKIP_SAVE"},
        precision=3,
    )
    weight: bpy.props.FloatProperty(
        name="Weight",
        description="Multiplier applied before writing to the VRM expression preview",
        default=1.0,
        min=-5.0,
        max=5.0,
        precision=3,
    )
    smooth: bpy.props.FloatProperty(
        name="Smooth",
        description="Spring-Damper smoothing for this layer's virtual expression value",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    output_value: bpy.props.FloatProperty(
        name="Output",
        description="Value written to the VRM expression preview",
        default=0.0,
        options={"SKIP_SAVE"},
        precision=3,
    )


class YUGO_PG_keybind(bpy.types.PropertyGroup):
    key: bpy.props.EnumProperty(
        name="Key", items=KEYBIND_KEY_ITEMS, default="NONE"
    )
    use_shift: bpy.props.BoolProperty(name="Shift", default=False)
    use_ctrl: bpy.props.BoolProperty(name="Ctrl", default=False)
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("HOLD", "Hold", "Active while the shortcut is held"),
            ("TRIGGER", "Trigger", "Toggle active on each key press"),
            (
                "SOLO",
                "Solo",
                "Toggle this keybind and temporarily ignore all other keybinds",
            ),
        ),
        default="HOLD",
    )
    expression_id: bpy.props.EnumProperty(
        name="Blend Shape Proxy",
        description="Bound VRM expression driven by this shortcut",
        items=keybind_expression_items,
    )
    value: bpy.props.FloatProperty(
        name="Value",
        description="Maximum expression preview value",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    binary: bpy.props.BoolProperty(
        name="Binary",
        description="Switch instantly between off and on, ignoring Smooth",
        default=False,
    )
    smooth: bpy.props.FloatProperty(
        name="Smooth",
        description="Ease-in and ease-out time, from immediate to about one second",
        default=0.15,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    mask_exclusive: bpy.props.BoolProperty(
        name="Exclusive",
        description="While active, ignore all other keybinds in this layer",
        default=False,
    )
    mask_eyes: bpy.props.BoolProperty(
        name="Eyes",
        description="While active, lock other eye expressions to zero",
        default=False,
    )
    mask_mouth: bpy.props.BoolProperty(
        name="Mouth",
        description="While active, lock other mouth expressions to zero",
        default=False,
    )


def profile_factor(name, default):
    return bpy.props.FloatProperty(
        name=name,
        description="Rotation multiplier used by the iFacialMocap body profile",
        default=default,
        min=-5.0,
        max=5.0,
        precision=3,
    )


class YUGO_PG_ifacial_inverted_profile(bpy.types.PropertyGroup):
    head_pitch: profile_factor("Head Pitch", 0.7)
    head_yaw: profile_factor("Head Yaw", 1.0)
    head_roll: profile_factor("Head Roll", 0.4)
    neck_pitch: profile_factor("Neck Pitch", 0.6)
    neck_yaw: profile_factor("Neck Yaw", -0.4)
    neck_roll: profile_factor("Neck Roll", 0.5)
    chest_pitch: profile_factor("Chest Pitch", -0.3)
    chest_yaw: profile_factor("Chest Yaw", 0.35)
    chest_roll: profile_factor("Chest Roll", 0.2)
    spine_pitch: profile_factor("Spine Pitch", 0.85)
    spine_yaw: profile_factor("Spine Yaw", -0.5)
    spine_roll: profile_factor("Spine Roll", -0.5)


class YUGO_PG_ifacial_normal_profile(bpy.types.PropertyGroup):
    head_pitch: profile_factor("Head Pitch", 0.8)
    head_yaw: profile_factor("Head Yaw", 0.8)
    head_roll: profile_factor("Head Roll", 0.6)
    neck_pitch: profile_factor("Neck Pitch", 0.5)
    neck_yaw: profile_factor("Neck Yaw", 0.5)
    neck_roll: profile_factor("Neck Roll", 0.3)
    chest_pitch: profile_factor("Chest Pitch", 0.6)
    chest_yaw: profile_factor("Chest Yaw", 0.6)
    chest_roll: profile_factor("Chest Roll", 0.5)
    spine_pitch: profile_factor("Spine Pitch", 0.3)
    spine_yaw: profile_factor("Spine Yaw", 0.3)
    spine_roll: profile_factor("Spine Roll", 0.2)


class YUGO_PG_motion_layer(bpy.types.PropertyGroup):
    runtime_id: bpy.props.StringProperty(default="", options={"HIDDEN"})
    name: bpy.props.StringProperty(name="Layer Name", default="Motion Layer")
    enabled: bpy.props.BoolProperty(
        name="Enabled",
        description="Enable this motion layer",
        default=True,
    )
    solo: bpy.props.BoolProperty(
        name="Solo",
        description="Temporarily use only this motion layer",
        default=False,
        options={"HIDDEN"},
    )
    source_initialized: bpy.props.BoolProperty(
        default=False,
        options={"HIDDEN"},
    )
    source_type: bpy.props.EnumProperty(
        name="Source Type",
        description="Motion data source used by this layer",
        items=SOURCE_TYPES,
        default="NONE",
        update=motion_layer_source_updated,
    )
    blend_mode: bpy.props.EnumProperty(
        name="Blend Mode",
        description="How this layer combines with lower-priority layers",
        items=(("REPLACE", "Replace", "Replace motion from lower-priority layers"),),
        default="REPLACE",
    )
    weight: bpy.props.FloatProperty(
        name="Weight",
        description="Overall influence of this motion layer",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    color_tag: bpy.props.EnumProperty(
        name="Layer Label",
        description="Fixed label color used to identify this motion layer",
        items=LAYER_COLOR_TAGS,
        default="GRAY",
    )
    mask_expanded: bpy.props.BoolProperty(
        name="Show Humanoid Mask",
        description="Expand the humanoid mask controls",
        default=False,
        options={"HIDDEN"},
    )
    blendshape_mask_expanded: bpy.props.BoolProperty(
        name="Show BlendShape Mask",
        description="Expand the BlendShape Mask controls",
        default=False,
        options={"HIDDEN"},
    )
    blendshape_arkit_expanded: bpy.props.BoolProperty(
        name="Show ARKit Expressions",
        default=True,
        options={"HIDDEN"},
    )
    blendshape_other_expanded: bpy.props.BoolProperty(
        name="Show Shapes",
        default=True,
        options={"HIDDEN"},
    )
    ifacial_profiles_expanded: bpy.props.BoolProperty(
        name="Show Rotation Profiles",
        description="Edit iFacialMocap body rotation profile multipliers",
        default=False,
        options={"HIDDEN"},
    )
    blendshape_masks: bpy.props.CollectionProperty(type=YUGO_PG_blendshape_mask_item)
    keybinds: bpy.props.CollectionProperty(type=YUGO_PG_keybind)
    keybind_active_index: bpy.props.IntProperty(
        name="Active Keybind", default=-1, min=-1
    )
    ifacial_inverted_profile: bpy.props.PointerProperty(
        type=YUGO_PG_ifacial_inverted_profile
    )
    ifacial_normal_profile: bpy.props.PointerProperty(
        type=YUGO_PG_ifacial_normal_profile
    )

    address: bpy.props.StringProperty(
        name="Address",
        description="Local network address used to receive motion data",
        default="127.0.0.1",
    )
    port: bpy.props.StringProperty(
        name="Port",
        description="Network port used to receive motion data",
        default="39539",
        maxlen=5,
    )
    connected: bpy.props.BoolProperty(
        name="Connected",
        description="UI preview of this source's connection state",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    ifacial_mirror: bpy.props.BoolProperty(
        name="Mirror",
        description="Swap left and right for iFacialMocap head rotation",
        default=True,
    )
    ifacial_invert_body_rotation: bpy.props.BoolProperty(
        name="Invert BodyRotation",
        description="Use counter-rotation on the chest and spine",
        default=True,
    )
    ifacial_data_mode: bpy.props.EnumProperty(
        name="Output Data",
        description="Choose which iFacialMocap data stage feeds this Motion Layer",
        items=IFACIAL_DATA_MODES,
        default="RAW",
    )
    vmc_data_mode: bpy.props.EnumProperty(
        name="Output Data",
        description="Choose whether Raw or Smooth VMC data feeds this Motion Layer",
        items=VMC_DATA_MODES,
        default="RAW",
    )
    action: bpy.props.PointerProperty(
        name="Action",
        description="Blender Action used by this layer",
        type=bpy.types.Action,
    )
    spring_pause_during_playback: bpy.props.BoolProperty(
        name="Pause During Timeline Playback",
        description="Avoid running Yugo Spring Bone in addition to timeline playback",
        default=False,
    )
    spring_show_hit_radius: bpy.props.BoolProperty(
        name="Show Hit Radius",
        description="Display the configured Spring Bone collision radius in the viewport",
        default=False,
        update=spring_show_hit_radius_updated,
    )
    spring_physics_rate: bpy.props.EnumProperty(
        name="Physics Rate",
        description="Spring Bone solve frequency; Auto uses 24 FPS",
        items=(
            ("AUTO", "Auto (24 FPS)", "Use a 24 FPS solve rate with 60 Hz SLERP output"),
            ("FPS_60", "60 FPS", "Solve Spring Bone on every Yugo mixer update"),
            ("FPS_24", "24 FPS", "Solve at 24 FPS with 60 Hz SLERP output"),
            ("FPS_12", "12 FPS", "Solve at 12 FPS with 60 Hz SLERP output"),
        ),
        default="AUTO",
    )
    spring_mode: bpy.props.StringProperty(
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    spring_last_error: bpy.props.StringProperty(
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )

    root: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    head: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    neck: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    chest: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    spine: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    hips: bpy.props.PointerProperty(type=YUGO_PG_mask_part)

    right_leg: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    left_leg: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    right_shoulder: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    left_shoulder: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    right_arm: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    left_arm: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    right_hand: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    left_hand: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    right_finger: bpy.props.PointerProperty(type=YUGO_PG_mask_part)
    left_finger: bpy.props.PointerProperty(type=YUGO_PG_mask_part)


def armature_poll(_self, candidate):
    return candidate is not None and candidate.type == "ARMATURE"


def _sync_selected_armature(scene):
    avatars = scene.yugo_avatars
    index = scene.yugo_avatar_index
    armature = avatars[index].armature if 0 <= index < len(avatars) else None
    scene.yugo_vrm_armature = armature


def avatar_index_updated(scene, _context):
    _sync_selected_armature(scene)


def avatar_armature_updated(_avatar, context):
    scene = getattr(context, "scene", None)
    if scene is not None:
        _sync_selected_armature(scene)


def _copy_property_group(source, target):
    for prop in target.bl_rna.properties:
        identifier = prop.identifier
        if identifier == "rna_type":
            continue
        try:
            value = getattr(source, identifier)
        except (AttributeError, ReferenceError):
            continue
        try:
            if prop.type == "COLLECTION":
                target_collection = getattr(target, identifier)
                for source_item in value:
                    _copy_property_group(source_item, target_collection.add())
            elif prop.type == "POINTER" and not isinstance(value, bpy.types.ID):
                if value is not None:
                    _copy_property_group(value, getattr(target, identifier))
            else:
                if prop.is_readonly:
                    continue
                setattr(target, identifier, value)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue


def _migrate_legacy_scene_data():
    for scene in bpy.data.scenes:
        if len(scene.yugo_avatars) or (
            scene.yugo_vrm_armature is None and not len(scene.yugo_motion_layers)
        ):
            continue
        avatar = scene.yugo_avatars.add()
        avatar.armature = scene.yugo_vrm_armature
        for old_layer in scene.yugo_motion_layers:
            _copy_property_group(old_layer, avatar.motion_layers.add())
        avatar.motion_layer_index = min(
            scene.yugo_motion_layer_index, len(avatar.motion_layers) - 1
        )
        scene.yugo_avatar_index = 0
    return None


class YUGO_PG_avatar(bpy.types.PropertyGroup):
    runtime_id: bpy.props.StringProperty(default="", options={"HIDDEN"})
    armature: bpy.props.PointerProperty(
        name="VRM Armature",
        description="VRM avatar driven by this independent Motion Layer stack",
        type=bpy.types.Object,
        poll=armature_poll,
        update=avatar_armature_updated,
    )
    motion_layers: bpy.props.CollectionProperty(type=YUGO_PG_motion_layer)
    motion_layer_index: bpy.props.IntProperty(
        name="Active Motion Layer", default=-1, min=-1
    )


CLASSES = (
    YUGO_PG_mask_part,
    YUGO_PG_blendshape_mask_item,
    YUGO_PG_keybind,
    YUGO_PG_ifacial_inverted_profile,
    YUGO_PG_ifacial_normal_profile,
    YUGO_PG_motion_layer,
    YUGO_PG_avatar,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.yugo_vrm_armature = bpy.props.PointerProperty(
        name="VRM Armature",
        description="VRM avatar armature driven by Yugo",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    bpy.types.Scene.yugo_motion_layers = bpy.props.CollectionProperty(
        name="Motion Layers",
        type=YUGO_PG_motion_layer,
    )
    bpy.types.Scene.yugo_motion_layer_index = bpy.props.IntProperty(
        name="Active Motion Layer",
        default=-1,
        min=-1,
    )
    bpy.types.Scene.yugo_avatars = bpy.props.CollectionProperty(
        name="VRM Avatars", type=YUGO_PG_avatar
    )
    bpy.types.Scene.yugo_avatar_index = bpy.props.IntProperty(
        name="Active VRM Avatar",
        default=-1,
        min=-1,
        update=avatar_index_updated,
    )
    bpy.types.Scene.yugo_ui_language = bpy.props.EnumProperty(
        name="Language",
        description="Yugo interface language (UI preview only)",
        items=(
            ("SYSTEM", "System Default", "Follow Blender's language"),
            ("ENGLISH", "English", "English interface"),
            ("TRADITIONAL_CHINESE", "繁體中文", "Traditional Chinese interface"),
            ("JAPANESE", "日本語", "Japanese interface"),
        ),
        default="SYSTEM",
    )
    bpy.types.Scene.yugo_ui_preset = bpy.props.EnumProperty(
        name="Preset",
        description="Yugo interface preset (UI preview only)",
        items=(
            ("DEFAULT", "Default", "Default Yugo layout"),
            ("CUSTOM", "Custom", "Custom Yugo layout"),
        ),
        default="DEFAULT",
    )
    bpy.types.Scene.yugo_view_shading = bpy.props.EnumProperty(
        name="View Mode",
        description="Viewport shading used by No UI Window",
        items=(
            ("RENDERED", "Rendered", "Use rendered viewport shading"),
            ("MATERIAL", "Material Preview", "Use material preview shading"),
            ("SOLID", "Solid", "Use solid viewport shading"),
            ("WIREFRAME", "Wireframe", "Use wireframe viewport shading"),
        ),
        default="RENDERED",
    )
    if not bpy.app.timers.is_registered(_migrate_legacy_scene_data):
        bpy.app.timers.register(
            _migrate_legacy_scene_data, first_interval=0.0, persistent=False
        )


def unregister():
    try:
        if bpy.app.timers.is_registered(_migrate_legacy_scene_data):
            bpy.app.timers.unregister(_migrate_legacy_scene_data)
    except ValueError:
        pass
    del bpy.types.Scene.yugo_view_shading
    del bpy.types.Scene.yugo_ui_preset
    del bpy.types.Scene.yugo_ui_language
    del bpy.types.Scene.yugo_avatar_index
    del bpy.types.Scene.yugo_avatars
    del bpy.types.Scene.yugo_motion_layer_index
    del bpy.types.Scene.yugo_motion_layers
    del bpy.types.Scene.yugo_vrm_armature

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
