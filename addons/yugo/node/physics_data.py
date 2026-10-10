"""Immutable, engine-neutral physics descriptions. Lengths are SI metres.

Model revision deliberately excludes sampled boundary poses: moving an animated
collider must not rebuild the world or discard velocity. Changes to authored
geometry, bindings, mass or joint settings do change the revision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math


def _finite(values, length, label):
    values = tuple(float(value) for value in values)
    if len(values) != length or not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} needs {length} finite values")
    return values


def _quaternion(values):
    values = _finite(values, 4, "quaternion")
    norm = math.sqrt(sum(value * value for value in values))
    if norm < 1e-12:
        raise ValueError("Physics quaternion must be nonzero")
    return tuple(value / norm for value in values)


@dataclass(frozen=True)
class RigidBody:
    body_id: str
    object_name: str
    motion: str = "DYNAMIC"
    shape: str = "BOX"
    size: tuple[float, ...] = (0.5, 0.5, 0.5)
    geom_position: tuple[float, ...] = (0.0, 0.0, 0.0)
    position: tuple[float, ...] = (0.0, 0.0, 0.0)
    quaternion: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0)
    object_scale: tuple[float, ...] = (1.0, 1.0, 1.0)
    mass: float = 1.0
    friction: float = 0.5
    restitution: float = 0.0
    linear_damping: float = 0.04
    angular_damping: float = 0.1
    margin: float = 0.0
    collision_mask: int = 1

    def __post_init__(self):
        if not self.body_id or not self.object_name:
            raise ValueError("Rigid body needs a body identity and source object")
        if self.motion not in {"DYNAMIC", "KINEMATIC"}:
            raise ValueError(f"Unsupported rigid body motion: {self.motion}")
        sizes = {"BOX": 3, "SPHERE": 1, "CAPSULE": 2, "CYLINDER": 2}
        if self.shape not in sizes:
            raise ValueError(f"Unsupported rigid body shape: {self.shape}")
        for name, count in (("size", sizes[self.shape]), ("geom_position", 3),
                            ("position", 3), ("quaternion", 4), ("object_scale", 3)):
            object.__setattr__(self, name, _finite(getattr(self, name), count, name))
        if any(value <= 0 for value in self.size + self.object_scale):
            raise ValueError("Rigid body dimensions and scale must be positive")
        if not math.isfinite(self.mass) or self.mass <= 0:
            raise ValueError("Rigid body mass must be positive kilograms")
        object.__setattr__(self, "mass", float(self.mass))
        for name in ("friction", "restitution", "linear_damping", "angular_damping", "margin"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid rigid body {name}")
            object.__setattr__(self, name, value)
        if self.restitution > 1 or self.linear_damping > 1 or self.angular_damping > 1:
            raise ValueError("Restitution and damping must be in [0, 1]")
        object.__setattr__(self, "quaternion", _quaternion(self.quaternion))
        if not 0 < int(self.collision_mask) < 1 << 30:
            raise ValueError("Rigid body needs at least one collision collection")
        object.__setattr__(self, "collision_mask", int(self.collision_mask))


@dataclass(frozen=True)
class SpringJoint:
    bone_name: str
    parent_bone: str
    position: tuple[float, ...]
    quaternion: tuple[float, ...]
    tail: tuple[float, ...]
    radius: float
    mass: float
    stiffness: float
    damping: float
    gravity: tuple[float, ...]
    # Optional authored-tail compatibility. Zero preserves the relative-joint
    # semantics of manually constructed physics descriptions.
    reference_hz: float = 0.0
    world_damping: float = 0.0

    def __post_init__(self):
        for name, count in (("position", 3), ("quaternion", 4), ("tail", 3), ("gravity", 3)):
            object.__setattr__(self, name, _finite(getattr(self, name), count, name))
        object.__setattr__(self, "quaternion", _quaternion(self.quaternion))
        if not self.bone_name or sum(value * value for value in self.tail) < 1e-12:
            raise ValueError("Spring joint needs a source bone and a nonzero segment")
        for name in ("radius", "mass", "stiffness", "damping", "reference_hz", "world_damping"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0 or (name == "mass" and value == 0):
                raise ValueError(f"Invalid Spring joint {name}")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class SpringChain:
    chain_id: str
    armature_name: str
    parent_bone: str
    joints: tuple[SpringJoint, ...]
    position: tuple[float, ...] = (0.0, 0.0, 0.0)
    quaternion: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0)
    collider_ids: tuple[str, ...] = ()
    armature_scale: float = 1.0

    def __post_init__(self):
        object.__setattr__(self, "joints", tuple(self.joints))
        object.__setattr__(self, "collider_ids", tuple(self.collider_ids))
        object.__setattr__(self, "position", _finite(self.position, 3, "chain position"))
        object.__setattr__(self, "quaternion", _quaternion(self.quaternion))
        if not self.chain_id or not self.armature_name or not self.joints:
            raise ValueError("Spring chain needs an identity, armature and joints")
        if not math.isfinite(self.armature_scale) or self.armature_scale <= 0:
            raise ValueError("Spring armature scale must be positive")
        object.__setattr__(self, "armature_scale", float(self.armature_scale))


@dataclass(frozen=True)
class SpringCollider:
    collider_id: str
    armature_name: str = ""
    bone_name: str = ""
    object_name: str = ""
    shape: str = "SPHERE"
    offset: tuple[float, ...] = (0.0, 0.0, 0.0)
    tail: tuple[float, ...] = (0.0, 0.0, 0.0)
    radius: float = 0.01
    position: tuple[float, ...] = (0.0, 0.0, 0.0)
    quaternion: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0)
    source_scale: tuple[float, ...] = (1.0, 1.0, 1.0)

    def __post_init__(self):
        for name, count in (("offset", 3), ("tail", 3), ("position", 3), ("quaternion", 4), ("source_scale", 3)):
            object.__setattr__(self, name, _finite(getattr(self, name), count, name))
        object.__setattr__(self, "quaternion", _quaternion(self.quaternion))
        if any(value <= 0 for value in self.source_scale):
            raise ValueError("Spring collider source scale must be positive")
        if self.shape not in {"SPHERE", "CAPSULE"}:
            raise ValueError(f"Unsupported Spring collider shape: {self.shape}")
        if not self.collider_id or not math.isfinite(self.radius) or self.radius <= 0:
            raise ValueError("Spring collider needs an identity and positive radius")
        object.__setattr__(self, "radius", float(self.radius))


def _model_payload(description):
    payload = asdict(description)
    payload.pop("revision", None)
    for group in ("bodies", "springs", "colliders"):
        for entry in payload[group]:
            entry.pop("position", None)
            entry.pop("quaternion", None)
    return payload


@dataclass(frozen=True)
class PhysicsDescription:
    bodies: tuple[RigidBody, ...] = ()
    springs: tuple[SpringChain, ...] = ()
    colliders: tuple[SpringCollider, ...] = ()
    unit_scale: float = 1.0
    revision: str = field(init=False)

    def __post_init__(self):
        for name, key in (("bodies", "body_id"), ("springs", "chain_id"), ("colliders", "collider_id")):
            entries = tuple(sorted(getattr(self, name), key=lambda value: getattr(value, key)))
            object.__setattr__(self, name, entries)
            identities = [getattr(entry, key) for entry in entries]
            if len(set(identities)) != len(identities):
                raise ValueError(f"Duplicate physics identity in {name}")
        if not math.isfinite(self.unit_scale) or self.unit_scale <= 0:
            raise ValueError("Scene length unit scale must be positive")
        object.__setattr__(self, "unit_scale", float(self.unit_scale))
        sources = [body.object_name for body in self.bodies]
        if len(set(sources)) != len(sources):
            raise ValueError("Multiple physics bodies target the same object")
        armatures = {spring.armature_name for spring in self.springs}
        if len(armatures) > 1:
            raise ValueError("This solver supports one Spring armature per world")
        bones = [(chain.armature_name, joint.bone_name) for chain in self.springs for joint in chain.joints]
        if len(set(bones)) != len(bones):
            raise ValueError("Multiple Spring chains target the same bone")
        collider_ids = {collider.collider_id for collider in self.colliders}
        for chain in self.springs:
            if (chain.armature_name, chain.parent_bone) in bones:
                raise ValueError("Spring attachment is simulated by another chain; combine those joints into one chain")
            known = set()
            for joint in chain.joints:
                if joint.parent_bone and joint.parent_bone not in known:
                    raise ValueError("Spring joints must list their simulated parent first")
                known.add(joint.bone_name)
            if not set(chain.collider_ids) <= collider_ids:
                raise ValueError("Spring chain refers to a missing collider")
        collider_sources = {collider.object_name for collider in self.colliders if collider.object_name}
        collider_source_list = [collider.object_name for collider in self.colliders if collider.object_name]
        if len(collider_sources) != len(collider_source_list):
            raise ValueError("Multiple Spring collider proxies refer to the same source object")
        if collider_sources & set(sources):
            raise ValueError("A source object cannot be both a rigid body and a Spring collider proxy")
        object.__setattr__(self, "revision", "")
        encoded = json.dumps(_model_payload(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        object.__setattr__(self, "revision", hashlib.sha256(encoded.encode()).hexdigest()[:24])


def merge_descriptions(values):
    descriptions = tuple(value for value in values if value is not None)
    if not descriptions:
        return PhysicsDescription()
    if any(not isinstance(value, PhysicsDescription) for value in descriptions):
        raise TypeError("Physics Mixer expects Physics Description inputs")
    scale = descriptions[0].unit_scale
    if any(value.unit_scale != scale for value in descriptions):
        raise ValueError("Cannot merge descriptions from different scene length units")
    merged = {}
    for name, key in (("bodies", "body_id"), ("springs", "chain_id"), ("colliders", "collider_id")):
        entries = {}
        for description in descriptions:
            for entry in getattr(description, name):
                identity = getattr(entry, key)
                previous = entries.get(identity)
                if previous is not None and previous != entry:
                    raise ValueError(f"Conflicting physics identity: {identity}")
                entries[identity] = entry
        merged[name] = tuple(entries.values())
    return PhysicsDescription(**merged, unit_scale=scale)


@dataclass(frozen=True)
class ObjectTransform:
    object_name: str
    matrix_world: tuple[tuple[float, ...], ...]

    def __post_init__(self):
        rows = tuple(_finite(row, 4, "object transform row") for row in self.matrix_world)
        if len(rows) != 4 or not self.object_name:
            raise ValueError("Object Transform needs a target and 4 by 4 world matrix")
        object.__setattr__(self, "matrix_world", rows)


@dataclass(frozen=True)
class ObjectTransforms:
    records: tuple[ObjectTransform, ...] = ()
    world_id: str = ""
    revision: str = ""
    tick: int = 0

    def __post_init__(self):
        object.__setattr__(self, "records", tuple(self.records))
        names = [record.object_name for record in self.records]
        if len(names) != len(set(names)):
            raise ValueError("Object Transforms contains duplicate targets")

    def copy(self):
        return self
