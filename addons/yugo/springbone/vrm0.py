"""VRM 0.x Secondary Animation preview using the UniVRM Verlet model."""

from __future__ import annotations

from dataclasses import dataclass
from sys import float_info

from mathutils import Matrix, Vector

from .vrm import vrm0_secondary_animation


def _bone_name(value) -> str:
    return str(getattr(value, "bone_name", "") or "")


def _safe_normalized(value: Vector, fallback: Vector) -> Vector:
    if value.length_squared <= float_info.epsilon:
        if fallback.length_squared <= float_info.epsilon:
            return Vector((0.0, 0.0, 1.0))
        return fallback.normalized()
    return value.normalized()


def _matrix_from_parts(location: Vector, rotation, scale: Vector) -> Matrix:
    return (
        Matrix.Translation(location)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )


@dataclass
class SphereCollider:
    center: Vector
    radius: float

    def push_out(
        self,
        target: Vector,
        target_radius: float,
        fallback_direction: Vector,
    ) -> Vector:
        total_radius = max(0.0, self.radius) + max(0.0, target_radius)
        delta = target - self.center
        if delta.length_squared >= total_radius * total_radius:
            return target
        direction = _safe_normalized(delta, fallback_direction)
        return self.center + direction * total_radius


@dataclass
class ColliderSource:
    bpy_object: object

    def world_collider(self) -> SphereCollider | None:
        bpy_object = self.bpy_object
        if bpy_object is None:
            return None
        try:
            matrix_world = bpy_object.matrix_world
            display_size = float(bpy_object.empty_display_size)
            scale = max(abs(component) for component in matrix_world.to_scale())
        except (AttributeError, ReferenceError, TypeError, ValueError):
            return None
        return SphereCollider(matrix_world.to_translation(), display_size * scale)


@dataclass
class JointState:
    pose_bone: object
    parent_index: int | None
    external_parent_name: str
    relative_matrix: Matrix
    base_matrix: Matrix
    base_basis_rotation: object
    local_axis: Vector
    length: float
    previous_tail: Vector
    current_tail: Vector


@dataclass
class GroupState:
    joints: list[JointState]
    collider_sources: list[ColliderSource]
    stiffness: float
    drag_force: float
    gravity_dir: Vector
    gravity_power: float
    hit_radius: float
    center_bone_name: str
    previous_center: Vector | None


