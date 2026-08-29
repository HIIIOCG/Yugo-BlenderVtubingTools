"""Cached VRM 1.0 Spring Bone solver owned entirely by Yugo.

The solver follows the VRMC_springBone 1.0 Verlet update procedure. VRM
PropertyGroups are read when the cache is built, while each physics step only
reads the armature pose and the transforms of collider bones.
"""

from __future__ import annotations

from dataclasses import dataclass
from sys import float_info

from mathutils import Matrix, Quaternion, Vector

from .vrm import spring_bone_data


def _bone_name(value) -> str:
    return str(getattr(value, "bone_name", "") or "")


def _vector(value, fallback=(0.0, 0.0, 0.0)) -> Vector:
    try:
        return Vector(value)
    except (TypeError, ValueError):
        return Vector(fallback)


def _safe_normalized(value: Vector, fallback: Vector) -> Vector:
    if value.length_squared > float_info.epsilon:
        return value.normalized()
    if fallback.length_squared > float_info.epsilon:
        return fallback.normalized()
    return Vector((0.0, 0.0, 1.0))


@dataclass(frozen=True)
class _SphereWorldCollider:
    center: Vector
    radius: float
    inside: bool = False

    def collision(self, target: Vector, target_radius: float):
        if self.inside:
            delta = self.center - target
            length = delta.length
            direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
            return direction, self.radius - length - target_radius
        delta = target - self.center
        length = delta.length
        direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
        return direction, length - target_radius - self.radius


@dataclass(frozen=True)
class _CapsuleWorldCollider:
    head: Vector
    tail: Vector
    radius: float
    inside: bool = False

    def collision(self, target: Vector, target_radius: float):
        segment = self.tail - self.head
        length_squared = segment.length_squared
        if length_squared <= float_info.epsilon:
            return _SphereWorldCollider(
                self.head, self.radius, self.inside
            ).collision(target, target_radius)
        ratio = max(
            0.0,
            min(1.0, segment.dot(target - self.head) / length_squared),
        )
        nearest = self.head + segment * ratio
        if self.inside:
            delta = nearest - target
            length = delta.length
            direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
            return direction, self.radius - length - target_radius
        delta = target - nearest
        length = delta.length
        direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
        return direction, length - target_radius - self.radius


@dataclass(frozen=True)
class _PlaneWorldCollider:
    point: Vector
    normal: Vector

    def collision(self, target: Vector, target_radius: float):
        normal = _safe_normalized(self.normal, Vector((0.0, 0.0, 1.0)))
        return normal, (target - self.point).dot(normal) - target_radius


@dataclass(frozen=True)
class _ColliderSource:
    bone_name: str
    shape: str
    offset: Vector
    tail: Vector
    normal: Vector
    radius: float
    inside: bool

    def world(self, armature_world: Matrix, pose_bones):
        pose_bone = pose_bones.get(self.bone_name)
        if pose_bone is None:
            return None
        matrix = armature_world @ pose_bone.matrix
        if self.shape == "SPHERE":
            return _SphereWorldCollider(
                matrix @ self.offset,
                self.radius,
                self.inside,
            )
        if self.shape == "CAPSULE":
            return _CapsuleWorldCollider(
                matrix @ self.offset,
                matrix @ self.tail,
                self.radius,
                self.inside,
            )
        if self.shape == "PLANE":
            return _PlaneWorldCollider(
                matrix @ self.offset,
                matrix.to_quaternion() @ self.normal,
            )
        return None


@dataclass
class _PairState:
    head_bone: object
    tail_bone: object
    head_rest_matrix: Matrix
    tail_rest_matrix: Matrix
    stiffness: float
    drag_force: float
    gravity_dir: Vector
    gravity_power: float
    hit_radius: float
    previous_tail: Vector
    current_tail: Vector


@dataclass
class _ChainState:
    pairs: list[_PairState]
    use_center_space: bool


