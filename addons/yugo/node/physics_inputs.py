"""Read authored Blender inputs into SI descriptions, without scene mutation."""
from __future__ import annotations

import cmath
import json
import math
from dataclasses import replace

from mathutils import Matrix, Quaternion, Vector

from .data import SpringData
from .physics_data import PhysicsDescription, RigidBody, SpringChain, SpringCollider, SpringJoint

SPRING_REFERENCE_MASS = 0.01


def unit_scale(scene):
    value = float(scene.unit_settings.scale_length)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Scene length unit scale must be positive")
    return value


def matrix_pose(matrix, length_scale=1.0, *, label="Physics boundary", uniform=False):
    location, rotation, scale = matrix.decompose()
    if any(not math.isfinite(value) or value <= 0 for value in scale):
        raise ValueError(f"{label}: negative or zero scale is not supported")
    reconstructed = Matrix.LocRotScale(location, rotation, scale)
    if max(abs(matrix[row][col] - reconstructed[row][col]) for row in range(4) for col in range(4)) > 1e-5:
        raise ValueError(f"{label}: shear is not supported")
    if uniform and max(scale) - min(scale) > 1e-5 * max(scale):
        raise ValueError(f"{label}: Spring physics needs uniform scale")
    return tuple(location * length_scale), tuple(rotation), tuple(scale)


def _object(scene, value):
    name = value if isinstance(value, str) else getattr(value, "name", "")
    obj = scene.objects.get(name)
    if obj is None:
        raise ValueError(f"Physics source is missing from the scene: {name}")
    return obj



