"""Blender smoke test for Yugo Composing registration and collection state."""

from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo  # noqa: E402


class LayoutRecorder:
    """Minimal UILayout stand-in that validates every referenced property."""

    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.enabled = True
        self.scale_x = 1.0
        self.alignment = "EXPAND"

    def row(self, **_kwargs):
        return LayoutRecorder(self.events)

    def column(self, **_kwargs):
        return LayoutRecorder(self.events)

    def box(self):
        return LayoutRecorder(self.events)

    def split(self, **_kwargs):
        return LayoutRecorder(self.events)

    def separator(self):
        return None

    def label(self, **kwargs):
        self.events.append(("LABEL", kwargs.get("text", "")))

    def prop(self, data, property_name, **kwargs):
        assert hasattr(data, property_name), property_name
        self.events.append(
            ("PROP", property_name, kwargs.get("text"), kwargs.get("index"))
        )

    def operator(self, *_args, **_kwargs):
        return SimpleNamespace(direction="")

    def template_list(
        self,
        _list_type,
        _list_id,
        collection_owner,
        collection_name,
        index_owner,
        index_name,
        **_kwargs,
    ):
        assert hasattr(collection_owner, collection_name), collection_name
        assert hasattr(index_owner, index_name), index_name


def main():
    yugo.register()
    try:
        from yugo.composing import addon, runtime

        assert addon.YUGO_COMPOSING_PT_main.bl_order == 1000

        scene = bpy.context.scene
        settings = scene.yugo_composing
        assert settings.initialized is False

        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        armature_data = bpy.data.armatures.new("CharacterRig")
        armature = bpy.data.objects.new("CharacterRig", armature_data)
        scene.collection.objects.link(armature)
        mesh_data = bpy.data.meshes.new("CharacterMesh")
        character_mesh = bpy.data.objects.new("CharacterMesh", mesh_data)
        scene.collection.objects.link(character_mesh)
        armature_modifier = character_mesh.modifiers.new("Armature", "ARMATURE")
        armature_modifier.object = armature
        avatar = scene.yugo_avatars.add()
        avatar.armature = armature
        scene.yugo_avatar_index = 0
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)

        avatar.composing_enabled = True
        assert avatar.composing_enabled is True, settings.last_error
        assert settings.initialized is True
        assert settings.last_error == ""
        for view_layer in scene.view_layers:
            assert view_layer.use_pass_cryptomatte_object
            assert view_layer.use_pass_cryptomatte_material
            assert view_layer.use_pass_ambient_occlusion
            assert view_layer.use_pass_z

        mainfx_nodes = runtime._active_mainfx_nodes(scene)
        assert any(
            node.bl_idname == "NodeFrame" and node.label == "MainFX_RimLight"
            for node in mainfx_nodes
        )
        assert any(
            node.bl_idname == "NodeFrame"
            and node.label == "MainFX_SeparatedShadows"
            for node in mainfx_nodes
        )
        assert any(
            node.bl_idname == "NodeFrame" and node.label == "MainFX_SeparatedRim"
            for node in mainfx_nodes
        )
        matte_nodes = [
            node
            for node in mainfx_nodes
            if node.bl_idname == "CompositorNodeCryptomatteV2"
            and node.label in {"MainFX_matte", "MainFX_mattle"}
        ]
        assert matte_nodes
        assert all(node.matte_id == "CharacterMesh" for node in matte_nodes)
        assert all(node.scene == scene for node in matte_nodes)

        render_layers_nodes = [
            node
            for node in mainfx_nodes
            if node.bl_idname == "CompositorNodeRLayers"
        ]
        assert len(render_layers_nodes) == 1
        assert render_layers_nodes[0].scene == scene

        compositor_tree = runtime._scene_compositor_tree(scene)
        viewers = [
            node
            for node in compositor_tree.nodes
            if node.bl_idname == "CompositorNodeViewer"
            and bool(node.get(runtime.VIEWER_NODE_KEY, False))
        ]
        assert len(viewers) == 1
        viewer = viewers[0]
        assert not viewer.mute
        assert not viewer.hide
        assert compositor_tree.nodes.active == viewer
        viewer_input = viewer.inputs.get("Image")
        assert viewer_input is not None
        assert len(viewer_input.links) == 1
        viewer_source = viewer_input.links[0].from_socket

        group_output = next(
            node
            for node in compositor_tree.nodes
            if node.bl_idname == "NodeGroupOutput"
        )
        group_image = group_output.inputs.get("Image")
        assert group_image is not None
        assert len(group_image.links) == 1
        assert group_image.links[0].from_socket == viewer_source

        scene_color_frame = next(
            node
            for node in mainfx_nodes
            if node.bl_idname == "NodeFrame"
            and node.label == "MainFX_SceneColorBlend"
        )
        scene_blend_group = next(
            node
            for node in mainfx_nodes
            if node.bl_idname == "CompositorNodeGroup"
            and node.parent == scene_color_frame
            and node.node_tree is not None
            and node.node_tree.name.startswith("SceneBlend")
        )
        mix_node = next(
            node
            for node in scene_blend_group.node_tree.nodes
            if node.bl_idname == "ShaderNodeMix"
        )
        assert mix_node.data_type == "RGBA"
        assert mix_node.factor_mode == "UNIFORM"
        assert mix_node.blend_type == "MIX"
        linked_color_inputs = {
            link.to_socket.identifier
            for link in scene_blend_group.node_tree.links
            if link.to_node == mix_node
        }
        assert {"A_Color", "B_Color"}.issubset(linked_color_inputs)

        math_node = next(
            node
            for node in scene_blend_group.node_tree.nodes
            if node.bl_idname == "ShaderNodeMath"
        )
        assert math_node.operation == "MULTIPLY"
        assert math_node.use_clamp is False

        assert abs(settings.scene_color_blend.intensity - 0.5) < 1.0e-5
        assert abs(settings.scene_color_blend.blur - 300.0) < 1.0e-5
        assert abs(settings.ambient_occlusion.intensity - 0.75) < 1.0e-5
        assert runtime._named_active_node(
            scene, "MainFX_AmbientOcclusion_Mix"
        ) is not None
        assert abs(settings.rim_light.offset[0] - 0.7) < 1.0e-5
        assert abs(settings.rim_light.offset[1] + 3.0) < 1.0e-5
        assert abs(settings.separated_rim.intensity - 0.9) < 1.0e-5
        assert abs(settings.separated_rim.blur - 2.0) < 1.0e-5
        assert abs(settings.separated_rim.offset[0] - 2.0) < 1.0e-5
        assert abs(settings.separated_rim.offset[1] + 5.0) < 1.0e-5
        assert abs(settings.secondary_shadow.blur - 5.0) < 1.0e-5
        assert abs(settings.separated_shadows.intensity - 0.8) < 1.0e-5
        assert abs(settings.separated_shadows.blur - 1.0) < 1.0e-5
        assert abs(settings.separated_shadows.offset[0] - 1.0) < 1.0e-5
        assert abs(settings.separated_shadows.offset[1] + 8.1) < 1.0e-5
        assert abs(settings.dark_bloom.offset[0]) < 1.0e-5
        assert abs(settings.dark_bloom.offset[1] + 13.0) < 1.0e-5
        assert abs(settings.light_bloom.offset[0]) < 1.0e-5
        assert abs(settings.light_bloom.offset[1] + 15.0) < 1.0e-5

        assert settings.rim_light.bl_rna.properties["blur"].hard_min == 0.0
        assert settings.rim_light.bl_rna.properties["blur"].hard_max == 100.0
        assert settings.dark_bloom.bl_rna.properties["range"].hard_min == 0.0
        assert settings.dark_bloom.bl_rna.properties["range"].hard_max == 500.0
        assert settings.light_bloom.bl_rna.properties["range"].hard_max == 500.0

        settings.rim_light.intensity = 0.75
        settings.rim_light.offset = (2.5, -4.0)
        assert abs(
            runtime._node_output_value(
                runtime._named_active_node(scene, "MainFX_RimLight_Mix")
            )
            - 0.75
        ) < 1.0e-5
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "MainFX_RimLight_X")
        ) == 2.5
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "MainFX_RimLight_Y")
        ) == -4.0

        settings.secondary_shadow.blur = 12.0
        settings.separated_rim.offset = (4.0, -6.0)
        settings.separated_shadows.blur = 8.0
        settings.dark_bloom.offset = (3.0, -8.0)
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "MainFX_SeconderyShadow_Blur")
        ) == 12.0
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "SeparatedRim_X")
        ) == 4.0
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "SeparatedRim_Y")
        ) == -6.0
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "SeparatedShadows_Blur")
        ) == 8.0
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "MainFX_DarkBloom_X")
        ) == 3.0
        assert runtime._node_output_value(
            runtime._named_active_node(scene, "MainFX_DarkBloom_Y")
        ) == -8.0

        for node in matte_nodes:
            node.matte_id = "Sentinel"
        settings.rim_light.intensity = 0.6
        assert all(node.matte_id == "Sentinel" for node in matte_nodes)

        assert len(settings.secondary_shadow.offset) == 2

        for effect in (
            settings.scene_color_blend,
            settings.rim_light,
            settings.separated_rim,
            settings.secondary_shadow,
            settings.separated_shadows,
            settings.gradient_color,
            settings.dark_bloom,
            settings.light_bloom,
        ):
            effect.expanded = True

        for effect in (
            settings.rim_light,
            settings.secondary_shadow,
            settings.gradient_color,
            settings.dark_bloom,
            settings.light_bloom,
        ):
            assert len(effect.color) == 4

        global_layout = LayoutRecorder()
        panel = SimpleNamespace(layout=global_layout)
        addon.YUGO_COMPOSING_PT_global.draw(panel, bpy.context)
        assert global_layout.enabled is True

        expected_order = [
            "Scene Color Blend",
            "Ambient Occlusion",
            "Rim Light",
            "Separated Rim",
            "Secondery Shadow",
            "Separated Shadows",
            "Gradient Color",
            "Dark Bloom",
            "Light Bloom",
        ]
        visible_effects = [
            value
            for event_type, value, *_rest in global_layout.events
            if event_type == "LABEL" and value in expected_order
        ]
        assert visible_effects == expected_order
        mix_sliders = [
            event
            for event in global_layout.events
            if event[0] == "PROP" and event[1] == "intensity" and event[2] == "Mix"
        ]
        assert len(mix_sliders) == len(expected_order)

        assert not hasattr(settings, "categories")
        assert not hasattr(addon, "YUGO_COMPOSING_PT_objects")

        main_layout = LayoutRecorder()
        main_panel = SimpleNamespace(layout=main_layout)
        addon.YUGO_COMPOSING_PT_main.draw(main_panel, bpy.context)
        assert (
            "LABEL",
            "For performance, all characters share",
        ) in main_layout.events
        assert any(
            event[0] == "PROP" and event[1] == "composing_enabled"
            for event in main_layout.events
        )

        compositor_tree = runtime._scene_compositor_tree(scene)
        original_generation = compositor_tree.get(runtime.ACTIVE_GENERATION_KEY)
        original_mainfx_pointers = {
            node.as_pointer() for node in runtime._active_mainfx_nodes(scene)
        }
        avatar.composing_enabled = False
        panel.layout = LayoutRecorder()
        addon.YUGO_COMPOSING_PT_global.draw(panel, bpy.context)
        assert panel.layout.enabled is True
        assert settings.initialized is True
        assert all(node.matte_id == "" for node in matte_nodes)

        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        avatar.composing_enabled = True
        assert avatar.composing_enabled is True, settings.last_error
        assert abs(settings.rim_light.intensity - 0.6) < 1.0e-5
        assert abs(settings.rim_light.offset[0] - 2.5) < 1.0e-5
        assert abs(settings.rim_light.offset[1] + 4.0) < 1.0e-5
        assert abs(settings.secondary_shadow.blur - 12.0) < 1.0e-5
        assert abs(settings.separated_rim.offset[0] - 4.0) < 1.0e-5
        assert abs(settings.separated_rim.offset[1] + 6.0) < 1.0e-5
        assert abs(
            runtime._node_output_value(
                runtime._named_active_node(scene, "MainFX_RimLight_Mix")
            )
            - 0.6
        ) < 1.0e-5
        reloaded_mattes = [
            node
            for node in runtime._active_mainfx_nodes(scene)
            if node.bl_idname == "CompositorNodeCryptomatteV2"
            and node.label in {"MainFX_matte", "MainFX_mattle"}
        ]
        assert len(reloaded_mattes) == len(matte_nodes)
        assert all(node.matte_id == "CharacterMesh" for node in reloaded_mattes)
        reloaded_viewers = [
            node
            for node in runtime._scene_compositor_tree(scene).nodes
            if node.bl_idname == "CompositorNodeViewer"
            and bool(node.get(runtime.VIEWER_NODE_KEY, False))
        ]
        assert len(reloaded_viewers) == 1
        assert not reloaded_viewers[0].mute
        assert len(reloaded_viewers[0].inputs["Image"].links) == 1
        assert compositor_tree.get(runtime.ACTIVE_GENERATION_KEY) == original_generation
        assert {
            node.as_pointer() for node in runtime._active_mainfx_nodes(scene)
        } == original_mainfx_pointers

        # Additional characters share the current values and only change the
        # Cryptomatte membership list; no MainFX nodes are rebuilt.
        second_armature_data = bpy.data.armatures.new("SecondCharacterRig")
        second_armature = bpy.data.objects.new(
            "SecondCharacterRig", second_armature_data
        )
        scene.collection.objects.link(second_armature)
        second_mesh_data = bpy.data.meshes.new("SecondCharacterMesh")
        second_mesh = bpy.data.objects.new("SecondCharacterMesh", second_mesh_data)
        scene.collection.objects.link(second_mesh)
        second_modifier = second_mesh.modifiers.new("Armature", "ARMATURE")
        second_modifier.object = second_armature
        second_avatar = scene.yugo_avatars.add()
        second_avatar.armature = second_armature
        scene.yugo_avatar_index = 1

        second_avatar.composing_enabled = True
        assert second_avatar.composing_enabled is True, settings.last_error
        assert abs(settings.secondary_shadow.blur - 12.0) < 1.0e-5
        assert all(
            node.matte_id == "CharacterMesh,SecondCharacterMesh"
            for node in reloaded_mattes
        ), [node.matte_id for node in reloaded_mattes]
        assert {
            node.as_pointer() for node in runtime._active_mainfx_nodes(scene)
        } == original_mainfx_pointers
        settings.secondary_shadow.blur = 7.0

        second_avatar.composing_enabled = False
        assert all(node.matte_id == "CharacterMesh" for node in reloaded_mattes)
        assert abs(settings.secondary_shadow.blur - 7.0) < 1.0e-5

        avatar.composing_enabled = False
        assert all(node.matte_id == "" for node in reloaded_mattes)
        avatar.composing_enabled = True
        assert abs(settings.secondary_shadow.blur - 7.0) < 1.0e-5
        assert all(node.matte_id == "CharacterMesh" for node in reloaded_mattes)
        assert {
            node.as_pointer() for node in runtime._active_mainfx_nodes(scene)
        } == original_mainfx_pointers

        print("Yugo Composing UI smoke test passed")
    finally:
        yugo.unregister()


main()
