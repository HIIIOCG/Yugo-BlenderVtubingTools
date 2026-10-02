"""Per-Motion-Layer VRM Spring Bone simulation after Yugo's final mix."""

from __future__ import annotations

import time

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from ..state import (
    ensure_runtime_id,
    iter_avatars,
    layer_locator,
    layers_for_armature,
    resolve_layer_locator,
)
from .vrm import (
    get_vrm_extension,
    is_vrm0,
    is_vrm1,
    spring_bone_data,
    spring_bone_stats,
    spring_joint_bone_names,
)
from .vrm0 import Vrm0SpringBoneSolver
from .vrm1 import Vrm1SpringBoneSolver
from . import visualization


TICK_INTERVAL = 1.0 / 60.0
FIXED_PHYSICS_STEP = 1.0 / 60.0
ROTATION_DOT_EPSILON = 1.0e-10
PHYSICS_RATE_FPS = {
    "AUTO": 24,
    "FPS_60": 60,
    "FPS_30": 30,
    "FPS_24": 24,
    "FPS_16": 16,
    "FPS_14": 14,
    "FPS_12": 12,
    "FPS_8": 8,
}
POST_APPLY_CALLBACKS_KEY = "yugo.motion_mixer.post_apply_callbacks"
CALLBACK_NAME = "yugo.springbone.layers"
STANDALONE_CALLBACK_NAME = "yugo_springbone.preview"

_targets = {}
_last_time = 0.0
_undo_in_progress = False


def _update_view_layer(context) -> None:
    context.view_layer.update()


def _set_reset_pose(armature, bone_names) -> None:
    pose = getattr(armature, "pose", None)
    bones = getattr(pose, "bones", None)
    if bones is None:
        return
    for name in bone_names:
        bone = bones.get(name)
        if bone is None:
            continue
        try:
            bone.matrix_basis = Matrix.Identity(4)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            continue


def _set_rotation_without_mode_change(pose_bone, rotation) -> bool:
    rotation.normalize()
    current = pose_bone.matrix_basis.to_quaternion()
    current.normalize()
    if 1.0 - abs(current.dot(rotation)) <= ROTATION_DOT_EPSILON:
        return False
    mode = pose_bone.rotation_mode
    if mode == "QUATERNION":
        pose_bone.rotation_quaternion = rotation
    elif mode == "AXIS_ANGLE":
        axis, angle = rotation.to_axis_angle()
        pose_bone.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pose_bone.rotation_euler = rotation.to_euler(mode, pose_bone.rotation_euler)
    return True


def _timeline_is_playing() -> bool:
    screen = getattr(bpy.context, "screen", None)
    return bool(screen and getattr(screen, "is_animation_playing", False))


def _layer_active(scene, armature, layer) -> bool:
    try:
        layers = layers_for_armature(scene, armature)
        any_solo = any(candidate.solo for candidate in layers)
        return bool(layer.enabled and (not any_solo or layer.solo))
    except (AttributeError, ReferenceError):
        return False


