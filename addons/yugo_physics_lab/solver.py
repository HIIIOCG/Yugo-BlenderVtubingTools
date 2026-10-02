"""Deterministic, bounded sphere rigid-body prototype without Blender dependencies.

Vectors are three-tuples and quaternions are (w, x, y, z), in world space.
Dynamic shapes are spheres; static shapes are oriented boxes and an optional
infinite floor. This is an interactive preview solver, not Blender's bake solver.
"""

from dataclasses import dataclass, field
import math


ZERO = (0.0, 0.0, 0.0)
IDENTITY = (1.0, 0.0, 0.0, 0.0)
_EPS = 1e-10
_CONTACT_SLOP = 1e-6
_MAX_SUBSTEPS = 32


def _scalar(value, label, minimum=None, maximum=None):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return value


def _vector(value, label="vector"):
    if len(value) != 3:
        raise ValueError(f"{label} must have three components")
    return tuple(_scalar(v, label, -1e9, 1e9) for v in value)


def _quaternion(value, label="orientation"):
    if len(value) != 4:
        raise ValueError(f"{label} must have four components")
    value = tuple(_scalar(v, label, -1e9, 1e9) for v in value)
    length = math.sqrt(sum(v * v for v in value))
    if length < _EPS:
        raise ValueError(f"{label} must have nonzero length")
    return tuple(v / length for v in value)


def _add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _mul(a, scale):
    return tuple(v * scale for v in a)


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _limit(a, maximum):
    length = _length(a)
    return _mul(a, maximum / length) if length > maximum else a


def _qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _conjugate(q):
    return (q[0], -q[1], -q[2], -q[3])


def _rotate(q, vector):
    result = _qmul(_qmul(q, (0.0,) + vector), _conjugate(q))
    return result[1:]


def _rotation_vector(target, current):
    error = _qmul(target, _conjugate(current))
    if error[0] < 0:
        error = tuple(-v for v in error)
    sine = _length(error[1:])
    if sine < _EPS:
        return ZERO
    angle = 2 * math.atan2(sine, max(0.0, error[0]))
    return _mul(error[1:], angle / sine)


def _integrate_orientation(q, angular_velocity, dt):
    angle = _length(angular_velocity) * dt
    if angle < _EPS:
        return q
    axis = _mul(angular_velocity, 1 / _length(angular_velocity))
    delta = (math.cos(angle / 2),) + _mul(axis, math.sin(angle / 2))
    return _quaternion(_qmul(delta, q))


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("name must be a nonempty string")
    return value


@dataclass
class Body:
    name: str
    position: tuple
    radius: float = 0.3
    mass: float = 1.0
    orientation: tuple = IDENTITY
    velocity: tuple = ZERO
    angular_velocity: tuple = ZERO
    damping: float = 0.15
    angular_damping: float = 0.2
    friction: float = 0.6
    restitution: float = 0.2
    home_position: tuple = field(init=False)
    home_orientation: tuple = field(init=False)
    force: tuple = field(default=ZERO, init=False)
    grab_anchor: str | None = field(default=None, init=False)
    recall_state: str = field(default="idle", init=False)
    recall_hold: bool = field(default=False, init=False)
    recall_elapsed: float = field(default=0.0, init=False)
    _grab: object = field(default=None, init=False, repr=False)
    _recall: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.name = _name(self.name)
        self.validate()
        self.home_position = self.position
        self.home_orientation = self.orientation

    def validate(self):
        """Validate snapshots before stepping, including direct service changes."""
        self.position = _vector(self.position, "position")
        self.orientation = _quaternion(self.orientation)
        self.velocity = _vector(self.velocity, "velocity")
        self.angular_velocity = _vector(self.angular_velocity, "angular velocity")
        self.force = _vector(self.force, "force")
        self.radius = _scalar(self.radius, "radius", 1e-3, 1e3)
        self.mass = _scalar(self.mass, "mass", 1e-4, 1e6)
        self.damping = _scalar(self.damping, "damping", 0, 1e4)
        self.angular_damping = _scalar(self.angular_damping, "angular damping", 0, 1e4)
        self.friction = _scalar(self.friction, "friction", 0, 2)
        self.restitution = _scalar(self.restitution, "restitution", 0, 1)

    @property
    def inverse_mass(self):
        return 1.0 / self.mass

    @property
    def inertia(self):
        return 0.4 * self.mass * self.radius * self.radius


