"""Reusable operations over standardized Humanoid and Expression values."""

from __future__ import annotations

import math

from mathutils import Quaternion, Vector

from .constants import MIRROR_BONE, humanoid_region
from .data import ExpressionFrame, HumanoidMask, HumanoidMotion, HumanoidTransform


def clamp(value, minimum=0.0, maximum=1.0):
    return min(max(float(value), minimum), maximum)


def weighted_motion(
    source: HumanoidMotion,
    weight: float,
    mask: HumanoidMask | None = None,
) -> HumanoidMotion:
    weight = clamp(weight)
    identity = Quaternion()
    zero = Vector((0.0, 0.0, 0.0))

    def weighted(value, influence):
        return HumanoidTransform(
            identity.slerp(value.rotation, influence)
            if value.rotation is not None
            else None,
            zero.lerp(value.location, influence)
            if value.location is not None
            else None,
        )

    return HumanoidMotion(
        {
            name: weighted(value, weight * _motion_mask_weight(mask, name))
            for name, value in source.bones.items()
        },
        weighted(source.root, weight * _motion_mask_weight(mask, None))
        if source.root is not None
        else None,
    )


def _motion_mask_weight(mask, name):
    if not isinstance(mask, HumanoidMask):
        return 1.0
    region = "root" if name is None else humanoid_region(name)
    return clamp(mask.weights.get(region, 1.0))


def mix_motion(
    base: HumanoidMotion,
    overlay: HumanoidMotion,
    factor: float,
    mask: HumanoidMask | None = None,
) -> HumanoidMotion:
    factor = clamp(factor)
    result = base.copy()
    for name, upper in overlay.bones.items():
        bone_factor = factor * _motion_mask_weight(mask, name)
        if bone_factor <= 0.0:
            continue
        lower = result.bones.get(name)
        if lower is None:
            result.bones[name] = weighted_motion(
                HumanoidMotion({name: upper}), bone_factor
            ).bones[name]
            continue
        if upper.rotation is not None:
            lower_rotation = lower.rotation or Quaternion()
            lower.rotation = lower_rotation.slerp(upper.rotation, bone_factor)
            lower.rotation.normalize()
        if upper.location is not None:
            lower_location = lower.location or Vector((0.0, 0.0, 0.0))
            lower.location = lower_location.lerp(upper.location, bone_factor)
    if overlay.root is not None:
        root_factor = factor * _motion_mask_weight(mask, None)
        if root_factor <= 0.0:
            return result
        if result.root is None:
            result.root = weighted_motion(
                HumanoidMotion(root=overlay.root), root_factor
            ).root
        else:
            upper = overlay.root
            if upper.rotation is not None:
                result.root.rotation = (result.root.rotation or Quaternion()).slerp(
                    upper.rotation, root_factor
                )
                result.root.rotation.normalize()
            if upper.location is not None:
                result.root.location = (
                    result.root.location or Vector((0.0, 0.0, 0.0))
                ).lerp(upper.location, root_factor)
    return result


def mask_motion(
    source: HumanoidMotion, weights: dict[str, float] | HumanoidMask
) -> HumanoidMotion:
    if isinstance(weights, HumanoidMask):
        weights = weights.weights
    result = HumanoidMotion()
    for name, transform in source.bones.items():
        weight = weights.get(humanoid_region(name), 1.0)
        if weight > 0.0:
            result.bones[name] = weighted_motion(
                HumanoidMotion({name: transform}), weight
            ).bones[name]
    root_weight = weights.get("root", 1.0)
    if source.root is not None and root_weight > 0.0:
        result.root = weighted_motion(
            HumanoidMotion(root=source.root), root_weight
        ).root
    return result