class _Target:
    def __init__(self, context, scene, layer, armature):
        self.locator = layer_locator(scene, layer)
        if self.locator is None:
            raise RuntimeError("Could not locate the Spring Bone Motion Layer")
        self.owner_id = ensure_runtime_id(layer)
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.armature_name = str(armature.name)
        self.extension = None
        self.stats = None
        self.spring_bone = None
        self.solver = None
        self.mode = ""
        self.bone_names = ()
        self.previous_enable_animation = False
        self.was_active = False
        self.last_error = ""
        self.configuration_check_accumulator = 0.0
        self.physics_rate = ""
        self.physics_accumulator = 0.0
        self.needs_initial_solve = True
        self.previous_physics_rotations = {}
        self.current_physics_rotations = {}
        self._initialize(context, reset_pose=True, preserve_previous=False)

    def resolve_live(self) -> bool:
        resolved = resolve_layer_locator(self.locator)
        if resolved is None:
            self.scene = None
            self.layer = None
            self.armature = None
            return False
        scene, _avatar, layer, armature = resolved
        if str(layer.source_type) != "SPRING_BONE":
            return False
        if armature is None or getattr(armature, "type", "") != "ARMATURE":
            return False
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.armature_name = str(armature.name)
        return True

    def _initialize(
        self,
        context,
        *,
        reset_pose: bool,
        preserve_previous: bool,
    ) -> None:
        extension = get_vrm_extension(self.armature)
        if extension is None:
            raise RuntimeError("Select an armature containing VRM Add-on data")
        stats = spring_bone_stats(extension)
        if stats.springs <= 0:
            raise RuntimeError("The selected VRM has no Spring Bone springs")

        previous_enable = self.previous_enable_animation
        spring_bone = None
        solver = None
        if is_vrm1(extension):
            if stats.joints < 2:
                raise RuntimeError("A VRM 1.0 Spring Bone spring needs at least two joints")
            spring_bone = spring_bone_data(extension)
            bone_names = spring_joint_bone_names(extension)
            mode = "VRM 1.0"
            if not preserve_previous:
                previous_enable = bool(
                    getattr(spring_bone, "enable_animation", False)
                )
            spring_bone.enable_animation = False
            if reset_pose:
                _set_reset_pose(self.armature, bone_names)
                _update_view_layer(context)
            solver = Vrm1SpringBoneSolver(self.armature, extension)
            bone_names = solver.bone_names
        elif is_vrm0(extension):
            if stats.joints < 1:
                raise RuntimeError(
                    "A VRM 0.x Spring Bone group needs at least one root bone"
                )
            if reset_pose:
                secondary = getattr(getattr(extension, "vrm0", None), "secondary_animation", None)
                roots = []
                for group in getattr(secondary, "bone_groups", ()) or ():
                    for reference in getattr(group, "bones", ()) or ():
                        name = str(getattr(reference, "bone_name", "") or "")
                        root = self.armature.pose.bones.get(name)
                        if root is not None:
                            stack = [root]
                            while stack:
                                bone = stack.pop()
                                if bone.name not in roots:
                                    roots.append(bone.name)
                                    stack.extend(bone.children)
                _set_reset_pose(self.armature, roots)
                _update_view_layer(context)
            solver = Vrm0SpringBoneSolver(self.armature, extension)
            bone_names = solver.bone_names
            mode = "VRM 0.x"
        else:
            raise RuntimeError("The selected armature has an unsupported VRM version")

        self.extension = extension
        self.stats = stats
        self.spring_bone = spring_bone
        self.solver = solver
        self.mode = mode
        self.bone_names = tuple(bone_names)
        self.previous_enable_animation = previous_enable
        self.was_active = False
        self.last_error = ""
        self.configuration_check_accumulator = 0.0
        self.reset_output_state()
        self.layer.connected = True
        self.layer.spring_mode = mode
        self.layer.spring_last_error = ""

    def active(self) -> bool:
        if not self.resolve_live():
            return False
        if not _layer_active(self.scene, self.armature, self.layer):
            return False
        if self.layer.spring_pause_during_playback and _timeline_is_playing():
            return False
        return True

    def capture_rotations(self):
        captured = {}
        bones = getattr(getattr(self.armature, "pose", None), "bones", None)
        if bones is None:
            return captured
        for name in self.bone_names:
            bone = bones.get(name)
            if bone is not None:
                captured[name] = bone.matrix_basis.to_quaternion()
        return captured

    def apply_rotations(self, rotations) -> int:
        bones = getattr(getattr(self.armature, "pose", None), "bones", None)
        if bones is None:
            return 0
        changed = 0
        for name, rotation in rotations.items():
            bone = bones.get(name)
            if bone is not None:
                changed += int(
                    _set_rotation_without_mode_change(bone, rotation.copy())
                )
        return changed

    def weight(self) -> float:
        try:
            return min(max(float(self.layer.weight), 0.0), 1.0)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            return 0.0

    def capture_weight_base(self):
        # The default full-weight path does not need an additional copy of
        # every Spring rotation just to blend it back onto itself.
        return None if self.weight() >= 1.0 else self.capture_rotations()

    def resolved_physics_fps(self) -> int:
        try:
            rate = str(self.layer.spring_physics_rate)
        except (AttributeError, ReferenceError):
            rate = "AUTO"
        return PHYSICS_RATE_FPS.get(rate, 24)

    def advance_physics_clock(self, elapsed: float):
        try:
            rate = str(self.layer.spring_physics_rate)
        except (AttributeError, ReferenceError):
            rate = "AUTO"
        fps = PHYSICS_RATE_FPS.get(rate, 24)
        interval = 1.0 / float(fps)
        if rate != self.physics_rate:
            self.physics_rate = rate
            self.physics_accumulator = 0.0
            self.needs_initial_solve = True

        elapsed = max(float(elapsed), 0.0)
        # Never catch up multiple physics steps from wall-clock lag. Doing so
        # creates a feedback loop: a slow frame requests more solves, which
        # makes the next frame slower and requests still more solves. Every
        # Yugo mixer tick advances exactly one fixed 1/60-second Verlet step.
        substeps = 1

        if fps >= 60:
            self.physics_accumulator = 0.0
            self.needs_initial_solve = False
            return True, 1.0, substeps

        self.physics_accumulator += elapsed
        due = self.needs_initial_solve or self.physics_accumulator >= interval
        if due:
            if self.physics_accumulator >= interval:
                self.physics_accumulator %= interval
            self.needs_initial_solve = False
        alpha = min(max(self.physics_accumulator / interval, 0.0), 1.0)
        return due, alpha, substeps

    def prepare_solve(self) -> None:
        if self.current_physics_rotations:
            # Swap immutable-per-step dictionaries instead of copying every
            # quaternion. The solver returns a fresh result buffer below.
            self.previous_physics_rotations = self.current_physics_rotations
        else:
            self.previous_physics_rotations = self.capture_rotations()

    def finish_solve(self, rotations) -> None:
        self.current_physics_rotations = rotations
        if not self.previous_physics_rotations:
            self.previous_physics_rotations = self.current_physics_rotations

    def apply_output(self, alpha: float, weight_base) -> int:
        bones = getattr(getattr(self.armature, "pose", None), "bones", None)
        if bones is None:
            return 0
        interpolate = self.resolved_physics_fps() < 60
        influence = self.weight()
        changed = 0
        for name, end in self.current_physics_rotations.items():
            pose_bone = bones.get(name)
            if pose_bone is None:
                continue

            if interpolate:
                start = self.previous_physics_rotations.get(name, end)
                target = end.copy()
                if start.dot(target) < 0.0:
                    target.negate()
                output = start.slerp(target, alpha)
            else:
                output = end.copy()

            if influence < 1.0 and weight_base is not None:
                start = weight_base.get(name, output)
                target = output.copy()
                if start.dot(target) < 0.0:
                    target.negate()
                output = start.slerp(target, influence)
            changed += int(
                _set_rotation_without_mode_change(pose_bone, output)
            )
        return changed

    def reset_output_state(self) -> None:
        self.physics_rate = ""
        self.physics_accumulator = 0.0
        self.needs_initial_solve = True
        self.previous_physics_rotations = {}
        self.current_physics_rotations = {}

    def refresh_configuration(self, context, elapsed: float) -> bool:
        if self.mode != "VRM 1.0" or self.solver is None:
            return False
        self.configuration_check_accumulator += max(float(elapsed), 0.0)
        if self.configuration_check_accumulator < 0.5:
            return False
        self.configuration_check_accumulator %= 0.5
        if not self.solver.settings_changed():
            return False
        old_bone_names = self.bone_names
        _set_reset_pose(self.armature, old_bone_names)
        _update_view_layer(context)
        self.solver = Vrm1SpringBoneSolver(self.armature, self.extension)
        self.bone_names = self.solver.bone_names
        self.stats = spring_bone_stats(self.extension)
        self.was_active = False
        self.reset_output_state()
        return True

    def reset(self) -> None:
        if not self.resolve_live():
            return
        if self.spring_bone is not None:
            self.spring_bone.enable_animation = False
        _set_reset_pose(self.armature, self.bone_names)
        _update_view_layer(bpy.context)
        if self.mode == "VRM 1.0":
            self.solver = Vrm1SpringBoneSolver(self.armature, self.extension)
        else:
            self.solver = Vrm0SpringBoneSolver(self.armature, self.extension)
        self.bone_names = self.solver.bone_names
        self.stats = spring_bone_stats(self.extension)
        self.configuration_check_accumulator = 0.0
        self.was_active = False
        self.reset_output_state()

    def detach_for_undo(self) -> None:
        if self.scene is not None and self.layer is not None:
            self.locator = layer_locator(self.scene, self.layer) or self.locator
        if self.spring_bone is not None:
            try:
                self.spring_bone.enable_animation = self.previous_enable_animation
            except (AttributeError, ReferenceError, TypeError):
                pass
        self.scene = None
        self.layer = None
        self.armature = None
        self.extension = None
        self.spring_bone = None
        self.solver = None

    def rebind_after_undo(self, context) -> bool:
        if not self.resolve_live():
            return False
        self._initialize(
            context,
            reset_pose=True,
            preserve_previous=True,
        )
        if self.layer.spring_show_hit_radius:
            visualization.rebuild(
                self.armature,
                self.extension,
                self.owner_id,
                context,
            )
        return True

    def shutdown(self, *, reset_pose: bool, clear_visualization: bool) -> None:
        if self.resolve_live():
            try:
                self.layer.connected = False
            except (AttributeError, ReferenceError):
                pass
        if self.spring_bone is not None:
            try:
                self.spring_bone.enable_animation = False
            except (AttributeError, ReferenceError, TypeError):
                pass
        if reset_pose and self.armature is not None:
            _set_reset_pose(self.armature, self.bone_names)
        if self.spring_bone is not None:
            try:
                self.spring_bone.enable_animation = self.previous_enable_animation
            except (AttributeError, ReferenceError, TypeError):
                pass
        if clear_visualization:
            visualization.clear(self.owner_id)
        self.scene = None
        self.layer = None
        self.armature = None
        self.extension = None
        self.spring_bone = None
        self.solver = None