@dataclass
class Box:
    name: str
    center: tuple
    half_extents: tuple
    orientation: tuple = IDENTITY
    friction: float = 0.6
    restitution: float = 0.2

    def __post_init__(self):
        self.name = _name(self.name)
        self.center = _vector(self.center, "box center")
        self.half_extents = _vector(self.half_extents, "box half extents")
        if any(v < 1e-5 or v > 1e6 for v in self.half_extents):
            raise ValueError("box half extents must be between 1e-5 and 1e6")
        self.orientation = _quaternion(self.orientation)
        self.friction = _scalar(self.friction, "box friction", 0, 2)
        self.restitution = _scalar(self.restitution, "box restitution", 0, 1)


@dataclass
class _Anchor:
    position: tuple
    orientation: tuple
    sampled_position: tuple
    sampled_orientation: tuple
    velocity: tuple = ZERO
    angular_velocity: tuple = ZERO


@dataclass
class _Control:
    strength: float
    damping: float
    max_force: float
    orient: bool = True
    hold: bool = True
    timeout: float = 8.0
    settle_time: float = 0.25
    tolerance: float = 0.04
    speed_tolerance: float = 0.15
    elapsed: float = 0.0
    stable_time: float = 0.0


def _control(strength, damping, max_force, **kwargs):
    return _Control(_scalar(strength, "strength", 0, 1e6),
                    _scalar(damping, "control damping", 0, 1e5),
                    _scalar(max_force, "max force", 0, 1e9), **kwargs)


