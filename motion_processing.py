"""Spring-Damper virtual values used only by Smooth Data mode."""

import math

from mathutils import Quaternion, Vector


def _clamp(value, minimum, maximum):
    return min(max(float(value), minimum), maximum)


def _quaternion_shortest(quaternion):
    result = quaternion.copy()
    result.normalize()
    if result.w < 0.0:
        result = Quaternion((-result.w, -result.x, -result.y, -result.z))
    return result


def quaternion_to_rotation_vector(quaternion):
    quaternion = _quaternion_shortest(quaternion)
    w = _clamp(quaternion.w, -1.0, 1.0)
    angle = 2.0 * math.acos(w)
    denominator = math.sqrt(max(1.0 - (w * w), 0.0))
    if denominator < 1e-8 or angle < 1e-8:
        return Vector((0.0, 0.0, 0.0))
    axis = Vector((quaternion.x, quaternion.y, quaternion.z)) / denominator
    return axis * angle


def rotation_vector_to_quaternion(vector):
    vector = Vector(vector)
    angle = vector.length
    if angle < 1e-8:
        return Quaternion()
    result = Quaternion(vector / angle, angle)
    result.normalize()
    return result


def _settling_time(smooth):
    smooth = _clamp(smooth, 0.0, 1.0)
    return 0.04 + (1.46 * smooth * smooth)


class ScalarSpring:
    """Stable, critically damped scalar Spring-Damper."""

    def __init__(self, value=0.0):
        self.value = float(value)
        self.velocity = 0.0

    def step(self, target, smooth, delta_time):
        target = float(target)
        smooth = _clamp(smooth, 0.0, 1.0)
        if smooth <= 0.0 or delta_time <= 0.0:
            self.value = target
            self.velocity = 0.0
            return self.value

        delta_time = min(float(delta_time), 0.1)
        omega = 4.0 / _settling_time(smooth)
        offset = self.value - target
        coefficient = self.velocity + (omega * offset)
        decay = math.exp(-omega * delta_time)
        self.value = target + ((offset + coefficient * delta_time) * decay)
        self.velocity = (
            self.velocity - (omega * coefficient * delta_time)
        ) * decay
        return self.value


class VectorSpring:
    """Three stable scalar springs evaluated as one Vector value."""

    def __init__(self, value=None):
        initial = Vector(value) if value is not None else Vector((0.0, 0.0, 0.0))
        self.components = tuple(ScalarSpring(component) for component in initial)

    def step(self, target, smooth, delta_time):
        target = Vector(target)
        return Vector(tuple(
            spring.step(component, smooth, delta_time)
            for spring, component in zip(self.components, target)
        ))


class QuaternionSpring:
    """Stable, critically damped quaternion Spring-Damper."""

    def __init__(self, value=None):
        self.value = value.copy() if value is not None else Quaternion()
        self.value.normalize()
        self.angular_velocity = Vector((0.0, 0.0, 0.0))

    def step(self, target, smooth, delta_time):
        target = target.copy()
        target.normalize()
        smooth = _clamp(smooth, 0.0, 1.0)
        if smooth <= 0.0 or delta_time <= 0.0:
            self.value = target
            self.angular_velocity = Vector((0.0, 0.0, 0.0))
            return self.value.copy()

        delta_time = min(float(delta_time), 0.1)
        omega = 4.0 / _settling_time(smooth)
        error = quaternion_to_rotation_vector(self.value @ target.inverted())
        coefficient = self.angular_velocity + (error * omega)
        decay = math.exp(-omega * delta_time)
        new_error = (error + coefficient * delta_time) * decay
        self.angular_velocity = (
            self.angular_velocity - coefficient * (omega * delta_time)
        ) * decay
        self.value = rotation_vector_to_quaternion(new_error) @ target
        self.value.normalize()
        return self.value.copy()
