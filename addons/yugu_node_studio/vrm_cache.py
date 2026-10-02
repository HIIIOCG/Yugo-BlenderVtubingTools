"""One-shot VRM parsing. Runtime evaluation never walks VRM Add-on RNA."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import bpy

from .constants import (
    EXPRESSION_PRESETS,
    HUMANOID_BONES,
    PROPERTY_BY_HUMANOID,
    normalize_name,
)
from .data import (
    BoneMapData,
    CharacterSnapshot,
    ExpressionDefinition,
    ExpressionMapData,
    MaterialTarget,
    MetaData,
    MorphTarget,
    SpringData,
)


_snapshots: dict[str, CharacterSnapshot] = {}


def _cache_key(node) -> str:
    return str(getattr(node, "runtime_id", "") or "")


def _extension(armature):
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        return None
    return getattr(getattr(armature, "data", None), "vrm_addon_extension", None)


def _bone_name(human_bone) -> str:
    node = getattr(human_bone, "node", None)
    if node is None and getattr(human_bone, "bone_name", ""):
        node = human_bone
    return str(
        getattr(node, "bone_name", "")
        or getattr(node, "value", "")
        or ""
    )


def _vrm0_canonical(name: str) -> str:
    if name.endswith("ThumbProximal"):
        return name.removesuffix("ThumbProximal") + "ThumbMetacarpal"
    if name.endswith("ThumbIntermediate"):
        return name.removesuffix("ThumbIntermediate") + "ThumbProximal"
    return name


def _fallback_bones(armature, mapping: dict[str, str]) -> None:
    pose_bones = getattr(getattr(armature, "pose", None), "bones", ())
    available = {normalize_name(bone.name): bone.name for bone in pose_bones}
    for humanoid in HUMANOID_BONES:
        if humanoid in mapping:
            continue
        name = available.get(normalize_name(humanoid))
        if name:
            mapping[humanoid] = name


def _read_bone_map(armature, extension) -> BoneMapData:
    mapping: dict[str, str] = {}
    version = str(getattr(extension, "spec_version", "") or "") if extension else ""
    if version.startswith("1"):
        humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
        human_bones = getattr(humanoid, "human_bones", None)
        for canonical, property_name in PROPERTY_BY_HUMANOID.items():
            name = _bone_name(getattr(human_bones, property_name, None))
            if name:
                mapping[canonical] = name
    elif version.startswith("0"):
        humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
        for human_bone in getattr(humanoid, "human_bones", ()) or ():
            canonical = _vrm0_canonical(str(getattr(human_bone, "bone", "") or ""))
            name = _bone_name(human_bone)
            if canonical in HUMANOID_BONES and name:
                mapping[canonical] = name
    _fallback_bones(armature, mapping)
    available = set(getattr(getattr(armature, "pose", None), "bones", {}).keys())
    mapping = {key: value for key, value in mapping.items() if value in available}
    return BoneMapData(
        armature.name,
        mapping,
        {bone_name: humanoid for humanoid, bone_name in mapping.items()},
    )


def _float_tuple(value) -> tuple[float, ...]:
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return ()


def _morph_target(bind, *, vrm0=False) -> MorphTarget | None:
    owner = getattr(bind, "mesh" if vrm0 else "node", None)
    object_name = str(getattr(owner, "mesh_object_name", "") or "")
    shape_key_name = str(getattr(bind, "index", "") or "")
    if not object_name or not shape_key_name:
        return None
    weight = float(getattr(bind, "weight", 1.0) or 0.0)
    if vrm0 and abs(weight) > 1.0:
        weight /= 100.0
    return MorphTarget(object_name, shape_key_name, weight)


def _material_target(bind, *, vrm0=False) -> MaterialTarget | None:
    material = getattr(bind, "material", None)
    material_name = str(getattr(material, "name", "") or "")
    if not material_name:
        return None
    if vrm0:
        bind_type = "VRM0_PROPERTY"
        property_name = str(getattr(bind, "property_name", "") or "")
        target = _float_tuple(getattr(bind, "target_value", ()))
    else:
        bind_type = str(getattr(bind, "type", "COLOR") or "COLOR")
        property_name = ""
        target = _float_tuple(getattr(bind, "target_value", ()))
    return MaterialTarget(material_name, bind_type, target, property_name)


def _definition(
    name,
    expression,
    morph_binds,
    material_binds,
    *,
    vrm0=False,
) -> ExpressionDefinition | None:
    normalized = normalize_name(name)
    if not normalized:
        return None
    morphs = tuple(
        target
        for bind in morph_binds or ()
        if (target := _morph_target(bind, vrm0=vrm0)) is not None
    )
    materials = tuple(
        target
        for bind in material_binds or ()
        if (target := _material_target(bind, vrm0=vrm0)) is not None
    )
    texture_binds = () if vrm0 else getattr(expression, "texture_transform_binds", ())
    for bind in texture_binds or ():
        material = getattr(bind, "material", None)
        material_name = str(getattr(material, "name", "") or "")
        if not material_name:
            continue
        scale = _float_tuple(getattr(bind, "scale", (1.0, 1.0)))
        offset = _float_tuple(getattr(bind, "offset", (0.0, 0.0)))
        materials += (
            MaterialTarget(
                material_name,
                "textureTransform",
                tuple(scale[:2]) + tuple(offset[:2]),
            ),
        )
    if not morphs and not materials:
        return None
    return ExpressionDefinition(
        str(name),
        normalized,
        bool(getattr(expression, "is_binary", False)),
        morphs,
        materials,
        str(getattr(expression, "override_blink", "NONE") or "NONE"),
        str(getattr(expression, "override_look_at", "NONE") or "NONE"),
        str(getattr(expression, "override_mouth", "NONE") or "NONE"),
    )


def _read_expressions(extension) -> ExpressionMapData:
    definitions: dict[str, ExpressionDefinition] = {}
    version = str(getattr(extension, "spec_version", "") or "") if extension else ""
    if version.startswith("1"):
        expressions = getattr(getattr(extension, "vrm1", None), "expressions", None)
        preset = getattr(expressions, "preset", None)
        entries = []
        for property_name, display_name in EXPRESSION_PRESETS:
            expression = getattr(preset, property_name, None)
            if expression is not None:
                entries.append((display_name, expression))
        entries.extend(
            (str(getattr(item, "custom_name", "") or ""), item)
            for item in getattr(expressions, "custom", ()) or ()
        )
        for name, expression in entries:
            item = _definition(
                name,
                expression,
                getattr(expression, "morph_target_binds", ()),
                getattr(expression, "material_color_binds", ()),
            )
            if item is not None:
                definitions.setdefault(item.normalized_name, item)
    elif version.startswith("0"):
        master = getattr(getattr(extension, "vrm0", None), "blend_shape_master", None)
        for expression in getattr(master, "blend_shape_groups", ()) or ():
            name = str(getattr(expression, "name", "") or "")
            item = _definition(
                name,
                expression,
                getattr(expression, "binds", ()),
                getattr(expression, "material_values", ()),
                vrm0=True,
            )
            if item is not None:
                definitions.setdefault(item.normalized_name, item)
    return ExpressionMapData(definitions)


def _vector(value) -> tuple[float, float, float]:
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (AttributeError, IndexError, TypeError, ValueError):
        return (0.0, 0.0, 0.0)


def _read_spring_data(extension, armature_name="") -> SpringData:
    version = str(getattr(extension, "spec_version", "") or "") if extension else ""
    springs: list[dict[str, Any]] = []
    colliders: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    if version.startswith("1"):
        spring_bone = getattr(extension, "spring_bone1", None)
        for collider in getattr(spring_bone, "colliders", ()) or ():
            extended = getattr(
                getattr(collider, "extensions", None),
                "vrmc_spring_bone_extended_collider",
                None,
            )
            use_extended = bool(getattr(extended, "enabled", False))
            if use_extended:
                shape_type = str(getattr(extended, "shape_type", "SPHERE") or "SPHERE")
                shape = getattr(extended, "shape", None)
            else:
                shape_type = str(getattr(collider, "shape_type", "SPHERE") or "SPHERE")
                shape = getattr(collider, "shape", None)
            shape_upper = shape_type.upper()
            if "CAPSULE" in shape_upper:
                shape_value = getattr(shape, "capsule", None)
            elif "PLANE" in shape_upper:
                shape_value = getattr(shape, "plane", None)
            else:
                shape_value = getattr(shape, "sphere", None)
            colliders.append(
                {
                    "uuid": str(getattr(collider, "uuid", "") or ""),
                    "bone": _bone_name(getattr(collider, "node", None)),
                    "shape": shape_type,
                    "offset": _vector(getattr(shape_value, "offset", (0, 0, 0))),
                    "tail": _vector(getattr(shape_value, "tail", (0, 0, 0))),
                    "normal": _vector(getattr(shape_value, "normal", (0, 0, 1))),
                    "radius": float(getattr(shape_value, "radius", 0.0) or 0.0),
                    "inside": bool(getattr(shape_value, "inside", False)),
                }
            )
        for group in getattr(spring_bone, "collider_groups", ()) or ():
            groups.append(
                {
                    "uuid": str(getattr(group, "uuid", "") or ""),
                    "name": str(getattr(group, "vrm_name", "") or ""),
                    "colliders": tuple(
                        str(getattr(ref, "collider_uuid", "") or "")
                        for ref in getattr(group, "colliders", ()) or ()
                    ),
                }
            )
        for spring in getattr(spring_bone, "springs", ()) or ():
            springs.append(
                {
                    "name": str(getattr(spring, "vrm_name", "") or ""),
                    "center": _bone_name(getattr(spring, "center", None)),
                    "collider_groups": tuple(
                        str(getattr(ref, "collider_group_uuid", "") or "")
                        for ref in getattr(spring, "collider_groups", ()) or ()
                    ),
                    "joints": tuple(
                        {
                            "bone": _bone_name(getattr(joint, "node", None)),
                            "hit_radius": float(getattr(joint, "hit_radius", 0.0) or 0.0),
                            "stiffness": float(getattr(joint, "stiffness", 0.0) or 0.0),
                            "gravity_power": float(getattr(joint, "gravity_power", 0.0) or 0.0),
                            "gravity_dir": _vector(getattr(joint, "gravity_dir", (0, 0, -1))),
                            "drag_force": float(getattr(joint, "drag_force", 0.0) or 0.0),
                        }
                        for joint in getattr(spring, "joints", ()) or ()
                    ),
                }
            )
    elif version.startswith("0"):
        secondary = getattr(getattr(extension, "vrm0", None), "secondary_animation", None)
        for group in getattr(secondary, "collider_groups", ()) or ():
            group_uuid = str(getattr(group, "uuid", "") or "")
            collider_ids = []
            for index, collider in enumerate(getattr(group, "colliders", ()) or ()):
                bpy_object = getattr(collider, "bpy_object", None)
                identifier = f"{group_uuid}:{index}"
                collider_ids.append(identifier)
                colliders.append(
                    {
                        "uuid": identifier,
                        "bone": _bone_name(getattr(group, "node", None)),
                        "shape": "SPHERE",
                        "object": str(getattr(bpy_object, "name", "") or ""),
                    }
                )
            groups.append(
                {"uuid": group_uuid, "name": group_uuid, "colliders": tuple(collider_ids)}
            )
        for group in getattr(secondary, "bone_groups", ()) or ():
            springs.append(
                {
                    "name": str(getattr(group, "comment", "") or ""),
                    "center": _bone_name(getattr(group, "center", None)),
                    "collider_groups": tuple(
                        str(getattr(ref, "collider_group_uuid", "") or "")
                        for ref in getattr(group, "collider_groups", ()) or ()
                    ),
                    "joints": tuple(
                        {"bone": _bone_name(ref)}
                        for ref in getattr(group, "bones", ()) or ()
                    ),
                    "hit_radius": float(getattr(group, "hit_radius", 0.0) or 0.0),
                    "stiffness": float(
                        getattr(
                            group,
                            "stiffiness",
                            getattr(group, "stiffness", 0.0),
                        )
                        or 0.0
                    ),
                    "gravity_power": float(getattr(group, "gravity_power", 0.0) or 0.0),
                    "gravity_dir": _vector(getattr(group, "gravity_dir", (0, 0, -1))),
                    "drag_force": float(getattr(group, "drag_force", 0.0) or 0.0),
                }
            )
    return SpringData(
        version,
        tuple(springs),
        tuple(colliders),
        tuple(groups),
        str(armature_name),
    )


def _meta_value(meta, *names, default=""):
    for name in names:
        value = getattr(meta, name, None)
        if value not in (None, ""):
            return value
    return default


def _read_meta(armature, extension) -> MetaData:
    version = str(getattr(extension, "spec_version", "") or "") if extension else ""
    meta = (
        getattr(getattr(extension, "vrm1", None), "meta", None)
        if version.startswith("1")
        else getattr(getattr(extension, "vrm0", None), "meta", None)
    )
    authors = tuple(
        str(getattr(item, "value", item))
        for item in getattr(meta, "authors", ()) or ()
    )
    values = {
        "name": str(_meta_value(meta, "name", "title", default=armature.name)),
        "version": str(_meta_value(meta, "version")),
        "authors": authors or (str(_meta_value(meta, "author")),),
        "copyright": str(_meta_value(meta, "copyright_information", "copyright")),
        "contact": str(_meta_value(meta, "contact_information", "contact_information")),
        "reference": str(_meta_value(meta, "reference", "references")),
        "vrm_version": version or "Blender Armature",
    }
    return MetaData(values)


def build_snapshot(armature, revision=1) -> CharacterSnapshot:
    from . import targets
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        raise ValueError("Select an Armature")
    extension = _extension(armature)
    version = str(getattr(extension, "spec_version", "") or "") if extension else ""
    return CharacterSnapshot(
        armature.name,
        version or "BLENDER",
        _read_bone_map(armature, extension),
        _read_expressions(extension),
        _read_spring_data(extension, armature.name),
        _read_meta(armature, extension),
        int(revision),
        targets.character_objects(armature),
    )


def reload_node(node) -> CharacterSnapshot:
    key = _cache_key(node)
    if not key:
        raise ValueError("VRM Data node has no runtime ID")
    # A changed snapshot can point to a different character or target set.
    # Restore values compiled from the previous snapshot before replacing it.
    try:
        from . import runtime

        runtime.restore_tree(node.id_data)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    revision = int(getattr(node, "cache_revision", 0)) + 1
    _snapshots.pop(key, None)
    from . import targets
    snapshot = build_snapshot(targets.vrm_armature(node), revision)
    _snapshots[key] = snapshot
    node.cache_revision = revision
    node.cache_status = "READY"
    node.cache_message = (
        f"{len(snapshot.bone_map.by_humanoid)} bones, "
        f"{len(snapshot.expression_map.definitions)} expressions, "
        f"{len(snapshot.spring_data.springs)} springs, {len(snapshot.objects)} objects"
    )
    return snapshot


def invalidate_node(node, message="Reload required") -> None:
    key = _cache_key(node)
    if key:
        _snapshots.pop(key, None)
    try:
        node.cache_status = "STALE"
        node.cache_message = message
    except (AttributeError, ReferenceError):
        pass


def get_snapshot(node) -> CharacterSnapshot | None:
    return _snapshots.get(_cache_key(node))


def clear_all() -> None:
    _snapshots.clear()


def snapshot_as_dict(node) -> dict[str, Any] | None:
    snapshot = get_snapshot(node)
    return asdict(snapshot) if snapshot is not None else None


def invalidate_saved_nodes() -> None:
    clear_all()
    for tree in bpy.data.node_groups:
        if getattr(tree, "bl_idname", "") != "YUGUS_NT_humanoid_motion":
            continue
        for node in tree.nodes:
            if getattr(node, "bl_idname", "") == "YUGUS_NODE_vrm_data":
                invalidate_node(node)
