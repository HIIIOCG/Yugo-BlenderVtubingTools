"""Deterministic trigger topology and wall-time physics regression tests."""
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
from mathutils import Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, runtime, nodes
from yugu_node_studio.data import *
from blender_integration import make_armature, make_face

addon.register()
rig = make_armature()
face, material = make_face()
tree = bpy.data.node_groups.new("Pull Flow Tests", "YUGUS_NT_humanoid_motion")
scene = bpy.context.scene
scene.yugu_node_studio_tree = tree
clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
assert not hasattr(clock, "enabled")
clock["enabled"] = False  # Obsolete saved 0.2.0 flag must not stop the clock.
flow_menu = dict(nodes.NODE_MENU)["Flow Control"]
assert ("YUGUS_NODE_hz_trigger", "Hz Trigger") in flow_menu
assert "YUGUS_NODE_hz_trigger" not in {item[0] for item in nodes.NODE_MENU[1][1]}
convert = tree.nodes.new("YUGUS_NODE_motion_to_rotation")
sink = tree.nodes.new("YUGUS_NODE_apply_rotation")
tree.links.new(convert.outputs["Bone Rotation"], sink.inputs["Bone Rotation"])
assert not hasattr(tree.nodes.new("YUGUS_NODE_blender_output"), "update_rate")
spring = tree.nodes.new("YUGUS_NODE_spring_simulation")
assert spring.outputs.get("Bone Rotation") and not hasattr(spring, "spring_fps")
assert not hasattr(spring, "weight")
spring["weight"] = 0.0  # An old saved Weight must not suppress Spring output.
assert "YUGUS_NODE_blender_output" not in {item[0] for _, group in nodes.NODE_MENU for item in group}
q = Quaternion((0, 0, 1), 0.4)
mapping = BoneMapData(rig.name, {"head": "head"}, {"head": "head"})
motion = HumanoidMotion({"head": HumanoidTransform(q, Vector((8, 9, 10)))})
context = SimpleNamespace(input=lambda n, name, fallback: {"Motion": motion, "Bone Map": mapping}.get(name, fallback))
pure = flow.evaluate(context, convert)["Bone Rotation"]
assert pure.armature_name == rig.name and "head" in pure.values
assert rig.pose.bones["head"].location.length == 0

original = runtime._evaluate_node
calls = []
def fake(context, node):
    if node == convert:
        calls.append(context.now)
        return {"Bone Rotation": pure}
    return original(context, node)
runtime._evaluate_node = fake
flow.tick(0)
flow.tick(1)
assert calls == []  # No Trigger, no pull, no writes.
tree.links.new(clock.outputs["Trigger"], sink.inputs["Enter"])
for hz in (8, 12, 24, 30, 60):
    flow.clear()
    calls.clear()
    clock.mute, clock.hz = False, str(hz)
    flow.tick(0)
    for frame in range(1, 241):
        flow.tick(frame / 120)
    assert len(calls) == hz * 2, (hz, len(calls))
    assert abs(rig.pose.bones["head"].rotation_quaternion.dot(pure.values["head"])) > 0.99999
    assert rig.pose.bones["head"].location.length == 0
    clock.mute = True
    flow.tick(2.1)
    assert rig.pose.bones["head"].matrix_basis.to_quaternion().angle < 1e-5
assert not flow._owners

# Shared pure source is evaluated once per pulse, even with two consumers.
clock.mute = False
clock.hz = "30"
other = tree.nodes.new("YUGUS_NODE_apply_rotation")
tree.links.new(clock.outputs["Trigger"], other.inputs["Enter"])
tree.links.new(convert.outputs["Bone Rotation"], other.inputs["Bone Rotation"])
flow.clear()
calls.clear()
flow.tick(0)
flow.tick(1/30)
assert len(calls) == 1
assert "already controls" in other.last_summary  # Overlapping writers are rejected.
tree.nodes.remove(other)
clock.mute = True
flow.tick(0.1)
runtime._evaluate_node = original

# Binary/override resolution and morph multiplication produce native values;
# conversion itself never writes shape keys or material colors.
expression = tree.nodes.new("YUGUS_NODE_expression_to_shapes")
shape_sink = tree.nodes.new("YUGUS_NODE_apply_shapes")
definition = ExpressionDefinition("happy", "happy", True, (MorphTarget(face.name, "Smile", 0.7),), ())
emap = ExpressionMapData({"happy": definition})
frame = ExpressionFrame({"happy": 0.8})
context = SimpleNamespace(input=lambda n, name, fallback: {"Expressions": frame, "Expression Map": emap}.get(name, fallback))
shape_value = flow.evaluate(context, expression)["Shape Keys Values"]
assert shape_value.values == {(face.name, "Smile"): 0.7}
assert face.data.shape_keys.key_blocks["Smile"].value == 0
flow.apply(shape_sink, shape_value)
assert abs(face.data.shape_keys.key_blocks["Smile"].value - 0.7) < 1e-6
flow.release(str(shape_sink.runtime_id))
assert face.data.shape_keys.key_blocks["Smile"].value == 0

