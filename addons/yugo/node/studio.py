"""Node Editor access to Yugo's shared materials, avatars and compositor."""

import bpy

from .. import mtoon_optimize, outline_bake
from .constants import TREE_ID
from ..composing import addon as composing_ui, runtime as composing


class YUGUS_OT_composer_character(bpy.types.Operator):
    bl_idname = "yugu_node_studio.composer_character"
    bl_label = "Composer Character"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(items=[(s, s.title(), "") for s in ("ADD", "REMOVE", "GRAPH")])

    def execute(self, context):
        scene = context.scene
        if self.action == "REMOVE":
            if 0 <= scene.yugo_avatar_index < len(scene.yugo_avatars):
                # The shared remover stops this avatar's legacy Motion Layers.
                return bpy.ops.yugo.avatar_remove()
        elif self.action == "ADD":
            bpy.ops.yugo.avatar_add()
            avatar = scene.yugo_avatars[scene.yugo_avatar_index]
            obj = context.active_object
            if obj and obj.type == "ARMATURE":
                avatar.armature = obj
        else:
            space = context.space_data
            tree = getattr(space, "edit_tree", None) or scene.yugu_node_studio_tree
            if not tree or tree.bl_idname != TREE_ID:
                self.report({"WARNING"}, "Select a Yugo Node graph first")
                return {"CANCELLED"}
            existing = {a.armature for a in scene.yugo_avatars}
            for node in tree.nodes:
                armature = getattr(node, "armature", None)
                if armature and armature.type == "ARMATURE" and armature not in existing:
                    bpy.ops.yugo.avatar_add()
                    scene.yugo_avatars[scene.yugo_avatar_index].armature = armature
                    existing.add(armature)
            if scene.yugo_avatars and scene.yugo_avatar_index < 0:
                scene.yugo_avatar_index = 0
        return {"FINISHED"}


class YUGUS_UL_composer_characters(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0):
        row = layout.row(align=True)
        row.prop(item, "composing_enabled", text="")
        row.prop(item, "armature", text="")


class YUGUS_OT_optimize(bpy.types.Operator):
    bl_idname = "yugu_node_studio.optimize"
    bl_label = "Optimize"
    bl_description = "Optimize selected MToon materials and optionally apply outlines as meshes"
    bl_options = {"REGISTER", "UNDO"}
    shadow_mix: bpy.props.FloatProperty(name="Shadow Mix", default=0.5, min=0, max=1, subtype="FACTOR")
    use_mtoon: bpy.props.BoolProperty(name="MToon Optimize", default=True)
    use_outline_to_mesh: bpy.props.BoolProperty(name="Outline Apply", default=False,
        description="Bake selected MToon outlines to meshes, then remove the original outline modifiers")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.label(text=f"Selected Objects: {mtoon_optimize.selected_object_count(context)}")
        self.layout.label(text=f"MToon Materials: {mtoon_optimize.selected_mtoon_material_count(context)}")
        self.layout.prop(self, "use_mtoon")
        row = self.layout.row()
        row.enabled = self.use_mtoon
        row.prop(self, "shadow_mix", slider=True)
        self.layout.prop(self, "use_outline_to_mesh")
        if self.use_outline_to_mesh:
            self.layout.label(text="Creates meshes; removes source outline modifiers", icon="INFO")

    def execute(self, context):
        count, errors = 0, []
        if self.use_mtoon:
            count, errors = mtoon_optimize.optimize_selected_mtoon_materials(context, self.shadow_mix)
            if count:
                self.report({"INFO"}, f"Optimized {count} MToon materials")
        if self.use_outline_to_mesh:
            try:
                outlines, outline_errors = outline_bake.apply_selected_outlines(context)
                count += len(outlines)
                errors.extend(outline_errors)
                if outlines:
                    self.report({"INFO"}, f"Applied {len(outlines)} outline meshes")
            except Exception as error:
                errors.append(f"Outline Apply: {error}")
        if errors:
            self.report({"WARNING"}, "; ".join(errors))
        return {"FINISHED"} if count else {"CANCELLED"}


class YUGUS_OT_composer_preview(bpy.types.Operator):
    bl_idname = "yugu_node_studio.composer_preview"
    bl_label = "Show in Viewport"
    bl_description = "Switch this viewport to Rendered shading with Compositor set to Always"

    @classmethod
    def poll(cls, context):
        return isinstance(context.space_data, bpy.types.SpaceView3D)

    def execute(self, context):
        shading = context.space_data.shading
        shading.type = "RENDERED"
        shading.use_compositor = "ALWAYS"
        context.area.tag_redraw()
        return {"FINISHED"}


class YUGUS_OT_composer_reload(bpy.types.Operator):
    bl_idname = "yugu_node_studio.composer_reload"
    bl_label = "Reload Composer"
    bl_description = "Rebuild Main FX while keeping the current effect values"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.yugo_composing
        try:
            composing._install_shared_mainfx(context.scene, pull_preset_values=not settings.initialized)
            settings.initialized = True
            composing.sync_matte_ids(context.scene)
            settings.last_error = ""
        except Exception as error:
            settings.last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_composer_membership(layout, context):
    scene = context.scene
    row = layout.row()
    row.template_list("YUGUS_UL_composer_characters", "", scene, "yugo_avatars", scene, "yugo_avatar_index", rows=3)
    buttons = row.column(align=True)
    buttons.operator("yugu_node_studio.composer_character", text="", icon="ADD").action = "ADD"
    buttons.operator("yugu_node_studio.composer_character", text="", icon="REMOVE").action = "REMOVE"
    layout.operator("yugu_node_studio.composer_character", text="Collect Graph Characters", icon="NODETREE").action = "GRAPH"


class _MaterialPanel:
    bl_label = "Optimize"
    bl_region_type = "UI"
    bl_category = "Yugo"
    def draw(self, context):
        self.layout.label(text="Selected objects")
        self.layout.operator("yugu_node_studio.optimize", icon="MATERIAL")
        self.layout.label(text="Custom material · material sync 2 Hz")


class YUGUS_PT_node_materials(_MaterialPanel, bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == TREE_ID


class YUGUS_PT_node_composer(bpy.types.Panel):
    bl_label = "Composer"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    poll = classmethod(YUGUS_PT_node_materials.poll.__func__)
    def draw(self, context):
        draw_composer_membership(self.layout, context)
        composing_ui.YUGO_COMPOSING_PT_main.draw(self, context)
        self.layout.operator("yugu_node_studio.composer_reload", icon="FILE_REFRESH")


class YUGUS_PT_node_mainfx(bpy.types.Panel):
    bl_label = "Main FX"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugo"
    bl_parent_id = "YUGUS_PT_node_composer"
    poll = classmethod(YUGUS_PT_node_materials.poll.__func__)
    def draw(self, context):
        composing_ui.YUGO_COMPOSING_PT_global.draw(self, context)


CLASSES = (YUGUS_OT_composer_character,
           YUGUS_UL_composer_characters, YUGUS_OT_optimize,
           YUGUS_OT_composer_preview, YUGUS_OT_composer_reload,
           YUGUS_PT_node_materials, YUGUS_PT_node_composer, YUGUS_PT_node_mainfx)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
