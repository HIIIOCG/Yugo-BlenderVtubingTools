"""Blender pose writer for the standalone ragdoll solver.

The caller owns scheduling. Sessions contain plain identifiers and copied math
values only; RNA is resolved and checked anew for each operation.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from .solver import BoneSpec, JointLimit, Ragdoll, Settings


MAX_BONES = 128
_EPSILON = 1e-5
_BODY_BONES = (
    "hips", "spine", "chest", "upper_chest", "neck", "head",
    "left_shoulder", "left_upper_arm", "left_lower_arm", "left_hand",
    "right_shoulder", "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot", "left_toes",
    "right_upper_leg", "right_lower_leg", "right_foot", "right_toes",
)
_sessions = {}
_finished = OrderedDict()
_writing = 0


class RagdollError(ValueError):
    """A target or session cannot safely be written."""


@dataclass
class _Session:
    session_id: str
    owner: str
    object_name: str
    object_id: int
    data_id: int
    scene_name: str
    scene_id: int
    names: tuple[str, ...]
    bone_ids: dict
    signature: tuple
    object_matrix: tuple
    original: dict
    expected: dict
    initial_world: dict
    solver: Ragdoll
    target_pose: dict | None = None
    error: str = ""


def _matrix_values(matrix):
    return tuple(float(value) for row in matrix for value in row)


def _matrix_close(matrix, expected):
    return all(abs(a - b) <= _EPSILON for a, b in zip(_matrix_values(matrix), expected))


def _animation_active(data):
    animation = getattr(data, "animation_data", None)
    if animation is None:
        return False
    return bool(animation.action or animation.drivers or any(
        not track.mute and any(not strip.mute for strip in track.strips)
        for track in animation.nla_tracks
    ))


def _check_target(armature, scene):
    if armature is None or armature.type != "ARMATURE":
        raise RagdollError("Choose an armature object")
    if armature.name not in scene.objects:
        raise RagdollError("The armature is not in the session scene")
    if armature.mode != "OBJECT" and armature.mode != "POSE":
        raise RagdollError("Leave armature Edit Mode before starting ragdoll")
    if armature.library or armature.data.library or not armature.is_editable or not armature.data.is_editable:
        raise RagdollError("Linked or read-only armatures cannot be simulated")
    if armature.data.pose_position != "POSE":
        raise RagdollError("Set the armature to Pose Position")
    # A parent may animate or constrain this object's world transform too.
    current = armature
    while current is not None:
        if _animation_active(current) or current.constraints:
            raise RagdollError("Animated, driven, or constrained targets need an exclusive pose writer")
        if min(current.scale) <= 0 or max(current.scale) - min(current.scale) > _EPSILON:
            raise RagdollError("Armature and parent scale must be positive and uniform")
        current = current.parent
    if _animation_active(armature.data) or any(bone.constraints for bone in armature.pose.bones):
        raise RagdollError("Animated, driven, or constrained targets need an exclusive pose writer")
    basis = armature.matrix_world.to_3x3()
    lengths = [basis.col[index].length for index in range(3)]
    if min(lengths) <= _EPSILON or max(lengths) - min(lengths) > _EPSILON:
        raise RagdollError("Armature world scale must be positive and uniform")
    if basis.determinant() <= 0 or any(
        abs(basis.col[a].dot(basis.col[b])) > _EPSILON * lengths[a] * lengths[b]
        for a, b in ((0, 1), (0, 2), (1, 2))
    ):
        raise RagdollError("Mirrored or sheared armature transforms are unsupported")


def _signature(armature):
    return tuple(
        (bone.name, bone.as_pointer(), bone.parent.name if bone.parent else None,
         _matrix_values(bone.matrix_local), float(bone.length),
         bone.use_connect, bone.use_deform, bone.use_local_location,
         bone.inherit_scale, bone.use_inherit_rotation,
         armature.pose.bones[bone.name].rotation_mode,
         tuple((key, repr(bone.get(key))) for key in (
             "ragdoll_mass", "ragdoll_radius", "ragdoll_limit_degrees",
             "ragdoll_limit_min", "ragdoll_limit_max")))
        for bone in armature.data.bones
    )


def _vrm_names(armature):
    extension = getattr(armature.data, "vrm_addon_extension", None)
    if extension is None:
        return []
    version = str(getattr(extension, "spec_version", ""))
    names = []
    if version.startswith("1"):
        humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
        mapped = getattr(humanoid, "human_bones", None)
        for name in _BODY_BONES:
            node = getattr(getattr(mapped, name, None), "node", None)
            assigned = str(getattr(node, "bone_name", "") or getattr(node, "value", ""))
            if assigned and assigned in armature.pose.bones:
                names.append(assigned)
    else:
        humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
        allowed = {name.replace("_", "").casefold() for name in _BODY_BONES}
        for human in getattr(humanoid, "human_bones", ()):
            if str(getattr(human, "bone", "")).casefold() not in allowed:
                continue
            node = getattr(human, "node", None)
            assigned = str(getattr(node, "bone_name", "") or getattr(node, "value", ""))
            if assigned and assigned in armature.pose.bones:
                names.append(assigned)
    return list(dict.fromkeys(names))


def _bone_names(armature, names):
    if names is None:
        names = _vrm_names(armature) or [bone.name for bone in armature.data.bones if bone.use_deform]
    elif isinstance(names, str):
        raise RagdollError("Bone names must be a list, not a string")
    names = tuple(names)
    if not names or len(names) > MAX_BONES or len(set(names)) != len(names):
        raise RagdollError(f"Choose 1 to {MAX_BONES} unique bones")
    if any(not isinstance(name, str) or name not in armature.pose.bones for name in names):
        raise RagdollError("A selected bone no longer exists")
    # Connected children cannot translate independently in Blender. Complete
    # their connected parent chain so the simulation can reproduce its heads.
    expanded = set(names)
    for name in names:
        bone = armature.data.bones[name]
        while bone.use_connect and bone.parent is not None:
            expanded.add(bone.parent.name)
            bone = bone.parent
    if len(expanded) > MAX_BONES:
        raise RagdollError(f"Connected parent chains exceed the {MAX_BONES}-bone limit")
    def depth(name):
        return len(armature.data.bones[name].parent_recursive)
    return tuple(sorted(expanded, key=lambda name: (depth(name), name)))


def _resolve(session):
    scene = bpy.data.scenes.get(session.scene_name)
    obj = bpy.data.objects.get(session.object_name)
    if scene is None or scene.as_pointer() != session.scene_id:
        raise RagdollError("The session scene was deleted or replaced")
    if bpy.context.scene != scene:
        raise RagdollError("The active scene changed")
    if obj is None or obj.as_pointer() != session.object_id or obj.data is None or obj.data.as_pointer() != session.data_id:
        raise RagdollError("The armature was deleted or replaced")
    _check_target(obj, scene)
    if _signature(obj) != session.signature:
        raise RagdollError("The armature bones or configuration changed")
    if not _matrix_close(obj.matrix_world, session.object_matrix):
        raise RagdollError("The armature object transform changed")
    for name, matrix in session.expected.items():
        if name not in obj.pose.bones or not _matrix_close(obj.pose.bones[name].matrix_basis, matrix):
            raise RagdollError(f"Another pose writer changed the armature ({name})")
    return obj


def _snapshot(session, status=None):
    return {
        "session_id": session.session_id, "owner": session.owner,
        "armature": session.object_name, "status": status or session.solver.state,
        "pose": {name: {"head": list(head), "tail": list(tail)}
                 for name, (head, tail) in session.solver.pose().items()},
        "elapsed": session.solver.elapsed, "error": session.error,
        "speed": session.solver.speed, "contact": session.solver.contact,
        "since_hit": getattr(session.solver, "since_hit", session.solver._physics_elapsed),
        "settled_elapsed": getattr(session.solver, "settled_elapsed", session.solver._settled_elapsed),
        "dropped_time": session.solver.dropped_time,
    }


def _restore(session):
    global _writing
    obj = bpy.data.objects.get(session.object_name)
    if obj is None or obj.as_pointer() != session.object_id:
        obj = next((candidate for candidate in bpy.data.objects
                    if candidate.as_pointer() == session.object_id), None)
    if obj is None or obj.data is None or obj.data.as_pointer() != session.data_id:
        return
    current_names = {bone.as_pointer(): bone.name for bone in obj.data.bones}
    _writing += 1
    try:
        for name in session.names:
            current_name = current_names.get(session.bone_ids[name])
            bone = obj.pose.bones.get(current_name) if current_name else None
            expected = session.expected.get(name)
            # Restore only transforms still owned by this session. An external
            # writer's newest transform is never replaced by a stale snapshot.
            if bone is not None and expected is not None and _matrix_close(bone.matrix_basis, expected):
                bone.matrix_basis = session.original[name]
        bpy.context.view_layer.update()
    finally:
        _writing -= 1


def _archive(session, status):
    result = _snapshot(session, status)
    _sessions.pop(session.session_id, None)
    _finished[session.session_id] = deepcopy(result)
    while len(_finished) > 64:
        _finished.popitem(last=False)
    return result


def _checked(session_id):
    session = _sessions.get(session_id)
    if session is None:
        if session_id in _finished:
            return None
        raise RagdollError("Unknown ragdoll session")
    try:
        obj = _resolve(session)
    except (RagdollError, ReferenceError, RuntimeError) as exc:
        session.error = str(exc)
        _restore(session)
        session.solver.cancel()
        _archive(session, "FAILED")
        return None
    return session, obj


def _apply(session, obj):
    global _writing
    # Form absolute desired matrices first, then derive parent-relative basis
    # using Blender's own inheritance conversion. This handles disconnected
    # child offsets and avoids deferred parent evaluation during assignment.
    obj_inverse = obj.matrix_world.inverted()
    desired = {}
    for name, (head, tail) in session.solver.pose().items():
        initial = session.initial_world[name]
        initial_direction = initial.to_3x3().col[1].normalized()
        direction = Vector(tail) - Vector(head)
        if direction.length <= _EPSILON:
            raise RagdollError("The solver produced a zero-length bone")
        swing = initial_direction.rotation_difference(direction.normalized()).to_matrix().to_4x4()
        world = swing @ initial
        world.translation = Vector(head)
        desired[name] = obj_inverse @ world
    pose_matrices, bases = {}, {}
    ordered = sorted(obj.data.bones, key=lambda bone: len(bone.parent_recursive))
    for bone in ordered:
        name = bone.name
        kwargs = {}
        if bone.parent:
            kwargs = {
                "parent_matrix": pose_matrices[bone.parent.name],
                "parent_matrix_local": bone.parent.matrix_local,
            }
        if name in desired:
            pose_matrices[name] = desired[name]
            bases[name] = bone.convert_local_to_pose(desired[name], bone.matrix_local, invert=True, **kwargs)
            if bone.use_connect:
                # Blender clamps connected-bone local location on assignment.
                bases[name].translation = (0, 0, 0)
        else:
            pose_matrices[name] = bone.convert_local_to_pose(
                obj.pose.bones[name].matrix_basis, bone.matrix_local, **kwargs)
    # All validation precedes mutation, including any external writer detected
    # while the solver was running.
    _resolve(session)
    planned = dict(session.expected)
    planned.update({name: _matrix_values(basis) for name, basis in bases.items()})
    _writing += 1
    try:
        for name, basis in bases.items():
            obj.pose.bones[name].matrix_basis = basis
        session.expected = planned
        bpy.context.view_layer.update()
    finally:
        _writing -= 1
    _resolve(session)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for name, (head, tail) in session.solver.pose().items():
        pose_bone = evaluated.pose.bones[name]
        if ((obj.matrix_world @ pose_bone.head - Vector(head)).length > 2e-4
                or (obj.matrix_world @ pose_bone.tail - Vector(tail)).length > 2e-4):
            raise RagdollError(f"Blender could not reproduce the simulated pose for {name}")
    _resolve(session)


def start(armature, bone_names=None, settings=None, owner="preview"):
    """Capture the current evaluated world pose and acquire this armature."""
    scene = bpy.context.scene
    _check_target(armature, scene)
    if any(session.object_id == armature.as_pointer() for session in _sessions.values()):
        raise RagdollError("This armature already has a ragdoll pose writer")
    names = _bone_names(armature, bone_names)
    settings = Settings(**settings) if isinstance(settings, dict) else settings or Settings()
    if not isinstance(settings, Settings):
        raise RagdollError("Settings must be a Settings value or a parameter dictionary")
    bpy.context.view_layer.update()
    evaluated = armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
    specs, initial = [], {}
    selected = set(names)
    for name in names:
        pose_bone = evaluated.pose.bones[name]
        world = armature.matrix_world @ pose_bone.matrix
        scale = [world.to_3x3().col[i].length for i in range(3)]
        if min(scale) <= _EPSILON or max(scale) - min(scale) > _EPSILON or world.to_3x3().determinant() <= 0:
            raise RagdollError("Bone pose scale must be positive and uniform")
        head, tail = armature.matrix_world @ pose_bone.head, armature.matrix_world @ pose_bone.tail
        if (tail - head).length <= _EPSILON:
            raise RagdollError("Zero-length bones cannot be simulated")
        initial[name] = world.copy()
        parent = pose_bone.parent.name if pose_bone.parent and pose_bone.parent.name in selected else None
        length = (tail - head).length
        bone = armature.data.bones[name]
        mass = float(bone.get("ragdoll_mass", max(.1, length)))
        radius = float(bone.get("ragdoll_radius", max(.01, min(.08, length * .12))))
        limit = None
        if any(key in bone for key in ("ragdoll_limit_degrees", "ragdoll_limit_min", "ragdoll_limit_max")):
            minimum = float(bone.get("ragdoll_limit_min", 0))
            maximum = float(bone.get("ragdoll_limit_max", bone.get("ragdoll_limit_degrees", 180)))
            if not 0 <= minimum <= maximum <= 180 or not all(math.isfinite(v) for v in (minimum, maximum)):
                raise RagdollError("Bone joint limits must be ordered finite degrees from 0 to 180")
            limit = JointLimit(math.radians(minimum), math.radians(maximum))
        specs.append(BoneSpec(name=name, parent=parent, head=tuple(head), tail=tuple(tail),
                              mass=mass, radius=radius, limit=limit))
    session_id = uuid.uuid4().hex
    expected = {bone.name: _matrix_values(bone.matrix_basis) for bone in armature.pose.bones}
    session = _Session(
        session_id, str(owner), armature.name, armature.as_pointer(), armature.data.as_pointer(),
        scene.name, scene.as_pointer(), names,
        {name: armature.data.bones[name].as_pointer() for name in names},
        _signature(armature), _matrix_values(armature.matrix_world),
        {name: armature.pose.bones[name].matrix_basis.copy() for name in names}, expected,
        initial, Ragdoll(specs, settings),
    )
    _sessions[session_id] = session
    return session_id


def read(session_id):
    """Return a JSON snapshot without advancing solver time."""
    checked = _checked(session_id)
    return _snapshot(checked[0]) if checked else deepcopy(_finished[session_id])


def hit(session_id, bone, impulse):
    checked = _checked(session_id)
    if checked:
        checked[0].solver.hit(bone, impulse)
    return read(session_id)


def update(session_id, dt):
    checked = _checked(session_id)
    if not checked:
        return read(session_id)
    session, obj = checked
    try:
        session.solver.step(dt)
        if session.solver.state not in {"FAILED", "CANCELLED"}:
            _apply(session, obj)
        if session.solver.state == "FAILED":
            session.error = getattr(session.solver, "failure_reason", "The ragdoll solver failed")
            _restore(session)
            return _archive(session, "FAILED")
    except (ValueError, ReferenceError, RuntimeError) as exc:
        session.error = str(exc)
        _restore(session)
        session.solver.cancel()
        return _archive(session, "FAILED")
    return _snapshot(session)


def _target(session, target_pose):
    if not isinstance(target_pose, dict) or set(target_pose) != set(session.names):
        raise RagdollError("The recovery target must contain every session bone")
    result = {}
    obj = bpy.data.objects[session.object_name]
    for name, value in target_pose.items():
        endpoints = (value.get("head"), value.get("tail")) if isinstance(value, dict) else value
        if len(endpoints) != 2:
            raise RagdollError("Recovery poses contain head and tail endpoints")
        head, tail = (tuple(float(component) for component in endpoint) for endpoint in endpoints)
        if len(head) != 3 or len(tail) != 3 or not all(math.isfinite(x) for x in (*head, *tail)):
            raise RagdollError("Recovery endpoints must be finite 3D coordinates")
        initial = session.initial_world[name]
        initial_length = initial.to_3x3().col[1].length
        expected_length = obj.data.bones[name].length * initial_length
        if abs((Vector(tail) - Vector(head)).length - expected_length) > 1e-5:
            raise RagdollError("Recovery targets must preserve bone lengths")
        result[name] = (head, tail)
    specs = {bone.name: bone for bone in session.solver.bones}
    for name, spec in specs.items():
        if spec.parent is None:
            continue
        initially_connected = math.dist(spec.head, specs[spec.parent].tail) <= 1e-5
        if obj.data.bones[name].use_connect or initially_connected:
            if math.dist(result[name][0], result[spec.parent][1]) > 1e-5:
                raise RagdollError(f"Recovery target breaks the connected joint for {name}")
    # The solver performs the authoritative remaining attachment validation
    # before accepting this goal, including its deterministic 180-degree
    # parallel transport for nonzero parent offsets.
    return result


def set_target(session_id, target_pose):
    """Set the goal used by the next recovery without rebuilding the solver."""
    checked = _checked(session_id)
    if checked:
        target = _target(checked[0], target_pose)
        checked[0].solver.set_target(target)
        checked[0].target_pose = target
    return read(session_id)


def recover(session_id, target_pose=None):
    checked = _checked(session_id)
    if checked:
        session = checked[0]
        goal = _target(session, target_pose) if target_pose is not None else session.target_pose
        session.solver.recover(goal)
    return read(session_id)


def stop(session_id, restore=True):
    session = _sessions.get(session_id)
    if session is None:
        return read(session_id)
    if restore:
        _restore(session)
    session.solver.cancel()
    return _archive(session, "CANCELLED")


def cleanup():
    """Release all writers on load, undo, unregister, or caller cancellation."""
    for session_id in tuple(_sessions):
        stop(session_id)
    _finished.clear()


def is_writing():
    """Let the caller's lifecycle watchdog avoid reentry during pose writes."""
    return bool(_writing)
