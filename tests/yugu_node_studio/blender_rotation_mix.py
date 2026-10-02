"""Run in background Blender with factory startup."""
from pathlib import Path
from types import SimpleNamespace
import sys
import math
import bpy
from mathutils import Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, runtime, nodes
from yugu_node_studio.data import BoneMapData, HumanoidMask
from blender_integration import make_armature

def close(a, b):
    assert abs(a.normalized().dot(b.normalized())) > .99999, (a, b)

addon.register()
rig = make_armature()
identity = Quaternion()
quarter = Quaternion((0, 0, 1), math.pi / 2)
spring_marker = object()
base = flow.BoneRotations(rig.name, {"head": identity, "hips": quarter})
overlay = flow.BoneRotations(rig.name, {"head": quarter, "hair": quarter}, spring_marker)
for factor in (0, .5, 1):
    result = flow.mix_bone_rotations(base, overlay, factor)
    close(result.values["head"], Quaternion((0, 0, 1), math.pi / 2 * factor))
    close(result.values["hair"], result.values["head"])
    close(result.values["hips"], quarter)
    assert result.spring_data is spring_marker
close(base.values["head"], identity)
close(overlay.values["head"], quarter)
mapping = BoneMapData(rig.name, {"head": "head"}, {"head": "head"})
negated = quarter.copy(); negated.negate()
close(flow.mix_bone_rotations(flow.BoneRotations(rig.name, {"head": quarter}),
                             flow.BoneRotations(rig.name, {"head": negated}), .5).values["head"], quarter)
assert flow.mix_bone_rotations(flow.BoneRotations(), flow.BoneRotations(), .5).values == {}
for args in ((base, flow.BoneRotations("Other"), .5),):
    try:
        flow.mix_bone_rotations(*args)
        raise AssertionError("Expected invalid-target error")
    except ValueError:
        pass

tree = bpy.data.node_groups.new("Rotation Mix Test", "YUGUS_NT_humanoid_motion")
mix = tree.nodes.new("YUGUS_NODE_bone_rotation_mix")
assert [s.name for s in mix.inputs] == ["Base", "Overlay"]
assert any(kind == mix.bl_idname for _, group in nodes.NODE_MENU for kind, label in group)
mix.factor = .5
context = SimpleNamespace(input=lambda node, name, default: {"Base": base, "Overlay": overlay}.get(name, default))
assert flow.evaluate(context, mix)["Bone Rotation"].spring_data is spring_marker

source = tree.nodes.new("YUGUS_NODE_motion_to_rotation")
clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
sink = tree.nodes.new("YUGUS_NODE_apply_rotation")
sink.armature = rig
tree.links.new(source.outputs["Bone Rotation"], mix.inputs["Overlay"])
tree.links.new(mix.outputs["Bone Rotation"], sink.inputs["Bone Rotation"])
original = runtime._evaluate_node
calls = []
def fake(context, node):
    if node == source:
        calls.append(context.now)
        return {"Bone Rotation": flow.BoneRotations(rig.name, {"head": quarter})}
    return original(context, node)
runtime._evaluate_node = fake
try:
    flow.tick(0); flow.tick(1)
    assert not calls
    tree.links.new(clock.outputs["Trigger"], sink.inputs["Enter"])
    flow.tick(2); flow.tick(2 + 1 / 60)
    assert len(calls) == 1 and not tree.last_error, tree.last_error
    close(rig.pose.bones["head"].rotation_quaternion, identity.slerp(quarter, .5))
    # Spring dependency discovery must see through the new mix node.
    spring = tree.nodes.new("YUGUS_NODE_spring_simulation")
    tree.links.new(spring.outputs["Bone Rotation"], mix.inputs["Base"])
    assert flow._has_spring(sink)
finally:
    runtime._evaluate_node = original
    flow.clear()
    bpy.data.node_groups.remove(tree)
    addon.unregister()
addon.register(); addon.unregister()
print("BONE ROTATION MIX TESTS PASSED")
