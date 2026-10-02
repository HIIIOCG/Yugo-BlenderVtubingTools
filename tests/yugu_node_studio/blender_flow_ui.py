from pathlib import Path
import sys
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, nodes, runtime, network
from blender_integration import make_face

addon.register()
tree=bpy.data.node_groups.new("Flow UI", "YUGUS_NT_humanoid_motion")
face,_=make_face()
clock=tree.nodes.new("YUGUS_NODE_hz_trigger")
source=tree.nodes.new("YUGUS_NODE_vmc_source")
receiver=network.VMCReceiver("",0)
receiver.expressions={"happy":.75}
network._vmc_receivers[str(source.runtime_id)]=receiver
mapping=tree.nodes.new("YUGUS_NODE_expression_map_value")
entry=mapping.expressions.add(); entry.name="happy"
bind=entry.morph_targets.add(); bind.mesh_object=face; bind.shape_key_name="Smile"; bind.weight=.8
convert=tree.nodes.new("YUGUS_NODE_expression_to_shapes")
sink=tree.nodes.new("YUGUS_NODE_apply_shapes")
sink.objects.add().object=face
end=tree.nodes.new("YUGUS_NODE_expression_to_shapes")
assert convert.inputs.get("Enter") is None and convert.outputs.get("End") is None
# Reconstruct the mistaken 0.4.3/0.4.4 layout, then migrate its event chain.
for node in (convert,end):
    node.inputs.new(flow.EVENT,"Enter")
    node.outputs.new(flow.EVENT,"End")
for a,out,b,inp in ((source,"Expressions",convert,"Expressions"),(mapping,"Expression Map",convert,"Expression Map"),
                     (convert,"Shape Keys Values",sink,"Shape Keys Values"),(clock,"Trigger",convert,"Enter"),
                     (convert,"End",sink,"Enter"),(sink,"End",end,"Enter")):
    tree.links.new(a.outputs[out],b.inputs[inp])
nodes.ensure_current_sockets()
assert convert.inputs.get("Enter") is None and convert.outputs.get("End") is None
assert sink.inputs["Enter"].links[0].from_node == clock
assert convert.outputs["Shape Keys Values"].is_linked
flow.tick(0); flow.tick(1/60)
assert not tree.last_error, tree.last_error
assert abs(face.data.shape_keys.key_blocks["Smile"].value-.6)<1e-6
assert sink.last_summary=="Applied 1 values"
assert flow._clock(sink)==clock
assert flow.YUGUS_NS_update_event.draw_color(None,None,None)==(.85,.12,.12,1)
assert flow.YUGUS_NS_bone_rotation.draw_color(None,None,None)==(.5,.5,.5,1)
# Simulate an old saved tree: preserve links and socket identifiers on migration.
sink.inputs["Enter"].name="Trigger"
old_id=sink.inputs["Trigger"].identifier
mix=tree.nodes.new("YUGUS_NODE_bone_rotation_mix")
mix.inputs.new("YUGUS_NS_bone_map","Bone Map")
nodes.ensure_current_sockets(); nodes.ensure_current_sockets()
print("MIGRATION", [(s.name,s.identifier,s.bl_idname) for s in sink.inputs],old_id)
assert sink.inputs[0].name=="Enter" and sink.inputs[0].identifier==old_id
assert sink.inputs[0].is_linked
assert [s.name for s in convert.inputs]==["Expressions","Expression Map"]
assert [s.name for s in convert.outputs]==["Shape Keys Values"]
assert [s.name for s in mix.inputs]==["Base","Overlay"]
assert tree.nodes.new("YUGUS_NODE_objects_value").bl_label=="Objects List"
# Upstream event failure blocks downstream application and releases the old value.
original=runtime._evaluate_node
def broken(context,node):
    if node==convert: raise ValueError("diagnostic failure")
    return original(context,node)
runtime._evaluate_node=broken
flow.tick(2/60)
assert "Error" in sink.last_summary
assert face.data.shape_keys.key_blocks["Smile"].value==0
runtime._evaluate_node=original
flow.clear(); addon.unregister()
print("FLOW UI, LEGACY EVENT BYPASS AND LAZY CONVERSION PASSED")
