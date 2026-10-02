"""Keep live VMC and Spring Motion Layers bound across list reordering."""

from math import cos, sin
from types import SimpleNamespace
import sys
from pathlib import Path

import bpy


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo
from yugo import motion_mixer
from yugo.springbone import runtime as spring_runtime
from yugo.state import ensure_runtime_id
from yugo.vmc import runtime as vmc_runtime
from yugo.vmc.protocol import VmcTransform


def make_armature():
    data = bpy.data.armatures.new("YugoLayerMoveData")
    armature = bpy.data.objects.new("YugoLayerMove", data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("Hips")
    hips.head = (0.0, 0.0, 0.0)
    hips.tail = (0.0, 0.0, 1.0)
    hair_root = data.edit_bones.new("HairRoot")
    hair_root.head = hips.tail
    hair_root.tail = (0.0, 0.0, 2.0)
    hair_root.parent = hips
    hair_tip = data.edit_bones.new("HairTip")
    hair_tip.head = hair_root.tail
    hair_tip.tail = (0.0, 0.0, 3.0)
    hair_tip.parent = hair_root
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def make_extension():
    joints = [
        SimpleNamespace(
            node=SimpleNamespace(bone_name="HairRoot"),
            hit_radius=0.0,
            stiffness=1.0,
            drag_force=0.2,
            gravity_dir=(0.0, 0.0, 0.0),
            gravity_power=0.0,
        ),
        SimpleNamespace(
            node=SimpleNamespace(bone_name="HairTip"),
            hit_radius=0.0,
            stiffness=1.0,
            drag_force=0.2,
            gravity_dir=(0.0, 0.0, 0.0),
            gravity_power=0.0,
        ),
    ]
    spring_bone = SimpleNamespace(
        enable_animation=False,
        springs=[
            SimpleNamespace(
                joints=joints,
                center=SimpleNamespace(bone_name=""),
                collider_groups=[],
            )
        ],
        colliders=[],
        collider_groups=[],
    )
    return SimpleNamespace(
        spec_version="1.0",
        is_vrm1=lambda: True,
        spring_bone1=spring_bone,
    )


def vmc_rotation(angle):
    return VmcTransform(
        "Hips",
        (0.0, 0.0, 0.0),
        (sin(angle * 0.5), 0.0, 0.0, cos(angle * 0.5)),
    )


def main():
    yugo.register()
    scene = bpy.context.scene
    armature = make_armature()
    avatar = scene.yugo_avatars.add()
    ensure_runtime_id(avatar)
    avatar.armature = armature
    scene.yugo_avatar_index = len(scene.yugo_avatars) - 1

    # Spring is above VMC, matching the reported running-layer arrangement.
    spring_layer = avatar.motion_layers.add()
    spring_layer.source_type = "SPRING_BONE"
    spring_id = ensure_runtime_id(spring_layer)
    vmc_layer = avatar.motion_layers.add()
    vmc_layer.source_type = "VMC"
    vmc_id = ensure_runtime_id(vmc_layer)
    avatar.motion_layer_index = 1

    extension = make_extension()
    original_get = spring_runtime.get_vrm_extension
    spring_runtime.get_vrm_extension = lambda _armature: extension

    receiver = None
    try:
        spring_runtime.start(bpy.context, scene, spring_layer, armature)
        receiver = vmc_runtime._Receiver("127.0.0.1", 0)
        target = vmc_runtime._Target(scene, vmc_layer, armature, {"hips": "Hips"})
        receiver.add_target(target)
        vmc_runtime._receivers[receiver.key] = receiver
        vmc_runtime._layer_receivers[target.layer_key] = receiver
        vmc_layer.connected = True
        assert spring_runtime.is_layer_running(spring_layer)
        assert vmc_runtime.is_layer_running(vmc_layer)

        receiver.bone_buffer = {"hips": vmc_rotation(0.2)}
        receiver._timer()
        motion_mixer.apply_scene(scene, armature)

        # Use the actual UI operator. Blender may replace CollectionProperty
        # RNA slots here, while the network socket and physics stay running.
        assert bpy.ops.yugo.layer_move(direction="UP") == {"FINISHED"}
        moved_vmc = next(layer for layer in avatar.motion_layers if layer.runtime_id == vmc_id)
        moved_spring = next(layer for layer in avatar.motion_layers if layer.runtime_id == spring_id)
        assert vmc_runtime.is_layer_running(moved_vmc)
        assert spring_runtime.is_layer_running(moved_spring)
        assert receiver.socket is not None and receiver.socket.fileno() >= 0

        receiver.bone_buffer = {"hips": vmc_rotation(0.8)}
        receiver._timer()
        motion_mixer.apply_scene(scene, armature)
        result = armature.pose.bones["Hips"].rotation_quaternion
        assert result.angle > 0.7, result.angle
        assert target.layer.runtime_id == vmc_id
        assert vmc_runtime.status(moved_vmc)["applied_bone_count"] == 1
        print("YUGO_LIVE_LAYER_REORDER_OK")
    finally:
        if receiver is not None:
            vmc_runtime.stop()
        spring_runtime.stop_all(reset_pose=True)
        spring_runtime.get_vrm_extension = original_get
        yugo.unregister()


if __name__ == "__main__":
    main()
