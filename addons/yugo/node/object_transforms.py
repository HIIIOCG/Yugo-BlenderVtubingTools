"""Generic world-space object transforms; physics ownership is delegated."""
import json
import math
import bpy
from mathutils import Matrix
from .nodes import YUGUS_NodeBase, _YUGUS_SocketBase
from .physics_data import ObjectTransform, ObjectTransforms
from . import flow

SOCKET = "YUGUS_NS_object_transforms"
APPLY = "YUGUS_NODE_apply_object_transform"
LEASE_MARKER = "_yugo_node_transform_lease_v1"
_owners = {}


class YUGUS_NS_object_transforms(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET
    bl_label = "Object Transforms"
    def draw_color(self, context, node):
        return (0.5, 0.5, 0.5, 1)


class YUGUS_NODE_apply_object_transform(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = APPLY
    bl_label = "Apply Transform"
    bl_icon = "OBJECT_ORIGIN"
    enabled: bpy.props.BoolProperty(name="Apply", default=True)
    last_summary: bpy.props.StringProperty(default="Connect Enter and Object Transforms", options={"SKIP_SAVE"})
    def init(self, context):
        self._init_base(250)
        self.inputs.new(flow.EVENT, "Enter")
        self.inputs.new(SOCKET, "Object Transforms")
        self.outputs.new(flow.EVENT, "End")
    def draw_buttons(self, context, layout):
        layout.prop(self, "enabled")
        layout.label(text=self.last_summary)
    def free(self):
        release(str(self.runtime_id))
        YUGUS_NodeBase.free(self)


def _snapshot(obj):
    return (obj.location.copy(), obj.rotation_mode, obj.rotation_quaternion.copy(),
            obj.rotation_euler.copy(), tuple(obj.rotation_axis_angle))


def _restore(obj, original):
    obj.location, mode, quaternion, euler, axis = original
    obj.rotation_mode = mode
    obj.rotation_quaternion, obj.rotation_euler, obj.rotation_axis_angle = quaternion, euler, axis


def _saved_vector(value, count):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError("Invalid saved transform lease")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise ValueError("Invalid saved transform lease")
    return result


def _read_marker(obj):
    """Saved markers are data, and must validate completely before a write."""
    raw = obj.get(LEASE_MARKER)
    if not isinstance(raw, str):
        raise ValueError("Invalid saved transform lease")
    marker = json.loads(raw)
    if (not isinstance(marker, dict) or marker.get("schema") != 1
            or not isinstance(marker.get("owner_uid"), str) or not marker["owner_uid"]
            or not isinstance(marker.get("world_id"), str)):
        raise ValueError("Invalid saved transform lease")
    original = marker.get("original")
    if not isinstance(original, dict):
        raise ValueError("Invalid saved transform lease")
    mode = original.get("rotation_mode")
    if mode not in {"QUATERNION", "AXIS_ANGLE", "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
        raise ValueError("Invalid saved transform lease")
    snapshot = (_saved_vector(original.get("location"), 3), mode,
                _saved_vector(original.get("quaternion"), 4),
                _saved_vector(original.get("euler"), 3),
                _saved_vector(original.get("axis_angle"), 4))
    return marker, snapshot


def _write_marker(obj, original, uid, world_id):
    location, mode, quaternion, euler, axis = original
    marker = {"schema": 1, "owner_uid": uid, "world_id": world_id,
              "original": {"location": list(location), "rotation_mode": mode,
                           "quaternion": list(quaternion), "euler": list(euler),
                           "axis_angle": list(axis)}}
    raw = json.dumps(marker, separators=(",", ":"), allow_nan=False)
    obj[LEASE_MARKER] = raw
    return raw


def _remove_marker(obj, uid):
    try:
        if LEASE_MARKER not in obj:
            return
        try:
            owned = _read_marker(obj)[0]["owner_uid"] == uid
        except (TypeError, ValueError):
            owned = True  # The live record is authoritative for a corrupt marker.
        if owned:
            del obj[LEASE_MARKER]
    except (ReferenceError, RuntimeError, KeyError):
        pass


def _same_object(first, second):
    try:
        return first is not None and second is not None and first.as_pointer() == second.as_pointer()
    except ReferenceError:
        return False


def _copied_marker(obj, marker):
    """Object.copy carries custom properties, but never the source's lease."""
    owner = _owners.get(marker['owner_uid'])
    if owner is None:
        return False
    live = False
    for original in owner.get('objects', {}).values():
        try:
            if original is None or not original.as_pointer():
                continue
            live = True
            if _same_object(obj, original):
                return False
        except ReferenceError:
            continue
    return live


def _owned_object(record, name, uid):
    obj = record.get("objects", {}).get(name)
    try:
        if obj is not None and obj.as_pointer():
            return obj
    except ReferenceError:
        pass
    # A removed object can be replaced with another object of the same name.
    # Only a matching persisted marker permits restoration through this fallback.
    obj = bpy.data.objects.get(name)
    if obj is not None:
        try:
            if _read_marker(obj)[0]["owner_uid"] == uid:
                return obj
        except (TypeError, ValueError):
            pass
    return None


def recover_saved_leases():
    """Restore authoring channels carried into an Undo or loaded-file snapshot."""
    restored = 0
    for obj in tuple(bpy.data.objects):
        if LEASE_MARKER not in obj:
            continue
        try:
            _marker, original = _read_marker(obj)
            _restore(obj, original)
            restored += 1
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            # A corrupt marker must never supply partial or nonfinite channels.
            pass
        finally:
            try:
                del obj[LEASE_MARKER]
            except (ReferenceError, RuntimeError, KeyError):
                pass
    return restored


def _desired(value):
    """Preflight the entire packet, including parent targets, before writes."""
    if not isinstance(value, ObjectTransforms):
        raise ValueError("Connect Object Transforms")
    world = {record.object_name: Matrix(record.matrix_world) for record in value.records}
    desired = {}
    for name, matrix in world.items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError(f"Object is unavailable: {name}")
        if obj.library and not obj.override_library:
            raise ValueError(f"Object is read-only: {name}")
        if obj.parent and obj.parent_type != 'OBJECT':
            raise ValueError(f"Apply Transform requires an object parent: {name}")
        if any(constraint.influence > 0 and not constraint.mute for constraint in obj.constraints):
            raise ValueError(f"Object has an active constraint: {name}")
        animation = obj.animation_data
        if animation and any(driver.data_path.startswith(("location", "rotation_", "delta_"))
                             for driver in animation.drivers):
            raise ValueError(f"Object has a transform driver: {name}")
        if obj.delta_location.length_squared > 1e-12 or sum(component * component for component in obj.delta_rotation_euler) > 1e-12 or abs(obj.delta_rotation_quaternion.angle) > 1e-6:
            raise ValueError(f"Apply Transform requires zero delta transforms: {name}")
        if any(abs(float(scale) - 1.0) > 1e-6 for scale in obj.delta_scale):
            raise ValueError(f"Apply Transform requires unit delta scale: {name}")
        if any(obj.lock_location) or any(obj.lock_rotation) or (obj.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'} and obj.lock_rotations_4d and obj.lock_rotation_w):
            raise ValueError(f"Object has locked position or rotation: {name}")
        if any(not math.isfinite(component) for row in matrix for component in row):
            raise ValueError(f"Object Transform is not finite: {name}")
        if any(abs(matrix[3][i] - float(i == 3)) > 1e-6 for i in range(4)):
            raise ValueError(f"Object Transform is not affine: {name}")
        if obj.parent:
            parent_world = world.get(obj.parent.name, obj.parent.matrix_world)
            parent_matrix = parent_world @ obj.matrix_parent_inverse
            if abs(parent_matrix.determinant()) < 1e-12:
                raise ValueError(f"Object has a singular parent transform: {name}")
            matrix = parent_matrix.inverted() @ matrix
        position, rotation, scale = matrix.decompose()
        rebuilt = Matrix.LocRotScale(position, rotation, scale)
        tolerance = 2e-5 * max(1.0, max(abs(component) for row in matrix for component in row))
        if any(abs(matrix[r][c] - rebuilt[r][c]) > tolerance for r in range(4) for c in range(4)):
            raise ValueError(f"Apply Transform cannot apply shear: {name}")
        # Physics and generic producers carry scale for coordinate conversion;
        # this sink owns position/rotation only and preserves authored scale.
        if any(abs(scale[i] - obj.scale[i]) > 2e-5 * max(1.0, abs(obj.scale[i])) for i in range(3)):
            raise ValueError(f"Object Transform scale differs from target: {name}")
        desired[name] = (obj, position, rotation.normalized())
    return desired


def apply(node, value):
    from . import physics, runtime
    uid = str(node.runtime_id)
    if node.mute or not node.enabled:
        release(uid)
        return
    desired = _desired(value)
    live_record = _owners.get(uid)
    # Keep a live object reference with the raw marker: copies and reused RNA
    # pointers must validate their own lease, while a rename keeps its cache.
    cached_markers = live_record.get('markers', {}) if live_record else {}
    copied_originals = {}
    for name, (obj, _position, _rotation) in desired.items():
        if any(_same_object(obj, _owned_object(record, original_name, other))
               for other, record in _owners.items() if other != uid
               for original_name in record['originals']):
            raise ValueError(f"Object already has another Apply Transform owner: {name}")
        if LEASE_MARKER in obj:
            cached = cached_markers.get(obj.as_pointer())
            if cached is None or not _same_object(obj, cached[0]) or obj.get(LEASE_MARKER) != cached[1]:
                try:
                    marker, _original = _read_marker(obj)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Object has an unresolved Apply Transform lease: {name}") from error
                copied = _copied_marker(obj, marker)
                if marker['owner_uid'] != uid and not copied:
                    raise ValueError(f"Object already has another Apply Transform owner: {name}")
                if copied:
                    # Capture the clone's birth channels before any lease changes.
                    copied_originals[name] = _snapshot(obj)
        if any(target[0] in {'object', 'object_location'} and _same_object(
                obj, physics.resolve_bone_object(target[1]) or bpy.data.objects.get(target[1]))
               for owned in flow._owners.values() for target in owned):
            raise ValueError(f"Object already has an Armature Apply owner: {name}")
    if uid in _owners and _owners[uid]['world_id'] != value.world_id:
        release(uid)
    physics.authorize_apply(value, node)
    record = _owners.setdefault(uid, {'originals': {}, 'objects': {}, 'world_id': value.world_id,
                                     'tree': node.id_data.name, 'node': node.name,
                                     'markers': {}, 'marker_pointers': {}})
    for name in tuple(record['originals']):
        obj = _owned_object(record, name, uid)
        if any(_same_object(obj, current[0]) for current in desired.values()):
            continue
        if obj is not None:
            _restore(obj, record['originals'][name])
            _remove_marker(obj, uid)
        del record['originals'][name]
        record['objects'].pop(name, None)
        record['markers'].pop(record['marker_pointers'].pop(name, None), None)
    try:
        for name, (obj, position, rotation) in desired.items():
            original_name = next((key for key in record['originals']
                                  if _same_object(obj, _owned_object(record, key, uid))), name)
            if original_name not in record['originals']:
                original = copied_originals.get(name)
                if original is None:
                    original = _read_marker(obj)[1] if LEASE_MARKER in obj else _snapshot(obj)
                record['originals'][original_name] = original
                record['objects'][original_name] = obj
            pointer = obj.as_pointer()
            old_pointer = record['marker_pointers'].get(original_name)
            if old_pointer != pointer:
                record['markers'].pop(old_pointer, None)
                record['marker_pointers'][original_name] = pointer
            cached = record['markers'].get(pointer)
            if cached is None or not _same_object(obj, cached[0]) or obj.get(LEASE_MARKER) != cached[1]:
                # Authored channels and ownership are stable throughout a lease.
                # Revalidate external edits above, then repair only changed markers.
                raw = _write_marker(obj, record['originals'][original_name], uid, value.world_id)
                record['markers'][pointer] = (obj, raw)
            obj.location = position
            runtime._assign_rotation(obj, rotation)
    except Exception:
        release(uid)
        raise
    node.last_summary = f"Applied {len(desired)} object transforms"


def release(uid):
    from . import physics
    uid = str(uid)
    record = _owners.pop(uid, None)
    try:
        if record:
            for name, original in record['originals'].items():
                obj = _owned_object(record, name, uid)
                if obj is not None:
                    try:
                        _restore(obj, original)
                    except ReferenceError:
                        pass
                    finally:
                        _remove_marker(obj, uid)
    finally:
        physics.release_apply(uid)


def release_world(world_id):
    for uid, record in tuple(_owners.items()):
        if record['world_id'] == world_id:
            release(uid)


def maintenance():
    from . import physics
    for uid, record in tuple(_owners.items()):
        tree = bpy.data.node_groups.get(record['tree'])
        node = tree.nodes.get(record['node']) if tree else None
        valid = node and str(node.runtime_id) == uid and node.enabled and not node.mute
        if valid:
            data = node.inputs.get('Object Transforms')
            enter = node.inputs.get('Enter')
            valid = data and data.is_linked and enter and enter.is_linked
        if valid:
            valid = uid in flow.live_apply_ids(tree)
        if valid and record['world_id']:
            valid = physics.has_world(record['world_id'])
            if valid:
                # A changed Mixer type or disconnected data branch retires its lease.
                try:
                    from .runtime import EvaluationContext
                    value = EvaluationContext(tree, bpy.context.scene, 0).input(node, 'Object Transforms', None)
                    valid = isinstance(value, ObjectTransforms) and value.world_id == record['world_id']
                except Exception:
                    valid = False
        if not valid:
            release(uid)


def clear():
    for uid in tuple(_owners):
        release(uid)


CLASSES = (YUGUS_NS_object_transforms, YUGUS_NODE_apply_object_transform)
