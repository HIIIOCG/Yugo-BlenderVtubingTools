import bpy
import textwrap

from .avatar_status import (
    get_arkit_bind_status,
    get_missing_humanoid_bones,
    get_vrm_version,
)
from .ifacialmocap import runtime as ifacialmocap_runtime
from .keybinds import runtime as keybinds_runtime
from .operators import active_keybind, active_layer
from .properties import SOURCE_TYPES, SOURCE_TYPE_LABELS, SOURCE_UNAVAILABLE_TYPES
from .state import active_armature, active_avatar
from .springbone import runtime as springbone_runtime
from .springbone.vrm import get_vrm_extension, is_vrm0, is_vrm1, spring_bone_stats
from .vrm import get_avatar_name, get_avatar_thumbnail, is_vrm_armature
from .vmc import runtime as vmc_runtime


CENTRAL_MASK_PARTS = (
    ("Root", "root"),
    ("Head", "head"),
    ("Neck", "neck"),
    ("Chest", "chest"),
    ("Spine", "spine"),
    ("Hips", "hips"),
)

BILATERAL_MASK_PARTS = (
    ("Leg", "right_leg", "left_leg"),
    ("Shoulder", "right_shoulder", "left_shoulder"),
    ("Arm", "right_arm", "left_arm"),
    ("Hand", "right_hand", "left_hand"),
    ("Finger", "right_finger", "left_finger"),
)

PROFILE_REGIONS = (
    ("Head", "head"),
    ("Neck", "neck"),
    ("Chest", "chest"),
    ("Spine", "spine"),
)


class YUGO_MT_source_type(bpy.types.Menu):
    bl_idname = "YUGO_MT_source_type"
    bl_label = "Source Type"

    def draw(self, context):
        layout = self.layout
        layer = active_layer(context.scene)
        current = str(layer.source_type) if layer is not None else "NONE"

        for identifier, label, description in SOURCE_TYPES:
            item = layout.operator(
                "yugo.source_type_set",
                text=label,
                icon="RADIOBUT_ON" if identifier == current else "BLANK1",
            )
            item.source_type = identifier

        for label, description in SOURCE_UNAVAILABLE_TYPES:
            item = layout.row()
            item.enabled = False
            item.label(text=label, icon="BLANK1")


def content_padding(region_width):
    width_mix = min(max((region_width - 300) / 180, 0.0), 1.0)
    return 0.05 + (0.13 * width_mix)


def indented_column(layout, region_width):
    row = layout.row()
    split = row.split(factor=content_padding(region_width))
    split.separator()
    return split.column()


def draw_source_action_button(layout, operator_id, text, icon):
    button_row = layout.row()
    button_split = button_row.split(factor=0.40)
    button_split.separator()
    button = button_split.row()
    button.scale_y = 1.35
    return button.operator(operator_id, text=text, icon=icon)


def draw_mask_control(layout, mask_part, smooth_enabled=True):
    row = layout.row(align=True)
    row.prop(mask_part, "enabled", text="")
    weight = row.row(align=True)
    weight.enabled = mask_part.enabled
    weight.prop(mask_part, "weight", text="", slider=True)
    smooth = row.row(align=True)
    smooth.enabled = mask_part.enabled and smooth_enabled
    smooth.prop(mask_part, "smooth", text="", slider=True)


def draw_rotation_profile_table(layout, title, profile):
    layout.label(text=title)
    header = layout.row(align=True)
    header_split = header.split(factor=0.28)
    header_split.label(text="")
    columns = header_split.row(align=True)
    columns.label(text="Pitch")
    columns.label(text="Yaw")
    columns.label(text="Roll")

    for label, prefix in PROFILE_REGIONS:
        row = layout.row(align=True)
        row_split = row.split(factor=0.28)
        row_split.label(text=f"{label}:")
        values = row_split.row(align=True)
        values.prop(profile, f"{prefix}_pitch", text="")
        values.prop(profile, f"{prefix}_yaw", text="")
        values.prop(profile, f"{prefix}_roll", text="")


