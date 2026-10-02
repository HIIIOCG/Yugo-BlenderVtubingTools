"""Transient graph values and immutable VRM snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mathutils import Quaternion, Vector


@dataclass
class HumanoidTransform:
    """Canonical Blender-space delta keyed by a VRM Humanoid name."""

    rotation: Quaternion | None = None
    location: Vector | None = None

    def copy(self) -> "HumanoidTransform":
        return HumanoidTransform(
            self.rotation.copy() if self.rotation is not None else None,
            self.location.copy() if self.location is not None else None,
        )


@dataclass
class HumanoidMotion:
    bones: dict[str, HumanoidTransform] = field(default_factory=dict)
    root: HumanoidTransform | None = None

    def copy(self) -> "HumanoidMotion":
        return HumanoidMotion(
            {name: value.copy() for name, value in self.bones.items()},
            self.root.copy() if self.root is not None else None,
        )


@dataclass(frozen=True)
class HumanoidMask:
    """Reusable per-region weights for canonical Humanoid motion operations."""

    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class NativePose:
    """Target-specific Blender local transforms, created only after Retarget."""

    armature_name: str = ""
    bones: dict[str, HumanoidTransform] = field(default_factory=dict)
    root: HumanoidTransform | None = None

    def copy(self) -> "NativePose":
        return NativePose(
            self.armature_name,
            {name: value.copy() for name, value in self.bones.items()},
            self.root.copy() if self.root is not None else None,
        )


@dataclass
class ExpressionFrame:
    values: dict[str, float] = field(default_factory=dict)

    def copy(self) -> "ExpressionFrame":
        return ExpressionFrame(dict(self.values))


@dataclass(frozen=True)
class BoneMapData:
    armature_name: str
    by_humanoid: dict[str, str]
    by_blender: dict[str, str]


@dataclass(frozen=True)
class MorphTarget:
    object_name: str
    shape_key_name: str
    weight: float


@dataclass(frozen=True)
class MaterialTarget:
    material_name: str
    bind_type: str
    target_value: tuple[float, ...]
    property_name: str = ""


@dataclass(frozen=True)
class ExpressionDefinition:
    name: str
    normalized_name: str
    is_binary: bool
    morph_targets: tuple[MorphTarget, ...]
    material_targets: tuple[MaterialTarget, ...]
    override_blink: str = "NONE"
    override_look_at: str = "NONE"
    override_mouth: str = "NONE"


@dataclass(frozen=True)
class ExpressionMapData:
    definitions: dict[str, ExpressionDefinition]


@dataclass
class NativeExpressions:
    """Resolved native targets created after the Expression Bind boundary."""

    expression_map: ExpressionMapData | None = None
    values: dict[ExpressionDefinition, float] = field(default_factory=dict)

    def copy(self) -> "NativeExpressions":
        return NativeExpressions(self.expression_map, dict(self.values))


@dataclass(frozen=True)
class SpringData:
    version: str
    springs: tuple[dict[str, Any], ...]
    colliders: tuple[dict[str, Any], ...]
    collider_groups: tuple[dict[str, Any], ...]
    armature_name: str = ""


@dataclass(frozen=True)
class MetaData:
    values: dict[str, Any]


@dataclass(frozen=True)
class CharacterSnapshot:
    armature_name: str
    vrm_version: str
    bone_map: BoneMapData
    expression_map: ExpressionMapData
    spring_data: SpringData
    meta: MetaData
    revision: int
    objects: tuple[str, ...] = ()


def empty_motion() -> HumanoidMotion:
    return HumanoidMotion()


def empty_expressions() -> ExpressionFrame:
    return ExpressionFrame()
