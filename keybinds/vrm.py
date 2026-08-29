"""VRM 0.x/1.0 Blend Shape Proxy discovery."""

from __future__ import annotations

from dataclasses import dataclass

from .logic import normalize_expression_name


VRM1_PRESET_EXPRESSIONS = (
    ("happy", "happy"),
    ("angry", "angry"),
    ("sad", "sad"),
    ("relaxed", "relaxed"),
    ("surprised", "surprised"),
    ("aa", "aa"),
    ("ih", "ih"),
    ("ou", "ou"),
    ("ee", "ee"),
    ("oh", "oh"),
    ("blink", "blink"),
    ("blink_left", "blinkLeft"),
    ("blink_right", "blinkRight"),
    ("look_up", "lookUp"),
    ("look_down", "lookDown"),
    ("look_left", "lookLeft"),
    ("look_right", "lookRight"),
    ("neutral", "neutral"),
)


@dataclass(frozen=True)
class ExpressionRecord:
    identifier: str
    name: str
    expression: object


def get_vrm_extension(armature):
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        return None
    return getattr(getattr(armature, "data", None), "vrm_addon_extension", None)


def is_vrm_armature(armature) -> bool:
    return get_vrm_extension(armature) is not None


def _has_vrm0_bind(expression) -> bool:
    return any(
        getattr(getattr(bind, "mesh", None), "mesh_object_name", "")
        and getattr(bind, "index", "")
        for bind in getattr(expression, "binds", ())
    )


def _has_vrm1_bind(expression) -> bool:
    return any(
        getattr(getattr(bind, "node", None), "mesh_object_name", "")
        and getattr(bind, "index", "")
        for bind in getattr(expression, "morph_target_binds", ())
    )


def iter_expressions(armature) -> list[ExpressionRecord]:
    extension = get_vrm_extension(armature)
    if extension is None:
        return []

    records = []
    version = str(getattr(extension, "spec_version", ""))
    if version == "0.0":
        master = getattr(getattr(extension, "vrm0", None), "blend_shape_master", None)
        for index, expression in enumerate(
            getattr(master, "blend_shape_groups", ())
        ):
            name = str(getattr(expression, "name", "") or "").strip()
            if name and _has_vrm0_bind(expression):
                records.append(
                    ExpressionRecord(
                        f"vrm0:{index}:{normalize_expression_name(name)}",
                        name,
                        expression,
                    )
                )
    elif version == "1.0":
        expressions = getattr(getattr(extension, "vrm1", None), "expressions", None)
        preset = getattr(expressions, "preset", None)
        for property_name, display_name in VRM1_PRESET_EXPRESSIONS:
            expression = getattr(preset, property_name, None)
            if expression is not None and _has_vrm1_bind(expression):
                records.append(
                    ExpressionRecord(
                        f"vrm1:preset:{property_name}", display_name, expression
                    )
                )
        for index, expression in enumerate(getattr(expressions, "custom", ())):
            name = str(getattr(expression, "custom_name", "") or "").strip()
            if name and _has_vrm1_bind(expression):
                records.append(
                    ExpressionRecord(
                        f"vrm1:custom:{index}:{normalize_expression_name(name)}",
                        name,
                        expression,
                    )
                )

    return records


def expression_lookup(armature) -> dict[str, ExpressionRecord]:
    return {record.identifier: record for record in iter_expressions(armature)}