@dataclass
class _SpringState:
    chains: list[_ChainState]
    collider_indices: tuple[int, ...]
    center_bone_name: str
    previous_center: Vector | None


def _collider_shape(collider):
    extended = getattr(
        getattr(collider, "extensions", None),
        "vrmc_spring_bone_extended_collider",
        None,
    )
    if extended is not None and bool(getattr(extended, "enabled", False)):
        shape_type = str(getattr(extended, "shape_type", "") or "").upper()
        shape = getattr(extended, "shape", None)
        if "CAPSULE" in shape_type:
            value = getattr(shape, "capsule", None)
            return (
                "CAPSULE",
                _vector(getattr(value, "offset", (0.0, 0.0, 0.0))),
                _vector(getattr(value, "tail", (0.0, 0.0, 0.0))),
                Vector((0.0, 0.0, 1.0)),
                max(0.0, float(getattr(value, "radius", 0.0) or 0.0)),
                bool(getattr(value, "inside", False)),
            )
        if "PLANE" in shape_type:
            value = getattr(shape, "plane", None)
            return (
                "PLANE",
                _vector(getattr(value, "offset", (0.0, 0.0, 0.0))),
                Vector(),
                _vector(getattr(value, "normal", (0.0, 0.0, 1.0))),
                0.0,
                False,
            )
        value = getattr(shape, "sphere", None)
        return (
            "SPHERE",
            _vector(getattr(value, "offset", (0.0, 0.0, 0.0))),
            Vector(),
            Vector((0.0, 0.0, 1.0)),
            max(0.0, float(getattr(value, "radius", 0.0) or 0.0)),
            bool(getattr(value, "inside", False)),
        )

    shape_type = str(getattr(collider, "shape_type", "SPHERE") or "SPHERE").upper()
    shape = getattr(collider, "shape", None)
    if "CAPSULE" in shape_type:
        value = getattr(shape, "capsule", None)
        return (
            "CAPSULE",
            _vector(getattr(value, "offset", (0.0, 0.0, 0.0))),
            _vector(getattr(value, "tail", (0.0, 0.0, 0.0))),
            Vector((0.0, 0.0, 1.0)),
            max(0.0, float(getattr(value, "radius", 0.0) or 0.0)),
            False,
        )
    value = getattr(shape, "sphere", None)
    return (
        "SPHERE",
        _vector(getattr(value, "offset", (0.0, 0.0, 0.0))),
        Vector(),
        Vector((0.0, 0.0, 1.0)),
        max(0.0, float(getattr(value, "radius", 0.0) or 0.0)),
        False,
    )


def _is_ancestor(ancestor, descendant) -> bool:
    bone = descendant.parent
    while bone is not None:
        if bone == ancestor:
            return True
        bone = bone.parent
    return False


def _sorted_chains(pose_bones, joints):
    entries = []
    seen = set()
    for joint in joints:
        bone = pose_bones.get(_bone_name(getattr(joint, "node", None)))
        if bone is None or bone.name in seen:
            continue
        seen.add(bone.name)
        entries.append((joint, bone))
    chains = []
    for entry in entries:
        _joint, bone = entry
        assigned = False
        for chain in chains:
            if _is_ancestor(bone, chain[0][1]):
                chain.insert(0, entry)
                assigned = True
                break
            if _is_ancestor(chain[-1][1], bone):
                chain.append(entry)
                assigned = True
                break
            for index in range(len(chain) - 1):
                if _is_ancestor(chain[index][1], bone) and _is_ancestor(
                    bone, chain[index + 1][1]
                ):
                    chain.insert(index + 1, entry)
                    assigned = True
                    break
            if assigned:
                break
        if not assigned:
            chains.append([entry])
    return chains


def _value_signature(value):
    if isinstance(value, Vector):
        return tuple(float(component) for component in value)
    try:
        return tuple(float(component) for component in value)
    except TypeError:
        return value