class World:
    """Fixed-step simulation; excess wall-clock stall time is deliberately dropped.

    Body names determine collision ordering. The speed cap also accounts for the
    smallest allowed sphere and 32 microsteps, limiting travel to half a radius
    per microstep. Directly mutated overlapping starting poses are not swept.
    """

    def __init__(self, gravity=(0.0, 0.0, -9.81), floor_height=0.0,
                 fixed_dt=1 / 120, max_frame_dt=0.1, max_speed=30.0):
        self.gravity = _vector(gravity, "gravity")
        self.floor_height = None if floor_height is None else _scalar(
            floor_height, "floor height", -1e9, 1e9)
        self.fixed_dt = _scalar(fixed_dt, "fixed dt", 1e-5, 0.1)
        self.max_frame_dt = _scalar(max_frame_dt, "max frame dt", self.fixed_dt, 1.0)
        self.max_speed = _scalar(max_speed, "max speed", 1e-3, 1e4)
        self.bodies = {}
        self.boxes = []
        self.anchors = {}
        self._accumulator = 0.0
        self.simulation_time = 0.0

    def add_body(self, body):
        if not isinstance(body, Body):
            raise TypeError("body must be a Body")
        if body.name in self.bodies:
            raise ValueError(f"duplicate body: {body.name}")
        body.validate()
        self.bodies[body.name] = body
        return body

    def add_box(self, box):
        if not isinstance(box, Box):
            raise TypeError("box must be a Box")
        if any(existing.name == box.name for existing in self.boxes):
            raise ValueError(f"duplicate box: {box.name}")
        self.boxes.append(box)
        return box

    def apply_impulse(self, name, linear, angular=ZERO):
        """Apply world-space linear/angular momentum, preserving existing velocity."""
        body = self.bodies[name]
        linear = _vector(linear, "linear impulse")
        angular = _vector(angular, "angular impulse")
        body.velocity = _add(body.velocity, _mul(linear, body.inverse_mass))
        body.angular_velocity = _add(body.angular_velocity, _mul(angular, 1 / body.inertia))

    def set_force(self, name, force):
        """Set persistent world-space force; set ZERO to clear it."""
        self.bodies[name].force = _vector(force, "force")

    def grab(self, name, anchor_id, position, orientation=IDENTITY,
             strength=120, damping=22, max_force=250):
        body = self.bodies[name]
        anchor_id = _name(anchor_id)
        position = _vector(position, "anchor position")
        orientation = _quaternion(orientation, "anchor orientation")
        control = _control(strength, damping, max_force)
        self.cancel_recall(name)
        self.release(name)
        anchor = self.anchors.get(anchor_id)
        if anchor is None:
            anchor = _Anchor(position, orientation, position, orientation)
            self.anchors[anchor_id] = anchor
        else:
            anchor.position = position
            anchor.orientation = orientation
        body.grab_anchor = anchor_id
        body._grab = control

    def move_anchor(self, anchor_id, position, orientation=IDENTITY):
        anchor = self.anchors[anchor_id]
        position = _vector(position, "anchor position")
        orientation = _quaternion(orientation, "anchor orientation")
        anchor.position = position
        anchor.orientation = orientation

    def release(self, name):
        """Detach the spring; current position and both velocities survive."""
        body = self.bodies[name]
        anchor_id = body.grab_anchor
        body.grab_anchor = None
        body._grab = None
        if anchor_id is not None and not any(
                other.grab_anchor == anchor_id for other in self.bodies.values()):
            self.anchors.pop(anchor_id, None)

    def recall(self, name, strength=80, damping=18, max_force=200,
               orient=True, hold=True, timeout=8, settle_time=0.25,
               tolerance=0.04, speed_tolerance=0.15):
        body = self.bodies[name]
        control = _control(
            strength, damping, max_force, orient=bool(orient), hold=bool(hold),
            timeout=_scalar(timeout, "timeout", self.fixed_dt, 1e4),
            settle_time=_scalar(settle_time, "settle time", 0, 1e3),
            tolerance=_scalar(tolerance, "tolerance", 1e-6, 1e3),
            speed_tolerance=_scalar(speed_tolerance, "speed tolerance", 1e-6, 1e3))
        self.release(name)
        body._recall = control
        body.recall_state = "seeking"
        body.recall_hold = bool(hold)
        body.recall_elapsed = 0.0

    def cancel_recall(self, name):
        body = self.bodies[name]
        body._recall = None
        body.recall_state = "idle"
        body.recall_hold = False
        body.recall_elapsed = 0.0

    def reset(self):
        self.anchors.clear()
        self._accumulator = 0.0
        self.simulation_time = 0.0
        for body in self.bodies.values():
            body.position = body.home_position
            body.orientation = body.home_orientation
            body.velocity = ZERO
            body.angular_velocity = ZERO
            body.force = ZERO
            body.grab_anchor = None
            body._grab = None
            self.cancel_recall(body.name)

    def advance(self, dt):
        dt = _scalar(dt, "frame dt", 0)
        if dt == 0:
            return 0
        dt = min(dt, self.max_frame_dt)
        ordered = [self.bodies[name] for name in sorted(self.bodies)]
        for body in ordered:
            body.validate()
        for anchor in self.anchors.values():
            anchor.velocity = _limit(_mul(_sub(anchor.position, anchor.sampled_position),
                                         1 / dt), self.max_speed)
            anchor.angular_velocity = _limit(_mul(_rotation_vector(
                anchor.orientation, anchor.sampled_orientation), 1 / dt), 60.0)
            anchor.sampled_position = anchor.position
            anchor.sampled_orientation = anchor.orientation
        self._accumulator = min(self._accumulator + dt, self.max_frame_dt)
        steps = 0
        while self._accumulator + 1e-12 >= self.fixed_dt:
            steps += self._fixed_step(ordered)
            self._accumulator = max(0.0, self._accumulator - self.fixed_dt)
            self.simulation_time += self.fixed_dt
        return steps

    def _speed_limit(self, body):
        return min(self.max_speed, body.radius * 0.5 * _MAX_SUBSTEPS / self.fixed_dt)

    def _forces(self, body):
        force = _add(_mul(self.gravity, body.mass), body.force)
        torque = ZERO
        control = body._grab or body._recall
        if control is None:
            return force, torque
        if body._grab is not None:
            anchor = self.anchors[body.grab_anchor]
            position, orientation = anchor.position, anchor.orientation
            target_velocity = anchor.velocity
            target_angular_velocity = anchor.angular_velocity
        else:
            position, orientation = body.home_position, body.home_orientation
            target_velocity = target_angular_velocity = ZERO
        spring = _add(_mul(_sub(position, body.position), control.strength),
                      _mul(_sub(target_velocity, body.velocity), control.damping))
        # Compensate gravity with force, allowing a held home pose to converge.
        spring = _sub(spring, _mul(self.gravity, body.mass))
        force = _add(force, _limit(spring, control.max_force))
        if control.orient:
            angular_acceleration = _add(
                _mul(_rotation_vector(orientation, body.orientation), control.strength),
                _mul(_sub(target_angular_velocity, body.angular_velocity), control.damping))
            torque = _limit(_mul(angular_acceleration, body.inertia),
                            control.max_force * body.radius)
        return force, torque

    def _fixed_step(self, ordered):
        substeps = 1
        for body in ordered:
            force, _ = self._forces(body)
            speed = min(self._speed_limit(body),
                        _length(body.velocity) + _length(force) * body.inverse_mass * self.fixed_dt)
            substeps = max(substeps, math.ceil(speed * self.fixed_dt / (body.radius * 0.5)))
        substeps = min(substeps, _MAX_SUBSTEPS)
        dt = self.fixed_dt / substeps
        for _ in range(substeps):
            previous_positions = {body.name: body.position for body in ordered}
            for body in ordered:
                force, torque = self._forces(body)
                body.velocity = _limit(_mul(_add(body.velocity, _mul(
                    force, dt * body.inverse_mass)), math.exp(-body.damping * dt)),
                    min(self._speed_limit(body), body.radius * 0.5 / dt))
                body.angular_velocity = _limit(_mul(_add(body.angular_velocity, _mul(
                    torque, dt / body.inertia)), math.exp(-body.angular_damping * dt)), 60.0)
                body.position = _add(body.position, _mul(body.velocity, dt))
                body.orientation = _integrate_orientation(body.orientation, body.angular_velocity, dt)
            for _iteration in range(4):
                for index, body in enumerate(ordered):
                    for other in ordered[index + 1:]:
                        self._sphere_contact(body, other)
                for body in ordered:
                    if self.floor_height is not None:
                        depth = self.floor_height + body.radius - body.position[2]
                        if depth >= -_CONTACT_SLOP:
                            self._static_contact(body, (0.0, 0.0, 1.0),
                                                 depth, body.friction, body.restitution)
                    for box in sorted(self.boxes, key=lambda item: item.name):
                        contact = self._box_contact(body, box, previous_positions[body.name])
                        if contact is not None:
                            self._static_contact(body, contact[0], contact[1],
                                                 box.friction, box.restitution)
            for body in ordered:
                self._update_recall(body, dt)
        return substeps

    @staticmethod
    def _box_contact(body, box, previous_position=None):
        local = _rotate(_conjugate(box.orientation), _sub(body.position, box.center))
        closest = tuple(max(-box.half_extents[i], min(box.half_extents[i], local[i]))
                        for i in range(3))
        delta = _sub(local, closest)
        distance = _length(delta)
        if distance > body.radius + _CONTACT_SLOP:
            normal, depth = None, 0.0
        elif distance > _EPS:
            normal = _mul(delta, 1 / distance)
            depth = body.radius - distance
        else:
            axis = min(range(3), key=lambda i: box.half_extents[i] - abs(local[i]))
            normal = tuple((1.0 if local[axis] >= 0 else -1.0) if i == axis else 0.0
                           for i in range(3))
            depth = body.radius + box.half_extents[axis] - abs(local[axis])
        if previous_position is not None:
            previous = _rotate(_conjugate(box.orientation), _sub(previous_position, box.center))
            previous_closest = tuple(max(-box.half_extents[i], min(box.half_extents[i], previous[i]))
                                     for i in range(3))
            previous_delta = _sub(previous, previous_closest)
            previous_distance = _length(previous_delta)
            if _EPS < previous_distance <= body.radius + _CONTACT_SLOP:
                previous_normal = _mul(previous_delta, 1 / previous_distance)
                separation = _dot(_sub(local, previous_closest), previous_normal)
                reversed_contact = normal is None or _dot(normal, previous_normal) < 0
                if separation < 0 and reversed_contact:
                    normal = previous_normal
                    depth = body.radius - separation
            swept = World._swept_box_face(previous, local, box.half_extents, body.radius)
            if swept is not None:
                incoming, axis = swept
                # Contact projection between unequal spheres can move a center
                # through a thin wall. Keep the original entry face instead of
                # accepting a normal from the opposite side of the same wall.
                sign = incoming[axis]
                crossed_far_face = sign * local[axis] < -box.half_extents[axis] - body.radius
                reversed_normal = normal is not None and _dot(normal, incoming) < 0
                if crossed_far_face or reversed_normal:
                    normal = incoming
                    depth = box.half_extents[axis] + body.radius - sign * local[axis]
        if normal is None:
            return None
        return _rotate(box.orientation, normal), depth

    @staticmethod
    def _swept_box_face(start, end, half_extents, radius):
        """Conservative entry face for a segment through the expanded box.

        Used only to prevent crossing/reversed contacts, not as a replacement
        for ordinary closest-point sphere contacts around corners.
        """
        expanded = tuple(v + radius for v in half_extents)
        if all(abs(start[i]) <= expanded[i] for i in range(3)):
            return None
        entry, leave, face_axis, face_sign = 0.0, 1.0, None, 0.0
        for axis in range(3):
            movement = end[axis] - start[axis]
            if abs(movement) < _EPS:
                if abs(start[axis]) > expanded[axis]:
                    return None
                continue
            near = (-expanded[axis] - start[axis]) / movement
            far = (expanded[axis] - start[axis]) / movement
            sign = -1.0
            if near > far:
                near, far, sign = far, near, 1.0
            if near > entry:
                entry, face_axis, face_sign = near, axis, sign
            leave = min(leave, far)
            if entry > leave:
                return None
        if face_axis is None or entry > 1 or leave < 0:
            return None
        normal = tuple(face_sign if i == face_axis else 0.0 for i in range(3))
        return normal, face_axis

    @staticmethod
    def _static_contact(body, normal, depth, friction, restitution):
        if depth > _CONTACT_SLOP:
            body.position = _add(body.position, _mul(normal, depth - _CONTACT_SLOP))
        arm = _mul(normal, -body.radius)
        contact_velocity = _add(body.velocity, _cross(body.angular_velocity, arm))
        closing = _dot(contact_velocity, normal)
        if closing >= 0:
            return
        bounce = min(body.restitution, restitution) if closing < -0.5 else 0.0
        normal_impulse = -(1 + bounce) * closing / body.inverse_mass
        impulse = _mul(normal, normal_impulse)
        body.velocity = _add(body.velocity, _mul(impulse, body.inverse_mass))
        tangent = _sub(contact_velocity, _mul(normal, closing))
        effective_mass = body.inverse_mass + body.radius * body.radius / body.inertia
        friction_impulse = _limit(_mul(tangent, -1 / effective_mass),
                                 math.sqrt(body.friction * friction) * normal_impulse)
        body.velocity = _add(body.velocity, _mul(friction_impulse, body.inverse_mass))
        body.angular_velocity = _add(body.angular_velocity,
                                     _mul(_cross(arm, friction_impulse), 1 / body.inertia))

    @staticmethod
    def _sphere_contact(a, b):
        difference = _sub(b.position, a.position)
        distance = _length(difference)
        depth = a.radius + b.radius - distance
        if depth < -_CONTACT_SLOP:
            return
        normal = _mul(difference, 1 / distance) if distance > _EPS else (1.0, 0.0, 0.0)
        inverse_mass = a.inverse_mass + b.inverse_mass
        if depth > _CONTACT_SLOP:
            correction = _mul(normal, 0.8 * (depth - _CONTACT_SLOP) / inverse_mass)
            a.position = _sub(a.position, _mul(correction, a.inverse_mass))
            b.position = _add(b.position, _mul(correction, b.inverse_mass))
        arm_a, arm_b = _mul(normal, a.radius), _mul(normal, -b.radius)
        relative = _sub(_add(b.velocity, _cross(b.angular_velocity, arm_b)),
                        _add(a.velocity, _cross(a.angular_velocity, arm_a)))
        closing = _dot(relative, normal)
        if closing >= 0:
            return
        bounce = min(a.restitution, b.restitution) if closing < -0.5 else 0.0
        normal_impulse = -(1 + bounce) * closing / inverse_mass
        impulse = _mul(normal, normal_impulse)
        a.velocity = _sub(a.velocity, _mul(impulse, a.inverse_mass))
        b.velocity = _add(b.velocity, _mul(impulse, b.inverse_mass))
        tangent = _sub(relative, _mul(normal, closing))
        effective_mass = inverse_mass + a.radius ** 2 / a.inertia + b.radius ** 2 / b.inertia
        friction_impulse = _limit(_mul(tangent, -1 / effective_mass),
                                 math.sqrt(a.friction * b.friction) * normal_impulse)
        a.velocity = _sub(a.velocity, _mul(friction_impulse, a.inverse_mass))
        b.velocity = _add(b.velocity, _mul(friction_impulse, b.inverse_mass))
        a.angular_velocity = _sub(a.angular_velocity,
                                  _mul(_cross(arm_a, friction_impulse), 1 / a.inertia))
        b.angular_velocity = _add(b.angular_velocity,
                                  _mul(_cross(arm_b, friction_impulse), 1 / b.inertia))

    @staticmethod
    def _update_recall(body, dt):
        control = body._recall
        if control is None or body.recall_state != "seeking":
            return
        control.elapsed += dt
        body.recall_elapsed = control.elapsed
        settled = (_length(_sub(body.position, body.home_position)) <= control.tolerance
                   and _length(body.velocity) <= control.speed_tolerance)
        if control.orient:
            settled = (settled and _length(_rotation_vector(
                body.home_orientation, body.orientation)) <= control.tolerance
                and _length(body.angular_velocity) <= control.speed_tolerance)
        control.stable_time = control.stable_time + dt if settled else 0.0
        if control.stable_time + 1e-12 >= control.settle_time and settled:
            body.recall_state = "settled"
            if not control.hold:
                body._recall = None
        elif control.elapsed + 1e-12 >= control.timeout:
            body.recall_state = "timeout"
            body.recall_hold = False
            body._recall = None
