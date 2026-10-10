"""Compile editable map nodes and merge their transient graph values."""

from __future__ import annotations

from .constants import normalize_name
from .data import (
    BoneMapData,
    ExpressionDefinition,
    ExpressionMapData,
    MaterialTarget,
    MorphTarget,
)


def _data_block_name(value) -> str:
    try:
        return str(value.name) if value is not None else ""
    except ReferenceError:
        return ""


def bone_map_from_node(node) -> BoneMapData:
    """Build an immutable Bone Map from an editable Value node."""

    by_humanoid = {}
    for entry in getattr(node, "bone_mappings", ()):
        humanoid = str(getattr(entry, "humanoid", "") or "")
        bone_name = str(getattr(entry, "bone_name", "") or "").strip()
        if humanoid and bone_name:
            # Later duplicate rows intentionally replace earlier rows.
            by_humanoid[humanoid] = bone_name
    return BoneMapData(
        _data_block_name(getattr(node, "armature", None)),
        by_humanoid,
        {bone_name: humanoid for humanoid, bone_name in by_humanoid.items()},
    )


def expression_map_from_node(node) -> ExpressionMapData:
    """Build an immutable Expression Map from an editable Value node."""

    definitions = {}
    for entry in getattr(node, "expressions", ()):
        name = str(getattr(entry, "name", "") or "").strip()
        normalized = normalize_name(name)
        if not normalized:
            continue
        morph_targets = []
        for bind in getattr(entry, "morph_targets", ()):
            object_name = _data_block_name(getattr(bind, "mesh_object", None))
            shape_key_name = str(
                getattr(bind, "shape_key_name", "") or ""
            ).strip()
            if not object_name or not shape_key_name:
                continue
            morph_targets.append(
                MorphTarget(
                    object_name,
                    shape_key_name,
                    min(max(float(getattr(bind, "weight", 1.0)), 0.0), 1.0),
                )
            )
        material_targets = []
        for bind in getattr(entry, "material_targets", ()):
            material_name = _data_block_name(getattr(bind, "material", None))
            if not material_name:
                continue
            material_targets.append(
                MaterialTarget(
                    material_name,
                    str(getattr(bind, "bind_type", "color") or "color"),
                    tuple(float(value) for value in bind.target_value),
                )
            )
        definitions[normalized] = ExpressionDefinition(
            name,
            normalized,
            bool(getattr(entry, "is_binary", False)),
            tuple(morph_targets),
            tuple(material_targets),
            str(getattr(entry, "override_blink", "NONE") or "NONE"),
            "NONE",
            str(getattr(entry, "override_mouth", "NONE") or "NONE"),
        )
    return ExpressionMapData(definitions)


def mix_bone_maps(base, overlay) -> BoneMapData:
    """Replace Base rows with nonblank Overlay rows."""

    base = base if isinstance(base, BoneMapData) else BoneMapData("", {}, {})
    overlay = (
        overlay if isinstance(overlay, BoneMapData) else BoneMapData("", {}, {})
    )
    by_humanoid = {
        humanoid: bone_name
        for humanoid, bone_name in base.by_humanoid.items()
        if bone_name
    }
    by_humanoid.update(
        {
            humanoid: bone_name
            for humanoid, bone_name in overlay.by_humanoid.items()
            if bone_name
        }
    )
    return BoneMapData(
        overlay.armature_name or base.armature_name,
        by_humanoid,
        {bone_name: humanoid for humanoid, bone_name in by_humanoid.items()},
    )


def mix_expression_maps(base, overlay) -> ExpressionMapData:
    """Replace same-name definitions and append names missing from Base."""

    base = base if isinstance(base, ExpressionMapData) else ExpressionMapData({})
    overlay = (
        overlay if isinstance(overlay, ExpressionMapData) else ExpressionMapData({})
    )
    definitions = {}
    for key, definition in base.definitions.items():
        normalized = normalize_name(
            definition.normalized_name or definition.name or key
        )
        if normalized:
            definitions[normalized] = definition
    for key, definition in overlay.definitions.items():
        normalized = normalize_name(
            definition.normalized_name or definition.name or key
        )
        if normalized:
            definitions[normalized] = definition
    return ExpressionMapData(definitions)