def mirror_motion(source: HumanoidMotion) -> HumanoidMotion:
    result = HumanoidMotion()
    for name, value in source.bones.items():
        rotation = value.rotation.copy() if value.rotation is not None else None
        location = value.location.copy() if value.location is not None else None
        if rotation is not None:
            rotation = Quaternion((rotation.w, rotation.x, -rotation.y, -rotation.z))
            rotation.normalize()
        if location is not None:
            location.x = -location.x
        result.bones[MIRROR_BONE.get(name, name)] = HumanoidTransform(
            rotation, location
        )
    result.root = source.root.copy() if source.root is not None else None
    if result.root is not None:
        if result.root.rotation is not None:
            q = result.root.rotation
            result.root.rotation = Quaternion((q.w, q.x, -q.y, -q.z))
        if result.root.location is not None:
            result.root.location.x = -result.root.location.x
    return result


def weighted_expressions(source: ExpressionFrame, weight: float) -> ExpressionFrame:
    weight = float(weight)
    return ExpressionFrame({name: value * weight for name, value in source.values.items()})


def mix_expressions(
    base: ExpressionFrame,
    overlay: ExpressionFrame,
    factor: float,
    mode: str = "REPLACE",
) -> ExpressionFrame:
    factor = clamp(factor)
    result = dict(base.values)
    for name, upper in overlay.values.items():
        lower = result.get(name, 0.0)
        if mode == "ADD":
            value = lower + upper * factor
        elif mode == "MAX":
            value = max(lower, upper * factor)
        else:
            value = lower + (upper - lower) * factor
        result[name] = clamp(value, -5.0, 5.0)
    return ExpressionFrame(result)


class ScalarSpring:
    def __init__(self, value=0.0):
        self.value = float(value)
        self.velocity = 0.0

    def step(self, target, smooth, delta_time):
        target = float(target)
        smooth = clamp(smooth)
        if smooth <= 0.0 or delta_time <= 0.0:
            self.value = target
            self.velocity = 0.0
            return self.value
        delta_time = min(float(delta_time), 0.1)
        settling_time = 0.04 + 1.46 * smooth * smooth
        omega = 4.0 / settling_time
        offset = self.value - target
        coefficient = self.velocity + omega * offset
        decay = math.exp(-omega * delta_time)
        self.value = target + (offset + coefficient * delta_time) * decay
        self.velocity = (self.velocity - omega * coefficient * delta_time) * decay
        return self.value


class VectorSpring:
    def __init__(self):
        self.components = tuple(ScalarSpring() for _ in range(3))

    def step(self, target, smooth, delta_time):
        return Vector(
            tuple(
                spring.step(value, smooth, delta_time)
                for spring, value in zip(self.components, target)
            )
        )


def _rotation_vector(quaternion):
    q = quaternion.copy()
    q.normalize()
    if q.w < 0.0:
        q.negate()
    angle = 2.0 * math.acos(clamp(q.w, -1.0, 1.0))
    denominator = math.sqrt(max(1.0 - q.w * q.w, 0.0))
    if denominator < 1.0e-8 or angle < 1.0e-8:
        return Vector((0.0, 0.0, 0.0))
    return Vector((q.x, q.y, q.z)) / denominator * angle


def _quaternion(vector):
    angle = vector.length
    if angle < 1.0e-8:
        return Quaternion()
    result = Quaternion(vector / angle, angle)
    result.normalize()
    return result


class QuaternionSpring:
    def __init__(self):
        self.value = Quaternion()
        self.angular_velocity = Vector((0.0, 0.0, 0.0))

    def step(self, target, smooth, delta_time):
        target = target.copy()
        target.normalize()
        smooth = clamp(smooth)
        if smooth <= 0.0 or delta_time <= 0.0:
            self.value = target
            self.angular_velocity = Vector((0.0, 0.0, 0.0))
            return self.value.copy()
        delta_time = min(float(delta_time), 0.1)
        settling_time = 0.04 + 1.46 * smooth * smooth
        omega = 4.0 / settling_time
        error = _rotation_vector(self.value @ target.inverted())
        coefficient = self.angular_velocity + error * omega
        decay = math.exp(-omega * delta_time)
        new_error = (error + coefficient * delta_time) * decay
        self.angular_velocity = (
            self.angular_velocity - coefficient * omega * delta_time
        ) * decay
        self.value = _quaternion(new_error) @ target
        self.value.normalize()
        return self.value.copy()
