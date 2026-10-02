"""Check picker placement, label order, and linked-state disablement."""
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

class Layout:
    def __init__(self, events=None, parent=None):
        self.events = events if events is not None else []
        self.parent = parent
        self.enabled = True
    def active(self):
        return self.enabled and (self.parent is None or self.parent.active())
    def column(self, **kwargs): return Layout(self.events, self)
    def row(self, **kwargs): return Layout(self.events, self)
    def label(self, **kwargs): self.events.append(("label",kwargs.get("text"),self.active()))
    def prop(self, owner, name, **kwargs): self.events.append(("prop",name,self.active()))
    def operator(self, name, **kwargs):
        self.events.append(("operator",name,self.active()))
        return SimpleNamespace()

addon.register()
tree=bpy.data.node_groups.new("Socket pickers", "YUGUS_NT_humanoid_motion")
arm=tree.nodes.new("YUGUS_NODE_armature_value")
objects=tree.nodes.new("YUGUS_NODE_objects_value")
for kind,socket_name,source,output,property_name in (
    ("vrm_data","Armature",arm,"Armatures","armature"),
    ("apply_rotation","Armatures",arm,"Armatures","armature"),
    ("apply_shapes","Objects",objects,"Objects","object")):
    node=tree.nodes.new("YUGUS_NODE_"+kind)
    if kind=="apply_shapes": node.objects.add()
    body=Layout(); node.draw_buttons(bpy.context,body)
    assert not any(e[:2]==("prop",property_name) for e in body.events), body.events
    socket=node.inputs[socket_name]
    for linked in (False,True):
        if linked: tree.links.new(source.outputs[output],socket)
        layout=Layout(); socket.draw(bpy.context,layout,node,socket.name)
        assert layout.events[0][0]=="label"
        assert ("prop",property_name,not linked) in layout.events, layout.events
    out_layout=Layout()
    source.outputs[output].draw(bpy.context,out_layout,source,output)
    assert not any(e[0]=="prop" for e in out_layout.events)
    source_layout=Layout(); source.draw_buttons(bpy.context,source_layout)
    assert source_layout.events  # Value nodes without inputs retain their controls.
addon.unregister()
print("SOCKET PICKER LAYOUT TESTS PASSED")
