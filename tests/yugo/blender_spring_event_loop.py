"""Verify Spring write-back is evaluated by Blender's normal UI event loop."""

import sys
from pathlib import Path
import traceback

import bpy


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo
from yugo.springbone import runtime
from blender_spring_post_mix import (
    add_avatar_layer,
    make_armature,
    make_extension,
)


ORIGINAL_GET = None
ORIGINAL_UPDATE = None
ARMATURE = None
LAYER = None
INITIAL_MATRIX = None
VIEW_UPDATES = [0]


def run_test():
    global ORIGINAL_GET, ORIGINAL_UPDATE, ARMATURE, LAYER, INITIAL_MATRIX
    try:
        yugo.register()
        scene = bpy.context.scene
        ARMATURE = make_armature("YugoSpringEventLoop")
        _avatar, LAYER = add_avatar_layer(scene, ARMATURE)
        LAYER.spring_physics_rate = "FPS_24"
        extension = make_extension()
        ORIGINAL_GET = runtime.get_vrm_extension
        ORIGINAL_UPDATE = runtime._update_view_layer
        runtime.get_vrm_extension = lambda _armature: extension
        runtime.start(bpy.context, scene, LAYER, ARMATURE)
        INITIAL_MATRIX = ARMATURE.pose.bones["HairRoot"].matrix.copy()

        def counted_update(context):
            VIEW_UPDATES[0] += 1
            return ORIGINAL_UPDATE(context)

        runtime._update_view_layer = counted_update
        bpy.app.timers.register(verify_test, first_interval=1.0)
    except Exception:
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
    return None


def verify_test():
    exit_code = 0
    try:
        pose_bone = ARMATURE.pose.bones["HairRoot"]
        matrix_change = INITIAL_MATRIX.to_quaternion().rotation_difference(
            pose_bone.matrix.to_quaternion()
        ).angle
        assert matrix_change > 0.001, matrix_change
        # At 24 FPS the synchronous pull refresh must follow physics cadence,
        # not the 60 Hz interpolation/write-back cadence.
        assert 5 <= VIEW_UPDATES[0] < 40, VIEW_UPDATES[0]
        assert not runtime._targets[LAYER.runtime_id].spring_bone.enable_animation
        print("YUGO_SPRING_EVENT_LOOP_WRITEBACK_OK", VIEW_UPDATES[0])
    except Exception:
        exit_code = 1
        traceback.print_exc()
    finally:
        runtime.stop_all(reset_pose=True)
        if ORIGINAL_GET is not None:
            runtime.get_vrm_extension = ORIGINAL_GET
        if ORIGINAL_UPDATE is not None:
            runtime._update_view_layer = ORIGINAL_UPDATE
        yugo.unregister()
        bpy.context.window_manager["yugo_spring_event_loop_exit"] = exit_code
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run_test, first_interval=0.5)