def _rigid_authored_settings(obj, snapshots):
    original = snapshots.get(obj.name)
    if original is not None:
        return original
    # A duplicated Object can inherit the temporary takeover flags and the
    # recovery marker, but it is not part of the live owner's name registry.
    raw = obj.get("_yugo_node_physics_lease_v1")
    if raw is None:
        return {}
    try:
        marker = json.loads(raw)
        original = marker["original"]
        valid = (marker.get("schema") == 1 and isinstance(original, dict)
                 and original.get("type") in {"ACTIVE", "PASSIVE"}
                 and isinstance(original.get("enabled"), bool)
                 and isinstance(original.get("kinematic"), bool))
    except (TypeError, ValueError, KeyError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(f"Invalid Yugo rigid body recovery metadata: {obj.name}")
    return original


def _rigid_identity(obj):
    # Blender's Object data-block identity survives renaming and is distinct
    # for real Object copies; no ID properties are written to the scene.
    return f"rigid:session:{obj.session_uid}"


def describe_rigid(objects, scene, authored_settings=None):
    """Explicit selection only. Saved flags remain authoritative during takeover.

    Active non-Animated bodies are Dynamic; Passive and Animated are Kinematic.
    The latter track the evaluated Blender boundary, without writing it back.
    """
    if hasattr(objects, "names"):
        objects = objects.names
    elif hasattr(objects, "objects"):
        objects = objects.objects
    if objects is None:
        objects = ()
    units = unit_scale(scene)
    snapshots = authored_settings or {}
    bodies = []
    for value in objects:
        obj = _object(scene, value)
        rb = obj.rigid_body
        if rb is None:
            raise ValueError(f"{obj.name}: add a Blender Rigid Body first")
        original = _rigid_authored_settings(obj, snapshots)
        def setting(name):
            return original.get(name, getattr(rb, name))
        if not setting("enabled"):
            raise ValueError(f"{obj.name}: rigid body is disabled")
        if obj.type != "MESH":
            raise ValueError(f"{obj.name}: only Mesh rigid body sources are supported")
        if obj.rigid_body_constraint is not None:
            raise ValueError(f"{obj.name}: Blender rigid body constraints are not supported")
        position, quaternion, scale = matrix_pose(obj.matrix_world, units, label=obj.name)
        shape = str(rb.collision_shape)
        if shape not in {"BOX", "SPHERE", "CAPSULE", "CYLINDER"}:
            raise ValueError(f"{obj.name}: {shape} collision is not supported; use Box, Sphere, Capsule or Cylinder")
        points = [Vector(point) for point in obj.bound_box]
        lower = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
        upper = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
        half = tuple((upper[axis] - lower[axis]) * 0.5 * scale[axis] * units for axis in range(3))
        center = tuple((upper[axis] + lower[axis]) * 0.5 * scale[axis] * units for axis in range(3))
        if shape == "BOX":
            size = half
        elif shape == "SPHERE":
            size = (max(half),)
        else:
            if abs(half[0] - half[1]) > 1e-5 * max(half[0], half[1]):
                raise ValueError(f"{obj.name}: {shape} requires equal X/Y radius")
            radius = half[0]
            length = half[2] - radius if shape == "CAPSULE" else half[2]
            if length <= 1e-7:
                raise ValueError(f"{obj.name}: Capsule needs height greater than its diameter; use Sphere instead")
            size = (radius, length)
        mask = sum(1 << index for index, enabled in enumerate(rb.collision_collections) if enabled)
        bodies.append(RigidBody(
            body_id=_rigid_identity(obj), object_name=obj.name,
            motion="DYNAMIC" if setting("type") == "ACTIVE" and not setting("kinematic") else "KINEMATIC",
            shape=shape, size=size, geom_position=center, position=position,
            quaternion=quaternion, object_scale=scale, mass=float(rb.mass),
            friction=float(rb.friction), restitution=float(rb.restitution),
            linear_damping=float(rb.linear_damping), angular_damping=float(rb.angular_damping),
            margin=float(rb.collision_margin) * units if rb.use_margin else 0.0,
            collision_mask=mask,
        ))
    selected_names = {body.object_name for body in bodies}
    for constraint_obj in scene.objects:
        constraint = constraint_obj.rigid_body_constraint
        if constraint is not None and constraint.enabled:
            linked = {value.name for value in (constraint.object1, constraint.object2) if value is not None}
            if linked & selected_names:
                raise ValueError(f"{constraint_obj.name}: Blender rigid body constraints are not supported")
    return PhysicsDescription(bodies=tuple(bodies), unit_scale=units)


def _positive(value, name, *, allow_zero=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'}")
    return value


def _support_spring_subtrees(joints):
    """Support descendant masses as well as the joint's own tail.

    VRM solves independent tails. Native articulated descendants also load
    every ancestor: match rest-direction torque and drag using subtree levers
    and inertia in the neutral configuration.
    """
    matrices, parents, moments = {}, {}, {}
    for joint in joints:
        matrix = Quaternion(joint.quaternion).to_matrix().to_4x4()
        matrix.translation = joint.position
        parents[joint.bone_name] = joint.parent_bone
        matrices[joint.bone_name] = matrices[joint.parent_bone] @ matrix if joint.parent_bone else matrix
        moments[joint.bone_name] = [0.0, 0.0]
    for joint in joints:
        endpoint = matrices[joint.bone_name] @ Vector(joint.tail)
        ancestor = joint.bone_name
        while ancestor:
            distance = (endpoint - matrices[ancestor].translation).length
            moments[ancestor][0] += joint.mass * distance
            moments[ancestor][1] += joint.mass * distance * distance
            ancestor = parents[ancestor]
    return tuple(replace(joint,
                         stiffness=joint.stiffness * moments[joint.bone_name][0] / (joint.mass * Vector(joint.tail).length),
                         damping=joint.damping * moments[joint.bone_name][1] / (joint.mass * Vector(joint.tail).length_squared))
                 for joint in joints)


def _spring_tail_inertia(mass, length):
    """Finite tail rotational inertia, shared by conversion and MJCF."""
    return max(2e-15, mass * length * length * 1e-4)


def _spring_reference_rates(length, stiffness, drag):
    """Continuous poles of the linearized 60 Hz authored-tail recurrence.

    Tail projection contributes damping as well as stiffness. Mapping the
    logarithms of both discrete poles preserves reference relaxation and
    oscillation much more closely than interpreting Stiffness as SI torque.
    The force scale retains the authored gravity/stiffness equilibrium ratio.
    Full drag is the finite 1e-6-retention limit; exact zero retention cannot
    be represented by a finite continuous damping coefficient.
    """
    hz = 60.0
    retention = max(1e-6, 1.0 - drag)
    projection = length / (length + stiffness / hz)
    summed = projection * (1.0 + retention)
    product = projection * retention
    first = (summed + cmath.sqrt(summed * summed - 4.0 * product)) * 0.5
    second = product / first
    poles = (cmath.log(first) * hz, cmath.log(second) * hz)
    gamma = max(0.0, -(poles[0] + poles[1]).real)
    omega2 = max(0.0, (poles[0] * poles[1]).real)
    force_scale = (omega2 * length / (stiffness * hz) if stiffness > 0
                   else (-math.log(retention) / drag if drag > 0 else 1.0))
    return gamma, omega2, force_scale


def describe_spring(data, scene, mass=SPRING_REFERENCE_MASS, *, _armature_scale=None, _collider_scales=None):
    """Translate VRM geometry into native ball-joint springs.

    Match linearized reference poles and world-tail drag at fixed 60 Hz,
    independently of the selected MuJoCo Physics Hz. Descendant masses taper
    with a finite floor to reduce reaction loads absent in authored tails.
    Subtree support keeps gravity equilibria close; finite coupled mechanics
    and large-angle force laws remain an approximation. No posed channels are read
    as joint rest geometry, so solver writeback cannot change the model revision.
    VRM 1 lists each segment's head and terminal endpoint; the endpoint is not
    an additional physical joint. VRM 0 descendant leaves use their bone tail.
    """
    if not isinstance(data, SpringData):
        raise TypeError("Spring to YugoPhysics expects Spring Data")
    if not data.version.startswith(("0", "1")):
        raise ValueError(f"Unsupported Spring version: {data.version}")
    armature = _object(scene, data.armature_name)
    if armature.type != "ARMATURE":
        raise ValueError("Spring Data source must be an Armature")
    units = unit_scale(scene)
    _, _, armature_scale = matrix_pose(armature.matrix_world, units, label=armature.name, uniform=True)
    # Only the cache supplies these already validated scale values. Preserve
    # its reference when a pose rotation perturbs matrix decomposition, so a
    # settings edit cannot invent changed joint rest geometry on conversion.
    scale = armature_scale[0] if _armature_scale is None else _armature_scale
    length_scale = units * scale
    mass = _positive(mass, "Spring mass")
    collider_entries = {str(item.get("uuid", "")): item for item in data.colliders}
    groups = {str(group.get("uuid", "")): tuple(group.get("colliders", ())) for group in data.collider_groups}
    used_colliders = set()
    chains = []
    for index, spring in enumerate(data.springs):
        if spring.get("center"):
            raise ValueError("Spring Center displacement compensation is not supported by native MuJoCo; clear Center")
        refs = []
        for group_id in spring.get("collider_groups", ()):
            if group_id not in groups:
                raise ValueError(f"Spring refers to missing collider group: {group_id}")
            refs.extend(groups[group_id])
        refs = tuple(dict.fromkeys(refs))
        used_colliders.update(refs)
        entries = {}
        for entry in spring.get("joints", ()):
            name = str(entry.get("bone", ""))
            bone = armature.data.bones.get(name)
            if bone is None:
                raise ValueError(f"Spring bone is missing: {name}")
            if name in entries:
                if data.version.startswith("0"):
                    continue
                raise ValueError(f"Duplicate Spring bone: {name}")
            entries[name] = entry
            if data.version.startswith("0"):
                def descend(parent):
                    for child in parent.children:
                        entries.setdefault(child.name, entry)
                        descend(child)
                descend(bone)
        if not entries:
            continue
        next_bones = {}
        if data.version.startswith("1"):
            for name in entries:
                bone = armature.data.bones[name]
                children = [child for child in bone.children if child.name in entries]
                if len(children) > 1:
                    raise ValueError("VRM 1 Spring branches are not supported; use separate straight chains")
                parent = bone.parent
                while parent is not None and parent.name not in entries:
                    parent = parent.parent
                if parent is not None and bone.parent != parent:
                    raise ValueError("VRM 1 Spring joints must be directly connected in the bone hierarchy")
                if children:
                    next_bones[name] = children[0]
                elif bone.parent is None or bone.parent.name not in entries:
                    raise ValueError("VRM 1 Spring requires at least two directly connected joint entries")
            entries = {name: entry for name, entry in entries.items() if name in next_bones}
        # Every simulated root gets one mocap attachment. Branched descendants
        # stay in the same articulated tree and retain their real bone parents.
        roots = [armature.data.bones[name] for name in entries
                 if armature.data.bones[name].parent is None or armature.data.bones[name].parent.name not in entries]
        for root in sorted(roots, key=lambda bone: bone.name):
            joints = []
            def append(bone, parent_name="", depth=0):
                if bone.name not in entries:
                    return
                entry = spring if data.version.startswith("0") else entries[bone.name]
                relative = (bone.parent.matrix_local.inverted() @ bone.matrix_local
                            if bone.parent is not None else bone.matrix_local.copy())
                pos, quat, _ = matrix_pose(relative, length_scale, label=f"Spring {bone.name}", uniform=True)
                endpoint = (next_bones[bone.name].head_local if data.version.startswith("1")
                            else (bone.children[0].head_local if bone.children else bone.tail_local))
                local_tail = bone.matrix_local.inverted() @ endpoint
                tail = tuple(local_tail * length_scale)
                length = Vector(tail).length
                stiffness = _positive(entry.get("stiffness", 0.0), "Spring stiffness", allow_zero=True)
                drag = float(entry.get("drag_force", 0.0))
                if not math.isfinite(drag) or not 0 <= drag <= 1:
                    raise ValueError("Spring Drag must be in [0, 1]")
                gravity = Vector(entry.get("gravity_dir", (0.0, 0.0, -1.0)))
                power = _positive(entry.get("gravity_power", 0.0), "Spring gravity power", allow_zero=True)
                if gravity.length_squared > 0:
                    gravity.normalize()
                # Root mass remains authored. A bounded depth profile reduces
                # child-to-parent reaction without underflowing long chains.
                # Keep deep short segments away from a near-singular native
                # mass matrix as well as satisfying MuJoCo's body minimum.
                floor = min(mass, max(1e-9, mass * 1e-7, 1e-11 / (length * length)))
                # A completely free chain needs no force-response matching;
                # retain uniform mass to avoid ill-conditioned free dynamics.
                tail_mass = mass if stiffness == 0.0 and drag == 0.0 else max(floor, mass * 0.03 ** depth)
                finite_inertia = _spring_tail_inertia(tail_mass, length)
                # Finite isotropic joint armature stabilizes twist. Include it
                # in the mapping rather than silently doubling swing inertia.
                swing_inertia = 2.0 * tail_mass * length * length + finite_inertia
                gamma, omega2, force_scale = _spring_reference_rates(length, stiffness * units, drag)
                gravity *= power * units * 60.0 * force_scale * swing_inertia / (tail_mass * length * length)
                radius = _positive(entry.get("hit_radius", 0.0), "Spring hit radius", allow_zero=True) * length_scale
                joints.append(SpringJoint(
                    bone.name, parent_name, pos, quat, tail, radius, tail_mass,
                    swing_inertia * omega2,
                    0.0, tuple(gravity), reference_hz=60.0,
                    world_damping=gamma * swing_inertia / (length * length),
                ))
                for child in sorted(bone.children, key=lambda value: value.name):
                    append(child, bone.name, depth + 1)
            append(root)
            parent_name = root.parent.name if root.parent is not None else ""
            matrix = armature.matrix_world.copy()
            if parent_name:
                matrix = matrix @ armature.pose.bones[parent_name].matrix
            position, quaternion, _ = matrix_pose(matrix, units, label=f"Spring {root.name} attachment", uniform=True)
            chains.append(SpringChain(
                f"spring:{armature.name}:{root.name}", armature.name, parent_name,
                _support_spring_subtrees(joints), position, quaternion,
                tuple(f"collider:{armature.name}:{identity}" for identity in refs), scale,
            ))
    colliders = []
    for identity in sorted(used_colliders):
        item = collider_entries.get(identity)
        if item is None:
            raise ValueError(f"Spring refers to missing collider: {identity}")
        shape = str(item.get("shape", "SPHERE")).upper()
        if shape not in {"SPHERE", "CAPSULE"} or item.get("inside", False):
            raise ValueError(f"Native MuJoCo does not support Spring collider {shape} / Inside")
        object_name = str(item.get("object", ""))
        bone_name = str(item.get("bone", ""))
        if object_name:
            collider_obj = _object(scene, object_name)
            position, quaternion, object_scale = matrix_pose(collider_obj.matrix_world, units, label=object_name, uniform=True)
            object_scale = (_collider_scales or {}).get(identity, object_scale)
            radius = float(collider_obj.empty_display_size) * object_scale[0] * units
            offset = (0.0, 0.0, 0.0)
            tail = (0.0, 0.0, 0.0)
        else:
            bone = armature.pose.bones.get(bone_name)
            if bone is None:
                raise ValueError(f"Spring collider bone is missing: {bone_name}")
            matrix = armature.matrix_world @ bone.matrix
            position, quaternion, object_scale = matrix_pose(matrix, units, label=f"Collider {identity}", uniform=True)
            object_scale = (_collider_scales or {}).get(identity, object_scale)
            if max(abs(value - scale) for value in object_scale) > 1e-5:
                raise ValueError(f"Collider {identity}: animated bone scaling is not supported")
            radius = float(item.get("radius", 0.0)) * length_scale
            offset = tuple(Vector(item.get("offset", (0.0, 0.0, 0.0))) * length_scale)
            tail = tuple(Vector(item.get("tail", (0.0, 0.0, 0.0))) * length_scale)
        colliders.append(SpringCollider(
            f"collider:{armature.name}:{identity}", armature.name, bone_name, object_name,
            shape, offset, tail, radius, position, quaternion, object_scale,
        ))
    if not chains:
        raise ValueError("Spring Data contains no supported joints")
    return PhysicsDescription(springs=tuple(chains), colliders=tuple(colliders), unit_scale=units)


# Converter-local cache: pose samples change every frame, while authored rest
# geometry and Spring settings usually remain the same for an entire session.
# Bound the registry even if a converter is deleted without a lifecycle hook.
_SPRING_DESCRIPTION_CACHE_LIMIT = 128
_spring_description_cache = {}


def clear_spring_description_cache(cache_key=None):
    """Retire converter descriptions on removal, load, undo or runtime clear."""
    if cache_key is None:
        _spring_description_cache.clear()
    else:
        for key in tuple(_spring_description_cache):
            if key[0] == cache_key:
                _spring_description_cache.pop(key, None)


def _spring_authored_value(value):
    """Snapshot nested dictionaries too; SpringData is only shallowly frozen."""
    if isinstance(value, dict):
        return tuple((key, _spring_authored_value(item)) for key, item in value.items())
    if isinstance(value, (tuple, list, Vector, Quaternion, Matrix)):
        return tuple(_spring_authored_value(item) for item in value)
    return value


def _spring_stable_scale(current, previous):
    # Rotations alone can perturb Blender's decomposed float32 scale. Use the
    # same tolerance as live model synchronization, relative to the cached model
    # rather than rounding every frame or accumulating small scale changes.
    if previous is not None and all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-12)
                                    for a, b in zip(current, previous)):
        return previous
    return current