data = SpringData("1.0", ({"name": "Hair", "center": "head", "collider_groups": (),
    "joints": tuple({"bone": name, "hit_radius": 0.0, "stiffness": 0.5,
                     "gravity_power": 0.5, "gravity_dir": (0, 0, -1), "drag_force": 0.3}
                    for name in ("hairRoot", "hairTip"))},), (), (), rig.name)
results = []
for hz in (8, 12, 24, 30, 60):
    flow.clear()
    bpy.context.view_layer.update()
    before = rig.pose.bones["hairRoot"].matrix_basis.copy()
    for index in range(1, hz * 2 + 1):
        value = flow.spring_value(spring, data, index / hz, 1 / hz)
    state = flow._springs[str(spring.runtime_id)]
    assert state["steps"] == 120, (hz, state["steps"])
    assert rig.pose.bones["hairRoot"].matrix_basis == before, "Spring conversion wrote pose"
    assert value.values["hairRoot"].angle > 0.01
    results.append(value.values["hairRoot"])
for rotation in results[1:]:
    assert abs(results[0].dot(rotation)) > 0.999999

# A stalled UI must not silently discard physics time.
flow.clear()
flow.spring_value(spring, data, 5.0, 5.0)
assert flow._springs[str(spring.runtime_id)]["steps"] == 240
assert abs(flow._springs[str(spring.runtime_id)]["debt"] - 1) < 1e-6
flow.spring_value(spring, data, 5.0, 0.0)
assert flow._springs[str(spring.runtime_id)]["steps"] == 300

# Pull Spring through the real graph, ensure it sleeps on disconnect.
flow.clear()
spring_sink = tree.nodes.new("YUGUS_NODE_apply_rotation")
source = tree.nodes.new("YUGUS_NODE_vrm_data")
tree.links.new(source.outputs["Spring Data"], spring.inputs["Spring Data"])
tree.links.new(spring.outputs["Bone Rotation"], spring_sink.inputs["Bone Rotation"])
tree.links.new(clock.outputs["Trigger"], spring_sink.inputs["Enter"])
def source_data(context, node):
    if node == source:
        return {"Spring Data": data}
    return original(context, node)
runtime._evaluate_node = source_data
sink.enabled = False
clock.mute = False
clock.hz = "8"
flow.tick(0)
assert not flow._springs
flow.tick(0.125)
assert flow._springs[str(spring.runtime_id)]["steps"] == 7
flow.tick(0.25)
assert flow._springs[str(spring.runtime_id)]["steps"] == 15
spring_sink.enabled = False
flow.tick(0.5)
assert not flow._springs
runtime._evaluate_node = original
nodes.ensure_current_sockets()
assert spring.outputs.get("Bone Rotation")
# VRM 0 subtree solving is rate-independent too, including applying each sample.
legacy_data = SpringData("0.0", ({"name": "Legacy Hair", "center": "head",
    "collider_groups": (), "joints": ({"bone": "hairRoot"},),
    "hit_radius": 0, "stiffness": 0.5, "gravity_power": 0.5,
    "gravity_dir": (0, 0, -1), "drag_force": 0.3},), (), (), rig.name)
legacy_results = []
for hz in (8, 12, 24, 30, 60):
    flow.clear()
    bpy.context.view_layer.update()
    for index in range(1, hz * 2 + 1):
        value = flow.spring_value(spring, legacy_data, index / hz, 1/hz)
        flow.apply(spring_sink, value)
        bpy.context.view_layer.update()
    legacy_results.append({name: q.copy() for name, q in value.values.items()})
for result in legacy_results[1:]:
    for name, rotation in result.items():
        assert abs(legacy_results[0][name].dot(rotation)) > 0.999999, name
flow.clear()

# Shape apply driven through a clock; trigger and data reroutes both work.
reroute = tree.nodes.new("NodeReroute")
tree.links.new(clock.outputs["Trigger"], reroute.inputs[0])
tree.links.new(reroute.outputs[0], shape_sink.inputs["Enter"])
data_reroute = tree.nodes.new("NodeReroute")
tree.links.new(expression.outputs["Shape Keys Values"], data_reroute.inputs[0])
tree.links.new(data_reroute.outputs[0], shape_sink.inputs["Shape Keys Values"])
def fake_shapes(context, node):
    if node == expression:
        return {"Shape Keys Values": shape_value}
    return original(context, node)
runtime._evaluate_node = fake_shapes
flow.tick(0)
flow.tick(0.125)
assert abs(face.data.shape_keys.key_blocks["Smile"].value - 0.7) < 1e-6
clock.mute = True
flow.tick(0.25)
assert face.data.shape_keys.key_blocks["Smile"].value == 0
runtime._evaluate_node = original
assert "Completed steps" in runtime.spring_debug_report(spring, scene)
from blender_composing import LayoutRecorder
for ui_node in (clock, convert, sink, expression, shape_sink, spring):
    ui_node.draw_buttons(bpy.context, LayoutRecorder())
addon.unregister()
print("YUGU_TRIGGER_FLOW_OK: all rates, lazy pulls, native apply, restoration, wall-time spring")