def _callbacks():
    callbacks = bpy.app.driver_namespace.get(POST_APPLY_CALLBACKS_KEY)
    if not isinstance(callbacks, dict):
        callbacks = {}
        bpy.app.driver_namespace[POST_APPLY_CALLBACKS_KEY] = callbacks
    return callbacks


def _register_callback() -> None:
    _callbacks()[CALLBACK_NAME] = _post_mix_update


def _unregister_callback() -> None:
    callbacks = bpy.app.driver_namespace.get(POST_APPLY_CALLBACKS_KEY)
    if isinstance(callbacks, dict) and callbacks.get(CALLBACK_NAME) is _post_mix_update:
        callbacks.pop(CALLBACK_NAME, None)


def _post_mix_update(context, delta_time) -> None:
    global _last_time
    if _undo_in_progress:
        return
    if not _targets:
        _unregister_callback()
        return
    callbacks = bpy.app.driver_namespace.get(POST_APPLY_CALLBACKS_KEY)
    if isinstance(callbacks, dict) and callable(
        callbacks.get(STANDALONE_CALLBACK_NAME)
    ):
        message = (
            "The standalone Yugo Spring Bone Preview was started; "
            "stop it before using Spring Bone Motion Layers"
        )
        for target in tuple(_targets.values()):
            target.last_error = message
            try:
                target.layer.spring_last_error = message
            except (AttributeError, ReferenceError):
                pass
        stop_all(reset_pose=True)
        return

    now = time.monotonic()
    elapsed = min(max(float(delta_time), 0.0), 0.1)
    if elapsed <= 0.0:
        elapsed = min(max(now - _last_time, 0.0), 0.1) if _last_time else TICK_INTERVAL
    _last_time = now

    failed = []
    active = []
    schedules = {}
    try:
        for owner_id, target in tuple(_targets.items()):
            try:
                is_active = target.active()
                if is_active:
                    target.refresh_configuration(context, elapsed)
                    active.append(target)
                    schedules[owner_id] = target.advance_physics_clock(elapsed)
                elif target.was_active:
                    _set_reset_pose(target.armature, target.bone_names)
                    target.reset_output_state()
                target.was_active = is_active
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
                target.last_error = str(error)
                failed.append(owner_id)

        if not active:
            return

        weight_bases = {
            target.owner_id: target.capture_weight_base()
            for target in active
        }
        snapshot_targets = [
            target for target in active if schedules[target.owner_id][0]
        ]
        if snapshot_targets:
            for target in snapshot_targets:
                target.prepare_solve()
            _update_view_layer(context)

        solved_rotations = {}
        for target in active:
            rotations = None
            for _index in range(schedules[target.owner_id][2]):
                rotations = target.solver.step(FIXED_PHYSICS_STEP)
            if rotations is not None:
                solved_rotations[target.owner_id] = rotations
        for target in snapshot_targets:
            rotations = solved_rotations.get(target.owner_id)
            if rotations is not None:
                target.finish_solve(rotations)

        for target in active:
            _due, alpha, _substeps = schedules[target.owner_id]
            target.apply_output(alpha, weight_bases[target.owner_id])
        # Do not force a second synchronous dependency-graph evaluation after
        # write-back. Blender's main event loop evaluates the tagged pose for
        # viewport drawing. The next due solve performs the one required pull
        # update before reading pose matrices, matching FastSpringBone's
        # Pull -> Solve -> Push pipeline.
    except Exception as error:
        for target in active:
            target.last_error = str(error)
            try:
                target.layer.spring_last_error = str(error)
            except (AttributeError, ReferenceError):
                pass
    finally:
        for owner_id in failed:
            target = _targets.pop(owner_id, None)
            if target is not None:
                target.shutdown(reset_pose=False, clear_visualization=True)