def configuration_signature(extension):
    """Create a low-frequency signature of editable VRM Spring settings."""

    spring_bone = spring_bone_data(extension)
    if spring_bone is None:
        return ()
    colliders = []
    for collider in getattr(spring_bone, "colliders", ()) or ():
        shape = _collider_shape(collider)
        colliders.append(
            (
                str(getattr(collider, "uuid", "") or ""),
                _bone_name(getattr(collider, "node", None)),
                shape[0],
                _value_signature(shape[1]),
                _value_signature(shape[2]),
                _value_signature(shape[3]),
                shape[4],
                shape[5],
            )
        )
    groups = tuple(
        (
            str(getattr(group, "uuid", "") or ""),
            tuple(
                str(getattr(reference, "collider_uuid", "") or "")
                for reference in getattr(group, "colliders", ()) or ()
            ),
        )
        for group in getattr(spring_bone, "collider_groups", ()) or ()
    )
    springs = []
    for spring in getattr(spring_bone, "springs", ()) or ():
        joints = tuple(
            (
                _bone_name(getattr(joint, "node", None)),
                float(getattr(joint, "hit_radius", 0.0) or 0.0),
                float(getattr(joint, "stiffness", 0.0) or 0.0),
                float(getattr(joint, "gravity_power", 0.0) or 0.0),
                _value_signature(getattr(joint, "gravity_dir", (0.0, 0.0, 0.0))),
                float(getattr(joint, "drag_force", 0.0) or 0.0),
            )
            for joint in getattr(spring, "joints", ()) or ()
        )
        collider_groups = tuple(
            str(getattr(reference, "collider_group_uuid", "") or "")
            for reference in getattr(spring, "collider_groups", ()) or ()
        )
        springs.append(
            (
                _bone_name(getattr(spring, "center", None)),
                joints,
                collider_groups,
            )
        )
    return tuple(colliders), groups, tuple(springs)


