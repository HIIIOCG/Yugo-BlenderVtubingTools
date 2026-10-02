"""Real source-cache and mapping evaluation with explicit selected objects."""
from pathlib import Path
import sys
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, network
from blender_integration import make_face

addon.register()
tree = bpy.data.node_groups.new("Selected shapes diagnostics", "YUGUS_NT_humanoid_motion")
face, _ = make_face()
face.data.shape_keys.key_blocks["Smile"].data[0].co.z = 1
source = tree.nodes.new("YUGUS_NODE_vmc_source")
receiver = network.VMCReceiver("", 0)
network._vmc_receivers[str(source.runtime_id)] = receiver
mapping = tree.nodes.new("YUGUS_NODE_expression_map_value")
entry = mapping.expressions.add(); entry.name = "happy"
bind = entry.morph_targets.add()
bind.mesh_object = face; bind.shape_key_name = "Smile"; bind.weight = .8
convert = tree.nodes.new("YUGUS_NODE_expression_to_shapes")
sink = tree.nodes.new("YUGUS_NODE_apply_shapes")
clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
objects = tree.nodes.new("YUGUS_NODE_objects_value")
for a, out, b, inp in ((source,"Expressions",convert,"Expressions"),
                       (mapping,"Expression Map",convert,"Expression Map"),
                       (convert,"Shape Keys Values",sink,"Shape Keys Values"),
                       (clock,"Trigger",sink,"Enter")):
    tree.links.new(a.outputs[out], b.inputs[inp])

def check(expected, label):
    flow.tick(0); flow.tick(1/60)
    assert not tree.last_error, tree.last_error
    value = face.data.shape_keys.key_blocks["Smile"].value
    evaluated = face.evaluated_get(bpy.context.evaluated_depsgraph_get())
    displacement = evaluated.data.vertices[0].co.z
    assert abs(value - expected) < 1e-6, (label, value)
    assert abs(displacement - expected) < 1e-6, (label, displacement)
    print(label, "weight=", value, "mesh displacement=", displacement, "status=", sink.last_summary)
    flow.clear()

try:
    receiver.expressions = {"happy": .75}
    sink.objects.add().object = face
    check(.6, "PASS manual selected object")
    objects.objects.add().object = face
    tree.links.new(objects.outputs["Objects"], sink.inputs["Objects"])
    check(.6, "PASS linked Objects selection")
    # Selection alone cannot convert an unrelated expression channel to a bind.
    receiver.expressions = {"jawOpen": .75}
    check(0, "REPRO input channel not in expression map: selected object does not move")
    receiver.expressions = {"happy": .75}
    face.data.shape_keys.key_blocks["Smile"].mute = True
    flow.tick(0); flow.tick(1/60)
    assert abs(face.data.shape_keys.key_blocks["Smile"].value - .6) < 1e-6
    evaluated = face.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert abs(evaluated.data.vertices[0].co.z) < 1e-6
    print("REPRO muted Shape Key: weight=.6, mesh does not move, status=", sink.last_summary)
finally:
    flow.clear()
    addon.unregister()
