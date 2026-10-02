"""Compare head presets and canonical rotations with the original Yugo code.

Run with Blender --background --factory-startup --python this_file.py.
Only generated nodes are used; the original add-on is never registered.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import nodes, runtime
from yugu_node_studio.constants import TREE_ID
from yugu_node_studio.data import HumanoidMotion, HumanoidTransform

REGIONS = ("head", "neck", "chest", "spine")
AXES = ("pitch", "yaw", "roll")


def original_defaults():
    """Read the original RNA defaults instead of repeating the new constants."""
    classes = {
        "YUGO_PG_ifacial_normal_profile": "NORMAL",
        "YUGO_PG_ifacial_inverted_profile": "LIVE2D",
    }
    source = ast.parse((ROOT / "addons/yugo/properties.py").read_text(encoding="utf-8-sig"))
    result = {}
    for definition in source.body:
        if not isinstance(definition, ast.ClassDef) or definition.name not in classes:
            continue
        values = {
            item.target.id: ast.literal_eval(item.annotation.args[1])
            for item in definition.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and isinstance(item.annotation, ast.Call)
            and isinstance(item.annotation.func, ast.Name)
            and item.annotation.func.id == "profile_factor"
        }
        result[classes[definition.name]] = {
            region: tuple(values[f"{region}_{axis}"] for axis in AXES)
            for region in REGIONS
        }
    assert set(result) == {"NORMAL", "LIVE2D"}
    return result


def original_rotation_functions():
    """Execute only the four pure conversion/profile functions, without receivers."""
    path = ROOT / "addons/yugo/ifacialmocap/runtime.py"
    source = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {
        "_ifacial_rotation_to_source_quaternion",
        "_ifacial_rotation_to_blender_quaternion",
        "_calibrated_rotation",
        "_wrap_degrees",
        "_body_rotation_profile",
    }
    selected = [item for item in source.body
                if isinstance(item, ast.FunctionDef) and item.name in names]
    assert {item.name for item in selected} == names
    namespace = {"math": math, "Quaternion": Quaternion,
                 "_calibration_rotation": (0.0, 0.0, 0.0), "_target_layer": None}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def coefficients(node):
    return {region: tuple(getattr(node, region)) for region in REGIONS}


def close_coefficients(actual, expected):
    for region in REGIONS:
        assert len(actual[region]) == 3
        for value, desired in zip(actual[region], expected[region]):
            assert abs(value - desired) < 1e-6, (region, actual, expected)


def angle(first, second):
    delta = first.rotation_difference(second).normalized()
    vector_length = math.sqrt(delta.x ** 2 + delta.y ** 2 + delta.z ** 2)
    return 2 * math.atan2(vector_length, abs(delta.w))


def signature(motion):
    def transform(value):
        return (tuple(value.rotation) if value.rotation is not None else None,
                tuple(value.location) if value.location is not None else None)
    return ({name: transform(value) for name, value in motion.bones.items()},
            transform(motion.root) if motion.root is not None else None)


def preset_and_copy_tests(tree, original):
    node = tree.nodes.new("YUGUS_NODE_head_profile")
    assert node.preset == "NORMAL"
    close_coefficients(coefficients(node), original["NORMAL"])
    items = node.bl_rna.properties["preset"].enum_items
    assert [(item.identifier, item.name) for item in items] == [
        ("NORMAL", "Normal"), ("LIVE2D", "Live2D"), ("CUSTOM", "Custom")]
    assert [socket.name for socket in node.inputs] == ["Motion"]
    assert [socket.name for socket in node.outputs] == ["Motion"]
    for preset in ("LIVE2D", "NORMAL", "LIVE2D"):
        node.preset = preset
        assert node.preset == preset, "Preset callbacks marked their own assignments Custom"
        close_coefficients(coefficients(node), original[preset])

    # All four vector properties and vector component editing must mark Custom.
    for region in REGIONS:
        node.preset = "NORMAL"
        changed = tuple(value + 0.11 for value in getattr(node, region))
        setattr(node, region, changed)
        assert node.preset == "CUSTOM", region
        assert all(abs(value - expected) < 1e-6
                   for value, expected in zip(getattr(node, region), changed))
    node.preset = "LIVE2D"
    node.neck[1] = -0.125
    assert node.preset == "CUSTOM", "Editing one vector component did not mark Custom"
    before = coefficients(node)
    node.preset = "CUSTOM"
    close_coefficients(coefficients(node), before)

    # Actual Blender tree copying preserves presets, vectors, and independence.
    for preset in ("NORMAL", "LIVE2D", "CUSTOM"):
        node.preset = preset
        if preset == "CUSTOM":
            node.chest = (-0.17, 0.19, -0.23)
        before = coefficients(node)
        copied = tree.copy()
        try:
            duplicate = copied.nodes[node.name]
            assert duplicate.preset == preset
            close_coefficients(coefficients(duplicate), before)
            duplicate.spine[0] = -0.71
            assert duplicate.preset == "CUSTOM"
            assert node.preset == preset
            close_coefficients(coefficients(node), before)
        finally:
            bpy.data.node_groups.remove(copied)
    return node


def saved_value_migration_tests(tree, original):
    # Old saved nodes contain vector ID properties but no preset RNA property.
    edited = tree.nodes.new("YUGUS_NODE_head_profile")
    saved = {region: tuple(value + 0.021 * (index + 1) for value in original["NORMAL"][region])
             for index, region in enumerate(REGIONS)}
    for region, values in saved.items():
        # RNA creates the float array format used by saved nodes. Plain Python
        # ID-property arrays are doubles and Blender ignores them for this RNA.
        setattr(edited, region, values)
    edited.property_unset("preset")
    assert not edited.is_property_set("preset")
    normal = tree.nodes.new("YUGUS_NODE_head_profile")
    for region, values in original["NORMAL"].items():
        setattr(normal, region, values)
    selected_custom = tree.nodes.new("YUGUS_NODE_head_profile")
    selected_custom.preset = "CUSTOM"
    before_custom = coefficients(selected_custom)
    assert nodes.ensure_current_sockets()
    assert edited.preset == "CUSTOM", (edited.preset, coefficients(edited), saved, dict(edited.items()))
    close_coefficients(coefficients(edited), saved)
    assert normal.preset == "NORMAL"
    close_coefficients(coefficients(normal), original["NORMAL"])
    assert selected_custom.preset == "CUSTOM"
    close_coefficients(coefficients(selected_custom), before_custom)
    # Re-running saved-node migration must be idempotent.
    assert nodes.ensure_current_sockets()
    close_coefficients(coefficients(edited), saved)
    edited.preset = "LIVE2D"
    close_coefficients(coefficients(edited), original["LIVE2D"])


def rotation_parity_tests(node, original, original_functions):
    convert = original_functions["_ifacial_rotation_to_blender_quaternion"]
    profile = original_functions["_body_rotation_profile"]
    inputs = (
        (23.0, -31.0, 17.0), (-29.0, 14.0, -22.0), (51.0, 42.0, -33.0),
        (-89.0, 52.0, 7.0), (0.0, 0.0, 0.0),
        (60.0, 0.0, 0.0), (0.0, 60.0, 0.0), (0.0, 0.0, 60.0),
        (-60.0, 0.0, 0.0), (0.0, -60.0, 0.0), (0.0, 0.0, -60.0),
    )
    maximum_error = 0.0
    for preset in ("NORMAL", "LIVE2D", "CUSTOM"):
        node.preset = preset
        if preset == "CUSTOM":
            for index, region in enumerate(REGIONS):
                setattr(node, region, (0.21 * (index + 1), -0.13 * (index + 1), 0.17 * (index + 1)))
        factors = coefficients(node)
        # Feed the original function the same coefficients when testing Custom.
        values = {f"{region}_{axis}": factor for region in REGIONS
                  for axis, factor in zip(AXES, factors[region])}
        original_functions["_target_layer"] = SimpleNamespace(
            ifacial_mirror=False, ifacial_invert_body_rotation=preset == "LIVE2D",
            ifacial_normal_profile=SimpleNamespace(**values),
            ifacial_inverted_profile=SimpleNamespace(**values))
        for raw in inputs:
            source = HumanoidMotion({
                "head": HumanoidTransform(convert(raw), Vector((1, 2, 3))),
                "chest": HumanoidTransform(Quaternion((0, 1, 0), 0.35), Vector((4, 5, 6))),
                "leftUpperArm": HumanoidTransform(Quaternion((0, 0, 1), 0.2), Vector((7, 8, 9))),
            }, HumanoidTransform(Quaternion((1, 0, 0), 0.1), Vector((10, 11, 12))))
            before = signature(source)
            context = SimpleNamespace(input=lambda _node, _name, _fallback: source)
            result = runtime._evaluate_node(context, node)["Motion"]
            expected_degrees = profile(raw)
            for region in REGIONS:
                actual = result.bones[region]
                assert actual.location is None, (preset, region, "Profile must output pure rotation")
                error = angle(actual.rotation, convert(expected_degrees[region]))
                maximum_error = max(maximum_error, error)
                assert error < 2e-5, (preset, raw, region, "Original Yugo rotation mismatch", error)
                assert abs(actual.rotation.magnitude - 1.0) < 1e-6
            assert signature(source) == before, "Profile modified input motion"
            assert result.bones["leftUpperArm"] is not source.bones["leftUpperArm"]
            assert result.root is not source.root
            assert tuple(result.bones["leftUpperArm"].location) == tuple(source.bones["leftUpperArm"].location)
            result.bones["leftUpperArm"].location.x = 99
            result.root.location.y = 99
            result.bones["head"].rotation.identity()
            assert signature(source) == before, "Returned values alias input motion"
    for source in (HumanoidMotion(), HumanoidMotion({"head": HumanoidTransform(None, Vector((1, 2, 3)))})):
        before = signature(source)
        result = runtime._head_profile(node, source)
        assert result is not source and signature(result) == before
        if "head" in source.bones:
            assert result.bones["head"] is not source.bones["head"]
    return maximum_error


def main():
    original = original_defaults()
    assert nodes.HEAD_PROFILE_PRESETS == original
    original_functions = original_rotation_functions()
    addon.register()
    tree = bpy.data.node_groups.new("Head Profile Regression", TREE_ID)
    try:
        node = preset_and_copy_tests(tree, original)
        saved_value_migration_tests(tree, original)
        maximum_error = rotation_parity_tests(node, original, original_functions)
        print("YUGU_HEAD_PROFILE_METRICS: " + json.dumps({
            "profile_variants": 3, "input_rotations": 11,
            "compared_region_rotations": 132,
            "maximum_original_error_rad": maximum_error,
        }, sort_keys=True))
        print("YUGU_HEAD_PROFILE_OK")
    finally:
        bpy.data.node_groups.remove(tree)
        addon.unregister()


if __name__ == "__main__":
    main()
