"""Exclusive, main-thread Blender adapter for the timer-independent solver.

The caller owns scheduling and load/undo handlers. Runtime registries contain
names, pointer identities and copied numbers, never retained Blender RNA.
"""

from dataclasses import dataclass, field
import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from .solver import Body, Box, World


MAX_BODIES = 32
MATRIX_TOLERANCE = 2e-4
_session = None
_message = "Idle"


@dataclass
class _Reference:
    name: str
    identity: tuple


@dataclass
class _Owned(_Reference):
    home: tuple
    expected: tuple
    scale: tuple
    bounds: tuple = ()


@dataclass
class _Session:
    scene: _Reference
    world: object
    settings: dict
    bodies: dict = field(default_factory=dict)
    colliders: dict = field(default_factory=dict)
    anchors: dict = field(default_factory=dict)
    running: bool = True


def _identity(obj):
    # Session UID also detects same-name replacement when the allocator reuses
    # a freed pointer address. Both values are plain integers.
    return int(obj.as_pointer()), int(getattr(obj, "session_uid", 0))


def _reference(obj):
    return _Reference(str(obj.name), _identity(obj))


def _matrix_tuple(matrix):
    return tuple(tuple(float(value) for value in row) for row in matrix)


def _matrix_close(left, right):
    return all(abs(left[row][column] - right[row][column]) <= MATRIX_TOLERANCE
               for row in range(4) for column in range(4))


def _vector(value, name, length=3):
    values = tuple(float(number) for number in value)
    if len(values) != length or not all(math.isfinite(number) for number in values):
        raise ValueError(f"{name} must contain {length} finite numbers")
    return values


def _number(settings, name, default, *, minimum=0.0, positive=False):
    value = float(getattr(settings, name, default))
    if not math.isfinite(value) or value < minimum or (positive and value <= minimum):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
    return value


def _snapshot_settings(settings):
    result = {"gravity": _vector(getattr(settings, "gravity", (0, 0, -9.81)), "Gravity"),
              "floor_height": float(getattr(settings, "floor_height", 0.0))
              if bool(getattr(settings, "use_floor", True)) else None,
              "recall_hold": bool(getattr(settings, "recall_hold", True)),
              "recall_orientation": bool(getattr(settings, "recall_orientation", True))}
    if result["floor_height"] is not None and not math.isfinite(result["floor_height"]):
        raise ValueError("Floor height must be finite")
    defaults = {"radius": .3, "mass": 1.0, "damping": .3, "angular_damping": .3,
                "friction": .6, "restitution": .25, "max_speed": 30.0,
                "grab_radius": 1.5, "grab_strength": 120., "grab_damping": 22.,
                "grab_max_force": 250., "recall_strength": 80., "recall_damping": 18.,
                "recall_max_force": 200., "recall_timeout": 8.0}
    for name, default in defaults.items():
        result[name] = _number(settings, name, default, positive=name in {"radius", "mass", "max_speed"})
    if result["restitution"] > 1:
        raise ValueError("Restitution must be between 0 and 1")
    return result


def _members(collection):
    if collection is None:
        return []
    members = getattr(collection, "all_objects", collection)
    return sorted({int(obj.as_pointer()): obj for obj in members}.values(), key=lambda obj: obj.name)


def _unsupported(obj):
    if obj.library is not None or not bool(getattr(obj, "is_editable", True)):
        return "linked or read-only object"
    if obj.rigid_body is not None or obj.rigid_body_constraint is not None:
        return "native rigid body ownership"
    if obj.constraints:
        return "object constraints"
    animation = obj.animation_data
    if animation is not None and (animation.action is not None or animation.drivers or animation.nla_tracks):
        return "animation or drivers"
    return None


def _validate_object(obj, scene, *, dynamic=False):
    if obj.name not in scene.objects or scene.objects.get(obj.name) != obj:
        raise ValueError(f"{obj.name}: object is outside the selected scene")
    problem = _unsupported(obj)
    if problem:
        raise ValueError(f"{obj.name}: cannot simulate {problem}")
    if dynamic and obj.parent is not None:
        raise ValueError(f"{obj.name}: dynamic bodies must have no parent")
    if not all(math.isfinite(float(value)) for row in obj.matrix_world for value in row):
        raise ValueError(f"{obj.name}: transform contains nonfinite values")
    if dynamic:
        axes = [obj.matrix_world.to_3x3().col[index].copy() for index in range(3)]
        if min(axis.length for axis in axes) <= 1e-8:
            raise ValueError(f"{obj.name}: dynamic body scale must be nonzero")
        axes = [axis.normalized() for axis in axes]
        if any(abs(axes[a].dot(axes[b])) > 1e-4 for a, b in ((0, 1), (0, 2), (1, 2))):
            raise ValueError(f"{obj.name}: sheared dynamic transforms are unsupported")


