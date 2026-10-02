"""Blender-independent articulated capsule ragdoll, in world-space metres.

Fixed steps own all time advancement. Reading ``pose`` never advances physics.
The endpoint model represents bone swing, not axial twist or mesh deformation.
"""

from dataclasses import dataclass, field
import math


Vec3 = tuple[float, float, float]
PHYSICS = "PHYSICS"
RECOVERING = "RECOVERING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
_EPS = 1.0e-10


def _vec(value, label="vector"):
    try:
        result = tuple(float(x) for x in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must contain three finite numbers") from error
    if len(result) != 3 or not all(math.isfinite(x) for x in result):
        raise ValueError(f"{label} must contain three finite numbers")
    return result


def _add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _mul(a, scale):
    return tuple(x * scale for x in a)


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.hypot(*a)


def _unit(a, fallback=(0.0, 0.0, 1.0)):
    length = _length(a)
    return _mul(a, 1.0 / length) if length > _EPS else fallback


def _perpendicular(a):
    axis = min(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
               key=lambda candidate: abs(_dot(a, candidate)))
    return _unit(_cross(a, axis))


def _rotate(vector, axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    return _add(_add(_mul(vector, c), _mul(_cross(axis, vector), s)),
                _mul(axis, _dot(axis, vector) * (1.0 - c)))


def _follow(vector, rest_direction, direction):
    """Parallel-transport an attachment/hinge axis with its parent bone."""
    cosine = max(-1.0, min(1.0, _dot(rest_direction, direction)))
    axis = _cross(rest_direction, direction)
    sine = _length(axis)
    if sine > _EPS:
        return _rotate(vector, _mul(axis, 1.0 / sine), math.atan2(sine, cosine))
    if cosine < 0.0:
        return _rotate(vector, _perpendicular(rest_direction), math.pi)
    return vector


@dataclass(frozen=True)
class JointLimit:
    """Parent/child swing range in radians; optional rest-world hinge axis.

    With a hinge, the range is signed about that axis. Without a hinge it is
    the unsigned angle between the two bone directions (0 .. pi).
    """

    min_angle: float = 0.0
    max_angle: float = math.pi
    hinge_axis: Vec3 | None = None

    def __post_init__(self):
        if not (math.isfinite(self.min_angle) and math.isfinite(self.max_angle)
                and -math.pi <= self.min_angle <= self.max_angle <= math.pi):
            raise ValueError("joint limits must be an ordered range within -pi .. pi")
        if self.hinge_axis is None and self.min_angle < 0.0:
            raise ValueError("a cone limit cannot have a negative minimum angle")
        if self.hinge_axis is not None:
            axis = _vec(self.hinge_axis, "hinge axis")
            if _length(axis) <= _EPS:
                raise ValueError("hinge axis must not be zero")
            object.__setattr__(self, "hinge_axis", _unit(axis))


@dataclass(frozen=True)
class BoneSpec:
    name: str
    parent: str | None
    head: Vec3
    tail: Vec3
    mass: float = 1.0
    radius: float = 0.04
    limit: JointLimit | float | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("bone name must not be empty")
        if self.parent is not None and (not isinstance(self.parent, str) or not self.parent):
            raise ValueError("bone parent must be a nonempty name or None")
        object.__setattr__(self, "head", _vec(self.head, "bone head"))
        object.__setattr__(self, "tail", _vec(self.tail, "bone tail"))
        length = _length(_sub(self.tail, self.head))
        if not math.isfinite(length) or length <= _EPS:
            raise ValueError("bone length must be positive and finite")
        if not math.isfinite(self.mass) or self.mass < 1.0e-12:
            raise ValueError("bone mass must be finite and at least 1e-12")
        if not math.isfinite(self.radius) or self.radius < 0.0:
            raise ValueError("bone radius must be nonnegative and finite")
        if self.limit is not None and not isinstance(self.limit, JointLimit):
            object.__setattr__(self, "limit", JointLimit(max_angle=float(self.limit)))


@dataclass(frozen=True)
class SphereCollider:
    center: Vec3
    radius: float

    def __post_init__(self):
        object.__setattr__(self, "center", _vec(self.center, "sphere center"))
        if not math.isfinite(self.radius) or self.radius <= 0.0:
            raise ValueError("sphere radius must be positive and finite")


@dataclass(frozen=True)
class BoxCollider:
    minimum: Vec3
    maximum: Vec3

    def __post_init__(self):
        object.__setattr__(self, "minimum", _vec(self.minimum, "box minimum"))
        object.__setattr__(self, "maximum", _vec(self.maximum, "box maximum"))
        if any(a >= b for a, b in zip(self.minimum, self.maximum)):
            raise ValueError("box minimum must be below maximum on every axis")


@dataclass(frozen=True)
class Settings:
    gravity: Vec3 = (0.0, 0.0, -9.81)
    damping: float = 2.0
    iterations: int = 16
    fixed_dt: float = 1.0 / 120.0
    max_substeps: int = 32
    max_frame_dt: float = 0.25
    ground_z: float = 0.0
    min_hold: float = 1.0
    settle_speed: float = 0.15
    settle_time: float = 0.4
    timeout: float = 20.0
    recovery_duration: float = 0.8
    auto_recover: bool = True
    restitution: float = 0.05
    friction: float = 0.8
    compliance: float = 0.0
    colliders: tuple = field(default_factory=tuple)

    def __post_init__(self):
        object.__setattr__(self, "gravity", _vec(self.gravity, "gravity"))
        nonnegative = ("damping", "min_hold", "settle_speed", "settle_time",
                       "recovery_duration", "friction", "compliance")
        for name in nonnegative:
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be nonnegative and finite")
        for name in ("fixed_dt", "max_frame_dt", "timeout"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        if not math.isfinite(self.ground_z):
            raise ValueError("ground_z must be finite")
        if not math.isfinite(self.restitution) or not 0.0 <= self.restitution <= 1.0:
            raise ValueError("restitution must be within 0 .. 1")
        for name in ("iterations", "max_substeps"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.fixed_dt > self.max_frame_dt:
            raise ValueError("fixed_dt must not exceed max_frame_dt")
        if not all(isinstance(collider, (SphereCollider, BoxCollider))
                   for collider in self.colliders):
            raise ValueError("colliders must contain SphereCollider or BoxCollider")
        object.__setattr__(self, "colliders", tuple(self.colliders))


def _default_limit(name):
    name = name.lower().replace("_", "").replace(" ", "")
    if "lowerleg" in name or "calf" in name or "lowerarm" in name or "forearm" in name:
        return JointLimit(0.0, math.radians(145.0))
    if any(part in name for part in ("spine", "chest", "neck", "head")):
        return JointLimit(0.0, math.radians(70.0))
    if "upperleg" in name or "thigh" in name:
        return JointLimit(math.radians(35.0), math.pi)
    return JointLimit()


class Ragdoll:
    """Mass-weighted articulated endpoint PBD/XPBD simulation.

    ``hit`` adds impulse to the selected bone's existing velocities. Recovery
    is gated by hold time, ground contact and a continuous quiet dwell. A new
    hit during recovery resumes physics from the current interpolated pose.
    Settings and construction validate before any mutable state is created.
    """

    def __init__(self, bones, settings=None):
        self.settings = settings or Settings()
        if not isinstance(self.settings, Settings):
            raise TypeError("settings must be a Settings instance")
        self.bones = tuple(bones)
        if not self.bones or not all(isinstance(bone, BoneSpec) for bone in self.bones):
            raise ValueError("bones must be a nonempty collection of BoneSpec")
        names = [bone.name for bone in self.bones]
        if len(names) != len(set(names)):
            raise ValueError("bone names must be unique")
        self._indices = {name: index for index, name in enumerate(names)}
        for bone in self.bones:
            if bone.parent is not None and bone.parent not in self._indices:
                raise ValueError(f"unknown parent for {bone.name}: {bone.parent}")
        for bone in self.bones:
            visited = {bone.name}
            ancestor = bone.parent
            while ancestor is not None:
                if ancestor in visited:
                    raise ValueError("bone hierarchy contains a cycle")
                visited.add(ancestor)
                ancestor = self.bones[self._indices[ancestor]].parent
        self._order = sorted(range(len(self.bones)), key=self._depth)
        self._roots = {}
        for index in self._order:
            parent = self.bones[index].parent
            self._roots[index] = index if parent is None else self._roots[self._indices[parent]]
        self._positions = [point for bone in self.bones for point in (bone.head, bone.tail)]
        self._velocities = [(0.0, 0.0, 0.0) for _ in self._positions]
        self._weights = [2.0 / bone.mass for bone in self.bones for _ in range(2)]
        self._lengths = [_length(_sub(bone.tail, bone.head)) for bone in self.bones]
        self._rest_directions = [_unit(_sub(bone.tail, bone.head)) for bone in self.bones]
        self._attachments = []
        for index, bone in enumerate(self.bones):
            if bone.parent is not None:
                parent = self._indices[bone.parent]
                offset = _sub(bone.head, self.bones[parent].tail)
                self._attachments.append((parent, index, offset, bone.limit or _default_limit(bone.name)))
        self._targets = self.pose()
        self._accumulator = 0.0
        self._physics_elapsed = 0.0
        self._recovery_wait_elapsed = 0.0
        self._settled_elapsed = 0.0
        self._recovery_elapsed = 0.0
        self._recovery_start = None
        self._recovery_requested = self.settings.auto_recover
        self.elapsed = 0.0
        self.state = PHYSICS
        self.failure_reason = ""
        self.contact = False
        self.dropped_time = 0.0

    @property
    def velocities(self):
        return {bone.name: (self._velocities[2 * index], self._velocities[2 * index + 1])
                for index, bone in enumerate(self.bones)}

    @property
    def error(self):
        return self.failure_reason

    @property
    def since_hit(self):
        return self._physics_elapsed

    @property
    def settled_elapsed(self):
        return self._settled_elapsed

    @property
    def speed(self):
        return max(_length(velocity) for velocity in self._velocities)

    def pose(self):
        return {bone.name: (self._positions[2 * index], self._positions[2 * index + 1])
                for index, bone in enumerate(self.bones)}

    def _depth(self, index):
        depth, parent = 0, self.bones[index].parent
        while parent is not None:
            depth += 1
            parent = self.bones[self._indices[parent]].parent
        return depth

    def hit(self, bone, impulse):
        if bone not in self._indices:
            raise ValueError(f"unknown hit bone: {bone}")
        impulse = _vec(impulse, "impulse")
        if self.state in (FAILED, CANCELLED):
            raise RuntimeError(f"cannot hit a {self.state.lower()} ragdoll")
        index = self._indices[bone]
        delta = _mul(impulse, 1.0 / self.bones[index].mass)
        candidates = [_add(self._velocities[2 * index + endpoint], delta) for endpoint in range(2)]
        if not all(all(math.isfinite(value) for value in velocity) for velocity in candidates):
            raise ValueError("impulse overflows the bone's velocity")
        self._velocities[2 * index:2 * index + 2] = candidates
        self.state = PHYSICS
        self._physics_elapsed = 0.0
        self._recovery_wait_elapsed = 0.0
        self._settled_elapsed = 0.0
        self._recovery_elapsed = 0.0
        self._recovery_start = None
        self._recovery_requested = self.settings.auto_recover

    def set_target(self, target_pose):
        if set(target_pose) != set(self._indices):
            raise ValueError("recovery target must contain every bone exactly once")
        targets = {}
        for bone in self.bones:
            pair = target_pose[bone.name]
            if len(pair) != 2:
                raise ValueError("recovery target requires head and tail for every bone")
            head, tail = _vec(pair[0], "target head"), _vec(pair[1], "target tail")
            if abs(_length(_sub(tail, head)) - self._lengths[self._indices[bone.name]]) > 1.0e-5:
                raise ValueError("recovery targets must preserve bone lengths")
            targets[bone.name] = (head, tail)
        for parent, child, offset, _limit in self._attachments:
            parent_pose = targets[self.bones[parent].name]
            parent_dir = _unit(_sub(parent_pose[1], parent_pose[0]))
            expected_head = _add(parent_pose[1], _follow(offset, self._rest_directions[parent], parent_dir))
            actual_head = targets[self.bones[child].name][0]
            if _length(_sub(actual_head, expected_head)) > 1.0e-5:
                raise ValueError("recovery targets must preserve parent attachments")
        self._targets = targets

    def recover(self, target_pose=None):
        if self.state in (FAILED, CANCELLED):
            raise RuntimeError(f"cannot recover a {self.state.lower()} ragdoll")
        if target_pose is not None:
            self.set_target(target_pose)
        if not self._recovery_requested:
            self._recovery_wait_elapsed = 0.0
        self._recovery_requested = True
        if self.state == RECOVERING:
            self._recovery_start = list(self._positions)
            self._recovery_elapsed = 0.0

    def cancel(self):
        if self.state not in (COMPLETED, FAILED, CANCELLED):
            self.state = CANCELLED
            self._velocities = [(0.0, 0.0, 0.0) for _ in self._positions]
            self._accumulator = 0.0

    def step(self, dt):
        if not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt < 0.0:
            raise ValueError("dt must be nonnegative and finite")
        if self.state in (COMPLETED, FAILED, CANCELLED) or dt == 0.0:
            return self.state
        settings = self.settings
        accepted = min(dt, settings.max_frame_dt, settings.fixed_dt * settings.max_substeps)
        self.dropped_time += dt - accepted
        self._accumulator += accepted
        count = min(settings.max_substeps, int((self._accumulator + _EPS) / settings.fixed_dt))
        self._accumulator = max(0.0, self._accumulator - count * settings.fixed_dt)
        for _ in range(count):
            self.elapsed += settings.fixed_dt
            safe_positions = list(self._positions)
            try:
                if self.state == PHYSICS:
                    self._physics_step(settings.fixed_dt)
                elif self.state == RECOVERING:
                    self._recover_step(settings.fixed_dt)
                finite = all(all(math.isfinite(x) for x in position)
                             for position in self._positions + self._velocities)
                finite = finite and all(
                    abs(_length(_sub(self._positions[2 * index + 1], self._positions[2 * index])) - length)
                    <= 1e-5 * max(1.0, length)
                    for index, length in enumerate(self._lengths))
            except (ValueError, OverflowError, ZeroDivisionError):
                finite = False
            if not finite:
                self._positions = safe_positions
                self._velocities = [(0.0, 0.0, 0.0) for _ in self._positions]
                self.state, self.failure_reason = FAILED, "simulation exceeded finite numerical precision"
            if self.state in (COMPLETED, FAILED, CANCELLED):
                break
        return self.state

    def _distance(self, first, second, length, alpha, lambdas, key):
        difference = _sub(self._positions[second], self._positions[first])
        distance = _length(difference)
        direction = _unit(difference, self._rest_directions[key])
        weight = self._weights[first] + self._weights[second]
        delta_lambda = (-(distance - length) - alpha * lambdas[key]) / (weight + alpha)
        lambdas[key] += delta_lambda
        self._positions[first] = _sub(self._positions[first], _mul(direction, self._weights[first] * delta_lambda))
        self._positions[second] = _add(self._positions[second], _mul(direction, self._weights[second] * delta_lambda))

    def _attachment(self, parent, child, offset):
        ph, pt, ch = 2 * parent, 2 * parent + 1, 2 * child
        if _length(offset) > _EPS:
            # Off-axis attachments exert torque on both parent endpoints. Merely
            # translating the parent tail is unstable when a hip offset is as
            # long as the hips bone. Solve the three scalar Jacobian rows.
            for axis in range(3):
                segment = _sub(self._positions[pt], self._positions[ph])
                length = max(_length(segment), _EPS)
                direction = _unit(segment, self._rest_directions[parent])
                rotated = _follow(offset, self._rest_directions[parent], direction)
                error = self._positions[ch][axis] - self._positions[pt][axis] - rotated[axis]
                dot = _dot(direction, rotated)
                gradient_head = tuple(((dot if axis == coordinate else 0.0)
                                       - direction[axis] * rotated[coordinate]) / length
                                      for coordinate in range(3))
                gradient_tail = tuple(-gradient_head[coordinate] - (1.0 if axis == coordinate else 0.0)
                                      for coordinate in range(3))
                weight = (self._weights[ch] + self._weights[ph] * _dot(gradient_head, gradient_head)
                          + self._weights[pt] * _dot(gradient_tail, gradient_tail))
                factor = error / weight
                self._positions[ph] = _sub(self._positions[ph], _mul(gradient_head, self._weights[ph] * factor))
                self._positions[pt] = _sub(self._positions[pt], _mul(gradient_tail, self._weights[pt] * factor))
                correction = tuple(self._weights[ch] * factor if coordinate == axis else 0.0 for coordinate in range(3))
                self._positions[ch] = _sub(self._positions[ch], correction)
            return
        direction = _unit(_sub(self._positions[pt], self._positions[ph]), self._rest_directions[parent])
        rotated = _follow(offset, self._rest_directions[parent], direction)
        error = _sub(self._positions[ch], _add(self._positions[pt], rotated))
        weight = self._weights[pt] + self._weights[ch]
        self._positions[pt] = _add(self._positions[pt], _mul(error, self._weights[pt] / weight))
        self._positions[ch] = _sub(self._positions[ch], _mul(error, self._weights[ch] / weight))

    def _angle(self, parent, child, limit):
        ph, pt, ch, ct = 2 * parent, 2 * parent + 1, 2 * child, 2 * child + 1
        u = _unit(_sub(self._positions[pt], self._positions[ph]), self._rest_directions[parent])
        v = _unit(_sub(self._positions[ct], self._positions[ch]), self._rest_directions[child])
        cosine = max(-1.0, min(1.0, _dot(u, v)))
        if limit.hinge_axis is not None:
            axis = _unit(_follow(limit.hinge_axis, self._rest_directions[parent], u))
            # Project the hinge normal onto the plane perpendicular to the parent.
            axis = _unit(_sub(axis, _mul(u, _dot(axis, u))), _perpendicular(u))
            planar = _unit(_sub(v, _mul(axis, _dot(v, axis))), u)
            angle = math.atan2(_dot(axis, _cross(u, planar)), _dot(u, planar))
            target = _rotate(u, axis, max(limit.min_angle, min(limit.max_angle, angle)))
            correction = _mul(_sub(target, v), self._lengths[child])
            self._positions[ch] = _sub(self._positions[ch], _mul(correction, 0.5))
            self._positions[ct] = _add(self._positions[ct], _mul(correction, 0.5))
            return
        angle = math.acos(cosine)
        target = max(limit.min_angle, min(limit.max_angle, angle))
        if abs(angle - target) < 1.0e-7:
            return
        sine = math.sqrt(max(0.0, 1.0 - cosine * cosine))
        if sine < 1.0e-5:
            desired = _rotate(u, _perpendicular(u), target)
            correction = _mul(_sub(desired, v), self._lengths[child])
            self._positions[ch] = _sub(self._positions[ch], _mul(correction, 0.5))
            self._positions[ct] = _add(self._positions[ct], _mul(correction, 0.5))
            return
        parent_length = max(_length(_sub(self._positions[pt], self._positions[ph])), _EPS)
        child_length = max(_length(_sub(self._positions[ct], self._positions[ch])), _EPS)
        gp = _mul(_sub(v, _mul(u, cosine)), 1.0 / (parent_length * sine))
        gc = _mul(_sub(u, _mul(v, cosine)), 1.0 / (child_length * sine))
        gradients = (gp, _mul(gp, -1.0), gc, _mul(gc, -1.0))
        indices = (ph, pt, ch, ct)
        weight = sum(self._weights[index] * _dot(gradient, gradient)
                     for index, gradient in zip(indices, gradients))
        factor = (angle - target) / max(weight, _EPS)
        for index, gradient in zip(indices, gradients):
            self._positions[index] = _sub(self._positions[index], _mul(gradient, self._weights[index] * factor))

    def _capsule_correction(self, first, second, t, correction):
        a, b = 1.0 - t, t
        weight = self._weights[first] * a * a + self._weights[second] * b * b
        self._positions[first] = _add(self._positions[first], _mul(correction, self._weights[first] * a / weight))
        self._positions[second] = _add(self._positions[second], _mul(correction, self._weights[second] * b / weight))

    def _collisions(self, contacts):
        ground = self.settings.ground_z
        for index, bone in enumerate(self.bones):
            first, second = 2 * index, 2 * index + 1
            for endpoint in (first, second):
                x, y, z = self._positions[endpoint]
                if z <= ground + bone.radius + 1.0e-6:
                    contacts[endpoint] = (0.0, 0.0, 1.0)
                    self._positions[endpoint] = (x, y, max(z, ground + bone.radius))
            for collider in self.settings.colliders:
                a, b = self._positions[first], self._positions[second]
                segment = _sub(b, a)
                if isinstance(collider, SphereCollider):
                    t = max(0.0, min(1.0, _dot(_sub(collider.center, a), segment) /
                                     max(_dot(segment, segment), _EPS)))
                    closest = _add(a, _mul(segment, t))
                    difference = _sub(closest, collider.center)
                    distance, radius = _length(difference), collider.radius + bone.radius
                    if distance < radius:
                        normal = _unit(difference, _perpendicular(_unit(segment)))
                        self._capsule_correction(first, second, t, _mul(normal, radius - distance))
                        contacts[first] = contacts[second] = normal
                else:
                    # Capsule versus expanded AABB: conservative at rounded corners.
                    low = tuple(x - bone.radius for x in collider.minimum)
                    high = tuple(x + bone.radius for x in collider.maximum)
                    enter, leave = 0.0, 1.0
                    for axis in range(3):
                        if abs(segment[axis]) <= _EPS:
                            if not low[axis] < a[axis] < high[axis]:
                                enter, leave = 1.0, 0.0
                                break
                        else:
                            t1 = (low[axis] - a[axis]) / segment[axis]
                            t2 = (high[axis] - a[axis]) / segment[axis]
                            enter, leave = max(enter, min(t1, t2)), min(leave, max(t1, t2))
                    if enter < leave:
                        t = (enter + leave) * 0.5
                        point = _add(a, _mul(segment, t))
                        candidates = [(point[axis] - low[axis], axis, -1.0) for axis in range(3)]
                        candidates += [(high[axis] - point[axis], axis, 1.0) for axis in range(3)]
                        depth, axis, sign = min(candidates)
                        normal = tuple(sign if i == axis else 0.0 for i in range(3))
                        self._capsule_correction(first, second, t, _mul(normal, max(depth, 0.0)))
                        contacts[first] = contacts[second] = normal

    def _finish_articulation(self, contacts):
        """Make the pose exactly representable by rotation-only pose bones.

        Iterative PBD solves the dynamics. Its residual stretch must never leak
        into an output requiring rigid bones. A forward kinematic projection
        restores exact lengths/attachments, then translates each root component
        out of static colliders; component translation preserves those equalities.
        """
        for index in self._order:
            first, second = 2 * index, 2 * index + 1
            direction = _unit(_sub(self._positions[second], self._positions[first]),
                              self._rest_directions[index])
            parent_name = self.bones[index].parent
            if parent_name is None:
                middle = _mul(_add(self._positions[first], self._positions[second]), .5)
                head = _sub(middle, _mul(direction, self._lengths[index] * .5))
            else:
                parent = self._indices[parent_name]
                ph, pt = 2 * parent, 2 * parent + 1
                parent_dir = _unit(_sub(self._positions[pt], self._positions[ph]), self._rest_directions[parent])
                limit = self.bones[index].limit or _default_limit(self.bones[index].name)
                if limit.hinge_axis is not None:
                    axis = _unit(_follow(limit.hinge_axis, self._rest_directions[parent], parent_dir))
                    axis = _unit(_sub(axis, _mul(parent_dir, _dot(axis, parent_dir))), _perpendicular(parent_dir))
                    planar = _unit(_sub(direction, _mul(axis, _dot(direction, axis))), parent_dir)
                    angle = math.atan2(_dot(axis, _cross(parent_dir, planar)), _dot(parent_dir, planar))
                    direction = _rotate(parent_dir, axis, max(limit.min_angle, min(limit.max_angle, angle)))
                else:
                    angle = math.acos(max(-1.0, min(1.0, _dot(parent_dir, direction))))
                    limited = max(limit.min_angle, min(limit.max_angle, angle))
                    if abs(angle - limited) > 1e-8:
                        axis = _unit(_cross(parent_dir, direction), _perpendicular(parent_dir))
                        direction = _rotate(parent_dir, axis, limited)
                offset = _sub(self.bones[index].head, self.bones[parent].tail)
                head = _add(self._positions[pt], _follow(offset, self._rest_directions[parent], parent_dir))
            self._positions[first] = head
            self._positions[second] = _add(head, _mul(direction, self._lengths[index]))
        # Ground is an exact capsule/plane condition: the lowest endpoint plus
        # radius controls the whole straight bone segment, not only its centre.
        shifts = {}
        for index, bone in enumerate(self.bones):
            low = min(self._positions[2 * index][2], self._positions[2 * index + 1][2])
            root = self._roots[index]
            shifts[root] = max(shifts.get(root, 0.0), self.settings.ground_z + bone.radius - low)
        for index in range(len(self.bones)):
            shift = shifts[self._roots[index]]
            if shift > 0.0:
                for endpoint in (2 * index, 2 * index + 1):
                    self._positions[endpoint] = _add(self._positions[endpoint], (0.0, 0.0, shift))
        # Static obstacle cleanup translates the component rather than stretching
        # the contact bone. This is deliberately a conservative contact response.
        for _ in range(32 if self.settings.colliders else 0):
            before = list(self._positions)
            temporary_contacts = {}
            self._collisions(temporary_contacts)
            component_corrections = {}
            for index in range(len(self.bones)):
                for endpoint in (2 * index, 2 * index + 1):
                    delta = _sub(self._positions[endpoint], before[endpoint])
                    root = self._roots[index]
                    if _length(delta) > _length(component_corrections.get(root, (0.0, 0.0, 0.0))):
                        component_corrections[root] = delta
            self._positions = before
            if not component_corrections:
                break
            for index in range(len(self.bones)):
                delta = component_corrections.get(self._roots[index], (0.0, 0.0, 0.0))
                for endpoint in (2 * index, 2 * index + 1):
                    self._positions[endpoint] = _add(self._positions[endpoint], delta)
        contacts.clear()
        for index, bone in enumerate(self.bones):
            for endpoint in (2 * index, 2 * index + 1):
                if self._positions[endpoint][2] <= self.settings.ground_z + bone.radius + 2.0e-5:
                    contacts[endpoint] = (0.0, 0.0, 1.0)

    def _physics_step(self, h):
        settings = self.settings
        old = list(self._positions)
        drag = math.exp(-settings.damping * h)
        predicted_velocities = [_mul(_add(velocity, _mul(settings.gravity, h)), drag)
                                for velocity in self._velocities]
        self._positions = [_add(position, _mul(velocity, h))
                           for position, velocity in zip(old, predicted_velocities)]
        alpha, lambdas = settings.compliance / (h * h), [0.0 for _ in self.bones]
        contacts = {}
        for _ in range(settings.iterations):
            for index, length in enumerate(self._lengths):
                self._distance(2 * index, 2 * index + 1, length, alpha, lambdas, index)
            for parent, child, offset, limit in self._attachments:
                self._attachment(parent, child, offset)
                self._angle(parent, child, limit)
            self._collisions(contacts)
        self._finish_articulation(contacts)
        self._velocities = [_mul(_sub(position, previous), 1.0 / h)
                            for position, previous in zip(self._positions, old)]
        for index, normal in contacts.items():
            velocity = self._velocities[index]
            normal_speed = _dot(velocity, normal)
            impact = max(0.0, -_dot(predicted_velocities[index], normal))
            tangent = _sub(velocity, _mul(normal, normal_speed))
            # Coulomb-like friction only while touching; no airborne damping from contacts.
            tangent_speed = _length(tangent)
            friction = min(1.0, settings.friction * (impact + _length(settings.gravity) * h) /
                           max(tangent_speed, _EPS))
            self._velocities[index] = _add(_mul(tangent, 1.0 - friction),
                                         _mul(normal, max(normal_speed, impact * settings.restitution, 0.0)))
        self.contact = any(position[2] <= settings.ground_z + bone.radius + 2.0e-5
                           for index, bone in enumerate(self.bones)
                           for position in self._positions[2 * index:2 * index + 2])
        self._physics_elapsed += h
        if self._recovery_requested:
            self._recovery_wait_elapsed += h
        quiet = self.contact and self.speed <= settings.settle_speed
        self._settled_elapsed = self._settled_elapsed + h if quiet else 0.0
        ready = (self._physics_elapsed + _EPS >= settings.min_hold and quiet
                 and self._settled_elapsed + _EPS >= settings.settle_time)
        if self._recovery_requested and ready:
            self.state = RECOVERING
            self._recovery_start = list(self._positions)
            self._recovery_elapsed = 0.0
            if settings.recovery_duration == 0.0:
                self._recover_step(0.0)
        elif self._recovery_requested and self._recovery_wait_elapsed + _EPS >= settings.timeout:
            self.state, self.failure_reason = FAILED, "ragdoll did not settle before timeout"

    def _recover_step(self, h):
        self._recovery_elapsed += h
        duration = self.settings.recovery_duration
        t = min(1.0, self._recovery_elapsed / duration) if duration > 0.0 else 1.0
        blend = t * t * (3.0 - 2.0 * t)
        target_points = [point for bone in self.bones for point in self._targets[bone.name]]
        previous = self._positions
        self._positions = list(previous)
        for index in self._order:
            first, second = 2 * index, 2 * index + 1
            start_head, start_tail = self._recovery_start[first:second + 1]
            target_head, target_tail = target_points[first:second + 1]
            start_dir, target_dir = _unit(_sub(start_tail, start_head)), _unit(_sub(target_tail, target_head))
            cosine = max(-1.0, min(1.0, _dot(start_dir, target_dir)))
            axis = _unit(_cross(start_dir, target_dir), _perpendicular(start_dir))
            direction = _rotate(start_dir, axis, math.acos(cosine) * blend)
            parent_name = self.bones[index].parent
            if parent_name is None:
                head = _add(_mul(start_head, 1.0 - blend), _mul(target_head, blend))
            else:
                parent = self._indices[parent_name]
                ph, pt = 2 * parent, 2 * parent + 1
                parent_dir = _unit(_sub(self._positions[pt], self._positions[ph]))
                start_parent_dir = _unit(_sub(self._recovery_start[pt], self._recovery_start[ph]))
                target_parent_dir = _unit(_sub(target_points[pt], target_points[ph]))
                start_offset = _follow(_sub(start_head, self._recovery_start[pt]), start_parent_dir, parent_dir)
                target_offset = _follow(_sub(target_head, target_points[pt]), target_parent_dir, parent_dir)
                offset = _add(_mul(start_offset, 1.0 - blend), _mul(target_offset, blend))
                head = _add(self._positions[pt], offset)
            self._positions[first] = head
            self._positions[second] = _add(head, _mul(direction, self._lengths[index]))
        self._velocities = ([_mul(_sub(point, old), 1.0 / h)
                             for point, old in zip(self._positions, previous)] if h > 0.0
                            else [(0.0, 0.0, 0.0) for _ in self._positions])
        if t >= 1.0 - _EPS:
            self._positions = target_points
            self._velocities = [(0.0, 0.0, 0.0) for _ in self._positions]
            self.state = COMPLETED