class Vrm0SpringBoneSolver:
    """Evaluate legacy VRM 0.x root-bone groups in armature pose space."""

    def __init__(self, armature, extension):
        self.armature = armature
        self.extension = extension
        self.groups: list[GroupState] = []
        self.bone_names: tuple[str, ...] = ()
        self._build()

    def _build(self) -> None:
        secondary = vrm0_secondary_animation(self.extension)
        if secondary is None:
            raise RuntimeError("The selected VRM 0.x armature has no Secondary Animation data")
        pose_bones = self.armature.pose.bones
        collider_groups = tuple(getattr(secondary, "collider_groups", ()) or ())
        collider_group_by_uuid = {
            str(getattr(group, "uuid", "") or ""): group
            for group in collider_groups
            if str(getattr(group, "uuid", "") or "")
        }
        all_names: list[str] = []
        all_seen: set[str] = set()

        for bone_group in getattr(secondary, "bone_groups", ()) or ():
            collider_sources: list[ColliderSource] = []
            collider_seen: set[int] = set()
            for reference in getattr(bone_group, "collider_groups", ()) or ():
                uuid = str(getattr(reference, "collider_group_uuid", "") or "")
                collider_group = collider_group_by_uuid.get(uuid)
                if collider_group is None:
                    continue
                for collider in getattr(collider_group, "colliders", ()) or ():
                    bpy_object = getattr(collider, "bpy_object", None)
                    if bpy_object is None:
                        continue
                    key = id(bpy_object)
                    if key in collider_seen:
                        continue
                    collider_seen.add(key)
                    collider_sources.append(ColliderSource(bpy_object))

            joints: list[JointState] = []
            joint_index_by_name: dict[str, int] = {}

            def add_subtree(pose_bone, parent_index: int | None) -> None:
                if pose_bone.name in joint_index_by_name:
                    return
                current_matrix = pose_bone.matrix.copy()
                if pose_bone.children:
                    target_object = pose_bone.children[0].matrix.to_translation()
                else:
                    target_object = pose_bone.tail.copy()
                local_target = current_matrix.inverted_safe() @ target_object
                local_axis = _safe_normalized(local_target, Vector((0.0, 1.0, 0.0)))
                head_world = self.armature.matrix_world @ current_matrix.to_translation()
                target_world = self.armature.matrix_world @ target_object
                length = max((target_world - head_world).length, 1.0e-6)

                if parent_index is not None:
                    parent_matrix = joints[parent_index].base_matrix
                    relative_matrix = parent_matrix.inverted_safe() @ current_matrix
                    external_parent_name = ""
                elif pose_bone.parent is not None:
                    relative_matrix = pose_bone.parent.matrix.inverted_safe() @ current_matrix
                    external_parent_name = pose_bone.parent.name
                else:
                    relative_matrix = current_matrix.copy()
                    external_parent_name = ""

                index = len(joints)
                joint_index_by_name[pose_bone.name] = index
                joints.append(
                    JointState(
                        pose_bone=pose_bone,
                        parent_index=parent_index,
                        external_parent_name=external_parent_name,
                        relative_matrix=relative_matrix,
                        base_matrix=current_matrix,
                        base_basis_rotation=pose_bone.matrix_basis.to_quaternion(),
                        local_axis=local_axis,
                        length=length,
                        previous_tail=target_world.copy(),
                        current_tail=target_world.copy(),
                    )
                )
                if pose_bone.name not in all_seen:
                    all_seen.add(pose_bone.name)
                    all_names.append(pose_bone.name)
                for child in pose_bone.children:
                    add_subtree(child, index)

            for root_reference in getattr(bone_group, "bones", ()) or ():
                root = pose_bones.get(_bone_name(root_reference))
                if root is not None:
                    add_subtree(root, None)

            if not joints:
                continue
            center_name = _bone_name(getattr(bone_group, "center", None))
            center_bone = pose_bones.get(center_name)
            previous_center = (
                self.armature.matrix_world @ center_bone.matrix.to_translation()
                if center_bone is not None
                else None
            )
            stiffness = float(
                getattr(
                    bone_group,
                    "stiffiness",
                    getattr(bone_group, "stiffness", 0.0),
                )
                or 0.0
            )
            self.groups.append(
                GroupState(
                    joints=joints,
                    collider_sources=collider_sources,
                    stiffness=max(0.0, stiffness),
                    drag_force=min(
                        max(float(getattr(bone_group, "drag_force", 0.0) or 0.0), 0.0),
                        1.0,
                    ),
                    gravity_dir=Vector(
                        getattr(bone_group, "gravity_dir", (0.0, 0.0, 0.0))
                    ),
                    gravity_power=max(
                        0.0, float(getattr(bone_group, "gravity_power", 0.0) or 0.0)
                    ),
                    hit_radius=max(
                        0.0, float(getattr(bone_group, "hit_radius", 0.0) or 0.0)
                    ),
                    center_bone_name=center_name,
                    previous_center=previous_center,
                )
            )
        self.bone_names = tuple(all_names)
        if not self.groups:
            raise RuntimeError("No valid VRM 0.x Spring Bone root bones were found")

    def _center_delta(self, group: GroupState) -> Vector:
        center_bone = self.armature.pose.bones.get(group.center_bone_name)
        if center_bone is None:
            group.previous_center = None
            return Vector((0.0, 0.0, 0.0))
        current = self.armature.matrix_world @ center_bone.matrix.to_translation()
        if group.previous_center is None:
            group.previous_center = current.copy()
            return Vector((0.0, 0.0, 0.0))
        delta = current - group.previous_center
        group.previous_center = current.copy()
        return delta

    def step(self, delta_time: float) -> dict:
        delta_time = max(0.0, float(delta_time))
        armature_world = self.armature.matrix_world.copy()
        armature_world_inverse = armature_world.inverted_safe()
        world_scale = max(abs(component) for component in armature_world.to_scale())
        pose_bones = self.armature.pose.bones

        rotations = {}
        for group in self.groups:
            center_delta = self._center_delta(group)
            colliders = [
                collider
                for source in group.collider_sources
                if (collider := source.world_collider()) is not None
            ]
            result_matrices: dict[int, Matrix] = {}
            for index, joint in enumerate(group.joints):
                if joint.parent_index is not None:
                    parent_matrix = result_matrices[joint.parent_index]
                    before_rotation = parent_matrix @ joint.relative_matrix
                elif joint.external_parent_name:
                    parent = pose_bones.get(joint.external_parent_name)
                    before_rotation = (
                        parent.matrix @ joint.relative_matrix
                        if parent is not None
                        else joint.base_matrix.copy()
                    )
                else:
                    before_rotation = joint.base_matrix.copy()

                head_world = armature_world @ before_rotation.to_translation()
                previous_tail = joint.previous_tail + center_delta
                current_tail = joint.current_tail + center_delta
                inertia = (current_tail - previous_tail) * (1.0 - group.drag_force)
                base_direction_world = armature_world.to_quaternion() @ (
                    before_rotation.to_quaternion() @ joint.local_axis
                )
                stiffness = (
                    _safe_normalized(base_direction_world, Vector((0.0, 0.0, 1.0)))
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
                    pushed = collider.push_out(
                        next_tail,
                        joint_radius,
                        head_world - collider.center,
                    )
                    if (pushed - next_tail).length_squared <= float_info.epsilon:
                        continue
                    next_tail = head_world + _safe_normalized(
                        pushed - head_world, fallback
                    ) * joint.length

                joint.previous_tail = current_tail
                joint.current_tail = next_tail
                next_tail_object = armature_world_inverse @ next_tail
                end_direction_local = _safe_normalized(
                    before_rotation.inverted_safe() @ next_tail_object,
                    joint.local_axis,
                )
                delta_rotation = joint.local_axis.rotation_difference(end_direction_local)
                location, rotation, scale = before_rotation.decompose()
                result = _matrix_from_parts(
                    location,
                    rotation @ delta_rotation,
                    scale,
                )
                rotations[joint.pose_bone.name] = (
                    joint.base_basis_rotation @ delta_rotation
                )
                result_matrices[index] = result
        return rotations

    def reset(self) -> None:
        self.groups.clear()
        self.bone_names = ()
        self._build()