def _resolve(reference):
    obj = bpy.data.objects.get(reference.name)
    if obj is None or _identity(obj) != reference.identity:
        raise RuntimeError(f"{reference.name}: object was deleted, renamed or replaced")
    return obj


def _box(obj, settings, depsgraph):
    if obj.type != "MESH":
        raise ValueError(f"{obj.name}: colliders must be mesh objects")
    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world.copy()
    axes = [matrix.to_3x3().col[index].copy() for index in range(3)]
    scales = [axis.length for axis in axes]
    if min(scales) <= 1e-8:
        raise ValueError(f"{obj.name}: collider scale must be nonzero")
    axes = [axis / scale for axis, scale in zip(axes, scales)]
    if any(abs(axes[a].dot(axes[b])) > 1e-4 for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError(f"{obj.name}: sheared collider transforms are unsupported")
    corners = [Vector(corner) for corner in evaluated.bound_box]
    minimum = Vector(tuple(min(corner[index] for corner in corners) for index in range(3)))
    maximum = Vector(tuple(max(corner[index] for corner in corners) for index in range(3)))
    center = matrix @ ((minimum + maximum) * .5)
    extents = tuple((maximum[index] - minimum[index]) * scales[index] * .5 for index in range(3))
    if min(extents) <= 1e-8:
        raise ValueError(f"{obj.name}: collider bounding box must have volume")
    # A box is unchanged by reversing an axis; remove reflected scale before
    # constructing the solver's proper quaternion basis.
    if axes[0].cross(axes[1]).dot(axes[2]) < 0:
        axes[0] = -axes[0]
    orientation = Matrix(axes).transposed().to_quaternion().normalized()
    return Box(obj.name, tuple(center), extents, tuple(orientation),
               friction=settings["friction"], restitution=settings["restitution"])


def _bounds(obj, depsgraph):
    return tuple(tuple(float(value) for value in corner)
                 for corner in obj.evaluated_get(depsgraph).bound_box)


def _bounds_close(left, right):
    return len(left) == len(right) and all(
        abs(a - b) <= MATRIX_TOLERANCE for old, new in zip(left, right) for a, b in zip(old, new))


def start(scene, settings):
    """Capture a world and home transforms; do not register any timer."""
    global _session, _message
    if active():
        raise RuntimeError("A physics world is already running; stop it before starting another")
    if scene is None or scene != bpy.context.scene:
        raise ValueError("Start physics in the current scene")
    values = _snapshot_settings(settings)
    objects = _members(getattr(settings, "bodies", None))
    colliders = _members(getattr(settings, "colliders", None))
    if not objects:
        raise ValueError("Choose a body collection containing at least one object")
    if len(objects) > MAX_BODIES:
        raise ValueError(f"The prototype supports at most {MAX_BODIES} dynamic bodies")
    body_ids = {obj.as_pointer() for obj in objects}
    collider_ids = {obj.as_pointer() for obj in colliders}
    if body_ids & collider_ids:
        raise ValueError("Body and collider collections must contain different objects")
    anchors = [getattr(settings, name, None) for name in ("left_anchor", "right_anchor")]
    anchors = {obj.name: obj for obj in anchors if obj is not None}
    if any(obj.as_pointer() in body_ids | collider_ids for obj in anchors.values()):
        raise ValueError("Grab anchors must be outside the physics body and collider collections")
    for obj in objects:
        _validate_object(obj, scene, dynamic=True)
    for obj in colliders:
        _validate_object(obj, scene)
    for obj in anchors.values():
        if obj.name not in scene.objects:
            raise ValueError(f"{obj.name}: anchor is outside the selected scene")
        if obj.rigid_body is not None or obj.rigid_body_constraint is not None:
            raise ValueError(f"{obj.name}: grab anchors must not have native rigid body ownership")
    world = World(gravity=values["gravity"], floor_height=values["floor_height"], max_speed=values["max_speed"])
    session = _Session(_reference(scene), world, values)
    for obj in objects:
        position, orientation, scale = obj.matrix_world.decompose()
        radius = float(obj.get("yugo_physics_radius", values["radius"]))
        mass = float(obj.get("yugo_physics_mass", values["mass"]))
        if not math.isfinite(radius) or radius <= 0 or not math.isfinite(mass) or mass <= 0:
            raise ValueError(f"{obj.name}: mass and radius overrides must be finite and positive")
        home = _matrix_tuple(obj.matrix_world)
        session.bodies[obj.name] = _Owned(obj.name, _identity(obj), home, home, tuple(scale))
        world.add_body(Body(obj.name, tuple(position), radius=radius, mass=mass,
                            orientation=tuple(orientation), damping=values["damping"],
                            angular_damping=values["angular_damping"], friction=values["friction"],
                            restitution=values["restitution"]))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in colliders:
        snapshot = _matrix_tuple(obj.matrix_world)
        session.colliders[obj.name] = _Owned(obj.name, _identity(obj), snapshot, snapshot, (), _bounds(obj, depsgraph))
        world.add_box(_box(obj, values, depsgraph))
    session.anchors = {name: _reference(obj) for name, obj in anchors.items()}
    _session = session
    _message = f"Running · {len(objects)} bodies · {len(colliders)} box colliders"
    return len(objects)


def active():
    return _session is not None and _session.running


def status():
    return _message


def stop(restore=False, clear=False):
    """Stop writing. Keep the current pose, or restore home when requested."""
    global _session, _message
    if _session is not None:
        try:
            if restore:
                reset()
        finally:
            _session.running = False
    if clear:
        _session = None
    _message = "Stopped" if _session is not None else "Idle"


def clear():
    """Drop all runtime snapshots without writing Blender data."""
    stop(clear=True)


def _write_transforms(home=False):
    global _message
    try:
        intended = {}
        for name, reference in _session.bodies.items():
            obj = _resolve(reference)
            if home:
                matrix = Matrix(reference.home)
            else:
                body = _session.world.bodies[name]
                matrix = Matrix.LocRotScale(Vector(body.position), Quaternion(body.orientation), Vector(reference.scale))
            intended[name] = _matrix_tuple(matrix)
            # Freeze our intended output before Blender runs dependency
            # handlers. Their writes must never become our ownership baseline.
            reference.expected = intended[name]
            obj.matrix_world = matrix
        bpy.context.view_layer.update()
        for name, reference in _session.bodies.items():
            if not _matrix_close(_matrix_tuple(_resolve(reference).matrix_world), intended[name]):
                raise RuntimeError(f"{name}: another writer changed the body during dependency update")
    except Exception as error:
        _session.running = False
        _message = f"Stopped · {error}"
        raise


def reset():
    """Restore captured home transforms and clear all body velocities/forces.

    A stopped session stays stopped. An active session keeps running.
    """
    global _message
    if _session is None:
        raise RuntimeError("No physics home snapshot is available; start a world first")
    _current_scene()
    # Resolve the complete selection first to avoid partially restoring homes.
    for reference in _session.bodies.values():
        _resolve(reference)
    _session.world.reset()
    _write_transforms(home=True)
    _message = "Running · reset to home" if active() else "Stopped · reset to home"


def _current_scene():
    scene = bpy.data.scenes.get(_session.scene.name)
    if scene is None or _identity(scene) != _session.scene.identity or scene != bpy.context.scene:
        raise RuntimeError("Scene changed; physics stopped")
    return scene


def _guard():
    scene = _current_scene()
    for reference in _session.bodies.values():
        obj = _resolve(reference)
        _validate_object(obj, scene, dynamic=True)
        if not _matrix_close(_matrix_tuple(obj.matrix_world), reference.expected):
            raise RuntimeError(f"{obj.name}: another writer or a user transform edit changed the body")
    depsgraph = bpy.context.evaluated_depsgraph_get() if _session.colliders else None
    for reference in _session.colliders.values():
        obj = _resolve(reference)
        _validate_object(obj, scene)
        if not _matrix_close(_matrix_tuple(obj.matrix_world), reference.expected):
            raise RuntimeError(f"{obj.name}: static collider moved; restart to rebuild its box")
        if obj.type != "MESH" or not _bounds_close(_bounds(obj, depsgraph), reference.bounds):
            raise RuntimeError(f"{obj.name}: static collider bounding box changed; restart to rebuild it")
    for reference in _session.anchors.values():
        obj = _resolve(reference)
        if obj.name not in scene.objects:
            raise RuntimeError(f"{obj.name}: anchor left the current scene")
        if obj.rigid_body is not None or obj.rigid_body_constraint is not None:
            raise RuntimeError(f"{obj.name}: anchor acquired native rigid body ownership")


def update(dt):
    """Advance the numerical world, then write world transforms once."""
    global _message
    if not active():
        return 0
    try:
        delta = float(dt)
        if not math.isfinite(delta) or delta < 0:
            raise ValueError("Update duration must be finite and nonnegative")
        _guard()
        for anchor_name in tuple(_session.world.anchors):
            reference = _session.anchors[anchor_name]
            obj = _resolve(reference)
            position, orientation, _scale = obj.matrix_world.decompose()
            _session.world.move_anchor(reference.name, tuple(position), tuple(orientation))
        steps = _session.world.advance(delta)
        _write_transforms()
        held = sum(body.grab_anchor is not None for body in _session.world.bodies.values())
        recalling = sum(body.recall_state == "seeking" for body in _session.world.bodies.values())
        _message = f"Running · {len(_session.bodies)} bodies · {held} held · {recalling} recalling"
        return steps
    except Exception as error:
        _session.running = False
        _message = f"Stopped · {error}"
        return 0


def _body_name(obj, *, require_active=True):
    if _session is None or (require_active and not active()):
        raise RuntimeError("Start the physics world before performing an action")
    name = obj if isinstance(obj, str) else obj.name if obj is not None else ""
    reference = _session.bodies.get(name)
    if reference is None:
        raise ValueError("Select an object in the active physics body collection")
    resolved = _resolve(reference)
    if not isinstance(obj, str) and obj != resolved:
        raise ValueError("The selected body identity changed")
    return name


def body_state(obj):
    """Return a copied numerical snapshot, or None for an unavailable target."""
    try:
        name = _body_name(obj, require_active=False)
    except (ValueError, RuntimeError, ReferenceError):
        return None
    body = _session.world.bodies[name]
    return {"name": name, "active": active(), "position": tuple(body.position),
            "orientation": tuple(body.orientation), "velocity": tuple(body.velocity),
            "angular_velocity": tuple(body.angular_velocity), "force": tuple(body.force),
            "home_position": tuple(body.home_position), "home_orientation": tuple(body.home_orientation),
            "grab_anchor": body.grab_anchor, "recall_state": body.recall_state,
            "recall_elapsed": float(body.recall_elapsed), "recall_hold": bool(body.recall_hold)}


def impulse(obj, linear, angular=(0, 0, 0)):
    name = _body_name(obj)
    _session.world.apply_impulse(name, _vector(linear, "Impulse"), _vector(angular, "Angular impulse"))


def force(obj, vector):
    """Set persistent force in Newtons; pass zero to clear it."""
    _session.world.set_force(_body_name(obj), _vector(vector, "Force"))


def _anchor(anchor):
    if not active():
        raise RuntimeError("Start the physics world before performing an action")
    name = anchor if isinstance(anchor, str) else anchor.name if anchor is not None else ""
    reference = _session.anchors.get(name)
    if reference is None:
        raise ValueError("Choose one of the configured grab anchors")
    obj = _resolve(reference)
    if not isinstance(anchor, str) and obj != anchor:
        raise ValueError("The selected anchor identity changed")
    return obj


def grab(anchor, radius=None):
    """Grab the nearest unheld body within the anchor's world-space radius."""
    obj = _anchor(anchor)
    values = _session.settings
    distance = values["grab_radius"] if radius is None else float(radius)
    if not math.isfinite(distance) or distance < 0:
        raise ValueError("Grab radius must be finite and nonnegative")
    if any(body.grab_anchor == obj.name for body in _session.world.bodies.values()):
        raise RuntimeError(f"{obj.name}: anchor is already holding a body")
    position, orientation, _scale = obj.matrix_world.decompose()
    candidates = [((Vector(body.position) - position).length, name)
                  for name, body in _session.world.bodies.items() if body.grab_anchor is None]
    candidates = [item for item in candidates if item[0] <= distance]
    if not candidates:
        raise ValueError("No unheld body is within the grab radius")
    name = min(candidates)[1]
    _session.world.grab(name, obj.name, tuple(position), tuple(orientation),
                        strength=values["grab_strength"], damping=values["grab_damping"],
                        max_force=values["grab_max_force"])
    return name


def release(anchor):
    obj = _anchor(anchor)
    released = []
    for name, body in _session.world.bodies.items():
        if body.grab_anchor == obj.name:
            _session.world.release(name)
            released.append(name)
    return tuple(released)


def recall(obj, params=None):
    """Apply a damped physical return force, preserving existing velocity."""
    name = _body_name(obj)
    values = _session.settings
    arguments = {"strength": values["recall_strength"], "damping": values["recall_damping"],
                 "max_force": values["recall_max_force"], "timeout": values["recall_timeout"],
                 "orient": values["recall_orientation"], "hold": values["recall_hold"]}
    if params is not None:
        supplied = params if isinstance(params, dict) else vars(params)
        unknown = set(supplied) - set(arguments) - {"settle_time", "tolerance", "speed_tolerance"}
        if unknown:
            raise ValueError(f"Unknown recall parameters: {', '.join(sorted(unknown))}")
        arguments.update(supplied)
    _session.world.recall(name, **arguments)


def cancel_recall(obj):
    _session.world.cancel_recall(_body_name(obj))
