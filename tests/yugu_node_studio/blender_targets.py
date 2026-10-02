"""Explicit targets, cached character membership, End execution and migration."""
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
from mathutils import Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, targets, runtime, vrm_cache, nodes, ui
from blender_integration import make_armature, make_face
from blender_composing import LayoutRecorder

addon.register()
rig_a, rig_b = make_armature(), make_armature()
face_a, _ = make_face()
face_b, _ = make_face()
empty = bpy.data.objects.new("Unsupported shape target", None)
bpy.context.scene.collection.objects.link(empty)
face_a.modifiers.new("Skin", "ARMATURE").object = rig_a
empty.parent = rig_a
tree = bpy.data.node_groups.new("Targets", "YUGUS_NT_humanoid_motion")
data = tree.nodes.new("YUGUS_NODE_vrm_data")
arm = tree.nodes.new("YUGUS_NODE_armature_value")
objects = tree.nodes.new("YUGUS_NODE_objects_value")
clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
bone = tree.nodes.new("YUGUS_NODE_apply_rotation")
shape = tree.nodes.new("YUGUS_NODE_apply_shapes")
convert = tree.nodes.new("YUGUS_NODE_motion_to_rotation")
expr = tree.nodes.new("YUGUS_NODE_expression_to_shapes")

assert not hasattr(flow, "_pulse_counts")
assert not hasattr(ui, "YUGUS_PT_live_flow")
assert not (Path(addon.__file__).parent / "flow_monitor.py").exists()
assert flow.YUGUS_NS_shape_values.draw_color(None, None, None) == (0.5, 0.5, 0.5, 1)
assert bone.bl_label == "BoneRotations to Armature"
assert shape.bl_label == "ShapeKeysValues to Objects List"

# Input overrides the inline picker, and membership changes only on Reload.
data.armature = rig_b
arm.armature = rig_a
tree.links.new(arm.outputs["Armatures"], data.inputs["Armature"])
snapshot = vrm_cache.reload_node(data)
assert snapshot.armature_name == rig_a.name
assert face_a.name in snapshot.objects and empty.name in snapshot.objects
assert face_b.name not in snapshot.objects
face_b.parent = rig_a
assert face_b.name not in vrm_cache.get_snapshot(data).objects
snapshot = vrm_cache.reload_node(data)
assert face_b.name in snapshot.objects
face_b.parent = None
snapshot = vrm_cache.reload_node(data)
assert face_b.name not in snapshot.objects
value = runtime.EvaluationContext(tree, bpy.context.scene, 0).evaluate(data)
assert value["Armatures"].names == (rig_a.name,)
assert value["Objects"].names == snapshot.objects

# UI +/- only; legacy collections are flattened once without losing targets.
assert bpy.ops.yugu_node_studio.target_object(tree_name=tree.name, node_name=objects.name) == {"FINISHED"}
objects.objects[0].object = face_b
objects.objects.add().object = empty
collection = bpy.data.collections.new("Target selection")
bpy.context.scene.collection.children.link(collection)
collection.objects.link(face_b)
assert not hasattr(objects, "collection") and not hasattr(shape, "collection")
objects["collection"] = collection
nodes.ensure_current_sockets()
assert "collection" not in objects
assert targets.selected_objects(objects).names == (face_b.name, empty.name)
assert bpy.ops.yugu_node_studio.target_object(tree_name=tree.name, node_name=objects.name, index=1) == {"FINISHED"}
assert targets.selected_objects(objects).names == (face_b.name,)

# Explicit manual targets retarget bone/shape names, not the packet's objects.
q = Quaternion((0, 0, 1), 0.4)
rotations = flow.BoneRotations(rig_a.name, {"head": q})
bone.armature = rig_b
flow.apply(bone, rotations)
assert abs(rig_b.pose.bones["head"].rotation_quaternion.dot(q)) > 0.99999
assert rig_a.pose.bones["head"].rotation_quaternion.angle < 1e-5
flow.release(str(bone.runtime_id))
weights = flow.ShapeValues({(face_a.name, "Smile"): 0.65})
shape.objects.add().object = face_b
shape.objects.add().object = empty
flow.apply(shape, weights)
assert abs(face_b.data.shape_keys.key_blocks["Smile"].value - 0.65) < 1e-6
assert face_a.data.shape_keys.key_blocks["Smile"].value == 0
flow.release(str(shape.runtime_id))

# A lattice also supports shape keys: capability detection is not Mesh-only.
lattice = bpy.data.objects.new("Lattice target", bpy.data.lattices.new("Lattice"))
bpy.context.scene.collection.objects.link(lattice)
lattice.shape_key_add(name="Basis")
lattice.shape_key_add(name="Smile").value = 0
shape.objects.add().object = lattice
flow.apply(shape, weights)
assert abs(lattice.data.shape_keys.key_blocks["Smile"].value - 0.65) < 1e-6
flow.release(str(shape.runtime_id))

# Linked target overrides manual selection. End propagates only after success.
arm.armature = rig_b
tree.links.new(arm.outputs["Armatures"], bone.inputs["Armatures"])
tree.links.new(objects.outputs["Objects"], shape.inputs["Objects"])
tree.links.new(clock.outputs["Trigger"], bone.inputs["Enter"])
tree.links.new(bone.outputs["End"], shape.inputs["Enter"])
tree.links.new(convert.outputs["Bone Rotation"], bone.inputs["Bone Rotation"])
tree.links.new(expr.outputs["Shape Keys Values"], shape.inputs["Shape Keys Values"])
old_evaluate = runtime._evaluate_node
order = []
def fake(context, node):
    if node == convert:
        order.append("bone")
        return {"Bone Rotation": rotations}
    if node == expr:
        assert abs(rig_b.pose.bones["head"].rotation_quaternion.dot(q)) > 0.99999
        order.append("shape")
        return {"Shape Keys Values": weights}
    return old_evaluate(context, node)
runtime._evaluate_node = fake
clock.hz = "30"
flow.tick(0)
flow.tick(1/30)
assert order == ["bone", "shape"], order
assert abs(face_b.data.shape_keys.key_blocks["Smile"].value - 0.65) < 1e-6
assert lattice.data.shape_keys.key_blocks["Smile"].value == 0

arm.armature = None
order.clear()
flow.tick(2/30)
assert order == ["bone"] and "Blocked" in shape.last_summary
assert face_b.data.shape_keys.key_blocks["Smile"].value == 0
arm.armature = rig_b
flow.tick(3/30)
assert order == ["bone", "bone", "shape"]

# Old Trigger link is renamed in-place, retaining its connection.
bone.inputs["Enter"].name = "Trigger"
bone.inputs.remove(bone.inputs["Armatures"])
data.outputs.remove(data.outputs["Objects"])
nodes.ensure_current_sockets()
assert bone.inputs["Enter"].is_linked and bone.inputs.get("Armatures")
assert data.outputs.get("Objects")

runtime._evaluate_node = old_evaluate
flow.clear()
for node in (data, arm, objects, bone, shape):
    node.draw_buttons(bpy.context, LayoutRecorder())
addon.unregister()
print("YUGU_TARGETS_END_FLOW_OK")
