"""Properties and UI for Yugo Composing."""

from __future__ import annotations

import math

import bpy

from ..state import active_avatar
from . import runtime


def _effect_intensity_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "intensity")


def _effect_color_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "color")


def _effect_blur_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "blur")


def _effect_offset_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "offset")


def _effect_range_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "range")


def _effect_rotation_updated(effect, context):
    runtime.update_effect_property(context.scene, effect, "rotation")


def _factor_property(
    name: str,
    description: str,
    default: float = 1.0,
    update=None,
):
    return bpy.props.FloatProperty(
        name=name,
        description=description,
        default=default,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=update,
    )


def _amount_property(
    name: str,
    description: str,
    default: float = 0.0,
    soft_max: float = 100.0,
    hard_max: float | None = None,
    update=None,
):
    options = {
        "name": name,
        "description": description,
        "default": default,
        "min": 0.0,
        "soft_max": soft_max,
        "update": update,
    }
    if hard_max is not None:
        options["max"] = hard_max
    return bpy.props.FloatProperty(
        **options,
    )


def _expanded_property():
    return bpy.props.BoolProperty(
        name="Expanded",
        description="Show this effect's detailed parameters",
        default=False,
        options={"HIDDEN"},
    )


def _color_property(
    name: str,
    default: tuple[float, float, float, float],
    update=None,
):
    return bpy.props.FloatVectorProperty(
        name=name,
        description=f"{name} used by the preset compositing effect",
        default=default,
        size=4,
        min=0.0,
        max=1.0,
        subtype="COLOR",
        update=update,
    )


def _offset_property():
    return bpy.props.FloatVectorProperty(
        name="Offset",
        description="XY offset used by the preset compositing effect",
        default=(0.0, 0.0),
        size=2,
        update=_effect_offset_updated,
    )


class YUGUS_COMPOSING_PG_blur_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of this compositing effect",
        update=_effect_intensity_updated,
    )
    blur: _amount_property(
        "Blur",
        "Blur amount of this compositing effect",
        soft_max=1000.0,
        update=_effect_blur_updated,
    )


class YUGUS_COMPOSING_PG_mix_effect(bpy.types.PropertyGroup):
    intensity: _factor_property(
        "Intensity",
        "Strength of this compositing effect",
        update=_effect_intensity_updated,
    )


class YUGUS_COMPOSING_PG_offset_blur_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of this compositing effect",
        update=_effect_intensity_updated,
    )
    blur: _amount_property(
        "Blur",
        "Blur amount of this compositing effect",
        soft_max=100.0,
        hard_max=100.0,
        update=_effect_blur_updated,
    )
    offset: _offset_property()


class YUGUS_COMPOSING_PG_shadow_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of the secondary shadow",
        update=_effect_intensity_updated,
    )
    color: _color_property(
        "Color",
        (1.0, 1.0, 1.0, 1.0),
        update=_effect_color_updated,
    )
    blur: _amount_property(
        "Blur",
        "Blur amount of the secondary shadow",
        soft_max=100.0,
        hard_max=100.0,
        update=_effect_blur_updated,
    )
    offset: _offset_property()


class YUGUS_COMPOSING_PG_light_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of this light effect",
        update=_effect_intensity_updated,
    )
    color: _color_property(
        "Color",
        (1.0, 1.0, 1.0, 1.0),
        update=_effect_color_updated,
    )
    blur: _amount_property(
        "Blur",
        "Blur amount of this light effect",
        soft_max=100.0,
        hard_max=100.0,
        update=_effect_blur_updated,
    )
    offset: _offset_property()


class YUGUS_COMPOSING_PG_edge_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of this edge effect",
        update=_effect_intensity_updated,
    )
    color: _color_property(
        "Color",
        (1.0, 1.0, 1.0, 1.0),
        update=_effect_color_updated,
    )
    range: _amount_property(
        "Range",
        "Width or coverage of this edge effect",
        default=0.5,
        soft_max=500.0,
        hard_max=500.0,
        update=_effect_range_updated,
    )
    offset: _offset_property()


class YUGUS_COMPOSING_PG_overlay_effect(bpy.types.PropertyGroup):
    expanded: _expanded_property()
    intensity: _factor_property(
        "Intensity",
        "Strength of this overlay effect",
        update=_effect_intensity_updated,
    )
    color: _color_property(
        "Color",
        (1.0, 1.0, 1.0, 1.0),
        update=_effect_color_updated,
    )
    range: _amount_property(
        "Range",
        "Width or coverage of this overlay effect",
        default=0.5,
        soft_max=100.0,
        update=_effect_range_updated,
    )
    rotation: bpy.props.FloatProperty(
        name="Rotation",
        description="Rotation of the overlay gradient",
        default=0.0,
        min=-math.pi,
        max=math.pi,
        subtype="ANGLE",
        update=_effect_rotation_updated,
    )


class YUGUS_COMPOSING_PG_settings(bpy.types.PropertyGroup):
    initialized: bpy.props.BoolProperty(
        name="Initialized",
        description="Whether the shared YugoComp data has been initialized",
        default=False,
        options={"HIDDEN"},
    )
    last_error: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    scene_color_blend: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_blur_effect)
    ambient_occlusion: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_mix_effect)
    rim_light: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_light_effect)
    separated_rim: bpy.props.PointerProperty(
        type=YUGUS_COMPOSING_PG_offset_blur_effect
    )
    secondary_shadow: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_shadow_effect)
    separated_shadows: bpy.props.PointerProperty(
        type=YUGUS_COMPOSING_PG_offset_blur_effect
    )
    gradient_color: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_overlay_effect)
    dark_bloom: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_edge_effect)
    light_bloom: bpy.props.PointerProperty(type=YUGUS_COMPOSING_PG_edge_effect)