class Vrm1SpringBoneSolver:
    """Evaluate one VRM 1.0 armature without calling VRM Add-on handlers."""

    def __init__(self, armature, extension):
        self.armature = armature
        self.extension = extension
        self.collider_sources: list[_ColliderSource] = []
        self.springs: list[_SpringState] = []
        self.bone_names: tuple[str, ...] = ()
        self.signature = ()
        self._build()

    def _build(self) -> None:
        spring_bone = spring_bone_data(self.extension)
        if spring_bone is None:
            raise RuntimeError("The selected VRM 1.0 armature has no Spring Bone data")
        pose_bones = self.armature.pose.bones
        armature_world = self.armature.matrix_world.copy()
        collider_index_by_uuid = {}
        for collider in getattr(spring_bone, "colliders", ()) or ():
            uuid = str(getattr(collider, "uuid", "") or "")
            bone_name = _bone_name(getattr(collider, "node", None))
            if not uuid or pose_bones.get(bone_name) is None:
                continue
            shape, offset, tail, normal, radius, inside = _collider_shape(collider)
            collider_index_by_uuid[uuid] = len(self.collider_sources)
            self.collider_sources.append(
                _ColliderSource(
                    bone_name,
                    shape,
                    offset,
                    tail,
                    normal,
                    radius,
                    inside,
                )
            )

        group_indices = {}
        for group in getattr(spring_bone, "collider_groups", ()) or ():
            uuid = str(getattr(group, "uuid", "") or "")
            indices = []
            for reference in getattr(group, "colliders", ()) or ():
                index = collider_index_by_uuid.get(
                    str(getattr(reference, "collider_uuid", "") or "")
                )
                if index is not None and index not in indices:
                    indices.append(index)
            if uuid:
                group_indices[uuid] = tuple(indices)

        all_names = []
        seen_names = set()
        for spring in getattr(spring_bone, "springs", ()) or ():
            collider_indices = []
            for reference in getattr(spring, "collider_groups", ()) or ():
                uuid = str(getattr(reference, "collider_group_uuid", "") or "")
                for index in group_indices.get(uuid, ()):
                    if index not in collider_indices:
                        collider_indices.append(index)
            center_name = _bone_name(getattr(spring, "center", None))
            center_bone = pose_bones.get(center_name)
            previous_center = (
                armature_world @ center_bone.matrix.to_translation()
                if center_bone is not None
                else None
            )
            chains = []
            for entries in _sorted_chains(
                pose_bones, getattr(spring, "joints", ()) or ()
            ):
                if len(entries) < 2:
                    continue
                first_bone = entries[0][1]
                use_center_space = bool(
                    center_bone is not None
                    and (center_bone == first_bone or _is_ancestor(center_bone, first_bone))
                )
                pairs = []
                for (head_joint, head_bone), (_tail_joint, tail_bone) in zip(
                    entries, entries[1:]
                ):
                    if not _is_ancestor(head_bone, tail_bone):
                        break
                    head_rest = head_bone.bone.convert_local_to_pose(
                        Matrix(), head_bone.bone.matrix_local
                    )
                    tail_rest = tail_bone.bone.convert_local_to_pose(
                        Matrix(), tail_bone.bone.matrix_local
                    )
                    initial_tail = (
                        armature_world @ tail_bone.matrix.to_translation()
                    )
                    pairs.append(
                        _PairState(
                            head_bone=head_bone,
                            tail_bone=tail_bone,
                            head_rest_matrix=head_rest,
                            tail_rest_matrix=tail_rest,
                            stiffness=max(
                                0.0,
                                float(getattr(head_joint, "stiffness", 0.0) or 0.0),
                            ),
                            drag_force=min(
                                max(
                                    float(
                                        getattr(head_joint, "drag_force", 0.0) or 0.0
                                    ),
                                    0.0,
                                ),
                                1.0,
                            ),
                            gravity_dir=_vector(
                                getattr(head_joint, "gravity_dir", (0.0, 0.0, 0.0))
                            ),
                            gravity_power=max(
                                0.0,
                                float(
                                    getattr(head_joint, "gravity_power", 0.0) or 0.0
                                ),
                            ),
                            hit_radius=max(
                                0.0,
                                float(
                                    getattr(head_joint, "hit_radius", 0.0) or 0.0
                                ),
                            ),
                            previous_tail=initial_tail.copy(),
                            current_tail=initial_tail.copy(),
                        )
                    )
                if pairs:
                    chains.append(_ChainState(pairs, use_center_space))
                for _joint, bone in entries:
                    if bone.name not in seen_names:
                        seen_names.add(bone.name)
                        all_names.append(bone.name)
            if chains:
                self.springs.append(
                    _SpringState(
                        chains,
                        tuple(collider_indices),
                        center_name,
                        previous_center,
                    )
                )
        self.bone_names = tuple(all_names)
        self.signature = configuration_signature(self.extension)
        if not self.springs:
            raise RuntimeError("No valid VRM 1.0 Spring Bone joint chains were found")

    def _center_delta(self, spring: _SpringState, armature_world: Matrix) -> Vector:
        center = self.armature.pose.bones.get(spring.center_bone_name)
        if center is None:
            spring.previous_center = None
            return Vector()
        current = armature_world @ center.matrix.to_translation()
        if spring.previous_center is None:
            spring.previous_center = current.copy()
            return Vector()
        delta = current - spring.previous_center
        spring.previous_center = current.copy()
        return delta

    def _solve_pair(
        self,
        pair: _PairState,
        before_matrix: Matrix,
        armature_world: Matrix,
        armature_world_inverse: Matrix,
        armature_world_rotation: Quaternion,
        center_delta: Vector,
        colliders,
        delta_time: float,
    ):
        location, parent_rotation, scale = before_matrix.decompose()
        head_world = armature_world @ location
        previous_tail = pair.previous_tail + center_delta
        current_tail = pair.current_tail + center_delta
        local_axis = pair.head_rest_matrix.inverted_safe() @ (
            pair.tail_rest_matrix.to_translation()
        )
        fallback_world = armature_world_rotation @ parent_rotation @ local_axis
        inertia = (current_tail - previous_tail) * (1.0 - pair.drag_force)
        stiffness = (
            _safe_normalized(fallback_world, Vector((0.0, 0.0, 1.0)))
            * pair.stiffness
            * delta_time
        )
        external = pair.gravity_dir * pair.gravity_power * delta_time
        next_tail = current_tail + inertia + stiffness + external
        # The chain matrix is already available in the solver buffer. Reading
        # the evaluated Spring pose here would couple a low-rate solve to the
        # currently displayed interpolation and require restoring the last
        # full physics pose before every step.
        tail_world = armature_world @ (before_matrix @ local_axis)
        length = max((tail_world - head_world).length, 1.0e-6)
        fallback = _safe_normalized(fallback_world, Vector((0.0, 0.0, 1.0)))
        next_tail = head_world + _safe_normalized(
            next_tail - head_world, fallback
        ) * length
        for collider in colliders:
            direction, distance = collider.collision(next_tail, pair.hit_radius)
            if distance >= 0.0:
                continue
            next_tail -= direction * distance
            next_tail = head_world + _safe_normalized(
                next_tail - head_world, fallback
            ) * length

        next_tail_object = armature_world_inverse @ next_tail
        end_axis = before_matrix.inverted_safe() @ next_tail_object
        start_axis = _safe_normalized(local_axis, Vector((0.0, 1.0, 0.0)))
        end_axis = _safe_normalized(end_axis, start_axis)
        delta_rotation = start_axis.rotation_difference(end_axis)
        object_rotation = parent_rotation @ delta_rotation
        result_matrix = (
            Matrix.Translation(location)
            @ object_rotation.to_matrix().to_4x4()
            @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        )
        next_before_matrix = (
            result_matrix
            @ pair.head_rest_matrix.inverted_safe()
            @ pair.tail_rest_matrix
        )
        pair.previous_tail = current_tail
        pair.current_tail = next_tail
        rotation = (
            delta_rotation
            if pair.head_bone.bone.use_inherit_rotation
            else object_rotation
        )
        return rotation, next_before_matrix

    def step(self, delta_time: float) -> dict[str, Quaternion]:
        delta_time = max(0.0, float(delta_time))
        armature_world = self.armature.matrix_world.copy()
        armature_world_inverse = armature_world.inverted_safe()
        armature_world_rotation = armature_world.to_quaternion()
        pose_bones = self.armature.pose.bones
        world_colliders = [
            source.world(armature_world, pose_bones)
            for source in self.collider_sources
        ]
        rotations: dict[str, Quaternion] = {}
        for spring in self.springs:
            center_delta = self._center_delta(spring, armature_world)
            colliders = [
                world_colliders[index]
                for index in spring.collider_indices
                if world_colliders[index] is not None
            ]
            for chain in spring.chains:
                next_before_matrix = None
                for pair in chain.pairs:
                    if next_before_matrix is None:
                        parent = pair.head_bone.parent
                        if parent is None:
                            next_before_matrix = pair.head_rest_matrix.copy()
                        else:
                            parent_rest = parent.bone.convert_local_to_pose(
                                Matrix(), parent.bone.matrix_local
                            )
                            next_before_matrix = parent.matrix @ (
                                parent_rest.inverted_safe() @ pair.head_rest_matrix
                            )
                    rotation, next_before_matrix = self._solve_pair(
                        pair,
                        next_before_matrix,
                        armature_world,
                        armature_world_inverse,
                        armature_world_rotation,
                        center_delta if chain.use_center_space else Vector(),
                        colliders,
                        delta_time,
                    )
                    rotations[pair.head_bone.name] = rotation
        return rotations

    def settings_changed(self) -> bool:
        return configuration_signature(self.extension) != self.signature

    def reset(self) -> None:
        self.collider_sources.clear()
        self.springs.clear()
        self.bone_names = ()
        self._build()
