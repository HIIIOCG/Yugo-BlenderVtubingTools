from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio
from yugu_node_studio import mtoon_optimize as addon, scheduler

yugu_node_studio.register()


# Match the official VRM Add-on property hierarchy without depending on a
# user-installed extension being importable under --factory-startup.
fake_mtoon = SimpleNamespace(shade_color_factor=(0.8, 0.6, 0.4))
fake_material = SimpleNamespace(
    vrm_addon_extension=SimpleNamespace(
        mtoon1=SimpleNamespace(
            extensions=SimpleNamespace(vrmc_materials_mtoon=fake_mtoon)
        )
    )
)
addon._apply_shadow_mix_once(fake_material, None, 0.5)
for actual, expected in zip(fake_mtoon.shade_color_factor, (0.4, 0.3, 0.2)):
    assert abs(actual - expected) < 0.0001


def add_interface_socket(group, name, socket_type):
    return group.interface.new_socket(
        name=name,
        in_out="INPUT",
        socket_type=socket_type,
    )


material = bpy.data.materials.new("Body_mtoon")
material.use_nodes = True
tree = material.node_tree
tree.nodes.clear()

official_group = bpy.data.node_groups.new(
    addon.OFFICIAL_OUTPUT_GROUP_NAME,
    "ShaderNodeTree",
)
add_interface_socket(official_group, "Shading Toony", "NodeSocketFloat")
add_interface_socket(official_group, "Lit Color", "NodeSocketColor")
add_interface_socket(official_group, "Shade Color", "NodeSocketColor")
add_interface_socket(official_group, "MatCap Factor", "NodeSocketColor")

official = tree.nodes.new("ShaderNodeGroup")
official.node_tree = official_group
official.name = addon.OFFICIAL_OUTPUT_NODE_NAME
official.inputs["Shading Toony"].default_value = 0.63
official.inputs["Lit Color"].default_value = (0.1, 0.2, 0.3, 0.4)
official.inputs["Shade Color"].default_value = (0.5, 0.6, 0.7, 0.8)
official.inputs["MatCap Factor"].default_value = (0.25, 0.35, 0.45, 0.55)

base_image = bpy.data.images.new("Base", width=8, height=8)
base_node = tree.nodes.new("ShaderNodeTexImage")
base_node.name = "Mtoon1BaseColorTexture.Image"
base_node.label = "Lit Color Texture"
base_node.image = base_image

shade_image = bpy.data.images.new("Shade", width=8, height=8)
shade_node = tree.nodes.new("ShaderNodeTexImage")
shade_node.name = "Mtoon1ShadeMultiplyTexture.Image"
shade_node.label = "Shade Color Texture"
shade_node.image = shade_image

matcap_image = bpy.data.images.new("MatCap", width=8, height=8)
matcap_node = tree.nodes.new("ShaderNodeTexImage")
matcap_node.name = "Mtoon1MatcapTexture.Image"
matcap_node.label = "MatCap Texture"
matcap_node.image = matcap_image

old_output = tree.nodes.new("ShaderNodeOutputMaterial")
old_output.is_active_output = True
native_shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
tree.links.new(native_shader.outputs["BSDF"], old_output.inputs["Surface"])

mesh = bpy.data.meshes.new("Mesh")
obj = bpy.data.objects.new("Object", mesh)
bpy.context.scene.collection.objects.link(obj)
mesh.materials.append(material)
for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj

assert addon.selected_object_count(bpy.context) == 1
assert addon.selected_mtoon_material_count(bpy.context) == 1
assert bpy.ops.yugu_node_studio.optimize(
) == {"FINISHED"}
assert material.name == "Body_MToonCustom", material.name
assert material.surface_render_method == "BLENDED"
assert addon.LEGACY_SHADOW_MIX_KEY not in tree
assert addon._alpha_mode_requires_blended_render("BLEND")
assert addon._alpha_mode_requires_blended_render("Transparent")
assert addon._alpha_mode_requires_blended_render("MASK")
assert addon._alpha_mode_requires_blended_render("Cutout")
assert not addon._alpha_mode_requires_blended_render("OPAQUE")