def _settings(scene):
    return getattr(scene, "yugus_composing", None)


def _draw_effect_header(layout, effect, label: str, show_color: bool = False):
    box = layout.box()
    header = box.split(factor=0.46, align=True)
    title = header.row(align=True)
    title.prop(
        effect,
        "expanded",
        text="",
        icon="TRIA_DOWN" if effect.expanded else "TRIA_RIGHT",
        emboss=False,
    )
    title.label(text=label)

    controls = header.row(align=True)
    if show_color:
        mix_and_color = controls.split(factor=0.82, align=True)
        mix_and_color.prop(effect, "intensity", text="Mix", slider=True)
        mix_and_color.prop(effect, "color", text="")
    else:
        controls.prop(effect, "intensity", text="Mix", slider=True)
    return box


def _draw_factor_row(layout, effect, property_name: str, label: str):
    row = layout.row(align=True)
    row.label(text=f"{label}:")
    row.prop(effect, property_name, text="", slider=True)


def _draw_offset_row(layout, effect):
    row = layout.row(align=True)
    row.label(text="Offset:")
    row.prop(effect, "offset", index=0, text="X")
    row.prop(effect, "offset", index=1, text="Y")


def _draw_blur_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label)
    if effect.expanded:
        _draw_factor_row(box, effect, "blur", "Blur")


def _draw_mix_effect(layout, effect, label: str):
    box = layout.box()
    header = box.split(factor=0.46, align=True)
    header.label(text=label)
    header.prop(effect, "intensity", text="Mix", slider=True)


def _draw_offset_blur_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label)
    if effect.expanded:
        body = box.column(align=True)
        _draw_factor_row(body, effect, "blur", "Blur")
        _draw_offset_row(body, effect)


def _draw_shadow_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label, show_color=True)
    if effect.expanded:
        body = box.column(align=True)
        _draw_factor_row(body, effect, "blur", "Blur")
        _draw_offset_row(body, effect)


def _draw_light_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label, show_color=True)
    if effect.expanded:
        body = box.column(align=True)
        _draw_factor_row(body, effect, "blur", "Blur")
        _draw_offset_row(body, effect)


def _draw_edge_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label, show_color=True)
    if effect.expanded:
        body = box.column(align=True)
        _draw_factor_row(body, effect, "range", "Range")
        _draw_offset_row(body, effect)


def _draw_overlay_effect(layout, effect, label: str):
    box = _draw_effect_header(layout, effect, label, show_color=True)
    if effect.expanded:
        body = box.column(align=True)
        _draw_factor_row(body, effect, "range", "Range")
        rotation = body.row(align=True)
        rotation.label(text="Rotation:")
        rotation.prop(effect, "rotation", text="")


class YUGUS_COMPOSING_PT_main(bpy.types.Panel):
    bl_idname = "YUGUS_COMPOSING_PT_main"
    bl_label = ""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Yugu Studio"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1000

    def draw_header(self, _context):
        self.layout.label(text="Composing", icon="NODE_COMPOSITING")

    def draw(self, context):
        from ..studio import draw_composer_membership
        draw_composer_membership(self.layout, context)
        settings = _settings(context.scene)
        self.layout.label(
            text="For performance, all characters share",
            icon="INFO",
        )
        self.layout.label(text="the same Composing data.")
        avatar = active_avatar(context.scene)
        if avatar is None:
            disabled = self.layout.row()
            disabled.enabled = False
            disabled.label(text="Select a character first")
        else:
            self.layout.prop(
                avatar,
                "composing_enabled",
                text="Enable YugoComp",
            )
        if settings.last_error:
            error = self.layout.row()
            error.alert = True
            error.label(text=settings.last_error, icon="ERROR")


class YUGUS_COMPOSING_PT_global(bpy.types.Panel):
    bl_idname = "YUGUS_COMPOSING_PT_global"
    bl_label = "Main FX"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Yugu Studio"
    bl_parent_id = "YUGUS_COMPOSING_PT_main"

    def draw(self, context):
        settings = _settings(context.scene)
        layout = self.layout
        layout.enabled = settings.initialized

        _draw_blur_effect(layout, settings.scene_color_blend, "Scene Color Blend")
        _draw_mix_effect(layout, settings.ambient_occlusion, "Ambient Occlusion")
        _draw_light_effect(layout, settings.rim_light, "Rim Light")
        _draw_offset_blur_effect(layout, settings.separated_rim, "Separated Rim")
        _draw_shadow_effect(layout, settings.secondary_shadow, "Secondery Shadow")
        _draw_offset_blur_effect(
            layout, settings.separated_shadows, "Separated Shadows"
        )
        _draw_overlay_effect(layout, settings.gradient_color, "Gradient Color")
        _draw_edge_effect(layout, settings.dark_bloom, "Dark Bloom")
        _draw_edge_effect(layout, settings.light_bloom, "Light Bloom")


CLASSES = (
    YUGUS_COMPOSING_PG_blur_effect,
    YUGUS_COMPOSING_PG_mix_effect,
    YUGUS_COMPOSING_PG_offset_blur_effect,
    YUGUS_COMPOSING_PG_shadow_effect,
    YUGUS_COMPOSING_PG_light_effect,
    YUGUS_COMPOSING_PG_edge_effect,
    YUGUS_COMPOSING_PG_overlay_effect,
    YUGUS_COMPOSING_PG_settings,
    YUGUS_COMPOSING_PT_main,
    YUGUS_COMPOSING_PT_global,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.yugus_composing = bpy.props.PointerProperty(
        type=YUGUS_COMPOSING_PG_settings
    )


def unregister():
    del bpy.types.Scene.yugus_composing
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