def _spring_cached_inputs(data, scene, mass, previous):
    if not isinstance(data, SpringData):
        raise TypeError("Spring to YugoPhysics expects Spring Data")
    if not data.version.startswith(("0", "1")):
        raise ValueError(f"Unsupported Spring version: {data.version}")
    armature = _object(scene, data.armature_name)
    if armature.type != "ARMATURE":
        raise ValueError("Spring Data source must be an Armature")
    units = unit_scale(scene)
    mass = _positive(mass, "Spring mass")
    _, _, scale = matrix_pose(armature.matrix_world, units, label=armature.name, uniform=True)
    same_units = previous is not None and units == previous.unit_scale
    old_scale = ((previous.springs[0].armature_scale,) * 3 if same_units and previous.springs else None)
    scale = _spring_stable_scale(scale, old_scale)
    # Scan actual bone data instead of relying on depsgraph dirty flags, which
    # also change during ordinary pose playback. Include child order because
    # VRM 0 takes the first child's head as its authored endpoint. Ancestors and
    # newly added descendants must also invalidate a cached conversion.
    geometry = tuple((bone.name, bone.parent.name if bone.parent else "",
                      tuple(tuple(row) for row in bone.matrix_local),
                      tuple(bone.tail_local), tuple(child.name for child in bone.children))
                     for bone in armature.data.bones)
    old_colliders = {collider.collider_id: collider for collider in previous.colliders} if same_units else {}
    collider_sources = []
    # Only selected collider groups are conversion inputs. Unused authored
    # colliders need not exist in the scene, matching describe_spring semantics.
    groups = {str(group.get("uuid", "")): tuple(group.get("colliders", ())) for group in data.collider_groups}
    selected = set()
    for spring in data.springs:
        for identity in spring.get("collider_groups", ()):
            selected.update(groups.get(identity, ()))
    collider_entries = {str(item.get("uuid", "")): item for item in data.colliders}
    for identity in sorted(selected):
        item = collider_entries.get(identity)
        if item is None:
            raise ValueError(f"Spring refers to missing collider: {identity}")
        object_name = str(item.get("object", ""))
        bone_name = str(item.get("bone", ""))
        label = object_name or f"Collider {identity}"
        if object_name:
            obj = _object(scene, object_name)
            matrix = obj.matrix_world
            size = float(obj.empty_display_size)
        else:
            bone = armature.pose.bones.get(bone_name)
            if bone is None:
                raise ValueError(f"Spring collider bone is missing: {bone_name}")
            matrix = armature.matrix_world @ bone.matrix
            size = None
        _, _, source_scale = matrix_pose(matrix, units, label=label, uniform=True)
        old = old_colliders.get(f"collider:{armature.name}:{identity}")
        source_scale = _spring_stable_scale(source_scale, old.source_scale if old else None)
        if not object_name and max(abs(value - scale[0]) for value in source_scale) > 1e-5:
            raise ValueError(f"Collider {identity}: animated bone scaling is not supported")
        collider_sources.append((identity, size, source_scale))
    fingerprint = (data.version, data.armature_name,
                   _spring_authored_value(data.springs),
                   _spring_authored_value(data.colliders),
                   _spring_authored_value(data.collider_groups),
                   armature.as_pointer(), armature.data.as_pointer(),
                   units, mass, scale, geometry, tuple(collider_sources))
    return fingerprint, armature, scale[0], {identity: source_scale for identity, _size, source_scale in collider_sources}


