from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()
from yugu_node_studio import outline_bake as addon
from bl_ext.blender_org.vrm.editor.mtoon1.property_group import (
    convert_material_to_mtoon1,
    refresh_mtoon1_outline,
)


avatar_collection = bpy.data.collections.new("AvatarMulti")
bpy.context.scene.collection.children.link(avatar_collection)
armature_data = bpy.data.armatures.new("ArmatureMulti")
armature = bpy.data.objects.new("ArmatureMulti", armature_data)
avatar_collection.objects.link(armature)

bpy.ops.mesh.primitive_cube_add()
source = bpy.context.object
for collection in tuple(source.users_collection):
    collection.objects.unlink(source)
avatar_collection.objects.link(source)
source.name = "MultiMaterialBody"
source.shape_key_add(name="Basis")
source.shape_key_add(name="Expression")

armature_modifier = source.modifiers.new("Armature", "ARMATURE")
armature_modifier.object = armature

materials = []
for material_name in ("MultiA", "MultiB"):
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    source.data.materials.append(material)
    convert_material_to_mtoon1(material, bpy.context)
    mtoon1 = material.vrm_addon_extension.mtoon1
    mtoon1.enabled = True
    mtoon = mtoon1.extensions.vrmc_materials_mtoon
    mtoon.outline_width_mode = mtoon.OUTLINE_WIDTH_MODE_WORLD_COORDINATES.identifier
    mtoon.outline_width_factor = 0.02
    mtoon.enable_outline_preview = True
    materials.append(material)

for polygon in source.data.polygons:
    polygon.material_index = polygon.index % 2

refresh_mtoon1_outline(bpy.context, create_modifier=True)
assert sum(addon.is_mtoon_outline_modifier(m) for m in source.modifiers) == 2

for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
source.select_set(True)
bpy.context.view_layer.objects.active = source

created, errors = addon.apply_selected_outlines(bpy.context)
assert not errors, errors
assert len(created) == 1
result = created[0]
assert len(result.data.polygons) == len(source.data.polygons), (
    len(result.data.polygons),
    len(source.data.polygons),
)
assert len(result.data.materials) == 1
assert result.data.materials[0].name.startswith(addon.OUTLINE_MATERIAL_NAME)
assert source.data.shape_keys is not None
assert result.data.shape_keys is not None
assert [key.name for key in result.data.shape_keys.key_blocks] == [
    "Basis",
    "Expression",
]
assert not any(addon.is_mtoon_outline_modifier(m) for m in source.modifiers)
for material in materials:
    mtoon = material.vrm_addon_extension.mtoon1.extensions.vrmc_materials_mtoon
    assert not mtoon.enable_outline_preview

print("YUGO_OUTLINE_MULTI_MATERIAL_TEST_OK")
