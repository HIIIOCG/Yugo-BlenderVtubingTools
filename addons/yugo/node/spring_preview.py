"""Live viewport spheres for the Spring converter's bone hit radii.

VRM 1 centers follow the next listed joint's head; its final sentinel has no
sphere. VRM 0 centers follow each bone's first child head, or its own tail.
The wire display follows the earlier Spring preview, while both versions use
the current MuJoCo radius: authored hit_radius times armature world scale.
World-space Empty matrices prevent bone/armature scale from being applied twice.
This module reads Spring Data and pose geometry; it never evaluates a Solver.
"""
from dataclasses import dataclass
import math

import bpy
from mathutils import Matrix

from .constants import TREE_ID
from .data import SpringData

NODE = 'YUGUS_NODE_spring_to_physics'
MARKER_PROPERTY = '_yugo_node_spring_collision_preview_v1'
OWNER_PROPERTY = '_yugo_node_spring_collision_preview_owner'
KEY_PROPERTY = '_yugo_node_spring_collision_preview_key'
BONE_PROPERTY = '_yugo_node_spring_collision_preview_bone'
_owners = {}
_needs_recovery = True


@dataclass(frozen=True)
class _Display:
    key: str
    name: str
    bone: str
    position: object
    radius: float


def _generated(obj, uid=None):
    """Only module-marked local Empties can be changed or deleted."""
    try:
        owner = obj.get(OWNER_PROPERTY)
        return (obj.type == 'EMPTY' and obj.data is None and obj.library is None
                and obj.get(MARKER_PROPERTY) == 1
                and isinstance(owner, str) and bool(owner)
                and (uid is None or owner == uid))
    except (ReferenceError, AttributeError):
        return False


def _remove(obj, uid=None):
    if _generated(obj, uid):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass


def release(uid):
    """Remove one converter's generated spheres, retaining all source objects."""
    uid = str(uid)
    objects = _owners.pop(uid, {})
    for obj in tuple(objects.values()):
        _remove(obj, uid)


def clear():
    """Clean live and saved previews during unregister, file load or Undo."""
    global _needs_recovery
    _owners.clear()
    for obj in tuple(getattr(bpy.data, 'objects', ())):
        _remove(obj)
    _needs_recovery = True


def _radius(value, scale):
    radius = float(value or 0.0) * scale
    if not math.isfinite(radius):
        raise ValueError('Spring hit radius must be finite')
    return max(0.0, radius)


def _displays(data, armature):
    version = str(data.version)
    if not version.startswith(('0', '1')):
        raise ValueError(f'Unsupported Spring version: {version}')
    bones = armature.pose.bones
    scale = max(abs(component) for component in armature.matrix_world.to_scale())
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Spring preview armature scale must be positive')
    displays = []
    for group_index, spring in enumerate(data.springs):
        name = str(spring.get('name', '') or f'Spring {group_index + 1}')
        joints = tuple(spring.get('joints', ()))
        if version.startswith('1'):
            for joint_index, (head, tail) in enumerate(zip(joints, joints[1:])):
                head_name, tail_name = str(head.get('bone', '')), str(tail.get('bone', ''))
                head_bone, tail_bone = bones.get(head_name), bones.get(tail_name)
                radius = _radius(head.get('hit_radius', 0.0), scale)
                if head_bone is None or tail_bone is None or radius <= 0:
                    continue
                position = armature.matrix_world @ tail_bone.matrix.to_translation()
                displays.append(_Display(f'1:{group_index}:{joint_index}',
                                         f'Yugo Spring Sphere {name} [{head_name}]',
                                         head_name, position, radius))
            continue
        radius = _radius(spring.get('hit_radius', 0.0), scale)
        if radius <= 0:
            continue
        seen = set()
        for entry in joints:
            root = bones.get(str(entry.get('bone', '')))
            pending = [root] if root is not None else []
            while pending:
                bone = pending.pop()
                if bone.name in seen:
                    continue
                seen.add(bone.name)
                children = tuple(bone.children)
                tail = children[0].matrix.to_translation() if children else bone.tail
                position = armature.matrix_world @ tail
                displays.append(_Display(f'0:{group_index}:{bone.name}',
                                         f'Yugo Spring Sphere {name} [{bone.name}]',
                                         bone.name, position, radius))
                pending.extend(reversed(children))
    if any(not all(math.isfinite(component) for component in display.position)
           for display in displays):
        raise ValueError('Spring preview position must be finite')
    return tuple(displays)