# Alpha fallback must not change an opaque MToon material.
opaque_material = bpy.data.materials.new("Opaque_mtoon")
opaque_material.use_nodes = True
opaque_tree = opaque_material.node_tree
opaque_tree.nodes.clear()
opaque_official = opaque_tree.nodes.new("ShaderNodeGroup")
opaque_official.node_tree = official_group
opaque_official.name = addon.OFFICIAL_OUTPUT_NODE_NAME
opaque_official.inputs["Lit Color"].default_value = (0.1, 0.2, 0.3, 1.0)
assert not addon._set_blended_render_method(opaque_material, opaque_official)
assert opaque_material.surface_render_method == "DITHERED"

custom_nodes = [node for node in tree.nodes if bool(node.get(addon.NODE_TAG_KEY))]
assert custom_nodes
custom_output = tree.nodes.get(addon.CUSTOM_OUTPUT_NAME)
assert custom_output is not None
assert custom_output.target == "EEVEE"
assert custom_output.is_active_output
assert not old_output.is_active_output
assert any(
    link.from_node == native_shader and link.to_node == old_output
    for link in tree.links
), "Optimize changed an existing native material link"

custom_frame = tree.nodes.get(addon.CUSTOM_FRAME_NAME)
assert custom_frame is not None
assert custom_frame.bl_idname == "NodeFrame"
assert all(
    node == custom_frame or node.parent == custom_frame
    for node in custom_nodes
), "Every copied custom node must remain inside Frame_MToonCustom"
assert len(
    [
        link
        for link in tree.links
        if bool(link.from_node.get(addon.NODE_TAG_KEY))
        and bool(link.to_node.get(addon.NODE_TAG_KEY))
    ]
) == 16, "Not every internal preset link was copied"
assert all(
    node.data_type == "RGBA"
    for node in custom_nodes
    if node.bl_idname == "ShaderNodeMix"
)
assert not any(
    bool(getattr(link.from_socket, "is_unavailable", False))
    or bool(getattr(link.to_socket, "is_unavailable", False))
    for link in tree.links
    if bool(link.from_node.get(addon.NODE_TAG_KEY))
    and bool(link.to_node.get(addon.NODE_TAG_KEY))
), "A preset link was connected to an inactive Mix socket"

custom_value = next(
    node
    for node in custom_nodes
    if node.name == "Shading Toony_MToonCustom"
    or node.label == "Shading Toony_MToonCustom"
)
custom_lit = next(
    node
    for node in custom_nodes
    if node.name == "Lit Color_MToonCustom"
    or node.label == "Lit Color_MToonCustom"
)
custom_shade = next(
    node
    for node in custom_nodes
    if node.name == "Shade Color_MToonCustom"
    or node.label == "Shade Color_MToonCustom"
)
custom_matcap = next(
    node
    for node in custom_nodes
    if node.name == "MatCap Factor_MToonCustom"
    or node.label == "MatCap Factor_MToonCustom"
)
custom_base_texture = next(
    node
    for node in custom_nodes
    if node.name == "Mtoon1BaseColorTexture.Image_MToonCustom"
)
custom_shade_texture = next(
    node
    for node in custom_nodes
    if node.name == "Mtoon1ShadeMultiplyTexture.Image_MToonCustom"
)
custom_matcap_texture = next(
    node
    for node in custom_nodes
    if node.name == "Mtoon1MatcapTexture.Image_MToonCustom"
)

bpy.context.view_layer.update()
scheduler.run_system_once()
assert abs(custom_value.outputs[0].default_value - 0.63) < 0.0001
for index, expected in enumerate((0.1, 0.2, 0.3, 0.4)):
    assert abs(custom_lit.outputs[0].default_value[index] - expected) < 0.0001
