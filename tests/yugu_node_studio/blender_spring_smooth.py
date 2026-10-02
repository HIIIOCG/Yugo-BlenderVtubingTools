"""Real Blender cadence regression: generated VRM 0/1 springs, no user files.

Run: blender --background --factory-startup --python this_file.py
Append ``-- --measure`` to print diagnostics without cadence assertions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Quaternion

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()
from yugu_node_studio import flow
from yugu_node_studio.data import SpringData


def make_rig():
    data = bpy.data.armatures.new("SpringCadenceRig")
    rig = bpy.data.objects.new("SpringCadenceRig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    parent = data.edit_bones.new("driver")
    parent.head, parent.tail = (0, 0, 0), (0, 0, 1)
    root = data.edit_bones.new("hairRoot")
    root.head, root.tail = (0, 0, 0.8), (0, 0.25, 0.8)
    root.parent = parent
    tip = data.edit_bones.new("hairTip")
    tip.head, tip.tail = root.tail, (0, 0.5, 0.8)
    tip.parent = root
    obstacle = data.edit_bones.new("obstacle")
    obstacle.head, obstacle.tail = (0, 0.2, 0.7), (0, 0.2, 0.8)
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in rig.pose.bones:
        bone.rotation_mode = "QUATERNION"
    collider = bpy.data.objects.new("SpringCadenceCollider", None)
    bpy.context.scene.collection.objects.link(collider)
    collider.location = (0, 0.2, 0.7)
    collider.empty_display_size = 0.08
    rig["test_collider"] = collider.name
    return rig


def spring_data(rig, version, collision=False, center=""):
    settings = {"hit_radius": 0.0, "stiffness": 0.5,
                "gravity_power": 0.25, "gravity_dir": (0, 0, -1),
                "drag_force": 0.15}
    if version == "0.0":
        spring = {"name": "Hair", "center": "", "collider_groups": (),
                  "joints": ({"bone": "hairRoot"},), **settings}
    else:
        spring = {"name": "Hair", "center": "", "collider_groups": (),
                  "joints": tuple({"bone": name, **settings}
                                  for name in ("hairRoot", "hairTip"))}
    colliders, groups = (), ()
    spring["center"] = center
    if collision:
        spring["collider_groups"] = ("moving-sphere-group",)
        groups = ({"uuid": "moving-sphere-group", "colliders": ("moving-sphere",)},)
        if version == "0.0":
            colliders = ({"uuid": "moving-sphere", "object": rig["test_collider"]},)
        else:
            colliders = ({"uuid": "moving-sphere", "bone": "obstacle",
                          "shape": "SPHERE", "offset": (0, 0, 0), "radius": 0.08},)
    return SpringData(version, (spring,), colliders, groups, rig.name)


def angle(first, second):
    """Quaternion distance without acos losing precision near identity."""
    delta = first.rotation_difference(second).normalized()
    vector_length = math.sqrt(delta.x ** 2 + delta.y ** 2 + delta.z ** 2)
    return 2 * math.atan2(vector_length, abs(delta.w))


def cadence(ticks, repeats):
    elapsed_ticks = 0
    times = [0.0]
    for _ in range(repeats):
        for tick in ticks:
            elapsed_ticks += tick
            times.append(elapsed_ticks / 240)
    return times


def sample(rig, data, times, moving=False, moving_collider=False,
           moving_world=False, moving_center=False):
    flow.clear()
    rig.location = (0, 0, 0)
    rig.rotation_euler = (0, 0, 0)
    for bone in rig.pose.bones:
        bone.location = (0, 0, 0)
        bone.rotation_quaternion = Quaternion()
    collider = bpy.data.objects[rig["test_collider"]]
    collider.location = (0, 0.2, 0.7)
    bpy.context.view_layer.update()
    node = SimpleNamespace(runtime_id="cadence-test",
                           spring_status="", spring_last_error="")
    samples = []
    missing = 0
    for index, now in enumerate(times):
        # Constant angular velocity is exactly recoverable by sampled SLERP.
        if moving:
            rig.pose.bones["driver"].rotation_quaternion = Quaternion((1, 0, 0), now * 0.8)
        if moving_collider:
            rig.pose.bones["obstacle"].location.x = now * 0.1
            collider.location.x = now * 0.1
        if moving_world:
            rig.location.x = now * 0.2
            rig.rotation_euler.z = now * 0.15
        if moving_center:
            rig.pose.bones["driver"].location.x = now * 0.1
        bpy.context.view_layer.update()
        before = {bone.name: bone.matrix_basis.copy() for bone in rig.pose.bones}
        before_world = rig.matrix_world.copy()
        elapsed = now - times[index - 1] if index else 0.0
        value = flow.spring_value(node, data, now, elapsed)
        assert all(bone.matrix_basis == before[bone.name] for bone in rig.pose.bones), "Spring pull wrote pose"
        assert rig.matrix_world == before_world, "Spring pull wrote armature transform"
        if "hairRoot" not in value.values:
            missing += 1
        q = value.values.get("hairRoot", Quaternion()).copy()
        samples.append((now, q))
        # Reproduce native Apply feedback without requiring node UI or a VRM add-on.
        for name, rotation in value.values.items():
            rig.pose.bones[name].rotation_quaternion = rotation
        bpy.context.view_layer.update()
    state = flow._springs[str(node.runtime_id)]
    return samples, missing, state["steps"]


def smoothness(samples):
    increments = [(right[0], angle(left[1], right[1]), right[0] - left[0])
                  for left, right in zip(samples, samples[1:])
                  if 0.05 <= right[0] <= 0.30]
    speeds = [change / elapsed for _, change, elapsed in increments]
    held = sum(change < 1e-7 for _, change, _ in increments)
    return {"held": held, "samples": len(increments),
            "max_speed": max(speeds), "mean_speed": sum(speeds) / len(speeds)}


def shared_error(reference, samples):
    by_time = {round(now, 9): q for now, q in reference}
    errors = [angle(by_time[round(now, 9)], q) for now, q in samples
              if round(now, 9) in by_time]
    return max(errors)


def main():
    measure = "--measure" in sys.argv
    rig = make_rig()
    metrics = {}
    try:
        for version in ("0.0", "1.0"):
            data = spring_data(rig, version)
            regular, _, _ = sample(rig, data, cadence((2,), 240))
            jitter, missing, steps = sample(rig, data, cadence((1, 3), 120))
            reference, _, _ = sample(rig, data, cadence((1,), 480), moving=True)
            moving_regular, _, _ = sample(rig, data, cadence((4,), 120), moving=True)
            moving_jitter, _, _ = sample(rig, data, cadence((1, 7), 60), moving=True)
            error_regular = shared_error(reference, moving_regular)
            error_jitter = shared_error(reference, moving_jitter)
            collider_data = spring_data(rig, version, collision=True)
            collider_reference, _, _ = sample(rig, collider_data, cadence((1,), 480), moving_collider=True)
            collider_jitter, _, _ = sample(rig, collider_data, cadence((1, 7), 60), moving_collider=True)
            collider_error = shared_error(collider_reference, collider_jitter)
            # Ensure this fixture exercises a contact instead of merely carrying metadata.
            collision_effect = shared_error(regular, collider_reference)
            world_reference, _, _ = sample(rig, data, cadence((1,), 480), moving_world=True)
            world_jitter, _, _ = sample(rig, data, cadence((1, 7), 60), moving_world=True)
            world_error = shared_error(world_reference, world_jitter)
            center_data = spring_data(rig, version, center="driver")
            center_reference, _, _ = sample(rig, center_data, cadence((1,), 480), moving=True,
                                             moving_world=True, moving_center=True)
            center_jitter, _, _ = sample(rig, center_data, cadence((1, 7), 60), moving=True,
                                          moving_world=True, moving_center=True)
            center_error = shared_error(center_reference, center_jitter)
            metric = {"static_regular": smoothness(regular),
                      "static_jitter": smoothness(jitter),
                      "missing_samples": missing, "steps": steps,
                      "moving_regular_error_rad": error_regular,
                      "moving_jitter_error_rad": error_jitter,
                      "collider_jitter_error_rad": collider_error,
                      "collision_effect_rad": collision_effect,
                      "world_jitter_error_rad": world_error,
                      "center_jitter_error_rad": center_error}
            metrics[version] = metric
            if not measure:
                assert missing == 0, (version, "No-step pulls must retain initial/current spring rotations", missing)
                assert steps == 120, (version, "Physics time changed", steps)
                assert metric["static_jitter"]["held"] == 0, (version, "Output holds between solver steps", metric)
                assert error_regular < 0.002, (version, "Regular sampled driving poses diverged", metric)
                assert error_jitter < 0.002, (version, "Catch-up used latest parent pose for earlier steps", metric)
                assert collision_effect > 0.01, (version, "Collider fixture never affected motion", metric)
                assert collider_error < 0.002, (version, "Catch-up used latest collider for earlier steps", metric)
                assert world_error < 0.002, (version, "Catch-up used latest armature transform for earlier steps", metric)
                assert center_error < 0.002, (version, "Catch-up used latest spring center for earlier steps", metric)
        print("YUGU_SPRING_SMOOTH_METRICS: " + json.dumps(metrics, sort_keys=True))
        print("YUGU_SPRING_SMOOTH_OK" if not measure else "YUGU_SPRING_SMOOTH_MEASURED")
    finally:
        flow.clear()


if __name__ == "__main__":
    main()
