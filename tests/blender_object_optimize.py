from pathlib import Path
import sys

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yugo
from yugo import object_optimize


yugo.register()


def select_only(obj):
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


operator_rna = bpy.ops.yugo.optimize.get_rna_type().properties
assert operator_rna["use_mtoon"].default is True
assert abs(operator_rna["shadow_mix"].default - 0.5) < 1.0e-6
assert operator_rna["use_mesh"].default is True
assert abs(operator_rna["merge_distance"].default - 0.0001) < 1.0e-6
assert abs(operator_rna["decimate_ratio"].default - 0.5) < 1.0e-6
assert operator_rna["use_outline_to_mesh"].default is True
assert operator_rna["use_collection_arrange"].default is False

# Merge by Distance edits the mesh datablock directly.
merge_mesh = bpy.data.meshes.new("YugoMergeMesh")
merge_mesh.from_pydata(
    [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
    [(0, 2), (1, 2)],
    [],
)
merge_obj = bpy.data.objects.new("YugoMergeObject", merge_mesh)
bpy.context.scene.collection.objects.link(merge_obj)
select_only(merge_obj)
merge_result = object_optimize.optimize_selected_meshes(bpy.context, 0.0001, 1.0)
assert not merge_result.errors, merge_result.errors
assert merge_result.merged_objects == 1
assert len(merge_mesh.vertices) == 2, len(merge_mesh.vertices)
assert len(merge_obj.modifiers) == 0

# Decimate uses Blender's Edit Mode Decimate Geometry operation and leaves no
# modifier behind.
decimate_mesh = bpy.data.meshes.new("YugoDecimateMesh")
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=10, y_segments=10, size=2.0)
bm.to_mesh(decimate_mesh)
bm.free()
decimate_obj = bpy.data.objects.new("YugoDecimateObject", decimate_mesh)
bpy.context.scene.collection.objects.link(decimate_obj)
select_only(decimate_obj)
before_faces = len(decimate_mesh.polygons)
decimate_result = object_optimize.optimize_selected_meshes(
    bpy.context,
    0.0001,
    0.5,
)
assert not decimate_result.errors, decimate_result.errors
assert decimate_result.merged_objects == 1
assert decimate_result.decimated_objects == 1
assert len(decimate_mesh.polygons) < before_faces, (
    len(decimate_mesh.polygons),
    before_faces,
)
assert len(decimate_obj.modifiers) == 0
assert bpy.context.mode == "OBJECT"
assert bpy.context.view_layer.objects.active == decimate_obj
assert decimate_obj.select_get()

# Topology operations are skipped when Shape Keys exist.
shape_mesh = bpy.data.meshes.new("YugoShapeMesh")
shape_mesh.from_pydata(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
    [],
    [(0, 1, 2)],
)
shape_obj = bpy.data.objects.new("YugoShapeObject", shape_mesh)
bpy.context.scene.collection.objects.link(shape_obj)
shape_obj.shape_key_add(name="Basis")
select_only(shape_obj)
shape_result = object_optimize.optimize_selected_meshes(bpy.context, 0.0001, 0.5)
assert shape_result.merged_objects == 0
assert shape_result.decimated_objects == 0
assert len(shape_mesh.vertices) == 3
assert any("Shape Keys" in error for error in shape_result.errors)

# Collection Arrange creates the requested hierarchy, moves related objects,
# and removes the emptied importer-generated collider collection.
scene = bpy.context.scene
armature_data = bpy.data.armatures.new("YugoArrangeRigData")
armature = bpy.data.objects.new("YugoArrangeRig", armature_data)
scene.collection.objects.link(armature)

body_mesh = bpy.data.meshes.new("YugoArrangeBodyData")
body = bpy.data.objects.new("YugoArrangeBody", body_mesh)
scene.collection.objects.link(body)
armature_modifier = body.modifiers.new("Armature", "ARMATURE")
armature_modifier.object = armature

accessory = bpy.data.objects.new("YugoArrangeAccessory", None)
scene.collection.objects.link(accessory)
accessory.parent = armature

old_colliders = bpy.data.collections.new("Colliders")
scene.collection.children.link(old_colliders)
collider = bpy.data.objects.new("YugoArrangeCollider", None)
old_colliders.objects.link(collider)
collider_child = bpy.data.objects.new("YugoArrangeColliderEnd", None)
old_colliders.objects.link(collider_child)
collider_child.parent = collider

arranged = object_optimize._arrange_collections(
    scene,
    armature,
    "AvatarName",
    {body},
    set(),
    {collider, collider_child},
)
top = bpy.data.collections["AvatarName"]
mesh_collection = bpy.data.collections["AvatarName_mesh"]
collider_collection = bpy.data.collections["AvatarName_vrm1_collider"]
assert top.name in scene.collection.children
assert mesh_collection.name in top.children
assert collider_collection.name in top.children
assert tuple(armature.users_collection) == (top,)
assert tuple(accessory.users_collection) == (top,)
assert tuple(body.users_collection) == (mesh_collection,)
assert tuple(collider.users_collection) == (collider_collection,)
assert tuple(collider_child.users_collection) == (collider_collection,)
assert bpy.data.collections.get("Colliders") is None
assert arranged.related_objects == 5
assert arranged.mesh_objects == 1
assert arranged.vrm1_colliders == 2
assert arranged.removed_collections == 1

print("YUGO_OBJECT_OPTIMIZE_TEST_OK")