for index, expected in enumerate((0.25, 0.3, 0.35, 0.8)):
    assert abs(custom_shade.outputs[0].default_value[index] - expected) < 0.0001
for index, expected in enumerate((0.25, 0.3, 0.35, 0.8)):
    assert abs(official.inputs["Shade Color"].default_value[index] - expected) < 0.0001
for index, expected in enumerate((0.25, 0.35, 0.45, 0.55)):
    assert abs(custom_matcap.outputs[0].default_value[index] - expected) < 0.0001
assert custom_base_texture != base_node
assert custom_shade_texture != shade_node
assert custom_matcap_texture != matcap_node
assert custom_base_texture.image == base_image
assert custom_shade_texture.image == shade_image
assert custom_matcap_texture.image == matcap_image

official.inputs["Shading Toony"].default_value = 0.21
official.inputs["Lit Color"].default_value = (0.9, 0.8, 0.7, 0.6)
official.inputs["MatCap Factor"].default_value = (0.15, 0.25, 0.35, 0.45)
new_base_image = bpy.data.images.new("Base Updated", width=8, height=8)
new_shade_image = bpy.data.images.new("Shade Updated", width=8, height=8)
new_matcap_image = bpy.data.images.new("MatCap Updated", width=8, height=8)
base_node.image = new_base_image
shade_node.image = new_shade_image
matcap_node.image = new_matcap_image
bpy.context.view_layer.update()
scheduler.run_system_once()
assert abs(custom_value.outputs[0].default_value - 0.21) < 0.0001
for index, expected in enumerate((0.9, 0.8, 0.7, 0.6)):
    assert abs(custom_lit.outputs[0].default_value[index] - expected) < 0.0001
for index, expected in enumerate((0.15, 0.25, 0.35, 0.45)):
    assert abs(custom_matcap.outputs[0].default_value[index] - expected) < 0.0001
assert custom_base_texture.image == new_base_image
assert custom_shade_texture.image == new_shade_image
assert custom_matcap_texture.image == new_matcap_image

# Native texture nodes never become part of the custom frame's wiring.
assert not any(
    link.from_node in {base_node, shade_node, matcap_node}
    and bool(link.to_node.get(addon.NODE_TAG_KEY))
    for link in tree.links
)

first_custom_node_count = len(custom_nodes)
optimized, errors = addon.optimize_selected_mtoon_materials(
    bpy.context,
    shadow_mix=0.25,
)
assert optimized == 1, errors
assert not errors, errors
assert material.name == "Body_MToonCustom"
assert len([node for node in tree.nodes if bool(node.get(addon.NODE_TAG_KEY))]) == first_custom_node_count
assert addon.LEGACY_SHADOW_MIX_KEY not in tree
updated_custom_shade = next(
    node
    for node in tree.nodes
    if bool(node.get(addon.NODE_TAG_KEY))
    and (
        node.name == "Shade Color_MToonCustom"
        or node.label == "Shade Color_MToonCustom"
    )
)
custom_output = tree.nodes.get(addon.CUSTOM_OUTPUT_NAME)
assert custom_output is not None
for index, expected in enumerate((0.25, 0.3, 0.35, 0.8)):
    assert abs(updated_custom_shade.outputs[0].default_value[index] - expected) < 0.0001

# Shadow Mix is absent from normal 2 Hz synchronization. Native Shade
# Color changes are copied directly, without applying the Optimize value again.
official.inputs["Shade Color"].default_value = (0.8, 0.6, 0.4, 0.7)
addon._sync_material(material)
for index, expected in enumerate((0.8, 0.6, 0.4, 0.7)):
    assert abs(updated_custom_shade.outputs[0].default_value[index] - expected) < 0.0001
assert custom_output.is_active_output
assert not old_output.is_active_output

