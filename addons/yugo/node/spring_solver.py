"""VRM Spring Bone solvers built only from cached primitive SpringData."""

from __future__ import annotations

from dataclasses import dataclass
from sys import float_info

from mathutils import Matrix, Quaternion, Vector

from .data import SpringData


def _safe_normalized(value, fallback):
    if value.length_squared > float_info.epsilon:
        return value.normalized()
    if fallback.length_squared > float_info.epsilon:
        return fallback.normalized()
    return Vector((0.0, 0.0, 1.0))


@dataclass(frozen=True)
class SphereCollider:
    center: Vector
    radius: float
    inside: bool = False

    def collision(self, target, target_radius):
        if self.inside:
            delta = self.center - target
            direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
            return direction, self.radius - delta.length - target_radius
        delta = target - self.center
        direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
        return direction, delta.length - target_radius - self.radius


@dataclass(frozen=True)
class CapsuleCollider:
    head: Vector
    tail: Vector
    radius: float
    inside: bool = False

    def collision(self, target, target_radius):
        segment = self.tail - self.head
        if segment.length_squared <= float_info.epsilon:
            return SphereCollider(self.head, self.radius, self.inside).collision(target, target_radius)
        ratio = max(
            0.0,
            min(1.0, segment.dot(target - self.head) / segment.length_squared),
        )
        nearest = self.head + segment * ratio
        if self.inside:
            delta = nearest - target
            direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
            return direction, self.radius - delta.length - target_radius
        delta = target - nearest
        direction = _safe_normalized(delta, Vector((0.0, 0.0, -1.0)))
        return direction, delta.length - target_radius - self.radius


@dataclass(frozen=True)
class PlaneCollider:
    point: Vector
    normal: Vector

    def collision(self, target, target_radius):
        normal = _safe_normalized(self.normal, Vector((0.0, 0.0, 1.0)))
        return normal, (target - self.point).dot(normal) - target_radius


def _interpolate_collider(start, end, factor):
    if isinstance(start, SphereCollider) and isinstance(end, SphereCollider):
        return SphereCollider(
            start.center.lerp(end.center, factor),
            start.radius + (end.radius - start.radius) * factor,
            start.inside if factor < 0.5 else end.inside,
        )
    if isinstance(start, CapsuleCollider) and isinstance(end, CapsuleCollider):
        return CapsuleCollider(
            start.head.lerp(end.head, factor),
            start.tail.lerp(end.tail, factor),
            start.radius + (end.radius - start.radius) * factor,
            start.inside if factor < 0.5 else end.inside,
        )
    if isinstance(start, PlaneCollider) and isinstance(end, PlaneCollider):
        return PlaneCollider(
            start.point.lerp(end.point, factor),
            _safe_normalized(start.normal.lerp(end.normal, factor), end.normal),
        )
    # Creation, removal, and shape changes have no continuous correspondence.
    return start if factor < 0.5 else end


@dataclass(frozen=True)
class SpringBoundary:
    """One evaluated sample of the moving inputs to the Spring simulation."""

    armature_world: Matrix
    parent_matrices: dict[str, Matrix]
    centers: dict[str, Vector]
    colliders: tuple[SphereCollider | CapsuleCollider | PlaneCollider | None, ...]

    def interpolate(self, other, factor):
        """Reconstruct inputs at a physics time between two pose samples."""

        factor = min(max(float(factor), 0.0), 1.0)
        parents = {}
        for name in self.parent_matrices.keys() | other.parent_matrices.keys():
            start = self.parent_matrices.get(name)
            end = other.parent_matrices.get(name)
            parents[name] = (
                start.lerp(end, factor)
                if start is not None and end is not None
                else (start if start is not None else end).copy()
            )
        centers = {}
        for name in self.centers.keys() | other.centers.keys():
            start = self.centers.get(name)
            end = other.centers.get(name)
            centers[name] = (
                start.lerp(end, factor)
                if start is not None and end is not None
                else (start if start is not None else end).copy()
            )
        colliders = tuple(
            _interpolate_collider(
                self.colliders[index] if index < len(self.colliders) else None,
                other.colliders[index] if index < len(other.colliders) else None,
                factor,
            )
            for index in range(max(len(self.colliders), len(other.colliders)))
        )
        return SpringBoundary(
            self.armature_world.lerp(other.armature_world, factor),
            parents,
            centers,
            colliders,
        )