def draw_ifacial_rotation_profiles(layout, layer):
    profile_box = layout.box()
    header = profile_box.row(align=True)
    header.prop(
        layer,
        "ifacial_profiles_expanded",
        text="Rotation Profiles",
        icon="TRIA_DOWN" if layer.ifacial_profiles_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not layer.ifacial_profiles_expanded:
        return

    draw_rotation_profile_table(
        profile_box,
        "Inverted Profile (Default)",
        layer.ifacial_inverted_profile,
    )
    profile_box.separator()
    draw_rotation_profile_table(
        profile_box,
        "Non-Inverted Profile",
        layer.ifacial_normal_profile,
    )


def draw_wrapped_text(layout, text, region_width, icon="NONE"):
    characters = max(24, int((region_width - 48) / 7))
    lines = textwrap.wrap(text, width=characters, break_long_words=False)
    for index, line in enumerate(lines or [""]):
        layout.label(text=line, icon=icon if index == 0 else "BLANK1")


def draw_avatar_status(layout, armature, region_width):
    status = layout
    version = get_vrm_version(armature)
    status.label(text=f"VRM Version: {version or 'Unavailable'}", icon="INFO")

    if version is None:
        return

    missing_bones = get_missing_humanoid_bones(armature)
    if missing_bones:
        draw_wrapped_text(
            status,
            f"Unassigned Humanoid Bones ({len(missing_bones)}): "
            + ", ".join(missing_bones),
            region_width,
            icon="ERROR",
        )
    else:
        status.label(text="Humanoid Ready", icon="CHECKMARK")

    bound_count, required_count, _missing_expressions = get_arkit_bind_status(armature)
    if bound_count == required_count:
        status.label(text="ARKit Ready", icon="CHECKMARK")
    else:
        status.label(
            text=f"ARKit Expressions: {bound_count}/{required_count} bound",
            icon="INFO",
        )


def draw_source_settings(layout, layer, region_width):
    layout = indented_column(layout, region_width)
    ifacialmocap_active = (
        layer.source_type == "IFACIALMOCAP"
        and ifacialmocap_runtime.is_layer_running(layer)
    )
    vmc_active = (
        layer.source_type == "VMC"
        and vmc_runtime.is_layer_running(layer)
    )
    keybinds_active = (
        layer.source_type == "KEYBINDS"
        and keybinds_runtime.is_layer_running(layer)
    )
    springbone_active = (
        layer.source_type == "SPRING_BONE"
        and springbone_runtime.is_layer_running(layer)
    )
    source_connected = (
        ifacialmocap_active or vmc_active or keybinds_active or springbone_active
    )
    source_row = layout.row(align=True)
    source_row.enabled = not source_connected
    source_split = source_row.split(factor=0.24)
    source_split.label(text="Source Type:")
    source_split.menu(
        "YUGO_MT_source_type",
        text=SOURCE_TYPE_LABELS.get(layer.source_type, "None"),
    )

    if layer.source_type == "NONE":
        reminder = layout.box()
        reminder.label(
            text="Select a Source Type to configure this Motion Layer",
            icon="INFO",
        )
    elif layer.source_type in {"VMC", "IFACIALMOCAP"}:
        network = layout.column(align=True)
        network.enabled = not source_connected

        address_row = network.row(align=True)
        address_split = address_row.split(factor=0.24)
        address_split.label(
            text="iPhone Address:"
            if layer.source_type == "IFACIALMOCAP"
            else "Address:"
        )
        address_split.prop(layer, "address", text="")

        port_row = network.row(align=True)
        port_split = port_row.split(factor=0.24)
        port_split.label(text="Port:")
        port_split.prop(layer, "port", text="")

        draw_source_action_button(
            layout,
            "yugo.connection_toggle",
            "Disconnect" if source_connected else "Connect",
            "PAUSE" if source_connected else "PLAY",
        )

        if layer.source_type == "IFACIALMOCAP":
            calibration = layout.column()
            calibration.enabled = ifacialmocap_active
            draw_source_action_button(
                calibration,
                "yugo.ifacial_calibrate",
                "Calibration",
                "ORIENTATION_LOCAL",
            )

            options = layout.column(align=True)
            options.prop(layer, "ifacial_mirror")
            options.prop(layer, "ifacial_invert_body_rotation")
            data_mode = options.row(align=True)
            data_mode.prop(layer, "ifacial_data_mode")
        elif layer.source_type == "VMC":
            data_mode = layout.row(align=True)
            data_mode.prop(layer, "vmc_data_mode")
    elif layer.source_type == "SPRING_BONE":
        button_row = layout.row()
        button_split = button_row.split(factor=0.40)
        button_split.separator()
        controls = button_split.row(align=True)
        controls.scale_y = 1.35
        control_split = controls.split(factor=0.88, align=True)
        control_split.operator(
            "yugo.connection_toggle",
            text=(
                "Stop Spring Physics"
                if springbone_active
                else "Start Spring Physics"
            ),
            icon="PAUSE" if springbone_active else "PLAY",
        )
        reset_button = control_split.row(align=True)
        reset_button.enabled = springbone_active
        reset_button.operator(
            "yugo.springbone_reset",
            text="",
            icon="FILE_REFRESH",
        )

        options = layout.column(align=True)
        options.prop(layer, "spring_physics_rate")
        options.prop(layer, "spring_show_hit_radius")

        info = layout.box()
        cached_info = springbone_runtime.cached_vrm_info(layer)
        if cached_info is not None:
            mode, stats = cached_info
        else:
            armature = active_armature(bpy.context.scene)
            extension = get_vrm_extension(armature)
            if extension is None:
                info.alert = True
                info.label(text="Select a VRM Armature", icon="ERROR")
                extension = None
                stats = None
                mode = ""
            else:
                stats = spring_bone_stats(extension)
                mode = (
                    "VRM 1.0"
                    if is_vrm1(extension)
                    else "VRM 0.x" if is_vrm0(extension) else "Unsupported"
                )
        if stats is not None:
            info.label(text=f"Detected Mode: {mode}", icon="PHYSICS")
            info.label(
                text=f"Springs: {stats.springs}    Joints: {stats.joints}"
            )
            info.label(text=f"Colliders: {stats.colliders}")
            if stats.springs <= 0:
                warning = info.row()
                warning.alert = True
                warning.label(text="No configured Spring Bone springs", icon="ERROR")
        error_message = springbone_runtime.last_error(layer)
        if error_message:
            error = layout.box()
            error.alert = True
            error.label(text=error_message, icon="ERROR")
    elif layer.source_type == "KEYBINDS":
        draw_keybind_settings(layout, layer, keybinds_active)
    else:
        action_row = layout.row(align=True)
        action_split = action_row.split(factor=0.16)
        action_split.label(text="Action:")
        action_split.prop(layer, "action", text="")


def draw_right_dropdown(layout, data, property_name, label):
    row = layout.row(align=True)
    split = row.split(factor=0.34)
    split.label(text=label)
    split.prop(data, property_name, text="")


def draw_keybind_settings(layout, layer, listener_active):
    listen = layout.row()
    listen.scale_y = 1.35
    listen.operator(
        "yugo.connection_toggle",
        text="Stop Keybinds" if listener_active else "Start Keybinds",
        icon="PAUSE" if listener_active else "PLAY",
    )
    if listener_active:
        layout.label(text="All assigned keybinds are listening", icon="CHECKMARK")

    header_row = layout.row(align=True)
    header_number = header_row.split(factor=0.30)
    header_number.label(text="No.")
    header_remaining = header_number.row(align=True)
    header_key = header_remaining.split(factor=0.40)
    header_key.label(text="Key")
    header_key.label(text="Bind Shapes")

    list_row = layout.row()
    list_row.template_list(
        "YUGO_UL_keybinds",
        "",
        layer,
        "keybinds",
        layer,
        "keybind_active_index",
        rows=4,
    )
    controls = list_row.column(align=True)
    controls.operator("yugo.keybind_add", text="", icon="ADD")
    controls.operator("yugo.keybind_remove", text="", icon="REMOVE")
    controls.separator()
    up = controls.operator("yugo.keybind_move", text="", icon="TRIA_UP")
    up.direction = "UP"
    down = controls.operator("yugo.keybind_move", text="", icon="TRIA_DOWN")
    down.direction = "DOWN"

    item = active_keybind(layer)
    if item is None:
        return
    box = layout.box()
    box.label(text=f"Keybind_{layer.keybind_active_index + 1:03d}")

    key_row = box.row(align=True)
    key_split = key_row.split(factor=0.34)
    key_split.label(text="Key:")
    key_controls = key_split.row(align=True)
    key_controls.prop(item, "key", text="")
    key_controls.prop(item, "use_shift", text="+ Shift", toggle=True)
    key_controls.prop(item, "use_ctrl", text="+ Ctrl", toggle=True)

    draw_right_dropdown(box, item, "mode", "Mode:")
    draw_right_dropdown(box, item, "expression_id", "Bind Shape:")

    action_row = box.row(align=True)
    action_row.prop(item, "binary")
    action_row.prop(item, "value", slider=True)
    smooth = box.row()
    smooth.enabled = not item.binary
    smooth.prop(item, "smooth", slider=True)

    mask = box.box()
    mask.label(text="Mask Lock")
    mask_row = mask.row(align=True)
    mask_row.prop(item, "mask_exclusive", toggle=True)
    mask_row.prop(item, "mask_eyes", toggle=True)
    mask_row.prop(item, "mask_mouth", toggle=True)


def layer_smooth_enabled(layer):
    if layer.source_type == "IFACIALMOCAP":
        return layer.ifacial_data_mode == "SMOOTH"
    if layer.source_type == "VMC":
        return layer.vmc_data_mode == "SMOOTH"
    return True


def draw_humanoid_mask(layout, layer, region_width):
    mask_box = layout.box()
    header = mask_box.row(align=True)
    header.prop(
        layer,
        "mask_expanded",
        text="Humanoid Mask",
        icon="TRIA_DOWN" if layer.mask_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not layer.mask_expanded:
        return

    mask_box.label(text="Each region: Weight | Smooth")
    smooth_enabled = layer_smooth_enabled(layer)
    padding = content_padding(region_width)
    central = mask_box.column(align=True)
    for label, property_name in CENTRAL_MASK_PARTS:
        row = central.row(align=True)
        padded = row.split(factor=padding)
        padded.separator()
        body = padded.row(align=True)
        body_split = body.split(factor=0.28)
        body_split.label(text=f"{label}:")
        draw_mask_control(
            body_split,
            getattr(layer, property_name),
            smooth_enabled,
        )

    mask_box.separator()

    header = mask_box.row(align=True)
    header_padded = header.split(factor=padding)
    header_padded.separator()
    header_body = header_padded.row(align=True)
    header_body_split = header_body.split(factor=0.32)
    header_body_split.label(text="")
    side_headers = header_body_split.row(align=True)
    side_headers.label(text="Right")
    side_headers.label(text="Left")

    sides = mask_box.column(align=True)
    for label, right_property, left_property in BILATERAL_MASK_PARTS:
        row = sides.row(align=True)
        padded = row.split(factor=padding)
        padded.separator()
        body = padded.row(align=True)
        body_split = body.split(factor=0.32)
        body_split.label(text=f"{label}:")
        controls = body_split.row(align=True)
        draw_mask_control(
            controls, getattr(layer, right_property), smooth_enabled
        )
        draw_mask_control(
            controls, getattr(layer, left_property), smooth_enabled
        )

    if layer.source_type == "IFACIALMOCAP":
        mask_box.separator()
        draw_ifacial_rotation_profiles(mask_box, layer)


def draw_blendshape_table(layout, items, smooth_enabled):
    header = layout.row(align=True)
    header_split = header.split(factor=0.34)
    header_split.label(text="Expression")
    values = header_split.row(align=True)
    values.label(text="Data Raw")
    values.label(text="Weight")
    values.label(text="Smooth")
    values.label(text="Output")

    for item in items:
        row = layout.row(align=True)
        row_split = row.split(factor=0.34)
        row_split.label(text=item.expression_name)
        values = row_split.row(align=True)

        raw = values.row(align=True)
        raw.enabled = False
        raw.prop(item, "raw_value", text="")
        values.prop(item, "weight", text="")
        smooth = values.row(align=True)
        smooth.enabled = smooth_enabled
        smooth.prop(item, "smooth", text="", slider=True)
        output = values.row(align=True)
        output.enabled = False
        output.prop(item, "output_value", text="")


def draw_blendshape_group(
    layout, layer, label, property_name, items, smooth_enabled
):
    header = layout.row(align=True)
    expanded = getattr(layer, property_name)
    header.prop(
        layer,
        property_name,
        text=f"{label} ({len(items)})",
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if expanded:
        draw_blendshape_table(layout, items, smooth_enabled)


def draw_blendshape_mask(layout, layer):
    mask_box = layout.box()
    header = mask_box.row(align=True)
    header.prop(
        layer,
        "blendshape_mask_expanded",
        text="BlendShape Mask",
        icon="TRIA_DOWN" if layer.blendshape_mask_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not layer.blendshape_mask_expanded:
        return

    refresh = mask_box.row(align=True)
    refresh.operator("yugo.blendshape_mask_refresh", icon="FILE_REFRESH")

    items = list(layer.blendshape_masks)
    if not items:
        mask_box.label(
            text="Read the selected VRM to create the expression list",
            icon="INFO",
        )
        return

    arkit_items = [item for item in items if item.is_arkit]
    other_items = [item for item in items if not item.is_arkit]
    smooth_enabled = layer_smooth_enabled(layer)
    if arkit_items:
        draw_blendshape_group(
            mask_box,
            layer,
            "ARKit",
            "blendshape_arkit_expanded",
            arkit_items,
            smooth_enabled,
        )
    if other_items:
        draw_blendshape_group(
            mask_box,
            layer,
            "Shapes",
            "blendshape_other_expanded",
            other_items,
            smooth_enabled,
        )


class YUGO_PT_base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Yugo"


def draw_addon_start(layout, region_width):
    buttons = indented_column(layout, region_width)
    utility_row = buttons.row(align=True)
    utility_row.operator("yugo.setting_popup", text="Setting", icon="PREFERENCES")
    utility_row.operator("yugo.help_placeholder", text="Help", icon="HELP")
    buttons.separator(factor=0.85)
    view_row = buttons.row(align=True)
    view_row.scale_y = 1.35
    view_split = view_row.split(factor=0.86, align=True)
    view_split.operator("yugo.view_window", text="No UI Window", icon="WINDOW")
    view_split.operator("yugo.view_window_settings", text="", icon="PREFERENCES")
    fast_started = buttons.row()
    fast_started.scale_y = 1.65
    fast_started.operator("yugo.fast_started", text="Fast Started")


class YUGO_PT_avatar(YUGO_PT_base, bpy.types.Panel):
    bl_idname = "YUGO_PT_avatar"
    bl_label = "Yugo"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        draw_addon_start(layout, context.region.width)
        layout.separator()

        layout.label(text="Avatar List")
        avatar_row = layout.row()
        avatar_row.template_list(
            "YUGO_UL_avatars",
            "",
            scene,
            "yugo_avatars",
            scene,
            "yugo_avatar_index",
            rows=3,
        )
        avatar_controls = avatar_row.column(align=True)
        avatar_controls.operator("yugo.avatar_add", text="", icon="ADD")
        avatar_controls.operator("yugo.avatar_remove", text="", icon="REMOVE")

        avatar = active_avatar(scene)
        if avatar is None:
            box = layout.box()
            box.label(text="Add a VRM avatar to begin", icon="INFO")
            return

        armature_field = indented_column(layout, context.region.width)
        armature_field.prop(avatar, "armature", text="VRM Armature")
        optimize = armature_field.row()
        optimize.scale_y = 1.65
        optimize.operator("yugo.optimize", text="Optimize", icon="SHADING_RENDERED")
        armature = active_armature(scene)

        if armature is None:
            box = layout.box()
            box.label(text="Select an armature in the avatar list", icon="INFO")
            return


class YUGO_PT_character_meta(YUGO_PT_base, bpy.types.Panel):
    bl_idname = "YUGO_PT_character_meta"
    bl_label = ""
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_armature(context.scene) is not None

    def draw_header(self, _context):
        self.layout.label(text="Character Meta", icon="FILE")

    def draw(self, context):
        layout = self.layout
        armature = active_armature(context.scene)
        if armature is None:
            return

        if not is_vrm_armature(armature):
            warning = layout.row()
            warning.alert = True
            warning.label(text="This armature has no VRM Add-on data", icon="ERROR")

        thumbnail = get_avatar_thumbnail(armature)
        if thumbnail is not None:
            try:
                preview = thumbnail.preview_ensure()
                layout.template_icon(icon_value=preview.icon_id, scale=6.0)
            except (AttributeError, RuntimeError):
                layout.label(text="Thumbnail unavailable", icon="IMAGE_DATA")
        else:
            placeholder = layout.column(align=True)
            placeholder.scale_y = 2.5
            placeholder.label(text="", icon="IMAGE_DATA")
            placeholder.label(text="No VRM thumbnail", icon="NONE")

        name_row = layout.row(align=True)
        name_row.alignment = "RIGHT"
        name_row.label(text="Name:")
        name_row.label(text=get_avatar_name(armature), icon="OUTLINER_OB_ARMATURE")

        layout.separator()
        draw_avatar_status(layout, armature, context.region.width)


class YUGO_PT_motion_layers(YUGO_PT_base, bpy.types.Panel):
    bl_idname = "YUGO_PT_motion_layers"
    bl_label = ""
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, _context):
        self.layout.label(text="Motion Layers", icon="ARMATURE_DATA")

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        avatar = active_avatar(scene)
        if avatar is None:
            layout.label(text="Add a VRM avatar above first", icon="INFO")
            return

        row = layout.row()
        row.template_list(
            "YUGO_UL_motion_layers",
            "",
            avatar,
            "motion_layers",
            avatar,
            "motion_layer_index",
            rows=4,
        )

        controls = row.column(align=True)
        controls.operator("yugo.layer_add", text="", icon="ADD")
        controls.operator("yugo.layer_remove", text="", icon="REMOVE")
        controls.separator()
        up = controls.operator("yugo.layer_move", text="", icon="TRIA_UP")
        up.direction = "UP"
        down = controls.operator("yugo.layer_move", text="", icon="TRIA_DOWN")
        down.direction = "DOWN"

        if not avatar.motion_layers:
            layout.label(
                text="Add a motion source or Spring Bone layer",
                icon="INFO",
            )
        else:
            layer = active_layer(scene)
            layout.separator()
            draw_source_settings(layout, layer, context.region.width)
            if layer.source_type not in {"NONE", "KEYBINDS", "SPRING_BONE"}:
                layout.separator()
                draw_humanoid_mask(layout, layer, context.region.width)
            if layer.source_type not in {"NONE", "SPRING_BONE"}:
                layout.separator()
                draw_blendshape_mask(layout, layer)


CLASSES = (
    YUGO_MT_source_type,
    YUGO_PT_avatar,
    YUGO_PT_character_meta,
    YUGO_PT_motion_layers,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
