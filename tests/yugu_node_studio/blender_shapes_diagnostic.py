"""Exercise real expression conversion/apply; reproduce selection edge cases."""
from pathlib import Path
from types import SimpleNamespace
import sys
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, runtime
from yugu_node_studio.data import ExpressionDefinition, ExpressionMapData, ExpressionFrame, MorphTarget
from blender_integration import make_face

addon.register()
tree = bpy.data.node_groups.new("Shape diagnostics", "YUGUS_NT_humanoid_motion")
face, _ = make_face()
other, _ = make_face()
face.data.shape_keys.key_blocks["Smile"].data[0].co.z = 1
mapping = ExpressionMapData({"happy": ExpressionDefinition("happy", "happy", False,
    (MorphTarget(face.name, "Smile", .8),), ())})
convert = tree.nodes.new("YUGUS_NODE_expression_to_shapes")
sink = tree.nodes.new("YUGUS_NODE_apply_shapes")
clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
source = tree.nodes.new("YUGUS_NODE_vmc_source")
map_node = tree.nodes.new("YUGUS_NODE_expression_map_value")
objects = tree.nodes.new("YUGUS_NODE_objects_value")
tree.links.new(source.outputs["Expressions"], convert.inputs["Expressions"])
tree.links.new(map_node.outputs["Expression Map"], convert.inputs["Expression Map"])
tree.links.new(convert.outputs["Shape Keys Values"], sink.inputs["Shape Keys Values"])
tree.links.new(clock.outputs["Trigger"], sink.inputs["Enter"])
original = runtime._evaluate_node
def injected(context, node):
    if node == source: return {"Expressions": ExpressionFrame({"happy": .75})}
    if node == map_node: return {"Expression Map": mapping}
    return original(context, node)
runtime._evaluate_node = injected
try:
    flow.tick(0); flow.tick(1/60)
    assert not tree.last_error, tree.last_error
    assert abs(face.data.shape_keys.key_blocks["Smile"].value - .6) < 1e-6
    evaluated = face.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert abs(evaluated.data.vertices[0].co.z - .6) < 1e-6
    print("PASS: Expression -> conversion -> Hz trigger -> actual mesh deformation")
    flow.clear()
    sink.objects.add()  # Empty picker row disables mapping fallback.
    flow.tick(0); flow.tick(1/60)
    assert face.data.shape_keys.key_blocks["Smile"].value == 0
    assert sink.last_summary == "Applied 0 values"
    print("REPRODUCED: blank Add Object row silently applies zero values")
    flow.clear(); sink.objects.clear()
    objects.objects.add().object = face
    objects.objects.add().object = other
    tree.links.new(objects.outputs["Objects"], sink.inputs["Objects"])
    flow.tick(0); flow.tick(1/60)
    assert abs(other.data.shape_keys.key_blocks["Smile"].value - .6) < 1e-6
    print("REPRODUCED: unmapped object in linked Objects receives another object's same-name key")
finally:
    runtime._evaluate_node = original
    flow.clear()
    addon.unregister()