def is_layer_running(layer) -> bool:
    try:
        owner_id = str(layer.runtime_id or "")
    except (AttributeError, ReferenceError):
        return False
    target = _targets.get(owner_id)
    return target is not None and target.resolve_live()


def start(context, scene, layer, armature) -> None:
    global _last_time
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        raise RuntimeError("Select a VRM Armature before starting Spring Bone")
    callbacks = bpy.app.driver_namespace.get(POST_APPLY_CALLBACKS_KEY)
    if isinstance(callbacks, dict) and callable(callbacks.get(STANDALONE_CALLBACK_NAME)):
        raise RuntimeError(
            "Stop the standalone Yugo Spring Bone Preview before starting this layer"
        )
    owner_id = ensure_runtime_id(layer)
    if owner_id in _targets:
        raise RuntimeError("This Spring Bone layer is already running")
    for target in _targets.values():
        try:
            if target.resolve_live() and target.armature.as_pointer() == armature.as_pointer():
                raise RuntimeError(
                    "Only one Spring Bone Motion Layer can run per VRM Avatar"
                )
        except ReferenceError:
            continue
    target = _Target(context, scene, layer, armature)
    _targets[owner_id] = target
    _last_time = time.monotonic()
    _register_callback()
    if layer.spring_show_hit_radius:
        visualization.rebuild(armature, target.extension, owner_id, context)


