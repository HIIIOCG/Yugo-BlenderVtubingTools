"""Viewport Empty visualization for VRM Spring Bone hit radii."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Matrix

from .vrm import is_vrm0, is_vrm1, spring_bone_data, vrm0_secondary_animation


MARKER_PROPERTY = "yugo_springbone_hit_radius"
OWNER_PROPERTY = "yugo_springbone_layer_id"


@dataclass(frozen=True)
class HitRadiusDisplay:
    name: str
    bone_name: str
    world_position: object
    radius: float


def _bone_name(node) -> str:
    return str(getattr(node, "bone_name", "") or "")


def _vrm1_displays(armature, extension) -> list[HitRadiusDisplay]:
    displays = []
    pose_bones = armature.pose.bones
    spring_bone = spring_bone_data(extension)
    for spring_index, spring in enumerate(
        getattr(spring_bone, "springs", ()) or (), start=1
    ):
        joints = tuple(getattr(spring, "joints", ()) or ())
        spring_name = str(getattr(spring, "vrm_name", "") or f"Spring {spring_index}")
        for joint_index, (head_joint, tail_joint) in enumerate(
            zip(joints, joints[1:]), start=1
        ):
            head_name = _bone_name(getattr(head_joint, "node", None))
            tail_name = _bone_name(getattr(tail_joint, "node", None))
            head_bone = pose_bones.get(head_name)
            tail_bone = pose_bones.get(tail_name)
            radius = max(0.0, float(getattr(head_joint, "hit_radius", 0.0) or 0.0))
            if head_bone is None or tail_bone is None or radius <= 0.0:
                continue
            position = armature.matrix_world @ tail_bone.matrix.to_translation()
            displays.append(
                HitRadiusDisplay(
                    name=f"Hit Radius {spring_name} {joint_index:03d} [{head_name}]",
                    bone_name=head_name,
                    world_position=position,
                    radius=radius,
                )
            )
    return displays


def _vrm0_displays(armature, extension) -> list[HitRadiusDisplay]:
    displays = []
    secondary = vrm0_secondary_animation(extension)
    if secondary is None:
        return displays
    pose_bones = armature.pose.bones
    world_scale = max(abs(component) for component in armature.matrix_world.to_scale())
    for group_index, group in enumerate(
        getattr(secondary, "bone_groups", ()) or (), start=1
    ):
        radius = max(0.0, float(getattr(group, "hit_radius", 0.0) or 0.0))
        if radius <= 0.0:
            continue
        group_name = str(getattr(group, "comment", "") or f"Bone Group {group_index}")
        seen = set()

        def add_subtree(pose_bone) -> None:
            if pose_bone.name in seen:
                return
            seen.add(pose_bone.name)
            target_object = (
                pose_bone.children[0].matrix.to_translation()
                if pose_bone.children
                else pose_bone.tail.copy()
            )
            displays.append(
                HitRadiusDisplay(
                    name=f"Hit Radius {group_name} [{pose_bone.name}]",
                    bone_name=pose_bone.name,
                    world_position=armature.matrix_world @ target_object,
                    radius=radius * world_scale,
                )
            )
            for child in pose_bone.children:
                add_subtree(child)

        for root_reference in getattr(group, "bones", ()) or ():
            root = pose_bones.get(_bone_name(root_reference))
            if root is not None:
                add_subtree(root)
    return displays


def collect_displays(armature, extension) -> list[HitRadiusDisplay]:
    if is_vrm1(extension):
        return _vrm1_displays(armature, extension)
    if is_vrm0(extension):
        return _vrm0_displays(armature, extension)
    return []


def clear(owner_id: str | None = None) -> None:
    # Failed extension enable can unwind under Blender's RestrictBlend context.
    # No runtime display can be created there, and scene data is unavailable.
    for obj in tuple(getattr(bpy.data, "objects", ())):
        try:
            is_marker = bool(obj.get(MARKER_PROPERTY, False))
            marker_owner = str(obj.get(OWNER_PROPERTY, "") or "")
            matches_owner = bool(marker_owner) and (
                owner_id is None or marker_owner == str(owner_id)
            )
        except (AttributeError, ReferenceError):
            continue
        if is_marker and matches_owner:
            bpy.data.objects.remove(obj, do_unlink=True)


def rebuild(armature, extension, owner_id: str, context=None) -> int:
    clear(owner_id)
    if armature is None or extension is None:
        return 0
    context = context or bpy.context
    displays = collect_displays(armature, extension)
    target_collections = tuple(getattr(armature, "users_collection", ()) or ())
    if not target_collections:
        target_collections = (context.scene.collection,)
    for display in displays:
        empty = bpy.data.objects.new(display.name, None)
        empty[MARKER_PROPERTY] = True
        empty[OWNER_PROPERTY] = str(owner_id)
        empty["yugo_springbone_armature"] = armature.name
        empty.empty_display_type = "SPHERE"
        empty.empty_display_size = display.radius
        empty.show_in_front = True
        empty.hide_render = True
        # Keep generated markers beside the armature in the Outliner. Parenting
        # alone is not enough when the Empty is linked to a different Collection.
        for collection in target_collections:
            collection.objects.link(empty)
        empty.parent = armature
        empty.parent_type = "BONE"
        empty.parent_bone = display.bone_name
        empty.matrix_world = Matrix.Translation(display.world_position)
    return len(displays)