@dataclass(frozen=True)
class ColliderSource:
    bone_name: str
    shape: str
    offset: Vector
    tail: Vector
    normal: Vector
    radius: float
    inside: bool

    def world(self, armature, *, pose_matrix=None):
        bone = armature.pose.bones.get(self.bone_name)
        if bone is None:
            return None
        matrix = armature.matrix_world @ (bone.matrix if pose_matrix is None else pose_matrix)
        scale = max(abs(value) for value in matrix.to_scale())
        if "PLANE" in self.shape:
            return PlaneCollider(
                matrix @ self.offset,
                matrix.to_quaternion() @ self.normal,
            )
        if "CAPSULE" in self.shape:
            return CapsuleCollider(
                matrix @ self.offset,
                matrix @ self.tail,
                self.radius * scale,
                self.inside,
            )
        return SphereCollider(matrix @ self.offset, self.radius * scale, self.inside)


@dataclass(frozen=True)
class ObjectColliderSource:
    object_name: str

    def world(self, _armature, *, matrix_world=None):
        import bpy

        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return None
        matrix = obj.matrix_world if matrix_world is None else matrix_world
        scale = max(abs(value) for value in matrix.to_scale())
        return SphereCollider(
            matrix.to_translation(),
            max(0.0, float(getattr(obj, "empty_display_size", 0.0))) * scale,
        )


@dataclass
class PairState:
    head_name: str
    tail_name: str
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
class ChainState:
    pairs: list[PairState]
    use_center_space: bool


@dataclass
class SpringState:
    chains: list[ChainState]
    collider_indices: tuple[int, ...]
    center_name: str
    previous_center: Vector | None


@dataclass
class Vrm0JointState:
    bone_name: str
    parent_index: int | None
    external_parent_name: str
    relative_matrix: Matrix
    base_matrix: Matrix
    base_basis_rotation: Quaternion
    local_axis: Vector
    length: float
    previous_tail: Vector
    current_tail: Vector


@dataclass
class Vrm0GroupState:
    joints: list[Vrm0JointState]
    collider_indices: tuple[int, ...]
    stiffness: float
    drag_force: float
    gravity_dir: Vector
    gravity_power: float
    hit_radius: float
    center_name: str
    previous_center: Vector | None


def _is_ancestor(ancestor, descendant):
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
        bone = pose_bones.get(str(joint.get("bone", "") or ""))
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