def stop(
    layer,
    *,
    reset_pose: bool = True,
    clear_visualization: bool = False,
) -> None:
    global _last_time
    try:
        owner_id = str(layer.runtime_id or "")
    except (AttributeError, ReferenceError):
        return
    target = _targets.pop(owner_id, None)
    if target is not None:
        target.shutdown(
            reset_pose=reset_pose,
            clear_visualization=clear_visualization,
        )
    elif clear_visualization:
        visualization.clear(owner_id)
    if not _targets:
        _unregister_callback()
        _last_time = 0.0


def reset(layer) -> None:
    owner_id = str(getattr(layer, "runtime_id", "") or "")
    target = _targets.get(owner_id)
    if target is None:
        raise RuntimeError("This Spring Bone layer is not running")
    target.reset()


def current_mode(layer) -> str:
    owner_id = str(getattr(layer, "runtime_id", "") or "")
    target = _targets.get(owner_id)
    return target.mode if target is not None else str(getattr(layer, "spring_mode", ""))


def cached_vrm_info(layer):
    """Return runtime-cached VRM mode/stats without rereading add-on data."""

    owner_id = str(getattr(layer, "runtime_id", "") or "")
    target = _targets.get(owner_id)
    if target is None or target.stats is None:
        return None
    return target.mode, target.stats


def last_error(layer) -> str:
    owner_id = str(getattr(layer, "runtime_id", "") or "")
    target = _targets.get(owner_id)
    return target.last_error if target is not None else str(
        getattr(layer, "spring_last_error", "") or ""
    )


def rebuild_layer_visualization(scene, layer) -> int:
    owner_id = ensure_runtime_id(layer)
    resolved = resolve_layer_locator(layer_locator(scene, layer))
    if resolved is None:
        visualization.clear(owner_id)
        return 0
    _scene, _avatar, live_layer, armature = resolved
    if not live_layer.spring_show_hit_radius or armature is None:
        visualization.clear(owner_id)
        return 0
    extension = get_vrm_extension(armature)
    return visualization.rebuild(armature, extension, owner_id, bpy.context)


def prepare_undo() -> None:
    global _undo_in_progress
    _undo_in_progress = True
    visualization.clear()
    for target in tuple(_targets.values()):
        target.detach_for_undo()


def finish_undo() -> None:
    global _undo_in_progress, _last_time
    failed = []
    for owner_id, target in tuple(_targets.items()):
        try:
            if not target.rebind_after_undo(bpy.context):
                failed.append(owner_id)
        except Exception as error:
            target.last_error = str(error)
            failed.append(owner_id)
    for owner_id in failed:
        _targets.pop(owner_id, None)
    _undo_in_progress = False
    _last_time = time.monotonic()
    if _targets:
        _register_callback()
    else:
        _unregister_callback()


def stop_all(*, reset_pose: bool = True) -> None:
    global _last_time, _undo_in_progress
    for target in tuple(_targets.values()):
        target.shutdown(reset_pose=reset_pose, clear_visualization=True)
    _targets.clear()
    _unregister_callback()
    visualization.clear()
    _last_time = 0.0
    _undo_in_progress = False


@persistent
def _load_pre(_unused):
    stop_all(reset_pose=False)


@persistent
def _load_post(_unused):
    visualization.clear()
    for scene in bpy.data.scenes:
        for avatar in iter_avatars(scene):
            for layer in avatar.motion_layers:
                if (
                    layer.source_type == "SPRING_BONE"
                    and layer.spring_show_hit_radius
                ):
                    rebuild_layer_visualization(scene, layer)


def register() -> None:
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister() -> None:
    stop_all(reset_pose=True)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
