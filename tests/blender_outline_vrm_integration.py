from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yugo import outline_bake as addon
from bl_ext.blender_org.vrm.editor.mtoon1.property_group import (
    convert_material_to_mtoon1,
    refresh_mtoon1_outline,
)


avatar_collection = bpy.data.collections.new("Avatar")
bpy.context.scene.collection.children.link(avatar_collection)
armature_data = bpy.data.armatures.new("Armature")
armature = bpy.data.objects.new("Armature", armature_data)
avatar_collection.objects.link(armature)

bpy.ops.mesh.primitive_cube_add()
source = bpy.context.object
for collection in tuple(source.users_collection):
    collection.objects.unlink(source)
avatar_collection.objects.link(source)
source.name = "VRMBody"
source.shape_key_add(name="Basis")
expression = source.shape_key_add(name="Expression")
expression.data[0].co.x += 0.1
expression.value = 0.25

armature_modifier = source.modifiers.new("Armature", "ARMATURE")
armature_modifier.object = armature

material = bpy.data.materials.new("VRMBodyMaterial")
material.use_nodes = True
source.data.materials.append(material)
convert_material_to_mtoon1(material, bpy.context)
mtoon1 = material.vrm_addon_extension.mtoon1
mtoon1.enabled = True
mtoon = mtoon1.extensions.vrmc_materials_mtoon
mtoon.outline_width_mode = mtoon.OUTLINE_WIDTH_MODE_WORLD_COORDINATES.identifier
mtoon.outline_width_factor = 0.02
mtoon.enable_outline_preview = True
refresh_mtoon1_outline(bpy.context, create_modifier=True)

assert any(addon.is_mtoon_outline_modifier(m) for m in source.modifiers)
for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
source.select_set(True)
bpy.context.view_layer.objects.active = source

created, errors = addon.apply_selected_outlines(bpy.context)
assert not errors, errors
assert len(created) == 1
result = created[0]
assert len(result.data.polygons) > 0
assert source.data.shape_keys is not None
assert result.data.shape_keys is not None
assert [key.name for key in result.data.shape_keys.key_blocks] == [
    "Basis",
    "Expression",
]
expression.value = 0.8
bpy.context.view_layer.update()
assert abs(result.data.shape_keys.key_blocks["Expression"].value - 0.8) < 0.000001
assert any(modifier.type == "ARMATURE" for modifier in result.modifiers)
assert not any(addon.is_mtoon_outline_modifier(m) for m in result.modifiers)
assert not any(addon.is_mtoon_outline_modifier(m) for m in source.modifiers)
assert not mtoon.enable_outline_preview
assert result.data.materials[0].name.startswith(addon.OUTLINE_MATERIAL_NAME)
assert result.name in avatar_collection.children[addon.OUTLINE_COLLECTION_NAME].objects

print("YUGO_OUTLINE_VRM_INTEGRATION_TEST_OK")
