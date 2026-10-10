"""Read saved DEV visual tags and explicitly rebuild with the legacy preset."""
from __future__ import annotations

import hashlib
import sys

sys.dont_write_bytecode = True

import bpy

from _bootstrap import LEGACY_PRESET_SHA256, load_node, package_directory


addon = load_node()
root = addon.root
mtoon = root.mtoon_optimize
comp = root.composing.runtime


def tags(owner):
    return tuple(sorted((key, repr(value)) for key, value in owner.items()))


def structure(tree):
    return (
        tuple(sorted((node.as_pointer(), node.name, node.label,
                      node.parent.as_pointer() if node.parent else 0, tags(node))
                     for node in tree.nodes)),
        tuple(sorted((link.from_socket.as_pointer(), link.to_socket.as_pointer())
                     for link in tree.links)),
        tags(tree),
    )


def custom_nodes(tree):
    return [node for node in tree.nodes
            if node.get("yugo_mtoon_custom_node") or node.get("yugus_mtoon_custom_node")]


def close_color(actual, expected):
    assert len(actual) == len(expected)
    assert all(abs(a - b) < 1e-5 for a, b in zip(actual, expected)), (tuple(actual), expected)


def native_material(group, name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    official = tree.nodes.new("ShaderNodeGroup")
    official.node_tree = group
    official.name = mtoon.OFFICIAL_OUTPUT_NODE_NAME
    official.inputs["Shading Toony"].default_value = 0.63
    official.inputs["Lit Color"].default_value = (0.1, 0.2, 0.3, 0.4)
    official.inputs["Shade Color"].default_value = (0.8, 0.6, 0.4, 0.7)
    official.inputs["MatCap Factor"].default_value = (0.2, 0.3, 0.4, 0.5)
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    mesh = bpy.data.meshes.new(name + " Mesh")
    obj = bpy.data.objects.new(name + " Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(material)
    return material, official, obj, shader, output


def select_only(obj):
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def test_shader_tags():
    # A synthetic native output layout keeps this regression independent of
    # the official VRM extension and private character files.
    group = bpy.data.node_groups.new(mtoon.OFFICIAL_OUTPUT_GROUP_NAME, "ShaderNodeTree")
    for name, kind in (("Shading Toony", "NodeSocketFloat"),
                       ("Lit Color", "NodeSocketColor"),
                       ("Shade Color", "NodeSocketColor"),
                       ("MatCap Factor", "NodeSocketColor")):
        group.interface.new_socket(name=name, in_out="INPUT", socket_type=kind)
    template = None
    try:
        for spelling in ("yugo", "yugus"):
            material, official, obj, shader, native_output = native_material(group, spelling + " Saved")
            tree = material.node_tree
            template = mtoon._append_template_material()
            mtoon.install_custom_shader(material, template, 0.5)
            bpy.data.materials.remove(template)
            template = None
            close_color(official.inputs["Shade Color"].default_value, (0.4, 0.3, 0.2, 0.7))
            if spelling == "yugus":
                tree["yugus_mtoon_custom_group"] = tree[mtoon.GROUP_TAG_KEY]
                del tree[mtoon.GROUP_TAG_KEY]
                for node in custom_nodes(tree):
                    node["yugus_mtoon_custom_node"] = node[mtoon.NODE_TAG_KEY]
                    del node[mtoon.NODE_TAG_KEY]
            initial_nodes = custom_nodes(tree)
            initial_pointers = {node.as_pointer() for node in initial_nodes}
            initial_count = len(initial_nodes)
            before_sync = structure(tree)
            shade = next(node for node in initial_nodes
                         if node.name == "Shade Color_MToonCustom" or node.label == "Shade Color_MToonCustom")
            native_color = (0.7, 0.6, 0.5, 0.9)
            official.inputs["Shade Color"].default_value = native_color
            mtoon._sync_material(material)
            close_color(shade.outputs[0].default_value, native_color)
            close_color(official.inputs["Shade Color"].default_value, native_color)
            assert structure(tree) == before_sync, "Reading saved tags rewrote nodes, links or tags"

            # Explicit Optimize replaces either tagged family with the one
            # legacy shader, without applying the saved shadow factor twice.
            select_only(obj)
            assert bpy.ops.yugu_node_studio.optimize(
                use_mtoon=True, use_outline_to_mesh=False, shadow_mix=0.25) == {"FINISHED"}
            close_color(official.inputs["Shade Color"].default_value, native_color)
            assert len(custom_nodes(tree)) == initial_count
            assert not initial_pointers.intersection(node.as_pointer() for node in tree.nodes)
            assert all(node.get(mtoon.NODE_TAG_KEY) and "yugus_mtoon_custom_node" not in node
                       for node in custom_nodes(tree))
            assert len([node for node in tree.nodes if node.bl_idname == "NodeFrame"
                        and (node.name == mtoon.CUSTOM_FRAME_NAME or node.label == mtoon.CUSTOM_FRAME_NAME)]) == 1
            assert any(link.from_node == shader and link.to_node == native_output for link in tree.links)
            count_after_rebuild = len(tree.nodes)
            assert bpy.ops.yugu_node_studio.optimize(
                use_mtoon=True, use_outline_to_mesh=False, shadow_mix=0.1) == {"FINISHED"}
            assert len(tree.nodes) == count_after_rebuild
            close_color(official.inputs["Shade Color"].default_value, native_color)

        # A matching node name alone must not opt an untagged material into sync.
        material, official, _obj, _shader, _output = native_material(group, "Untagged")
        shade = material.node_tree.nodes.new("ShaderNodeRGB")
        shade.name = "Shade Color_MToonCustom"
        shade.outputs[0].default_value = (0.11, 0.12, 0.13, 0.14)
        before_sync = structure(material.node_tree)
        mtoon._sync_material(material)
        close_color(shade.outputs[0].default_value, (0.11, 0.12, 0.13, 0.14))
        assert structure(material.node_tree) == before_sync
    finally:
        if template is not None:
            bpy.data.materials.remove(template)


def test_compositor_tags():
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new("Saved DEV compositor", "CompositorNodeTree")
    scene.compositing_node_group = tree
    user_source = tree.nodes.new("CompositorNodeRGB")
    user_viewer = tree.nodes.new("CompositorNodeViewer")
    user_viewer.name = "User Viewer"
    tree.links.new(user_source.outputs[0], user_viewer.inputs["Image"])
    user_pointer = user_viewer.as_pointer()
    user_tags = tags(user_viewer)
    assert bpy.ops.yugu_node_studio.composer_reload() == {"FINISHED"}
    active = tuple(comp._active_mainfx_nodes(scene))
    assert active
    original_pointers = {node.as_pointer() for node in active}
    for node in active:
        node["yugus_composing_mainfx_generation"] = node[comp.NODE_GENERATION_KEY]
        del node[comp.NODE_GENERATION_KEY]
    old_generation = tree[comp.ACTIVE_GENERATION_KEY]
    tree["yugus_composing_mainfx_active_generation"] = old_generation
    del tree[comp.ACTIVE_GENERATION_KEY]
    owned_viewer = next(node for node in tree.nodes if node.get(comp.VIEWER_NODE_KEY))
    owned_viewer["yugus_composing_viewer"] = owned_viewer[comp.VIEWER_NODE_KEY]
    del owned_viewer[comp.VIEWER_NODE_KEY]
    owned_pointer = owned_viewer.as_pointer()
    count_before_rebuild = len(tree.nodes)
    viewer_count_before_rebuild = sum(node.bl_idname == "CompositorNodeViewer" for node in tree.nodes)
    stale = tree.nodes.new("NodeFrame")
    stale["yugus_composing_mainfx_generation"] = "obsolete-dev-generation"
    stale_pointer = stale.as_pointer()
    before_read = structure(tree)
    assert {node.as_pointer() for node in comp._active_mainfx_nodes(scene)} == original_pointers
    assert structure(tree) == before_read, "Reading DEV compositor generations mutated saved metadata"
    scene.yugo_composing.rim_light.intensity = 0.37

    for iteration in range(2):
        assert bpy.ops.yugu_node_studio.composer_reload() == {"FINISHED"}
        assert len(tree.nodes) == count_before_rebuild, "Repeated rebuild accumulated compositor nodes"
        pointers = {node.as_pointer() for node in tree.nodes}
        if iteration == 0:
            assert not original_pointers.intersection(pointers) and stale_pointer not in pointers
        viewers = [node for node in tree.nodes if node.bl_idname == "CompositorNodeViewer"]
        assert len(viewers) == viewer_count_before_rebuild
        assert {owned_pointer, user_pointer}.issubset(node.as_pointer() for node in viewers)
        assert sum(bool(node.get("yugo_composing_viewer") or node.get("yugus_composing_viewer"))
                   for node in viewers) == 1
        assert owned_viewer.get("yugus_composing_viewer") and tags(user_viewer) == user_tags
        assert any(link.from_node == user_source and link.to_node == user_viewer for link in tree.links)
        assert tree["yugus_composing_mainfx_active_generation"] == old_generation
        assert tree[comp.ACTIVE_GENERATION_KEY] != old_generation
        new_active = tuple(comp._active_mainfx_nodes(scene))
        assert len(new_active) == len(active)
        assert all(node.get(comp.NODE_GENERATION_KEY) == tree[comp.ACTIVE_GENERATION_KEY]
                   and "yugus_composing_mainfx_generation" not in node for node in new_active)
        close_color((scene.yugo_composing.rim_light.intensity,), (0.37,))
        intensity_node = comp._named_active_node(scene, "MainFX_RimLight_Mix")
        assert intensity_node is not None
        close_color((intensity_node.outputs[0].default_value,), (0.37,))


root.register()
try:
    test_shader_tags()
    test_compositor_tags()
    preset = package_directory() / "preset.blend"
    assert hashlib.sha256(preset.read_bytes()).hexdigest() == LEGACY_PRESET_SHA256
finally:
    root.unregister()

print("FUSION_VISUAL_COMPATIBILITY_OK", flush=True)