# Files saved by 0.1.21 migrate their stored factor once, remove the old key,
# and finish with the custom output active.
tree[addon.LEGACY_SHADOW_MIX_KEY] = 0.25
old_output.is_active_output = True
addon._sync_material(material)
assert addon.LEGACY_SHADOW_MIX_KEY not in tree
for index, expected in enumerate((0.2, 0.15, 0.1, 0.7)):
    assert abs(official.inputs["Shade Color"].default_value[index] - expected) < 0.0001
    assert abs(updated_custom_shade.outputs[0].default_value[index] - expected) < 0.0001
assert custom_output.is_active_output
assert not old_output.is_active_output

# Explicit regression coverage for both dynamic ShaderNodeMix socket families.
mix_source_material = bpy.data.materials.new("Mix Source")
mix_source_material.use_nodes = True
mix_source_tree = mix_source_material.node_tree
mix_source_tree.nodes.clear()
mix_frame = mix_source_tree.nodes.new("NodeFrame")
mix_frame.name = addon.CUSTOM_FRAME_NAME
mix_frame.label = addon.CUSTOM_FRAME_NAME

color_a = mix_source_tree.nodes.new("ShaderNodeRGB")
color_a.parent = mix_frame
color_b = mix_source_tree.nodes.new("ShaderNodeRGB")
color_b.parent = mix_frame
color_mix = mix_source_tree.nodes.new("ShaderNodeMix")
color_mix.name = "Color Mix"
color_mix.data_type = "RGBA"
color_mix.parent = mix_frame
mix_source_tree.links.new(
    color_a.outputs["Color"],
    next(socket for socket in color_mix.inputs if socket.identifier == "A_Color"),
)
mix_source_tree.links.new(
    color_b.outputs["Color"],
    next(socket for socket in color_mix.inputs if socket.identifier == "B_Color"),
)

vector_a = mix_source_tree.nodes.new("ShaderNodeCombineXYZ")
vector_a.parent = mix_frame
vector_b = mix_source_tree.nodes.new("ShaderNodeCombineXYZ")
vector_b.parent = mix_frame
vector_factor = mix_source_tree.nodes.new("ShaderNodeCombineXYZ")
vector_factor.parent = mix_frame
vector_mix = mix_source_tree.nodes.new("ShaderNodeMix")
vector_mix.name = "Vector Mix"
vector_mix.data_type = "VECTOR"
vector_mix.factor_mode = "NON_UNIFORM"
vector_mix.parent = mix_frame
for source_node, identifier in (
    (vector_factor, "Factor_Vector"),
    (vector_a, "A_Vector"),
    (vector_b, "B_Vector"),
):
    mix_source_tree.links.new(
        source_node.outputs["Vector"],
        next(socket for socket in vector_mix.inputs if socket.identifier == identifier),
    )

mix_target_material = bpy.data.materials.new("Mix Target")
mix_target_material.use_nodes = True
mix_target_tree = mix_target_material.node_tree
mix_target_tree.nodes.clear()
_mix_map, mix_created_nodes = addon._clone_custom_nodes(
    mix_source_tree,
    mix_target_tree,
)
copied_color_mix = next(node for node in mix_created_nodes if node.name == "Color Mix")
copied_vector_mix = next(node for node in mix_created_nodes if node.name == "Vector Mix")
assert copied_color_mix.data_type == "RGBA"
assert copied_vector_mix.data_type == "VECTOR"
assert copied_vector_mix.factor_mode == "NON_UNIFORM"
assert {
    link.to_socket.identifier
    for link in mix_target_tree.links
    if link.to_node == copied_color_mix
} == {"A_Color", "B_Color"}
assert {
    link.to_socket.identifier
    for link in mix_target_tree.links
    if link.to_node == copied_vector_mix
} == {"Factor_Vector", "A_Vector", "B_Vector"}
assert not any(
    bool(getattr(link.from_socket, "is_unavailable", False))
    or bool(getattr(link.to_socket, "is_unavailable", False))
    for link in mix_target_tree.links
)

print("YUGUS_MTOON_CUSTOM_TEST_OK")
yugu_node_studio.unregister()