class NativeSpringSolver:
    """Solve cached SpringData without retaining VRM PropertyGroup objects."""

    def __init__(self, armature, data: SpringData, *, initialize_from_rest=False):
        self.armature_name = armature.name
        self.data = data
        # Diagnostics are opt-in and read the solver's own results, rather than
        # a Blender pose that another simulation may already have overwritten.
        self.trace_enabled = False
        self.trace_bones = {}
        self.initialize_from_rest = bool(initialize_from_rest)
        self.colliders = []
        self.springs = []
        self.bone_names = set()
        self._initial_matrices = (
            self._neutral_pose_matrices(armature) if self.initialize_from_rest else {}
        )
        self._build(armature)
        self.output_bone_names = {
            joint.bone_name
            for spring in self.springs
            if isinstance(spring, Vrm0GroupState)
            for joint in spring.joints
        } | {
            pair.head_name
            for spring in self.springs
            if isinstance(spring, SpringState)
            for chain in spring.chains
            for pair in chain.pairs
        }
        self.initial_rotations = {
            name: Quaternion() if self.initialize_from_rest else armature.pose.bones[name].matrix_basis.to_quaternion()
            for name in self.output_bone_names if name in armature.pose.bones
        }
        self.initial_boundary = None
        if self.initialize_from_rest:
            live = self.sample_boundary(armature)
            self.initial_boundary = SpringBoundary(
                live.armature_world,
                {name: self._initial_matrices[name].copy() for name in live.parent_matrices},
                {name: live.armature_world @ self._initial_matrices[name].to_translation()
                 for name in live.centers},
                self._neutral_colliders(armature, live.colliders),
            )
        # The solver retains only its chain data and sampled boundary, rather
        # than a second copy of every pose matrix for the rest of its lifetime.
        del self._initial_matrices

    def _neutral_pose_matrices(self, armature):
        """Reproduce a legacy Spring reset without changing native channels.

        VRM0 resets each named root and its descendants; VRM1 resets the listed
        joints, including terminal bones. External body poses stay evaluated
        in Blender. Bone conversion handles parent rotation, scale inheritance
        and Local Location while constructing affected descendants in memory.
        """
        pose_bones = armature.pose.bones
        reset_names = set()
        include_children = self.data.version.startswith("0")
        for spring in self.data.springs:
            for entry in spring.get("joints", ()):
                root = pose_bones.get(str(entry.get("bone", "") or ""))
                pending = [root] if root is not None else []
                while pending:
                    bone = pending.pop()
                    if bone.name in reset_names:
                        continue
                    reset_names.add(bone.name)
                    if include_children:
                        pending.extend(bone.children)
        matrices = {}
        affected = {}

        def pose(bone):
            if bone.name in matrices:
                return matrices[bone.name]
            parent = bone.parent
            if parent is not None:
                parent_matrix = pose(parent)
            else:
                parent_matrix = None
            changed = bone.name in reset_names or (
                parent is not None and affected[parent.name]
            )
            affected[bone.name] = changed
            if not changed:
                matrices[bone.name] = bone.matrix.copy()
                return matrices[bone.name]
            local = Matrix.Identity(4) if bone.name in reset_names else bone.matrix_basis.copy()
            kwargs = {}
            if parent is not None:
                kwargs = {"parent_matrix": parent_matrix,
                          "parent_matrix_local": parent.bone.matrix_local}
            matrices[bone.name] = bone.bone.convert_local_to_pose(
                local, bone.bone.matrix_local, **kwargs
            )
            return matrices[bone.name]

        for bone in pose_bones:
            pose(bone)
        return matrices

    def _initial_matrix(self, bone):
        if self.initialize_from_rest:
            return self._initial_matrices[bone.name].copy()
        return bone.matrix.copy()

    def _neutral_colliders(self, armature, live_colliders):
        """Sample reset-space shapes without moving their native sources."""
        import bpy

        colliders = []
        armature_world = armature.matrix_world
        for index, source in enumerate(self.colliders):
            collider = live_colliders[index]
            if isinstance(source, ColliderSource):
                matrix = self._initial_matrices.get(source.bone_name)
                if matrix is not None:
                    collider = source.world(armature, pose_matrix=matrix)
            elif isinstance(source, ObjectColliderSource):
                obj = bpy.data.objects.get(source.object_name)
                if (obj is not None and obj.parent == armature
                        and obj.parent_type == "BONE"):
                    parent = armature.pose.bones.get(obj.parent_bone)
                    matrix = self._initial_matrices.get(obj.parent_bone)
                    if parent is not None and matrix is not None:
                        current_parent = armature_world @ parent.matrix
                        neutral_parent = armature_world @ matrix
                        world = neutral_parent @ current_parent.inverted_safe() @ obj.matrix_world
                        collider = source.world(armature, matrix_world=world)
            colliders.append(collider)
        return tuple(colliders)

    def _build(self, armature):
        if self.data.version.startswith("0"):
            self._build_vrm0(armature)
            if not self.springs:
                raise ValueError("No valid cached VRM 0.x Spring chains")
            return
        pose_bones = armature.pose.bones
        collider_index = {}
        for item in self.data.colliders:
            uuid = str(item.get("uuid", ""))
            bone_name = str(item.get("bone", ""))
            if not uuid or pose_bones.get(bone_name) is None:
                continue
            collider_index[uuid] = len(self.colliders)
            self.colliders.append(
                ColliderSource(
                    bone_name,
                    str(item.get("shape", "SPHERE")).upper(),
                    Vector(item.get("offset", (0.0, 0.0, 0.0))),
                    Vector(item.get("tail", (0.0, 0.0, 0.0))),
                    Vector(item.get("normal", (0.0, 0.0, 1.0))),
                    max(0.0, float(item.get("radius", 0.0))),
                    bool(item.get("inside", False)),
                )
            )
        group_indices = {}
        for group in self.data.collider_groups:
            group_indices[str(group.get("uuid", ""))] = tuple(
                collider_index[uuid]
                for uuid in group.get("colliders", ())
                if uuid in collider_index
            )
        armature_world = armature.matrix_world.copy()
        for spring in self.data.springs:
            indices = []
            for uuid in spring.get("collider_groups", ()):
                for index in group_indices.get(uuid, ()):
                    if index not in indices:
                        indices.append(index)
            center_name = str(spring.get("center", ""))
            center = pose_bones.get(center_name)
            previous_center = (
                armature_world @ self._initial_matrix(center).to_translation()
                if center is not None
                else None
            )
            chains = []
            for entries in _sorted_chains(
                pose_bones, spring.get("joints", ())
            ):
                if len(entries) < 2:
                    continue
                first_bone = entries[0][1]
                use_center_space = bool(
                    center is not None
                    and (center == first_bone or _is_ancestor(center, first_bone))
                )
                pairs = []
                for (joint, head), (_next_joint, tail) in zip(
                    entries, entries[1:]
                ):
                    if not _is_ancestor(head, tail):
                        break
                    head_rest = head.bone.convert_local_to_pose(
                        Matrix(), head.bone.matrix_local
                    )
                    tail_rest = tail.bone.convert_local_to_pose(
                        Matrix(), tail.bone.matrix_local
                    )
                    tail_world = armature_world @ self._initial_matrix(tail).to_translation()
                    pairs.append(
                        PairState(
                            head.name,
                            tail.name,
                            head_rest,
                            tail_rest,
                            max(0.0, float(joint.get("stiffness", 0.0))),
                            min(
                                max(float(joint.get("drag_force", 0.0)), 0.0),
                                1.0,
                            ),
                            Vector(joint.get("gravity_dir", (0.0, 0.0, -1.0))),
                            max(0.0, float(joint.get("gravity_power", 0.0))),
                            max(0.0, float(joint.get("hit_radius", 0.0))),
                            tail_world.copy(),
                            tail_world.copy(),
                        )
                    )
                if pairs:
                    chains.append(ChainState(pairs, use_center_space))
                for _joint, bone in entries:
                    self.bone_names.add(bone.name)
            if chains:
                self.springs.append(
                    SpringState(
                        chains,
                        tuple(indices),
                        center_name,
                        previous_center,
                    )
                )
        if not self.springs:
            raise ValueError("No valid cached VRM 1.x Spring chains")

    def _build_vrm0(self, armature):
        pose_bones = armature.pose.bones
        collider_index = {}
        for item in self.data.colliders:
            uuid = str(item.get("uuid", ""))
            object_name = str(item.get("object", ""))
            if not uuid or not object_name:
                continue
            collider_index[uuid] = len(self.colliders)
            self.colliders.append(ObjectColliderSource(object_name))
        group_indices = {
            str(group.get("uuid", "")): tuple(
                collider_index[uuid]
                for uuid in group.get("colliders", ())
                if uuid in collider_index
            )
            for group in self.data.collider_groups
        }
        armature_world = armature.matrix_world.copy()

        for spring in self.data.springs:
            indices = []
            for uuid in spring.get("collider_groups", ()):
                for index in group_indices.get(uuid, ()):
                    if index not in indices:
                        indices.append(index)
            center_name = str(spring.get("center", ""))
            center = pose_bones.get(center_name)
            joints = []
            joint_index_by_name = {}

            def add_subtree(pose_bone, parent_index):
                if pose_bone.name in joint_index_by_name:
                    return
                current_matrix = self._initial_matrix(pose_bone)
                if pose_bone.children:
                    target_object = self._initial_matrix(pose_bone.children[0]).to_translation()
                else:
                    target_object = (
                        current_matrix @ Vector((0.0, pose_bone.bone.length, 0.0))
                        if self.initialize_from_rest else pose_bone.tail.copy()
                    )
                local_target = current_matrix.inverted_safe() @ target_object
                local_axis = _safe_normalized(
                    local_target, Vector((0.0, 1.0, 0.0))
                )
                head_world = armature_world @ current_matrix.to_translation()
                target_world = armature_world @ target_object
                length = max((target_world - head_world).length, 1.0e-6)

                if parent_index is not None:
                    parent_matrix = joints[parent_index].base_matrix
                    relative_matrix = parent_matrix.inverted_safe() @ current_matrix
                    external_parent_name = ""
                elif pose_bone.parent is not None:
                    relative_matrix = (
                        self._initial_matrix(pose_bone.parent).inverted_safe() @ current_matrix
                    )
                    external_parent_name = pose_bone.parent.name
                else:
                    relative_matrix = current_matrix.copy()
                    external_parent_name = ""

                index = len(joints)
                joint_index_by_name[pose_bone.name] = index
                joints.append(
                    Vrm0JointState(
                        pose_bone.name,
                        parent_index,
                        external_parent_name,
                        relative_matrix,
                        current_matrix,
                        Quaternion() if self.initialize_from_rest else pose_bone.matrix_basis.to_quaternion(),
                        local_axis,
                        length,
                        target_world.copy(),
                        target_world.copy(),
                    )
                )
                self.bone_names.add(pose_bone.name)
                for child in pose_bone.children:
                    add_subtree(child, index)

            for root_entry in spring.get("joints", ()):
                root = pose_bones.get(str(root_entry.get("bone", "")))
                if root is not None:
                    add_subtree(root, None)

            if not joints:
                continue
            previous_center = (
                armature_world @ self._initial_matrix(center).to_translation()
                if center is not None
                else None
            )
            self.springs.append(
                Vrm0GroupState(
                    joints,
                    tuple(indices),
                    max(0.0, float(spring.get("stiffness", 0.0))),
                    min(max(float(spring.get("drag_force", 0.0)), 0.0), 1.0),
                    Vector(spring.get("gravity_dir", (0.0, 0.0, -1.0))),
                    max(0.0, float(spring.get("gravity_power", 0.0))),
                    max(0.0, float(spring.get("hit_radius", 0.0))),
                    center_name,
                    previous_center,
                )
            )

    def sample_boundary(self, armature):
        """Capture moving parents, centers, and colliders without writing pose."""

        pose_bones = armature.pose.bones
        armature_world = armature.matrix_world.copy()
        parents = {}
        centers = {}
        for spring in self.springs:
            center = pose_bones.get(spring.center_name)
            if center is not None:
                centers[spring.center_name] = (
                    armature_world @ center.matrix.to_translation()
                )
            if isinstance(spring, Vrm0GroupState):
                parent_names = (
                    joint.external_parent_name
                    for joint in spring.joints
                    if joint.external_parent_name
                )
            else:
                parent_names = []
                for chain in spring.chains:
                    head = pose_bones.get(chain.pairs[0].head_name)
                    if head is not None and head.parent is not None:
                        parent_names.append(head.parent.name)
            for name in parent_names:
                parent = pose_bones.get(name)
                if parent is not None and name not in parents:
                    parents[name] = parent.matrix.copy()
        return SpringBoundary(
            armature_world,
            parents,
            centers,
            tuple(source.world(armature) for source in self.colliders),
        )

    def _center_delta(self, spring, boundary):
        current = boundary.centers.get(spring.center_name)
        if current is None:
            spring.previous_center = None
            return Vector()
        if spring.previous_center is None:
            spring.previous_center = current.copy()
            return Vector()
        delta = current - spring.previous_center
        spring.previous_center = current.copy()
        return delta

    def step(self, armature, delta_time, boundary=None):
        """Advance physics using a sampled boundary or the current live pose."""

        if self.trace_enabled:
            self.trace_bones = {}
        if boundary is None:
            boundary = self.sample_boundary(armature)
        if self.data.version.startswith("0"):
            return self._step_vrm0(armature, delta_time, boundary)

        delta_time = min(max(float(delta_time), 0.0), 0.1)
        armature_world = boundary.armature_world
        world_inverse = armature_world.inverted_safe()
        world_rotation = armature_world.to_quaternion()
        world_colliders = boundary.colliders
        rotations = {}
        for spring in self.springs:
            tracked_center_delta = self._center_delta(spring, boundary)
            colliders = [
                world_colliders[index]
                for index in spring.collider_indices
                if index < len(world_colliders) and world_colliders[index] is not None
            ]
            for chain in spring.chains:
                center_delta = (
                    tracked_center_delta if chain.use_center_space else Vector()
                )
                next_before = None
                for pair in chain.pairs:
                    head = armature.pose.bones.get(pair.head_name)
                    tail = armature.pose.bones.get(pair.tail_name)
                    if head is None or tail is None:
                        continue
                    if next_before is None:
                        parent = head.parent
                        if parent is None:
                            next_before = pair.head_rest_matrix.copy()
                        else:
                            parent_rest = parent.bone.convert_local_to_pose(
                                Matrix(), parent.bone.matrix_local
                            )
                            parent_matrix = boundary.parent_matrices.get(
                                parent.name, parent_rest
                            )
                            next_before = parent_matrix @ (
                                parent_rest.inverted_safe() @ pair.head_rest_matrix
                            )
                    location, parent_rotation, scale = next_before.decompose()
                    head_world = armature_world @ location
                    previous_tail = pair.previous_tail + center_delta
                    current_tail = pair.current_tail + center_delta
                    local_axis = pair.head_rest_matrix.inverted_safe() @ (
                        pair.tail_rest_matrix.to_translation()
                    )
                    fallback_world = world_rotation @ parent_rotation @ local_axis
                    inertia = (current_tail - previous_tail) * (1.0 - pair.drag_force)
                    stiffness = (
                        _safe_normalized(fallback_world, Vector((0.0, 0.0, 1.0)))
                        * pair.stiffness
                        * delta_time
                    )
                    external = pair.gravity_dir * pair.gravity_power * delta_time
                    next_tail = current_tail + inertia + stiffness + external
                    rest_tail_world = armature_world @ (next_before @ local_axis)
                    length = max((rest_tail_world - head_world).length, 1.0e-6)
                    fallback = _safe_normalized(
                        fallback_world, Vector((0.0, 0.0, 1.0))
                    )
                    next_tail = head_world + _safe_normalized(
                        next_tail - head_world, fallback
                    ) * length
                    for collider in colliders:
                        direction, distance = collider.collision(
                            next_tail, pair.hit_radius
                        )
                        if distance < 0.0:
                            next_tail -= direction * distance
                            next_tail = head_world + _safe_normalized(
                                next_tail - head_world, fallback
                            ) * length
                    end_axis = next_before.inverted_safe() @ (
                        world_inverse @ next_tail
                    )
                    start_axis = _safe_normalized(
                        local_axis, Vector((0.0, 1.0, 0.0))
                    )
                    end_axis = _safe_normalized(end_axis, start_axis)
                    delta_rotation = start_axis.rotation_difference(end_axis)
                    object_rotation = parent_rotation @ delta_rotation
                    result_matrix = (
                        Matrix.Translation(location)
                        @ object_rotation.to_matrix().to_4x4()
                        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
                    )
                    next_before = (
                        result_matrix
                        @ pair.head_rest_matrix.inverted_safe()
                        @ pair.tail_rest_matrix
                    )
                    pair.previous_tail = current_tail
                    pair.current_tail = next_tail
                    rotation = (
                        delta_rotation
                        if head.bone.use_inherit_rotation
                        else object_rotation
                    )
                    rotation.normalize()
                    rotations[head.name] = rotation
                    if self.trace_enabled:
                        self.trace_bones[head.name] = {
                            "head_world": tuple(head_world),
                            "tail_world": tuple(next_tail),
                            "previous_tail_world": tuple(previous_tail),
                            "local_rotation_wxyz": tuple(rotation),
                            "world_rotation_wxyz": tuple((world_rotation @ object_rotation).normalized()),
                            "parent_bone": head.parent.name if head.parent is not None else "",
                            "radius_world": float(pair.hit_radius),
                        }
        return rotations

    def _step_vrm0(self, armature, delta_time, boundary):
        delta_time = min(max(float(delta_time), 0.0), 0.1)
        armature_world = boundary.armature_world
        world_inverse = armature_world.inverted_safe()
        world_rotation = armature_world.to_quaternion()
        world_scale = max(abs(value) for value in armature_world.to_scale())
        world_colliders = boundary.colliders
        rotations = {}

        for group in self.springs:
            center_delta = self._center_delta(group, boundary)
            colliders = [
                world_colliders[index]
                for index in group.collider_indices
                if index < len(world_colliders) and world_colliders[index] is not None
            ]
            result_matrices = {}
            for index, joint in enumerate(group.joints):
                if joint.parent_index is not None:
                    parent_matrix = result_matrices[joint.parent_index]
                    before_rotation = parent_matrix @ joint.relative_matrix
                elif joint.external_parent_name:
                    parent_matrix = boundary.parent_matrices.get(
                        joint.external_parent_name
                    )
                    before_rotation = (
                        parent_matrix @ joint.relative_matrix
                        if parent_matrix is not None
                        else joint.base_matrix.copy()
                    )
                else:
                    before_rotation = joint.base_matrix.copy()

                head_world = armature_world @ before_rotation.to_translation()
                previous_tail = joint.previous_tail + center_delta
                current_tail = joint.current_tail + center_delta
                inertia = (current_tail - previous_tail) * (1.0 - group.drag_force)
                base_direction_world = world_rotation @ (
                    before_rotation.to_quaternion() @ joint.local_axis
                )
                stiffness = (
                    _safe_normalized(
                        base_direction_world, Vector((0.0, 0.0, 1.0))
                    )
                    * group.stiffness
                    * delta_time
                )
                external = group.gravity_dir * group.gravity_power * delta_time
                next_tail = current_tail + inertia + stiffness + external
                fallback = _safe_normalized(
                    base_direction_world, Vector((0.0, 0.0, 1.0))
                )
                next_tail = head_world + _safe_normalized(
                    next_tail - head_world, fallback
                ) * joint.length

                joint_radius = group.hit_radius * world_scale
                for collider in colliders:
                    total_radius = max(0.0, collider.radius) + joint_radius
                    delta = next_tail - collider.center
                    if delta.length_squared >= total_radius * total_radius:
                        continue
                    direction = _safe_normalized(
                        delta, head_world - collider.center
                    )
                    pushed = collider.center + direction * total_radius
                    next_tail = head_world + _safe_normalized(
                        pushed - head_world, fallback
                    ) * joint.length

                joint.previous_tail = current_tail
                joint.current_tail = next_tail
                next_tail_object = world_inverse @ next_tail
                end_direction_local = _safe_normalized(
                    before_rotation.inverted_safe() @ next_tail_object,
                    joint.local_axis,
                )
                delta_rotation = joint.local_axis.rotation_difference(
                    end_direction_local
                )
                location, rotation, scale = before_rotation.decompose()
                result_matrices[index] = (
                    Matrix.Translation(location)
                    @ (rotation @ delta_rotation).to_matrix().to_4x4()
                    @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
                )
                output_rotation = joint.base_basis_rotation @ delta_rotation
                output_rotation.normalize()
                rotations[joint.bone_name] = output_rotation
                if self.trace_enabled:
                    bone = armature.pose.bones.get(joint.bone_name)
                    self.trace_bones[joint.bone_name] = {
                        "head_world": tuple(head_world),
                        "tail_world": tuple(next_tail),
                        "previous_tail_world": tuple(previous_tail),
                        "local_rotation_wxyz": tuple(output_rotation),
                        "world_rotation_wxyz": tuple((world_rotation @ (rotation @ delta_rotation)).normalized()),
                        "parent_bone": bone.parent.name if bone is not None and bone.parent is not None else "",
                        "radius_world": float(joint_radius),
                    }

        return rotations
