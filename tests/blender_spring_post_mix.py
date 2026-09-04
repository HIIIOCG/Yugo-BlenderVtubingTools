"""Validate integrated Spring Bone ordering, multi-avatar, and RNA rebinding."""

from types import SimpleNamespace
import sys

import bpy


WORKSPACE = r"D:\Projects\BCON26\motion_codedev"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import yugo
from yugo import motion_mixer
from yugo.springbone import runtime
from yugo.springbone import visualization
from yugo.state import ensure_runtime_id


def make_armature(name, x_offset=0.0):
    data = bpy.data.armatures.new(f"{name}Data")
    armature = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    body = data.edit_bones.new("Body")
    body.head = (x_offset, 0.0, 0.0)
    body.tail = (x_offset, 0.0, 1.0)
    root = data.edit_bones.new("HairRoot")
    root.head = body.tail
    root.tail = (x_offset, 0.0, 2.0)
    root.parent = body
    tip = data.edit_bones.new("HairTip")
    tip.head = root.tail
    tip.tail = (x_offset, 0.0, 3.0)
    tip.parent = root
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.select_set(False)
    return armature


def make_extension():
    joint_a = SimpleNamespace(
        node=SimpleNamespace(bone_name="HairRoot"),
        hit_radius=0.1,
        stiffness=1.0,
        drag_force=0.2,
        gravity_dir=(1.0, 0.0, 0.0),
        gravity_power=0.5,
    )
    joint_b = SimpleNamespace(
        node=SimpleNamespace(bone_name="HairTip"),
        hit_radius=0.0,
        stiffness=1.0,
        drag_force=0.2,
        gravity_dir=(1.0, 0.0, 0.0),
        gravity_power=0.5,
    )
    spring_bone = SimpleNamespace(
        enable_animation=False,
        springs=[
            SimpleNamespace(
                joints=[joint_a, joint_b],
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


def add_avatar_layer(scene, armature):
    avatar = scene.yugo_avatars.add()
    ensure_runtime_id(avatar)
    avatar.armature = armature
    layer = avatar.motion_layers.add()
    ensure_runtime_id(layer)
    layer.source_type = "SPRING_BONE"
    layer.name = "SpringBone_001"
    return avatar, layer


def main():
    yugo.register()
    scene = bpy.context.scene
    armature_a = make_armature("YugoSpringA")
    armature_b = make_armature("YugoSpringB", 4.0)
    avatar_a, layer_a = add_avatar_layer(scene, armature_a)
    avatar_b, layer_b = add_avatar_layer(scene, armature_b)
    extension_by_name = {
        armature_a.name: make_extension(),
        armature_b.name: make_extension(),
    }

    events = []
    solve_calls = {}
    view_update_calls = [0]
    original_get = runtime.get_vrm_extension
    original_step = runtime.Vrm1SpringBoneSolver.step
    original_view_update = runtime._update_view_layer
    original_apply_scene = motion_mixer.apply_scene

    def counted_internal_step(self, delta_time):
        name = self.armature.name
        solve_calls[name] = solve_calls.get(name, 0) + 1
        events.append(f"spring:{name}")
        return original_step(self, delta_time)

    def counted_view_update(context):
        view_update_calls[0] += 1
        return original_view_update(context)

    def mixed_apply_scene(target_scene, target_armature, delta_time=motion_mixer.APPLY_INTERVAL):
        events.append(f"mix:{target_armature.name}")
        body = target_armature.pose.bones["Body"]
        body.rotation_mode = "QUATERNION"
        body.rotation_quaternion = (0.968912, 0.0, 0.247404, 0.0)
        original_apply_scene(target_scene, target_armature, delta_time)

    runtime.get_vrm_extension = lambda armature: extension_by_name.get(armature.name)
    runtime.Vrm1SpringBoneSolver.step = counted_internal_step
    runtime._update_view_layer = counted_view_update
    motion_mixer.apply_scene = mixed_apply_scene
    try:
        layer_a.spring_show_hit_radius = True
        runtime.start(bpy.context, scene, layer_a, armature_a)
        runtime.start(bpy.context, scene, layer_b, armature_b)
        assert runtime.is_layer_running(layer_a)
        assert runtime.is_layer_running(layer_b)
        assert len(
            [
                obj
                for obj in bpy.data.objects
                if str(obj.get(visualization.OWNER_PROPERTY, ""))
                == layer_a.runtime_id
            ]
        ) == 1

        # Stopping physics keeps the requested hit-radius display. Only the
        # Show Hit Radius checkbox owns its visibility.
        runtime.stop(layer_a)
        assert len(
            [
                obj
                for obj in bpy.data.objects
                if str(obj.get(visualization.OWNER_PROPERTY, ""))
                == layer_a.runtime_id
            ]
        ) == 1
        layer_a.spring_show_hit_radius = False
        assert not any(
            str(obj.get(visualization.OWNER_PROPERTY, "")) == layer_a.runtime_id
            for obj in bpy.data.objects
        )
        runtime.start(bpy.context, scene, layer_a, armature_a)

        # Each avatar is evaluated once by Yugo's cached internal solver after
        # both final mixed poses have already been written. VRM Add-on's own
        # handler remains disabled, preventing a second simulation pass.
        motion_mixer._last_apply_time = 0.0
        view_update_calls[0] = 0
        motion_mixer._apply_timer()
        assert events[:2] == ["mix:YugoSpringA", "mix:YugoSpringB"]
        assert set(events[2:4]) == {
            "spring:YugoSpringA",
            "spring:YugoSpringB",
        }
        assert solve_calls == {"YugoSpringA": 1, "YugoSpringB": 1}
        # One dependency refresh exposes the final mixed pose to physics.
        # Write-back is left tagged for Blender's normal event loop instead of
        # forcing a second synchronous whole-view-layer refresh.
        assert view_update_calls[0] == 1
        assert not extension_by_name[armature_a.name].spring_bone1.enable_animation
        assert not extension_by_name[armature_b.name].spring_bone1.enable_animation

        # Auto resolves to a 24 FPS Blender-pose sync. Internal physics still
        # advances at 60 Hz, while ticks between snapshots display SLERP.
        layer_b.enabled = False
        target_a = runtime._targets[layer_a.runtime_id]
        assert layer_a.spring_physics_rate == "AUTO"
        assert target_a.resolved_physics_fps() == 24
        runtime.reset(layer_a)
        calls_before = solve_calls[armature_a.name]
        updates_before = view_update_calls[0]
        original_output_write = runtime._set_rotation_without_mode_change
        write_attempts = []

        def counted_output_write(pose_bone, rotation):
            write_attempts[-1] += 1
            return original_output_write(pose_bone, rotation)

        runtime._set_rotation_without_mode_change = counted_output_write
        try:
            write_attempts.append(0)
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
            first_angle = (
                armature_a.pose.bones["HairRoot"]
                .matrix_basis.to_quaternion()
                .angle
            )
            assert solve_calls[armature_a.name] == calls_before + 1
            write_attempts.append(0)
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
            second_angle = (
                armature_a.pose.bones["HairRoot"]
                .matrix_basis.to_quaternion()
                .angle
            )
        finally:
            runtime._set_rotation_without_mode_change = original_output_write
        assert solve_calls[armature_a.name] == calls_before + 2
        assert view_update_calls[0] == updates_before + 1
        assert second_angle > first_angle
        # The buffered solver never writes an un-interpolated physics pose to
        # Blender. Each display tick has at most one output attempt per joint.
        assert all(
            attempts <= len(target_a.current_physics_rotations)
            for attempts in write_attempts
        ), write_attempts

        # Editable VRM settings are not scanned on every 60 Hz mixer tick.
        # Yugo checks a cached signature twice per second and rebuilds only
        # when that signature actually changes.
        settings_checks = [0]
        original_settings_changed = target_a.solver.settings_changed

        def counted_settings_changed():
            settings_checks[0] += 1
            return original_settings_changed()

        target_a.solver.settings_changed = counted_settings_changed
        target_a.configuration_check_accumulator = 0.0
        for _index in range(29):
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert settings_checks[0] == 0
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert settings_checks[0] == 1
        target_a.solver.settings_changed = original_settings_changed

        previous_solver = target_a.solver
        extension_by_name[armature_a.name].spring_bone1.springs[0].joints[
            0
        ].stiffness = 1.25
        for _index in range(31):
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert target_a.solver is not previous_solver

        # Full Weight skips the extra pre-physics rotation snapshot used only
        # by partial-weight blending. Interpolation snapshots remain required.
        original_capture = target_a.capture_rotations
        capture_calls = [0]

        def counted_capture():
            capture_calls[0] += 1
            return original_capture()

        target_a.capture_rotations = counted_capture
        layer_a.spring_physics_rate = "FPS_60"
        layer_a.weight = 1.0
        target_a.reset_output_state()
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        full_weight_captures = capture_calls[0]
        capture_calls[0] = 0
        layer_a.weight = 0.5
        target_a.reset_output_state()
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        partial_weight_captures = capture_calls[0]
        assert partial_weight_captures == full_weight_captures + 1
        target_a.capture_rotations = original_capture

        # Explicit rates keep their own pose-sync cadence while physics itself
        # advances at a fixed 60 Hz. A 12 FPS snapshot rate therefore still
        # performs one internal Verlet step on each mixer tick.
        calls_before = solve_calls[armature_a.name]
        layer_a.spring_physics_rate = "FPS_60"
        target_a.reset_output_state()
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert solve_calls[armature_a.name] == calls_before + 2
        calls_before = solve_calls[armature_a.name]
        layer_a.spring_physics_rate = "FPS_12"
        target_a.reset_output_state()
        for _index in range(4):
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert solve_calls[armature_a.name] == calls_before + 4
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert solve_calls[armature_a.name] == calls_before + 5

        # A slow Blender frame must not trigger multiple catch-up solves. That
        # feedback loop caused 60 FPS mode to fall from about 30 to 21 FPS.
        layer_a.spring_physics_rate = "FPS_60"
        target_a.reset_output_state()
        assert target_a.advance_physics_clock(1.0 / 20.0)[2] == 1

        # Every selectable low sync rate advances the same sixty fixed Verlet
        # steps per second and reaches the same internal tail state as 60 FPS
        # under identical input. Lower rates must not make Spring motion limp.
        def tail_signature():
            return tuple(
                tuple(pair.current_tail)
                for spring in target_a.solver.springs
                for chain in spring.chains
                for pair in chain.pairs
            )

        layer_a.spring_physics_rate = "FPS_60"
        runtime.reset(layer_a)
        calls_before = solve_calls[armature_a.name]
        for _index in range(60):
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert solve_calls[armature_a.name] == calls_before + 60
        sixty_fps_tails = tail_signature()

        for rate in (
            "FPS_8",
            "FPS_12",
            "FPS_14",
            "FPS_16",
            "FPS_24",
            "FPS_30",
        ):
            layer_a.spring_physics_rate = rate
            runtime.reset(layer_a)
            calls_before = solve_calls[armature_a.name]
            for _index in range(60):
                runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
            assert solve_calls[armature_a.name] == calls_before + 60, (
                rate,
                solve_calls[armature_a.name] - calls_before,
            )
            low_rate_tails = tail_signature()
            assert len(low_rate_tails) == len(sixty_fps_tails)
            assert all(
                sum((left - right) ** 2 for left, right in zip(low, sixty))
                <= 1.0e-10
                for low, sixty in zip(low_rate_tails, sixty_fps_tails)
            ), rate

        layer_a.spring_physics_rate = "AUTO"
        layer_a.weight = 1.0
        layer_b.enabled = True

        # Overall layer Weight blends the simulated rotation without stopping
        # the internal solver.
        runtime.reset(layer_a)
        layer_a.weight = 0.0
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert abs(armature_a.pose.bones["HairRoot"].rotation_euler.x) < 1.0e-6
        layer_a.weight = 1.0

        # Disabling a Spring Bone layer returns only its controlled joints to
        # reset pose and leaves the other avatar running.
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        layer_a.enabled = False
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert abs(armature_a.pose.bones["HairRoot"].rotation_euler.x) < 1.0e-6
        assert runtime.is_layer_running(layer_a)
        layer_a.enabled = True

        # Simulate Blender replacing CollectionProperty RNA during Undo. The
        # runtime must rebind by stable ID instead of retaining the old Layer.
        layer_b_id = layer_b.runtime_id
        runtime.prepare_undo()
        avatar_b.motion_layers.remove(0)
        restored_layer_b = avatar_b.motion_layers.add()
        restored_layer_b.source_type = "SPRING_BONE"
        restored_layer_b.runtime_id = layer_b_id
        runtime.finish_undo()
        assert runtime.is_layer_running(restored_layer_b)
        assert restored_layer_b.connected
        runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)

        # Exercise Blender's real Undo/Redo handlers while physics is live.
        layer_a_id = layer_a.runtime_id
        bpy.ops.ed.undo_push(message="Yugo Spring Baseline")
        armature_a.location.x = 1.0
        bpy.context.view_layer.update()
        bpy.ops.ed.undo_push(message="Yugo Spring Moved")
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        armature_a = bpy.data.objects["YugoSpringA"]
        armature_b = bpy.data.objects["YugoSpringB"]
        layer_a = next(
            layer
            for avatar in scene.yugo_avatars
            for layer in avatar.motion_layers
            if layer.runtime_id == layer_a_id
        )
        assert runtime.is_layer_running(layer_a)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        armature_a = bpy.data.objects["YugoSpringA"]
        armature_b = bpy.data.objects["YugoSpringB"]
        layer_a = next(
            layer
            for avatar in scene.yugo_avatars
            for layer in avatar.motion_layers
            if layer.runtime_id == layer_a_id
        )
        assert runtime.is_layer_running(layer_a)

        # The integrated source keeps the standalone plug-in's VRM 0.x
        # Secondary Animation solver as a per-avatar backend.
        armature_c = make_armature("YugoSpringVrm0", 8.0)
        avatar_c, layer_c = add_avatar_layer(scene, armature_c)
        bone_group = SimpleNamespace(
            bones=[SimpleNamespace(bone_name="HairRoot")],
            collider_groups=[],
            center=SimpleNamespace(bone_name=""),
            stiffiness=0.25,
            drag_force=0.2,
            gravity_dir=(1.0, 0.0, 0.0),
            gravity_power=1.0,
            hit_radius=0.02,
        )
        extension_by_name[armature_c.name] = SimpleNamespace(
            spec_version="0.0",
            is_vrm1=lambda: False,
            is_vrm0=lambda: True,
            vrm0=SimpleNamespace(
                secondary_animation=SimpleNamespace(
                    bone_groups=[bone_group],
                    collider_groups=[],
                )
            ),
        )
        runtime.start(bpy.context, scene, layer_c, armature_c)
        assert runtime.current_mode(layer_c) == "VRM 0.x"
        for _index in range(12):
            runtime._post_mix_update(bpy.context, runtime.TICK_INTERVAL)
        assert armature_c.pose.bones["HairRoot"].matrix_basis.to_quaternion().angle > 0.001
        print("YUGO_SPRING_MOTION_LAYER_INTEGRATION_OK")
    finally:
        runtime.stop_all(reset_pose=True)
        motion_mixer.apply_scene = original_apply_scene
        runtime.get_vrm_extension = original_get
        runtime.Vrm1SpringBoneSolver.step = original_step
        runtime._update_view_layer = original_view_update
        yugo.unregister()


if __name__ == "__main__":
    main()
