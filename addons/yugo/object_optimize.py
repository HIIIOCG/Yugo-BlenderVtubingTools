"""Destructive mesh optimization and VRM avatar collection organization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import bmesh
import bpy
from bpy.types import Context, Object

from .springbone.vrm import (
    get_vrm_extension,
    is_vrm0,
    is_vrm1,
    spring_bone_data,
    vrm0_secondary_animation,
)
from .vrm import get_avatar_name


@dataclass
class MeshOptimizeResult:
    merged_objects: int = 0
    decimated_objects: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CollectionArrangeResult:
    related_objects: int = 0
    mesh_objects: int = 0
    vrm0_colliders: int = 0
    vrm1_colliders: int = 0
    removed_collections: int = 0


def _selected_meshes(context: Context) -> list[Object]:
    return sorted(
        (obj for obj in context.selected_objects if obj.type == "MESH"),
        key=lambda item: item.name.casefold(),
    )


def _merge_by_distance(obj: Object, distance: float) -> None:
    mesh = obj.data
    if mesh.users > 1:
        mesh = mesh.copy()
        obj.data = mesh
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(
            bm,
            verts=tuple(bm.verts),
            dist=max(0.0, float(distance)),
        )
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()


def _decimate_geometry(context: Context, obj: Object, ratio: float) -> None:
    """Run Mesh > Clean Up > Decimate Geometry without creating a modifier."""

    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    previous_selected = tuple(context.selected_objects)
    try:
        for selected in previous_selected:
            selected.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        with context.temp_override(
            active_object=obj,
            object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
            view_layer=context.view_layer,
        ):
            if bpy.ops.object.mode_set(mode="EDIT") != {"FINISHED"}:
                raise RuntimeError("Blender could not enter Edit Mode")
            bpy.ops.mesh.select_all(action="SELECT")
            if bpy.ops.mesh.decimate(
                ratio=max(0.0, min(1.0, float(ratio)))
            ) != {"FINISHED"}:
                raise RuntimeError("Blender could not decimate the mesh geometry")
            if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
                raise RuntimeError("Blender could not leave Edit Mode")
    finally:
        if getattr(obj, "mode", "OBJECT") != "OBJECT":
            try:
                with context.temp_override(
                    active_object=obj,
                    object=obj,
                    selected_objects=[obj],
                    selected_editable_objects=[obj],
                    view_layer=view_layer,
                ):
                    bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        for selected in tuple(context.selected_objects):
            selected.select_set(False)
        for selected in previous_selected:
            try:
                selected.select_set(True)
            except (ReferenceError, RuntimeError):
                pass
        if previous_active is not None:
            try:
                view_layer.objects.active = previous_active
            except ReferenceError:
                pass


def optimize_selected_meshes(
    context: Context,
    merge_distance: float,
    decimate_ratio: float,
) -> MeshOptimizeResult:
    result = MeshOptimizeResult()
    if context.mode != "OBJECT":
        result.errors.append("Mesh optimization requires Object Mode")
        return result

    meshes = _selected_meshes(context)
    if not meshes:
        result.errors.append("No selected Mesh objects were found")
        return result

    for obj in meshes:
        shape_keys = getattr(obj.data, "shape_keys", None)
        if shape_keys is not None and len(shape_keys.key_blocks) > 0:
            result.errors.append(
                f'{obj.name}: skipped Mesh optimization to preserve Shape Keys'
            )
            continue
        try:
            _merge_by_distance(obj, merge_distance)
            result.merged_objects += 1
            if decimate_ratio < 0.999999:
                _decimate_geometry(context, obj, decimate_ratio)
                result.decimated_objects += 1
        except Exception as error:
            result.errors.append(f"{obj.name}: {error}")
    return result


def _children_recursive(obj: Object) -> set[Object]:
    result = set()
    pending = list(obj.children)
    while pending:
        child = pending.pop()
        if child in result:
            continue
        result.add(child)
        pending.extend(child.children)
    return result


def _vrm_collider_objects(armature: Object) -> tuple[set[Object], set[Object]]:
    extension = get_vrm_extension(armature)
    vrm0_objects: set[Object] = set()
    vrm1_objects: set[Object] = set()
    if extension is None:
        return vrm0_objects, vrm1_objects

    if is_vrm1(extension):
        spring_bone = spring_bone_data(extension)
        for collider in getattr(spring_bone, "colliders", ()) or ():
            collider_object = getattr(collider, "bpy_object", None)
            if collider_object is None:
                continue
            vrm1_objects.add(collider_object)
            vrm1_objects.update(_children_recursive(collider_object))

    if is_vrm0(extension):
        secondary = vrm0_secondary_animation(extension)
        for collider_group in getattr(secondary, "collider_groups", ()) or ():
            for collider in getattr(collider_group, "colliders", ()) or ():
                collider_object = getattr(collider, "bpy_object", None)
                if collider_object is None:
                    continue
                vrm0_objects.add(collider_object)
                vrm0_objects.update(_children_recursive(collider_object))

    return vrm0_objects, vrm1_objects


def _bound_meshes(scene, armature: Object) -> set[Object]:
    return {
        obj
        for obj in scene.objects
        if obj.type == "MESH"
        and any(
            modifier.type == "ARMATURE" and modifier.object == armature
            for modifier in obj.modifiers
        )
    }


def _is_descendant_of(obj: Object, ancestor: Object) -> bool:
    parent = obj.parent
    while parent is not None:
        if parent == ancestor:
            return True
        parent = parent.parent
    return False


def _ensure_collection(parent, name: str):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if parent.children.get(collection.name) is None:
        parent.children.link(collection)
    return collection


def _move_object(obj: Object, target_collection) -> None:
    if target_collection.objects.get(obj.name) is None:
        target_collection.objects.link(obj)
    for collection in tuple(obj.users_collection):
        if collection != target_collection:
            collection.objects.unlink(obj)


def _arrange_collections(
    scene,
    armature: Object,
    avatar_name: str,
    meshes: set[Object],
    vrm0_colliders: set[Object],
    vrm1_colliders: set[Object],
) -> CollectionArrangeResult:
    top_name = avatar_name.strip() or armature.name
    top_collection = _ensure_collection(scene.collection, top_name)
    mesh_collection = _ensure_collection(top_collection, f"{top_name}_mesh")

    all_colliders = vrm0_colliders | vrm1_colliders
    old_collider_collections = {
        collection
        for collider in all_colliders
        for collection in tuple(collider.users_collection)
        if re.fullmatch(r"Colliders(?:\.\d+)?", collection.name)
    }

    vrm0_collection = (
        _ensure_collection(top_collection, f"{top_name}_vrm0_collider")
        if vrm0_colliders
        else None
    )
    vrm1_collection = (
        _ensure_collection(top_collection, f"{top_name}_vrm1_collider")
        if vrm1_colliders
        else None
    )

    related_objects = {
        obj
        for obj in scene.objects
        if obj == armature or _is_descendant_of(obj, armature)
    }
    related_objects.update(meshes)
    related_objects.update(all_colliders)

    for obj in sorted(related_objects, key=lambda item: item.name.casefold()):
        if obj in vrm1_colliders and vrm1_collection is not None:
            _move_object(obj, vrm1_collection)
        elif obj in vrm0_colliders and vrm0_collection is not None:
            _move_object(obj, vrm0_collection)
        elif obj in meshes:
            _move_object(obj, mesh_collection)
        else:
            _move_object(obj, top_collection)

    removed_collections = 0
    for collection in tuple(old_collider_collections):
        # VRM Add-on importer collections contain only collider empties. If a
        # user has added unrelated objects, retain the collection instead.
        if any(obj not in all_colliders for obj in collection.objects):
            continue
        bpy.data.collections.remove(collection)
        removed_collections += 1

    return CollectionArrangeResult(
        related_objects=len(related_objects),
        mesh_objects=len(meshes),
        vrm0_colliders=len(vrm0_colliders),
        vrm1_colliders=len(vrm1_colliders),
        removed_collections=removed_collections,
    )


def arrange_avatar_collections(context: Context, armature: Object):
    if armature is None or armature.type != "ARMATURE":
        raise RuntimeError("Select a VRM Armature in the Avatar List first")
    meshes = _bound_meshes(context.scene, armature)
    vrm0_colliders, vrm1_colliders = _vrm_collider_objects(armature)
    return _arrange_collections(
        context.scene,
        armature,
        get_avatar_name(armature),
        meshes,
        vrm0_colliders,
        vrm1_colliders,
    )
