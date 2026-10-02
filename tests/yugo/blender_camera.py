"""Blender integration coverage for Yugo's integrated Camera Manager."""

import math
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo
from yugo import camera as cam_manager, scheduler


def make_camera(scene, name, location):
    data = bpy.data.cameras.new(f"{name}Data")
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    return camera


def run():
    yugo.register()
    assert cam_manager._idle_ui_timer is None
    assert "camera" in scheduler._motion_callbacks
    assert "camera" in scheduler._system_callbacks
    assert math.isclose(cam_manager.MOVE_INPUT_SCALE, 0.1)
    assert math.isclose(cam_manager.MOVE_MAX_MULTIPLIER, 10.0)
    assert math.isclose(cam_manager.MOVE_SAMPLE_RATE, 12.0)
    assert math.isclose(
        cam_manager.MOVE_DECAY_PER_SECOND,
        cam_manager.MOVE_RAMP_PER_SECOND * 9.0,
    )
    assert math.isclose(cam_manager.MOTION_INTERVAL, 1.0 / 60.0)
    assert math.isclose(cam_manager.UI_SYNC_INTERVAL, 0.5)
    assert cam_manager.TARGET_SAMPLE_HALF_WINDOW == 10
    assert cam_manager.TARGET_SAMPLE_WINDOW == 21
    assert not any(
        str(getattr(handler, "__module__", "")).endswith(
            ("yugo_cam.addon", "yugo.camera")
        )
        for handler in bpy.app.handlers.depsgraph_update_post
    )

    # Viewport mouse navigation always passes through the modal listener.
    listener = SimpleNamespace()
    cam_manager._owner = listener
    middle_press = SimpleNamespace(type="MIDDLEMOUSE", value="PRESS")
    middle_release = SimpleNamespace(type="MIDDLEMOUSE", value="RELEASE")
    wheel = SimpleNamespace(type="WHEELUPMOUSE", value="PRESS")
    modal = cam_manager.YUGO_CAM_OT_listener_toggle.modal
    assert modal(listener, bpy.context, middle_press) == {"PASS_THROUGH"}
    assert cam_manager._viewport_navigating is True
    assert modal(listener, bpy.context, middle_release) == {"PASS_THROUGH"}
    assert cam_manager._viewport_navigating is False
    assert modal(listener, bpy.context, wheel) == {"PASS_THROUGH"}
    cam_manager._owner = None

    scene = bpy.context.scene
    camera_a = make_camera(scene, "CamManager_A", (0.0, -10.0, 0.0))
    camera_b = make_camera(scene, "CamManager_B", (3.0, -8.0, 2.0))
    target_a = bpy.data.objects.new("CamTarget_A", None)
    target_b = bpy.data.objects.new("CamTarget_B", None)
    scene.collection.objects.link(target_a)
    scene.collection.objects.link(target_b)
    target_a.location = (0.0, 0.0, 0.0)
    target_b.location = (10.0, 0.0, 0.0)
    scene.camera = camera_b
    bpy.context.view_layer.update()

    # Camera CollectionProperty writes are performed by Yugo's shared 2 Hz
    # system loop, never by Panel.draw where Blender 5.2 forbids Scene writes.
    scheduler.run_system_once()
    assert any(entry.camera == camera_a for entry in scene.yugo_cam_entries)
    assert any(entry.camera == camera_b for entry in scene.yugo_cam_entries)
    camera_a.data.lens = 72.0
    assert math.isclose(camera_a.data.lens, 72.0, abs_tol=0.0001)

    ui_now = cam_manager.time.monotonic()
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now, force=True) is True
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now + 0.1) is False
    # A stable scene reaches the 2 FPS deadline without rescanning every object.
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now + 0.5) is False
    signature_probe = bpy.data.objects.new("CamSignatureProbe", None)
    scene.collection.objects.link(signature_probe)
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now + 1.0) is True
    scene.collection.objects.unlink(signature_probe)
    bpy.data.objects.remove(signature_probe)
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now + 1.5) is True

    # Running uses Yugo's central motion loop. UI/list
    # publication is deferred until Stop or an explicit forced refresh.
    cam_manager._owner = SimpleNamespace()
    cam_manager._start_runtime_timers(scene)
    assert cam_manager._idle_ui_timer is None
    assert cam_manager._motion_timer is None
    assert cam_manager._system_timer is None
    assert cam_manager._runtime_tracking_active(scene) is True
    assert cam_manager.maybe_sync_scene_cameras(scene, ui_now + 2.0) is False
    cam_manager.stop_listener()
    assert cam_manager._motion_timer is None
    assert cam_manager._system_timer is None
    assert cam_manager._runtime_tracking_active(scene) is False
    assert cam_manager._idle_ui_timer is None

    assert any(entry.camera == camera_a for entry in scene.yugo_cam_entries)
    assert any(entry.camera == camera_b for entry in scene.yugo_cam_entries)

    cam_manager.sync_scene_cameras(scene)
    entries = {
        entry.camera.name: entry
        for entry in scene.yugo_cam_entries
        if entry.camera is not None
    }
    assert camera_a.name in entries and camera_b.name in entries
    assert scene.camera == camera_b

    entry = entries[camera_a.name]
    camera_a.name = "CamManager_Renamed"
    assert entry.camera.name == "CamManager_Renamed"
    scene.yugo_cam_index = next(
        index
        for index, candidate in enumerate(scene.yugo_cam_entries)
        if candidate.camera == camera_a
    )
    entry.preset = "CUSTOM"
    first = entry.target_objects.add()
    first.object = target_a
    first.weight = 0.25
    second = entry.target_objects.add()
    second.object = target_b
    second.weight = 0.75
    entry.target_object_index = 0

    target, position_mask, _rotation, rotation_mask, _rotation_target = (
        cam_manager._combined_target(scene, entry)
    )
    assert (target - Vector((7.5, 0.0, 0.0))).length < 0.0001
    assert position_mask == (True, True, True)
    assert rotation_mask == (False, False, False)
    assert bpy.ops.yugo_cam.target_solo(index=0) == {"FINISHED"}
    target, _position_mask, _rotation, _rotation_mask, _rotation_target = (
        cam_manager._combined_target(scene, entry)
    )
    assert target.length < 0.0001

    # Editing virtual location is an immediate user override.
    entry.location_x = 2.0
    entry.location_y = -6.0
    entry.location_z = 3.0
    bpy.context.view_layer.update()
    assert (camera_a.matrix_world.translation - Vector((2.0, -6.0, 3.0))).length < 0.0001

    # Direct Blender manipulation becomes the new virtual state and is not pulled back.
    camera_a.location = (4.0, -7.0, 1.0)
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    assert (camera_a.matrix_world.translation - Vector((4.0, -7.0, 1.0))).length < 0.0001
    cam_manager._system_update(scene, 0.5)
    assert math.isclose(entry.location_x, 4.0, abs_tol=0.0001)
    assert math.isclose(entry.location_y, -7.0, abs_tol=0.0001)
    assert math.isclose(entry.location_z, 1.0, abs_tol=0.0001)
    cam_manager.reset_entry_state(entry)

    # A viewport mouse drag owns the camera until release. Releasing syncs the
    # edited transform into the virtual state, so the next damper tick cannot
    # snap the camera back to its pre-drag position.
    viewport_context = SimpleNamespace(
        scene=scene,
        area=SimpleNamespace(type="VIEW_3D"),
        region=SimpleNamespace(type="WINDOW"),
        active_object=camera_a,
        space_data=SimpleNamespace(
            region_3d=SimpleNamespace(view_perspective="PERSP")
        ),
    )
    listener = SimpleNamespace()
    cam_manager._owner = listener
    left_press = SimpleNamespace(type="LEFTMOUSE", value="PRESS")
    left_release = SimpleNamespace(type="LEFTMOUSE", value="RELEASE")
    modal = cam_manager.YUGO_CAM_OT_listener_toggle.modal
    assert modal(listener, viewport_context, left_press) == {"PASS_THROUGH"}
    assert cam_manager._transform_active is True
    camera_a.location = (6.0, -5.0, 2.0)
    target_a.location.x = 2.0
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    assert (camera_a.matrix_world.translation - Vector((6.0, -5.0, 2.0))).length < 0.0001
    assert math.isclose(
        cam_manager._entry_state(entry).target_position_samples[-1].x,
        2.0,
        abs_tol=0.0001,
    )
    assert modal(listener, viewport_context, left_release) == {"PASS_THROUGH"}
    assert cam_manager._transform_active is False
    cam_manager.tick(scene, 1.0 / 60.0)
    assert (camera_a.matrix_world.translation - Vector((6.0, -5.0, 2.0))).length < 0.0001
    assert (cam_manager._entry_state(entry).virtual_location - Vector((6.0, -5.0, 2.0))).length < 0.0001
    cam_manager._owner = None
    target_a.location.x = 0.0

    # Position targets make the virtual camera rotation look at the blended point.
    cam_manager.invalidate_entry_state(entry)
    cam_manager.tick(scene, 1.0 / 60.0)
    forward = camera_a.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    to_target = (
        target_a.matrix_world.translation - camera_a.matrix_world.translation
    ).normalized()
    assert forward.normalized().dot(to_target) > 0.999

    # Every 60 FPS tick is cached. Once samples 0..20 are present, the
    # ten-before/current/ten-after centered mean is exactly 10, and the final
    # forced Look At uses the resulting virtual target after camera location.
    entry.target_smooth = 0.0
    cam_manager.invalidate_entry_state(entry)
    for sample_x in range(21):
        target_a.location.x = float(sample_x)
        bpy.context.view_layer.update()
        cam_manager.tick(scene, 1.0 / 60.0)
    state = cam_manager._entry_state(entry)
    assert len(state.target_position_samples) == 21
    assert math.isclose(state.target_mean_position.x, 10.0, abs_tol=0.0001)
    assert math.isclose(state.solo_target_position.x, 10.0, abs_tol=0.0001)
    forward = camera_a.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    to_virtual_target = (
        state.solo_target_position - camera_a.matrix_world.translation
    ).normalized()
    assert forward.normalized().dot(to_virtual_target) > 0.999
    target_a.location.x = 21.0
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    assert math.isclose(state.target_mean_position.x, 11.0, abs_tol=0.0001)
    target_a.location.x = 0.0
    cam_manager.invalidate_entry_state(entry)
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)

    # Position tracking owns Camera rotation. A readback mismatch must not be
    # mistaken for manual interaction and pause tracking for 0.2 seconds.
    camera_a.rotation_euler.z += math.radians(2.0)
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    forward = camera_a.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    to_target = (
        target_a.matrix_world.translation - camera_a.matrix_world.translation
    ).normalized()
    assert forward.normalized().dot(to_target) > 0.999
    assert cam_manager._entry_state(entry).manual_until <= cam_manager.time.monotonic()

    # Target Smooth processes ordinary animated target motion every 60 FPS tick,
    # not only Solo changes. Camera aim continues between source-pose samples.
    entry.target_smooth = 0.4
    target_a.location.x = 1.0
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    state = cam_manager._entry_state(entry)
    first_mean_x = state.target_mean_position.x
    first_virtual_x = state.solo_target_position.x
    for _step in range(11):
        cam_manager.tick(scene, 1.0 / 60.0)
    assert state.target_mean_position.x > first_mean_x
    assert state.solo_target_position.x > first_virtual_x
    target_a.location.x = 0.0
    entry.target_smooth = 0.0
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)

    # Optional diagnostics print six-Hz raw/smoothed target samples without a
    # second app timer.
    entry.debug_target_data = True
    debug_output = io.StringIO()
    with redirect_stdout(debug_output):
        for _step in range(11):
            cam_manager.tick(scene, 1.0 / 60.0)
    debug_text = debug_output.getvalue()
    assert "[YUGO_CAM_TARGET]" in debug_text
    assert "raw_changes=" in debug_text
    assert "mean21=" in debug_text
    assert "manual_skips=" in debug_text
    assert "[YUGO_CAM_TARGET_ROW]" in debug_text
    snapshot = cam_manager._target_debug_snapshots[cam_manager._state_key(entry)]
    assert any("Raw Changes" in line for line in snapshot["panel"])
    assert bpy.ops.yugo_cam.copy_target_debug() == {"FINISHED"}
    entry.debug_target_data = False

    # Solo changes move the virtual look target through Target Smooth.
    entry.target_smooth = 0.5
    assert bpy.ops.yugo_cam.target_solo(index=1) == {"FINISHED"}
    cam_manager.tick(scene, 1.0 / 60.0)
    state = cam_manager._entry_state(entry)
    assert state.solo_transition_active is True
    assert 0.0 < state.solo_target_position.x < 10.0
    assert bpy.ops.yugo_cam.target_solo(index=0) == {"FINISHED"}
    entry.target_smooth = 0.0
    cam_manager.tick(scene, 1.0 / 60.0)
    assert state.solo_target_position.length < 0.0001

    entry.target_offset_x = 0.75
    entry.target_offset_y = 0.25
    assert math.isclose(camera_a.data.shift_x, 0.25, abs_tol=0.0001)

    assert bpy.ops.yugo_cam.keybind_add() == {"FINISHED"}
    binding = entry.keybinds[0]
    binding.key = "W"
    binding.affects = "CHANGE_POSITION"
    binding.amount_x = 10.0
    assert binding.runtime_id
    assert binding.preserve_blender_key is True
    event = SimpleNamespace(
        type="W", value="PRESS", shift=False, ctrl=False, is_repeat=False
    )
    matched, consume = cam_manager._process_key_event(scene, event)
    assert matched is True and consume is False
    assert cam_manager._runtime_hold_motion_busy(scene) is True
    assert cam_manager.maybe_sync_scene_cameras(
        scene, ui_now + 10.0
    ) is False
    before = camera_a.matrix_world.translation.copy()
    ui_before = cam_manager._entry_location(entry)
    view_direction = camera_a.matrix_world.to_quaternion() @ Vector((1.0, 0.0, 0.0))
    cam_manager.tick(scene, 1.0 / 60.0)
    first_distance = (camera_a.matrix_world.translation - before).length
    assert (
        camera_a.matrix_world.translation
        - (before + (view_direction * (1.0 / 60.0)))
    ).length < 0.0001
    assert (cam_manager._entry_location(entry) - ui_before).length < 0.0001
    cam_manager._system_update(scene, 0.5)
    assert (cam_manager._entry_location(entry) - ui_before).length < 0.0001

    # Held movement stays responsive at 60 FPS while its acceleration multiplier
    # changes only on the slower 12 Hz sampling boundary.
    state = cam_manager._entry_state(entry)
    key = cam_manager._binding_key(entry, binding, 0)
    assert math.isclose(state.move_multipliers[key], 1.0, abs_tol=0.0001)
    for _step in range(3):
        cam_manager.tick(scene, 1.0 / 60.0)
    assert math.isclose(state.move_multipliers[key], 1.0, abs_tol=0.0001)
    after_sample = camera_a.matrix_world.translation.copy()
    for _step in range(2):
        cam_manager.tick(scene, 1.0 / 60.0)
    accelerated_distance = (
        camera_a.matrix_world.translation - after_sample
    ).length / 2.0
    assert state.move_multipliers[key] > 1.0
    assert accelerated_distance > first_distance

    binding.amount_x = 0.0
    for _step in range(20):
        cam_manager._apply_keybind_motion(entry, state, 0.1)
    assert math.isclose(state.move_multipliers[key], 10.0, abs_tol=0.0001)

    release = SimpleNamespace(
        type="W", value="RELEASE", shift=False, ctrl=False, is_repeat=False
    )
    cam_manager._process_key_event(scene, release)
    half_sample = cam_manager.MOVE_SAMPLE_INTERVAL * 0.5
    cam_manager._apply_keybind_motion(entry, state, half_sample)
    first_decay = 10.0 - (cam_manager.MOVE_DECAY_PER_SECOND * half_sample)
    assert math.isclose(state.move_multipliers[key], first_decay, abs_tol=0.0001)
    cam_manager._apply_keybind_motion(entry, state, half_sample)
    expected_decay = 10.0 - (
        cam_manager.MOVE_DECAY_PER_SECOND * cam_manager.MOVE_SAMPLE_INTERVAL
    )
    assert math.isclose(
        state.move_multipliers[key], expected_decay, abs_tol=0.0001
    )
    for _step in range(20):
        cam_manager._apply_keybind_motion(entry, state, 0.1)
    assert key not in state.move_multipliers
    assert cam_manager._runtime_hold_motion_busy(scene) is False
    cam_manager._system_update(scene, 0.5)
    assert (
        cam_manager._entry_location(entry)
        - cam_manager._entry_state(entry).virtual_location
    ).length < 0.0001

    binding.preserve_blender_key = False
    _matched, consume = cam_manager._process_key_event(scene, event)
    assert consume is True
    cam_manager._process_key_event(scene, release)

    # Rotation targets orbit virtual position using their complete world rotation.
    first.position_enabled = False
    first.rotation_enabled = True
    entry.target_smooth = 0.0
    target_a.rotation_euler = (0.0, 0.0, 0.0)
    entry.location_x = 0.0
    entry.location_y = -5.0
    entry.location_z = 0.0
    bpy.context.view_layer.update()
    cam_manager.invalidate_entry_state(entry)
    cam_manager.tick(scene, 1.0 / 60.0)
    target_a.rotation_euler.z = math.radians(90.0)
    bpy.context.view_layer.update()
    cam_manager.tick(scene, 1.0 / 60.0)
    assert (
        camera_a.matrix_world.translation - Vector((5.0, 0.0, 0.0))
    ).length < 0.001

    # While Rotation Follow is enabled, view-space movement edits the captured
    # target-local offset instead of directly changing the virtual world position.
    binding.amount_x = 10.0
    binding.preserve_blender_key = True
    state = cam_manager._entry_state(entry)
    state.move_multipliers.clear()
    state.move_sample_accumulators.clear()
    move_before = camera_a.matrix_world.translation.copy()
    move_delta = (
        camera_a.matrix_world.to_quaternion()
        @ Vector((1.0, 0.0, 0.0))
    ) * (1.0 / 60.0)
    cam_manager._process_key_event(scene, event)
    cam_manager.tick(scene, 1.0 / 60.0)
    assert (
        camera_a.matrix_world.translation - (move_before + move_delta)
    ).length < 0.001
    cam_manager._process_key_event(scene, release)

    # Follow presets build their common settings and six movement bindings.
    assert "CIRCLE_FOLLOW" not in {item[0] for item in cam_manager.CAMERA_PRESETS}
    fake_vrm = SimpleNamespace(
        type="ARMATURE",
        data=SimpleNamespace(
            vrm_addon_extension=SimpleNamespace(
                spec_version="1.0",
                vrm1=SimpleNamespace(
                    humanoid=SimpleNamespace(
                        human_bones=SimpleNamespace(
                            head=SimpleNamespace(
                                node=SimpleNamespace(bone_name="MappedHead")
                            )
                        )
                    )
                ),
            )
        ),
        pose=SimpleNamespace(bones={"MappedHead": object()}),
    )
    assert cam_manager._vrm_head_bone_name(fake_vrm) == "MappedHead"

    target_a.rotation_euler.z = 0.0
    entry.preset = "PAN_FOLLOW"
    assert all(
        math.isclose(
            getattr(entry, f"location_smooth_{axis}"), 0.7, abs_tol=0.0001
        )
        for axis in cam_manager.AXES
    )
    assert math.isclose(entry.target_smooth, 0.4, abs_tol=0.0001)
    assert len(entry.target_objects) == 1
    assert entry.target_objects[0].position_enabled is True
    assert entry.target_objects[0].rotation_enabled is False
    assert [item.key for item in entry.keybinds] == [
        "UP_ARROW",
        "DOWN_ARROW",
        "LEFT_ARROW",
        "RIGHT_ARROW",
        "PAGE_UP",
        "PAGE_DOWN",
    ]
    assert all(item.mode == "HOLD" for item in entry.keybinds)
    assert all(item.affects == "CHANGE_POSITION" for item in entry.keybinds)
    assert all(item.preserve_blender_key is False for item in entry.keybinds)
    assert [
        (item.amount_x, item.amount_y, item.amount_z) for item in entry.keybinds
    ] == [item[1:] for item in cam_manager.PAN_FOLLOW_KEYBINDS]

    entry.preset = "AROUND_FOLLOW"
    assert entry.target_objects[0].position_enabled is True
    assert entry.target_objects[0].rotation_enabled is True

    entry.preset = "PAN_FOLLOW"
    first = entry.target_objects[0]
    first.object = target_a
    entry.location_x = 0.0
    entry.location_y = -10.0
    entry.location_z = 0.0
    entry.location_smooth_x = 0.0
    entry.location_smooth_y = 0.0
    entry.location_smooth_z = 0.0
    entry.use_lock_distance = True
    entry.lock_distance = 5.0
    entry.target_smooth = 0.0
    cam_manager.invalidate_entry_state(entry)
    cam_manager.tick(scene, 1.0)
    assert math.isclose(
        (camera_a.matrix_world.translation - target_a.matrix_world.translation).length,
        5.0,
        abs_tol=0.001,
    )

    assert bpy.ops.yugo_cam.set_scene_camera(index=scene.yugo_cam_index) == {"FINISHED"}
    assert scene.camera == camera_a

    scene.collection.objects.unlink(camera_b)
    cam_manager.sync_scene_cameras(scene)
    assert all(item.camera != camera_b for item in scene.yugo_cam_entries)
    yugo.unregister()
    assert cam_manager._idle_ui_timer is None
    print("CAM_MANAGER_TEST_OK")


run()
