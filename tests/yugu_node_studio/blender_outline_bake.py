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

import yugu_node_studio
from yugu_node_studio import outline_bake as addon


yugu_node_studio.register()


def new_input(group, name, socket_type):
    return group.interface.new_socket(
        name=name,
        in_out="INPUT",
        socket_type=socket_type,
    )


def new_output(group, name, socket_type):
    return group.interface.new_socket(
        name=name,
        in_out="OUTPUT",
        socket_type=socket_type,
    )


source_material = bpy.data.materials.new("Body")
fixture_material = bpy.data.materials.new(addon.OUTLINE_MATERIAL_NAME)
fixture_path = Path(bpy.app.tempdir) / "yugus_outline_test_preset.blend"
bpy.data.libraries.write(str(fixture_path), {fixture_material})
bpy.data.materials.remove(fixture_material)
addon.PRESET_PATH_OVERRIDE = fixture_path

group = bpy.data.node_groups.new(addon.OUTLINE_NODE_GROUP_NAME, "GeometryNodeTree")
new_input(group, "Geometry", "NodeSocketGeometry")
new_input(group, "Material", "NodeSocketMaterial")
new_input(group, "Outline Material", "NodeSocketMaterial")
new_input(group, "Outline Width Mode", "NodeSocketInt")
new_input(group, "Outline Width Factor", "NodeSocketFloat")
new_input(group, "Outline Width Texture", "NodeSocketImage")
new_input(group, "Outline Width Texture Exists", "NodeSocketBool")
new_input(group, "UV", "NodeSocketVector")
new_input(group, "UV Offset X", "NodeSocketFloat")
new_input(group, "UV Offset Y", "NodeSocketFloat")
new_input(group, "UV Scale X", "NodeSocketFloat")
new_input(group, "UV Scale Y", "NodeSocketFloat")
new_input(group, "Extrude Individual", "NodeSocketBool")
new_input(group, "Object", "NodeSocketObject")
new_input(group, "Enabled", "NodeSocketBool")
new_output(group, "Geometry", "NodeSocketGeometry")

group_input = group.nodes.new("NodeGroupInput")
group_output = group.nodes.new("NodeGroupOutput")
transform = group.nodes.new("GeometryNodeTransform")
transform.inputs["Scale"].default_value = (1.1, 1.1, 1.1)
set_material = group.nodes.new("GeometryNodeSetMaterial")
join = group.nodes.new("GeometryNodeJoinGeometry")
group.links.new(group_input.outputs["Geometry"], transform.inputs["Geometry"])
group.links.new(transform.outputs["Geometry"], set_material.inputs["Geometry"])
group.links.new(
    group_input.outputs["Outline Material"], set_material.inputs["Material"]
)
group.links.new(group_input.outputs["Geometry"], join.inputs["Geometry"])
group.links.new(set_material.outputs["Geometry"], join.inputs["Geometry"])
group.links.new(join.outputs["Geometry"], group_output.inputs["Geometry"])

avatar_collection = bpy.data.collections.new("Avatar")
bpy.context.scene.collection.children.link(avatar_collection)
armature_data = bpy.data.armatures.new("Armature")
armature = bpy.data.objects.new("Armature", armature_data)
avatar_collection.objects.link(armature)

source_mesh = bpy.data.meshes.new("FaceMesh")
source_mesh.from_pydata(
    [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (-1.0, 0.0, 0.0),
    ],
    [],
    [(0, 1, 2), (3, 4, 5)],
)
source = bpy.data.objects.new("Face", source_mesh)
avatar_collection.objects.link(source)
source.data.materials.append(source_material)
source.parent = armature
source.shape_key_add(name="Basis")
smile = source.shape_key_add(name="Smile")
smile.data[0].co.x += 0.2
smile.value = 0.5

bone_weights = source.vertex_groups.new(name="Bone")
bone_weights.add(range(len(source.data.vertices)), 1.0, "REPLACE")

armature_modifier = source.modifiers.new("Armature", "ARMATURE")
armature_modifier.object = armature
outline_modifier = source.modifiers.new("MToon Outline (Body)", "NODES")
outline_modifier.node_group = group
addon._set_modifier_input(outline_modifier, "Material", 1, source_material)
addon._set_modifier_input(outline_modifier, "Outline Material", 2, source_material)
addon._set_modifier_input(outline_modifier, "Object", 13, source)

for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
source.select_set(True)
bpy.context.view_layer.objects.active = source

assert bpy.ops.yugu_node_studio.optimize(
    use_mtoon=False,
    use_outline_to_mesh=True,
) == {"FINISHED"}
result = bpy.data.objects.get("Face_OutlineMesh")
assert result is not None
assert result.name == "Face_OutlineMesh", result.name
assert source.data.shape_keys is not None
assert len(source.data.vertices) == 6
assert len(source.data.shape_keys.key_blocks) == 2
assert result.data.shape_keys is not None
assert [key.name for key in result.data.shape_keys.key_blocks] == ["Basis", "Smile"]
assert len(result.data.vertices) == 4, len(result.data.vertices)
result_basis = result.data.shape_keys.key_blocks["Basis"]
result_smile = result.data.shape_keys.key_blocks["Smile"]
assert any(
    (basis.co - shaped.co).length > 0.000001
    for basis, shaped in zip(result_basis.data, result_smile.data)
)
smile.value = 0.75
bpy.context.view_layer.update()
assert abs(result_smile.value - 0.75) < 0.000001, result_smile.value
assert result.parent == armature
assert result.vertex_groups.get("Bone") is not None
assert len(result.data.polygons) == 2, len(result.data.polygons)
assert len(result.data.materials) == 1
assert result.data.materials[0].name.startswith(addon.OUTLINE_MATERIAL_NAME)
assert any(modifier.type == "ARMATURE" for modifier in result.modifiers)
assert not any(addon.is_mtoon_outline_modifier(m) for m in result.modifiers)
assert not any(addon.is_mtoon_outline_modifier(m) for m in source.modifiers)

plain = source.copy()
plain.data = source.data.copy()
plain.name = "FaceNoShapes"
avatar_collection.objects.link(plain)
addon._remove_all_shape_keys(plain)
plain_outline = plain.modifiers.new("MToon Outline (Body)", "NODES")
plain_outline.node_group = group
addon._set_modifier_input(plain_outline, "Material", 1, source_material)
addon._set_modifier_input(plain_outline, "Outline Material", 2, source_material)
addon._set_modifier_input(plain_outline, "Object", 13, plain)
for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
plain.select_set(True)
bpy.context.view_layer.objects.active = plain
plain_created, plain_errors = addon.apply_selected_outlines(bpy.context)
assert not plain_errors, plain_errors
assert len(plain_created) == 1
plain_result = plain_created[0]
assert plain_result.data.shape_keys is None
assert len(plain_result.data.vertices) == 4

outline_collection = avatar_collection.children.get(addon.OUTLINE_COLLECTION_NAME)
assert outline_collection is not None
assert result.name in outline_collection.objects
assert tuple(result.users_collection) == (outline_collection,)
assert armature.name in avatar_collection.objects
assert outline_collection.name in avatar_collection.children
assert not any(obj.name.startswith("__YugoOutlineBake__") for obj in bpy.data.objects)
assert not any(obj.name.startswith("__YugoOutlineReference__") for obj in bpy.data.objects)
assert not any(obj.name.startswith("__YugoOutlineEvaluate__") for obj in bpy.data.objects)
assert not any(
    group.name.startswith("__YugoOutlineRealized__")
    for group in bpy.data.node_groups
)

print("YUGO_OUTLINE_BAKE_TEST_OK")