def _collection(armature, scene):
    scene_collections = {scene.collection.as_pointer()}
    pending = list(scene.collection.children)
    while pending:
        collection = pending.pop()
        scene_collections.add(collection.as_pointer())
        pending.extend(collection.children)
    return next((collection for collection in armature.users_collection
                 if collection.as_pointer() in scene_collections), scene.collection)


def _recover_saved(live):
    """Sweep saved markers once per runtime, never on ordinary off/error ticks."""
    global _needs_recovery
    for obj in tuple(bpy.data.objects):
        if not _generated(obj):
            continue
        uid = obj.get(OWNER_PROPERTY)
        key = obj.get(KEY_PROPERTY)
        if uid not in live or not isinstance(key, str) or not key:
            _remove(obj, uid)
            continue
        objects = _owners.setdefault(uid, {})
        if key in objects:
            _remove(obj, uid)
        else:
            objects[key] = obj
    _needs_recovery = False


def _active(node, scene):
    socket = node.inputs.get('Spring Data')
    return (scene is not None and node.bl_idname == NODE
            and bool(getattr(node, 'show_collision_spheres', False))
            and not node.mute and bool(getattr(node, 'enabled', True))
            and socket is not None and socket.is_linked)


def _active_nodes(scene):
    return tuple(node for tree in tuple(getattr(bpy.data, 'node_groups', ()))
                 if tree.bl_idname == TREE_ID for node in tuple(tree.nodes)
                 if node.bl_idname == NODE and _active(node, scene))


def refresh(node, scene=None):
    """Show current upstream Spring Data independently of Solver connections."""
    uid = str(getattr(node, 'runtime_id', '') or '')
    if not uid:
        return 0
    scene = scene or getattr(bpy.context, 'scene', None)
    if not _active(node, scene):
        release(uid)
        return 0
    try:
        if _needs_recovery:
            _recover_saved({str(current.runtime_id) for current in _active_nodes(scene)})
        from .runtime import EvaluationContext
        data = EvaluationContext(node.id_data, scene, 0).input(node, 'Spring Data', None)
        if not isinstance(data, SpringData):
            raise ValueError('Connect Spring Data to preview collision spheres')
        armature = scene.objects.get(data.armature_name)
        if armature is None or armature.type != 'ARMATURE':
            raise ValueError('Spring preview requires its source Armature')
        depsgraph = (bpy.context.evaluated_depsgraph_get() if bpy.context.scene == scene
                     else scene.view_layers[0].depsgraph)
        displays = _displays(data, armature.evaluated_get(depsgraph))
        collection = _collection(armature, scene)
        objects = _owners.setdefault(uid, {})
        desired = {display.key for display in displays}
        for key, obj in tuple(objects.items()):
            if key not in desired or not _generated(obj, uid):
                _remove(obj, uid)
                objects.pop(key, None)
        for display in displays:
            obj = objects.get(display.key)
            if obj is None:
                obj = bpy.data.objects.new(display.name, None)
                obj[MARKER_PROPERTY], obj[OWNER_PROPERTY] = True, uid
                obj[KEY_PROPERTY] = display.key
                obj.empty_display_type = 'SPHERE'
                obj.show_in_front, obj.hide_render, obj.hide_select = True, True, True
                objects[display.key] = obj
            if obj.name not in collection.objects:
                for previous in tuple(obj.users_collection):
                    previous.objects.unlink(obj)
                collection.objects.link(obj)
            if obj.get(BONE_PROPERTY) != display.bone:
                obj[BONE_PROPERTY] = display.bone
            if not math.isclose(obj.empty_display_size, display.radius, rel_tol=1e-6, abs_tol=1e-8):
                obj.empty_display_size = display.radius
            matrix = Matrix.Translation(display.position)
            if obj.parent is not None:
                obj.parent = None
            current = obj.matrix_world
            if any(not math.isclose(current[row][column], matrix[row][column],
                                    rel_tol=0.0, abs_tol=1e-7)
                   for row in range(4) for column in range(4)):
                obj.matrix_world = matrix
        return len(displays)
    except Exception:
        # Invalid or temporarily unavailable input must not leave stale spheres.
        release(uid)
        return 0


def tick(context, delta):
    """Follow evaluated animation and prune previews from removed converters."""
    scene = getattr(context, 'scene', None)
    nodes = _active_nodes(scene)
    live = {str(node.runtime_id) for node in nodes}
    if _needs_recovery:
        _recover_saved(live)
    for uid in set(_owners) - live:
        release(uid)
    for node in nodes:
        refresh(node, scene)