def _spring_current_boundaries(description, armature, scene):
    """Refresh validated poses without serializing unchanged model geometry."""
    chains = []
    changed = False
    for chain in description.springs:
        matrix = armature.matrix_world.copy()
        if chain.parent_bone:
            parent = armature.pose.bones.get(chain.parent_bone)
            if parent is None:
                raise ValueError(f"Spring attachment bone is missing: {chain.parent_bone}")
            matrix = matrix @ parent.matrix
        position, quaternion, _ = matrix_pose(matrix, description.unit_scale,
                                              label=f"Spring {chain.chain_id} attachment", uniform=True)
        updated = (replace(chain, position=position, quaternion=quaternion)
                   if (position, quaternion) != (chain.position, chain.quaternion) else chain)
        changed |= updated is not chain
        chains.append(updated)
    colliders = []
    for collider in description.colliders:
        if collider.object_name:
            matrix = _object(scene, collider.object_name).matrix_world
        else:
            bone = armature.pose.bones.get(collider.bone_name)
            if bone is None:
                raise ValueError(f"Spring collider bone is missing: {collider.bone_name}")
            matrix = armature.matrix_world @ bone.matrix
        position, quaternion, _ = matrix_pose(matrix, description.unit_scale,
                                              label=collider.collider_id, uniform=True)
        updated = (replace(collider, position=position, quaternion=quaternion)
                   if (position, quaternion) != (collider.position, collider.quaternion) else collider)
        changed |= updated is not collider
        colliders.append(updated)
    if not changed:
        return description
    # Both record types validated the new poses above. The description's only
    # modified fields are top-level boundary position/quaternion, explicitly
    # excluded from its model payload; model identities and revision stay valid.
    # dataclasses.replace(description) would needlessly rehash every joint.
    from copy import copy
    result = copy(description)
    object.__setattr__(result, "springs", tuple(chains))
    object.__setattr__(result, "colliders", tuple(colliders))
    return result


