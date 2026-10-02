"""Run with Blender: blender --background --factory-startup --python this_file.py"""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy
from _bpy_restrict_state import RestrictBlend
from mathutils import Quaternion, Vector


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio
from yugu_node_studio import (
    action_source,
    map_values,
    network,
    nodes,
    runtime,
    vrm_cache,
)
from yugu_node_studio.constants import TREE_ID
from yugu_node_studio.data import (
    ExpressionFrame,
    HumanoidMotion,
    HumanoidTransform,
)


def make_armature():
    data = bpy.data.armatures.new("YuguTestRig")
    armature = bpy.data.objects.new("YuguTestRig", data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("hips")
    hips.head = (0.0, 0.0, 0.0)
    hips.tail = (0.0, 0.0, 1.0)
    head = data.edit_bones.new("head")
    head.head = (0.0, 0.0, 1.0)
    head.tail = (0.0, 0.0, 2.0)
    head.parent = hips
    hair_root = data.edit_bones.new("hairRoot")
    hair_root.head = (0.0, 0.0, 1.8)
    hair_root.tail = (0.0, 0.25, 1.8)
    hair_root.parent = head
    hair_tip = data.edit_bones.new("hairTip")
    hair_tip.head = hair_root.tail
    hair_tip.tail = (0.0, 0.5, 1.8)
    hair_tip.parent = hair_root
    hair_side = data.edit_bones.new("hairSide")
    hair_side.head = hair_root.tail
    hair_side.tail = (0.25, 0.5, 1.8)
    hair_side.parent = hair_root
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def bone_mapping(node, humanoid):
    return next(
        item for item in node.bone_mappings if item.humanoid == humanoid
    )


def make_face():
    mesh = bpy.data.meshes.new("YuguTestFace")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    obj = bpy.data.objects.new("YuguTestFace", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    smile = obj.shape_key_add(name="Smile")
    smile.value = 0.0
    material = bpy.data.materials.new("YuguTestFaceMaterial")
    material.use_nodes = True
    mesh.materials.append(material)
    return obj, material


def main():
    # Blender enables and reinstalls Extensions while bpy.data is _RestrictData.
    with RestrictBlend():
        yugu_node_studio.register()
    try:
        assert yugu_node_studio._deferred_socket_migration() is None
        armature = make_armature()
        tree = bpy.data.node_groups.new("Yugu Test", TREE_ID)
        data_node = tree.nodes.new("YUGUS_NODE_vrm_data")
        data_node.armature = armature
        snapshot = vrm_cache.get_snapshot(data_node)
        assert snapshot is not None
        assert snapshot.bone_map.by_humanoid["hips"] == "hips"
        assert snapshot.bone_map.by_humanoid["head"] == "head"

        empty_bone_value = tree.nodes.new("YUGUS_NODE_bone_map_value")
        empty_map = runtime.EvaluationContext(
            tree, bpy.context.scene, 1.0 / 60.0
        ).evaluate(empty_bone_value)["Bone Map"]
        assert tuple(
            item.humanoid for item in empty_bone_value.bone_mappings
        ) == nodes._BONE_MAP_ORDER
        assert empty_map.armature_name == ""
        assert empty_map.by_humanoid == {}

        base_bone_value = tree.nodes.new("YUGUS_NODE_bone_map_value")
        base_bone_value.armature = armature
        bone_mapping(base_bone_value, "hips").bone_name = "hips"
        bone_mapping(base_bone_value, "head").bone_name = "head"

        overlay_bone_value = tree.nodes.new("YUGUS_NODE_bone_map_value")
        overlay_bone_value.armature = armature
        bone_mapping(overlay_bone_value, "head").bone_name = "hips"
        bone_map_mix = tree.nodes.new("YUGUS_NODE_bone_map_mix")
        tree.links.new(base_bone_value.outputs["Bone Map"], bone_map_mix.inputs["Base"])
        tree.links.new(
            overlay_bone_value.outputs["Bone Map"], bone_map_mix.inputs["Overlay"]
        )
        mixed_bone_map = runtime.EvaluationContext(
            tree, bpy.context.scene, 1.0 / 60.0
        ).evaluate(bone_map_mix)["Bone Map"]
        assert mixed_bone_map.armature_name == armature.name
        assert mixed_bone_map.by_humanoid["hips"] == "hips"
        assert mixed_bone_map.by_humanoid["head"] == "hips"

        migrated_bone_value = tree.nodes.new("YUGUS_NODE_bone_map_value")
        migrated_bone_value.bone_mappings.clear()
        old_mapping = migrated_bone_value.bone_mappings.add()
        old_mapping.humanoid = "head"
        old_mapping.bone_name = "head"
        assert nodes.ensure_bone_map_entries(migrated_bone_value)
        assert tuple(
            item.humanoid for item in migrated_bone_value.bone_mappings
        ) == nodes._BONE_MAP_ORDER
        assert bone_mapping(migrated_bone_value, "head").bone_name == "head"

        operator_expression_value = tree.nodes.new(
            "YUGUS_NODE_expression_map_value"
        )
        for collection in ("EXPRESSIONS", "MORPH_TARGETS", "MATERIAL_TARGETS"):
            assert bpy.ops.yugu_node_studio.map_item_add(
                tree_name=tree.name,
                node_name=operator_expression_value.name,
                collection=collection,
            ) == {"FINISHED"}
        assert len(operator_expression_value.expressions) == 1
        assert len(operator_expression_value.expressions[0].morph_targets) == 1
        assert len(operator_expression_value.expressions[0].material_targets) == 1
        for collection in ("MORPH_TARGETS", "MATERIAL_TARGETS", "EXPRESSIONS"):
            assert bpy.ops.yugu_node_studio.map_item_remove(
                tree_name=tree.name,
                node_name=operator_expression_value.name,
                collection=collection,
            ) == {"FINISHED"}
        assert len(operator_expression_value.expressions) == 0

        face, material = make_face()
        base_expression_value = tree.nodes.new("YUGUS_NODE_expression_map_value")
        expression = base_expression_value.expressions.add()
        expression.name = "Happy"
        expression.is_binary = True
        expression.override_blink = "BLOCK"
        expression.override_mouth = "BLEND"
        morph = expression.morph_targets.add()
        morph.mesh_object = face
        morph.shape_key_name = "Smile"
        morph.weight = 0.8
        material_bind = expression.material_targets.add()
        material_bind.material = material
        material_bind.bind_type = "color"
        material_bind.target_value = (1.0, 0.0, 0.0, 1.0)
        expression = base_expression_value.expressions.add()
        expression.name = "sad"

        compiled_expression_map = map_values.expression_map_from_node(
            base_expression_value
        )
        happy_definition = compiled_expression_map.definitions["happy"]
        assert happy_definition.is_binary
        assert happy_definition.override_blink == "BLOCK"
        assert happy_definition.override_mouth == "BLEND"
        assert len(happy_definition.morph_targets) == 1
        assert happy_definition.morph_targets[0].object_name == face.name
        assert happy_definition.morph_targets[0].shape_key_name == "Smile"
        assert abs(happy_definition.morph_targets[0].weight - 0.8) < 1.0e-6
        assert len(happy_definition.material_targets) == 1
        assert happy_definition.material_targets[0].material_name == material.name
        assert happy_definition.material_targets[0].bind_type == "color"
        assert all(
            abs(value - expected) < 1.0e-6
            for value, expected in zip(
                happy_definition.material_targets[0].target_value,
                (1.0, 0.0, 0.0, 1.0),
            )
        )

        overlay_expression_value = tree.nodes.new("YUGUS_NODE_expression_map_value")
        expression = overlay_expression_value.expressions.add()
        expression.name = "HAPPY"
        expression.is_binary = False
        expression = overlay_expression_value.expressions.add()
        expression.name = "angry"
        expression_map_mix = tree.nodes.new("YUGUS_NODE_expression_map_mix")
        tree.links.new(
            base_expression_value.outputs["Expression Map"],
            expression_map_mix.inputs["Base"],
        )
        tree.links.new(
            overlay_expression_value.outputs["Expression Map"],
            expression_map_mix.inputs["Overlay"],
        )
        mixed_expression_map = runtime.EvaluationContext(
            tree, bpy.context.scene, 1.0 / 60.0
        ).evaluate(expression_map_mix)["Expression Map"]
        assert tuple(mixed_expression_map.definitions) == ("happy", "sad", "angry")
        assert mixed_expression_map.definitions["happy"].name == "HAPPY"
        assert mixed_expression_map.definitions["happy"].morph_targets == ()
        assert "sad" in mixed_expression_map.definitions
        assert "angry" in mixed_expression_map.definitions

        mapped_expression_action = tree.nodes.new("YUGUS_NODE_expression_action")
        tree.links.new(
            expression_map_mix.outputs["Expression Map"],
            mapped_expression_action.inputs["Expression Map"],
        )
        assert [
            item[0]
            for item in nodes._expression_action_items(mapped_expression_action, None)
        ] == ["happy", "sad", "angry"]

        head_bone = armature.pose.bones["head"]
        head_bone.rotation_mode = "QUATERNION"
        keyed_rotation = Quaternion((1.0, 0.0, 0.0), math.radians(20.0))
        head_bone.rotation_quaternion = keyed_rotation
        head_bone.keyframe_insert(data_path="rotation_quaternion", frame=1.0)
        action = armature.animation_data.action
        armature.animation_data.action = None
        head_bone.rotation_quaternion = Quaternion()
        action_motion = action_source.sample_action(
            action, snapshot.bone_map, 1.0, cyclic=False
        )
        assert "head" in action_motion.bones
        rest_rotation = head_bone.bone.matrix_local.to_3x3().normalized().to_quaternion()
        expected_canonical = rest_rotation @ keyed_rotation @ rest_rotation.inverted()
        assert 1.0 - abs(
            action_motion.bones["head"].rotation.dot(expected_canonical)
        ) < 1.0e-6

        from yugu_node_studio import flow, scheduler
        source = tree.nodes.new("YUGUS_NODE_vmc_source")
        source.monitor_open = True
        weight = tree.nodes.new("YUGUS_NODE_motion_weight")
        weight.weight = 0.5
        convert = tree.nodes.new("YUGUS_NODE_motion_to_rotation")
        bone_sink = tree.nodes.new("YUGUS_NODE_apply_rotation")
        shape_convert = tree.nodes.new("YUGUS_NODE_expression_to_shapes")
        shape_sink = tree.nodes.new("YUGUS_NODE_apply_shapes")
        clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
        clock.hz = "60"
        links = (
            (source, "Humanoid Motion", weight, "Motion"),
            (weight, "Motion", convert, "Motion"),
            (data_node, "Bone Map", convert, "Bone Map"),
            (convert, "Bone Rotation", bone_sink, "Bone Rotation"),
            (source, "Expressions", shape_convert, "Expressions"),
            (base_expression_value, "Expression Map", shape_convert, "Expression Map"),
            (shape_convert, "Shape Keys Values", shape_sink, "Shape Keys Values"),
            (bone_sink, "End", shape_sink, "Enter"),
        )
        for before, output_name, after, input_name in links:
            tree.links.new(before.outputs[output_name], after.inputs[input_name])
        base_expression_value.expressions[0].is_binary = False
        principled = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        original_color = tuple(principled.inputs["Base Color"].default_value)
        head_bone.location = (1, 2, 3)
        head_bone.scale = (1.1, 1.2, 1.3)
        original_location = head_bone.location.copy()
        original_scale = head_bone.scale.copy()
        expected = Quaternion((0, 0, 1), math.radians(60))
        calls = []
        original_source = network.source_snapshot

        def sample(_uid, _calibration, mirror=False):
            calls.append(_uid)
            return (
                HumanoidMotion({"head": HumanoidTransform(expected, Vector((8, 9, 10)))}),
                ExpressionFrame({"happy": 0.75}),
            )

        network.source_snapshot = sample
        try:
            # Saved retired nodes remain readable, but cannot restart their old scheduler.
            retired = tree.nodes.new("YUGUS_NODE_blender_output")
            assert not hasattr(retired, "update_rate")
            tree.links.new(source.outputs["Humanoid Motion"], retired.inputs["Humanoid Motion"])
            tree.links.new(data_node.outputs["Bone Map"], retired.inputs["Bone Map"])
            retired.is_active_output = True
            retired.active_validated = True
            assert runtime._timer() == runtime.TIMER_INTERVAL
            assert not calls, "A retired output evaluated its source"
            assert head_bone.rotation_quaternion.angle < 1e-6

            # Evaluating conversions and an untriggered graph cannot write Blender targets.
            values = flow.PullContext(tree, bpy.context.scene, 0, 1/60).evaluate(convert)
            expected_half = Quaternion().slerp(expected, 0.5)
            expected_local = rest_rotation.inverted() @ expected_half @ rest_rotation
            assert abs(values["Bone Rotation"].values["head"].dot(expected_local)) > 0.999999
            assert head_bone.rotation_quaternion.angle < 1e-6
            calls.clear()
            flow.tick(0)
            flow.tick(1)
            assert not calls and face.data.shape_keys.key_blocks["Smile"].value == 0

            # Real End chaining shares one source pull and applies both native target types.
            tree.links.new(clock.outputs["Trigger"], bone_sink.inputs["Enter"])
            flow.clear()
            flow.tick(0)
            flow.tick(1/60)
            assert len(calls) == 1, calls
            assert abs(head_bone.rotation_quaternion.dot(expected_local)) > 0.999999
            assert abs(face.data.shape_keys.key_blocks["Smile"].value - 0.6) < 1e-6
            assert "head:" in source.monitor_text
            assert not tree.last_error, tree.last_error
            assert head_bone.location == original_location
            assert head_bone.scale == original_scale
            assert tuple(principled.inputs["Base Color"].default_value) == original_color

            # Muting the clock restores captured values and stops source demand.
            clock.mute = True
            count = len(calls)
            flow.tick(2/60)
            assert len(calls) == count
            assert head_bone.rotation_quaternion.angle < 1e-6
            assert face.data.shape_keys.key_blocks["Smile"].value == 0
            assert head_bone.location == original_location and head_bone.scale == original_scale

            # Reconnecting and undoing release both owners under the shared scheduler.
            clock.mute = False
            flow.tick(3/60)
            flow.tick(4/60)
            assert abs(face.data.shape_keys.key_blocks["Smile"].value - 0.6) < 1e-6
            yugu_node_studio._undo_pre(None)
            assert scheduler.is_suspended()
            assert head_bone.rotation_quaternion.angle < 1e-6
            assert face.data.shape_keys.key_blocks["Smile"].value == 0
            yugu_node_studio._undo_post(None)
            assert not scheduler.is_suspended()
            assert not bpy.app.timers.is_registered(runtime._timer)
        finally:
            network.source_snapshot = original_source
            flow.clear()

        assert bpy.ops.yugu_node_studio.new_graph(name="Yugu Default Graph") == {"FINISHED"}
        default_tree = bpy.data.node_groups["Yugu Default Graph"]
        default_types = {node.bl_idname for node in default_tree.nodes}
        assert {"YUGUS_NODE_hz_trigger", "YUGUS_NODE_apply_rotation", "YUGUS_NODE_apply_shapes"} <= default_types
        assert "YUGUS_NODE_blender_output" not in default_types
        assert "YUGUS_NODE_humanoid_retarget" not in default_types
        assert "YUGUS_NODE_expression_bind" not in default_types
        print("YUGUS_NODE_INTEGRATION_OK")
    finally:
        with RestrictBlend():
            yugu_node_studio.unregister()
        assert not hasattr(bpy.types.Scene, "yugu_node_studio_tree")


if __name__ == "__main__":
    main()
