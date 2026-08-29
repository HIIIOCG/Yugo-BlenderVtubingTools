import bpy

from .keybinds import runtime as keybinds_runtime
from .keybinds.vrm import iter_expressions as iter_keybind_expressions
from .state import active_armature, active_layers
from .vrm import get_avatar_name


class YUGO_UL_avatars(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _flt_flag=0,
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            armature = item.armature
            layout.label(
                text=get_avatar_name(armature) if armature is not None else "Not Selected",
                icon="OUTLINER_OB_ARMATURE",
            )
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="OUTLINER_OB_ARMATURE")


class YUGO_UL_motion_layers(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _flt_flag=0,
    ):
        layer = item

        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(
                layer,
                "enabled",
                text="",
                icon="HIDE_OFF" if layer.enabled else "HIDE_ON",
                emboss=False,
            )
            solo = row.operator(
                "yugo.layer_solo_toggle",
                text="",
                icon="LAYER_ACTIVE" if layer.solo else "LAYER_USED",
                emboss=False,
            )
            solo.index = _index

            color_cell = row.row(align=True)
            color_cell.prop(layer, "color_tag", text="", icon_only=True, emboss=False)

            any_solo = any(item.solo for item in active_layers(context.scene))
            layer_is_audible = layer.enabled and (not any_solo or layer.solo)

            content = row.split(factor=0.48, align=True)
            name_row = content.row(align=True)
            name_row.enabled = layer_is_audible
            name_row.prop(
                layer,
                "name",
                text="",
                emboss=False,
            )

            settings = content.row(align=True)
            settings.enabled = layer_is_audible
            settings_split = settings.split(factor=0.55, align=True)
            settings_split.prop(layer, "blend_mode", text="")
            settings_split.prop(layer, "weight", text="Weight", slider=True)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(layer, "color_tag", text="", icon_only=True)


class YUGO_UL_keybinds(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        index=0,
        _flt_flag=0,
    ):
        row = layout.row(align=True)
        number = row.split(factor=0.30)
        number.label(text=f"Keybind_{index + 1:03d}")
        remaining = number.row(align=True)
        key_column = remaining.split(factor=0.40)
        if item.key == "NONE":
            shortcut = "—"
        else:
            shortcut = ("Ctrl + " if item.use_ctrl else "") + (
                "Shift + " if item.use_shift else ""
            ) + item.key
        key_column.label(text=shortcut)

        expression_name = "—"
        armature = active_armature(context.scene)
        for record in iter_keybind_expressions(armature):
            if record.identifier == item.expression_id:
                expression_name = record.name
                break
        key_column.label(text=expression_name)
        row.label(
            text="",
            icon=(
                "RADIOBUT_ON"
                if keybinds_runtime.binding_is_on(item)
                else "RADIOBUT_OFF"
            ),
        )


CLASSES = (YUGO_UL_avatars, YUGO_UL_motion_layers, YUGO_UL_keybinds)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