def cached_describe_spring(data, scene, *, cache_key, mass=SPRING_REFERENCE_MASS):
    """Reuse a converter's model, while returning its current boundary poses.

    The original describe_spring remains the authoritative full conversion.
    A hit still reads authored input dictionaries, rest bone geometry, units,
    scale and selected collider sizes, so direct edits apply on the next read.
    Runtime owners should clear this registry during load/undo/unregistration.
    """
    key = (cache_key, scene.as_pointer())
    # Keep the last valid scale reference during an invalid upstream edit.
    # Validation still rejects that edit; recovery must not lose the baseline
    # and mistake rotation noise for newly authored rest geometry.
    cached = _spring_description_cache.get(key)
    previous = cached[1] if cached else None
    fingerprint, armature, scale, collider_scales = _spring_cached_inputs(data, scene, mass, previous)
    if cached is not None and fingerprint == cached[0]:
        description = _spring_current_boundaries(previous, armature, scene)
    else:
        description = describe_spring(data, scene, mass, _armature_scale=scale, _collider_scales=collider_scales)
    _spring_description_cache.pop(key, None)
    _spring_description_cache[key] = (fingerprint, description)
    if len(_spring_description_cache) > _SPRING_DESCRIPTION_CACHE_LIMIT:
        _spring_description_cache.pop(next(iter(_spring_description_cache)))
    return description
