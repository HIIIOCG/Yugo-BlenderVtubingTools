"""Whole-armature feature leases coexist with existing per-bone Apply writers."""
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
from mathutils import Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
addon = load_node_studio()
from blender_integration import make_armature
from yugu_node_studio import extensions as api, flow, runtime, targets
from yugu_node_studio.data import NativePose, HumanoidTransform, SpringData
from yugu_node_studio.modules import physics

addon.register()
rig_a, rig_b = make_armature(), make_armature()
tree = bpy.data.node_groups.new('Pose ownership', 'YUGUS_NT_humanoid_motion')
first = tree.nodes.new('YUGUS_NODE_apply_rotation')
second = tree.nodes.new('YUGUS_NODE_apply_rotation')
first.armature = second.armature = rig_a
head = flow.BoneRotations(rig_a.name, {'head': Quaternion((0, 0, 1), .4)})
hips = flow.BoneRotations(rig_a.name, {'hips': Quaternion((1, 0, 0), .2)})

# Existing writers still share a rig when their bones do not overlap.
flow.apply(first, head)
flow.apply(second, hips)
assert len(flow.pose_writers(rig_a)) == 2
before = rig_a.pose.bones['head'].matrix_basis.copy()
try:
    api.claim_transforms('ragdoll-test', [rig_b, rig_a])
    raise AssertionError('An active bone writer must block the entire rig lease')
except api.ResourceConflict:
    pass
assert api.transform_owner(rig_b) is None
assert rig_a.pose.bones['head'].matrix_basis == before
flow.release(str(first.runtime_id))
flow.release(str(second.runtime_id))
assert not flow.pose_writers(rig_a)

# Retained bone owners follow the original live object across rename. A different
# object using its former name is independent, and release restores only the old
# object. Deleted/recreated names must not inherit the dead writer's reservation.
base = rig_a.pose.bones['head'].matrix_basis.copy()
flow.apply(first, head)
old_name = rig_a.name
rig_a.name = 'Renamed active Apply rig'
replacement = make_armature()
replacement.name = old_name
replacement.pose.bones['head'].rotation_mode = 'QUATERNION'
replacement.pose.bones['head'].rotation_quaternion = Quaternion((1, 0, 0), .9)
replacement_before = replacement.pose.bones['head'].matrix_basis.copy()
try:
    api.claim_transforms('renamed-active-test', [rig_a])
except api.ResourceConflict:
    pass
else:
    raise AssertionError('Renaming an active bone writer bypassed its reservation')
api.claim_transforms('replacement-test', [replacement])
assert not flow.pose_writers(replacement)
flow.release(str(first.runtime_id))
assert rig_a.pose.bones['head'].matrix_basis == base
assert replacement.pose.bones['head'].matrix_basis == replacement_before
api.release_owner('replacement-test')
first.armature = replacement
flow.apply(first, head)
bpy.data.objects.remove(replacement, do_unlink=True)
replacement = make_armature()
replacement.name = old_name
replacement.pose.bones['head'].rotation_mode = 'QUATERNION'
replacement.pose.bones['head'].rotation_quaternion = Quaternion((0, 1, 0), .8)
replacement_before = replacement.pose.bones['head'].matrix_basis.copy()
api.claim_transforms('recreated-test', [replacement])
flow.release(str(first.runtime_id))
assert replacement.pose.bones['head'].matrix_basis == replacement_before
api.release_owner('recreated-test')
first.armature = rig_a

# After explicit handoff, core Apply must reject the whole write atomically.
api.claim_transforms('ragdoll-test', [rig_b])
first.armature = None
before_a = rig_a.pose.bones['head'].matrix_basis.copy()
before_b = rig_b.pose.bones['head'].matrix_basis.copy()
context = SimpleNamespace(input=lambda node, name, default: targets.Armatures((rig_a.name, rig_b.name)))
try:
    flow.apply(first, head, context)
    raise AssertionError('Core Apply must reject the exclusively leased rig')
except ValueError as error:
    assert 'controls this armature pose' in str(error)
assert rig_a.pose.bones['head'].matrix_basis == before_a
assert rig_b.pose.bones['head'].matrix_basis == before_b
assert not flow.pose_writers(rig_a)

# The older native-pose writer observes the same reservation.
pose = NativePose(rig_b.name, {'head': HumanoidTransform(Quaternion((0, 1, 0), .7), None)})
native = SimpleNamespace(write_locations=True, write_root=True)
assert runtime._write_native_pose(native, pose)[0] == 0
assert rig_b.pose.bones['head'].matrix_basis == before_b
api.release_owner('ragdoll-test')
assert runtime._write_native_pose(native, pose)[0] > 0

# Retained native pose output also prevents an unsafe handoff.
node = tree.nodes.new('YUGUS_NODE_blender_output')
runtime._apply_pose(tree, node, pose)
old_name = rig_b.name
rig_b.name = 'Renamed active native pose rig'
try:
    api.claim_transforms('ragdoll-test', [rig_b])
    raise AssertionError('Retained native-pose output must release first')
except api.ResourceConflict:
    pass
native_replacement = make_armature()
native_replacement.name = old_name
native_replacement.pose.bones['head'].rotation_mode = 'QUATERNION'
native_replacement.pose.bones['head'].rotation_quaternion = Quaternion((1, 0, 0), .8)
replacement_before = native_replacement.pose.bones['head'].matrix_basis.copy()
api.claim_transforms('native-replacement-test', [native_replacement])
runtime.restore_tree(tree)
assert rig_b.pose.bones['head'].matrix_basis.is_identity
assert native_replacement.pose.bones['head'].matrix_basis == replacement_before
api.release_owner('native-replacement-test')
api.claim_transforms('ragdoll-test', [rig_b])
reference = api.object_ref(rig_b)
rig_b.name = 'Renamed leased rig'
assert api.transform_owner(rig_b) == 'ragdoll-test'
assert api.resolve_object(reference) == rig_b
api.release_owner('ragdoll-test')
first.armature = rig_b
flow.apply(first, head)
flow.release(str(first.runtime_id))

# Spring initialization, Start and Reload must reject an armature lease BEFORE
# resetting any bones, official switch, retained solver, or active flag. The
# official flag fixture is synthetic; no separately installed VRM is required.
spring_node = tree.nodes.new('YUGUS_NODE_spring_simulation')
settings = {'stiffness': .5, 'gravity_power': .2, 'gravity_dir': (0, 0, -1),
            'drag_force': .2, 'hit_radius': 0}
spring_data = SpringData('1.0', ({'name': 'Generated spring', 'center': '',
    'collider_groups': (), 'joints': tuple({'bone': name, **settings}
    for name in ('hairRoot', 'hairTip'))},), (), (), rig_a.name)
official = SimpleNamespace(enable_animation=True)
original_official, original_input = runtime._official_spring_bone, runtime._spring_input_value
try:
    runtime._official_spring_bone = lambda armature, data: official if armature == rig_a else None
    runtime._spring_input_value = lambda node, scene: spring_data
    hair = rig_a.pose.bones['hairRoot']
    hair.rotation_mode = 'QUATERNION'
    hair.rotation_quaternion = Quaternion((0, 0, 1), .6)
    before = {bone.name: bone.matrix_basis.copy() for bone in rig_a.pose.bones}
    api.claim_transforms('spring-initialize-test', [rig_a])
    states_before = set(runtime._node_state)
    for operation in (lambda: runtime._initialize_spring(spring_node, spring_data, bpy.context),
                      lambda: runtime.start_spring(spring_node, bpy.context)):
        try:
            operation()
        except RuntimeError as error:
            assert 'controls this armature pose' in str(error)
        else:
            raise AssertionError('Spring initialization ignored the armature lease')
        assert official.enable_animation and not spring_node.spring_active
        assert set(runtime._node_state) == states_before
        assert all(bone.matrix_basis == before[bone.name] for bone in rig_a.pose.bones)
    spring_node.spring_active = True
    try:
        runtime.reload_spring(spring_node, bpy.context)
    except RuntimeError as error:
        assert 'controls this armature pose' in str(error)
    else:
        raise AssertionError('Reload retired spring state before checking the lease')
    assert spring_node.spring_active and official.enable_animation
    assert all(bone.matrix_basis == before[bone.name] for bone in rig_a.pose.bones)
    spring_node.spring_active = False
    api.release_owner('spring-initialize-test')

    runtime.start_spring(spring_node, bpy.context)
    assert not official.enable_animation and spring_node.spring_active
    old_name = rig_a.name
    rig_a.name = 'Renamed active Spring rig'
    try:
        api.claim_transforms('active-spring-test', [rig_a])
    except api.ResourceConflict:
        pass
    else:
        raise AssertionError('Retained spring ownership was lost on rename')
    spring_replacement = make_armature()
    spring_replacement.name = old_name
    spring_replacement.pose.bones['hairRoot'].rotation_mode = 'QUATERNION'
    spring_replacement.pose.bones['hairRoot'].rotation_quaternion = Quaternion((1, 0, 0), .7)
    replacement_before = {bone.name: bone.matrix_basis.copy() for bone in spring_replacement.pose.bones}
    api.claim_transforms('spring-replacement-test', [spring_replacement])
    runtime._evaluate_spring(spring_node, spring_data)
    runtime.stop_spring(spring_node)
    assert official.enable_animation and not spring_node.spring_active
    assert all(bone.matrix_basis == replacement_before[bone.name] for bone in spring_replacement.pose.bones)
    api.release_owner('spring-replacement-test')
    api.claim_transforms('spring-stopped-test', [rig_a])
    api.release_owner('spring-stopped-test')

    # Empty flow spring output still retains the official switch. Releasing it
    # restores that exact rig even after rename before whole-rig handoff.
    first.armature = rig_a
    flow.apply(first, flow.BoneRotations(rig_a.name, {}, spring_data))
    assert not official.enable_animation
    rig_a.name = 'Renamed empty spring Apply rig'
    try:
        api.claim_transforms('empty-spring-test', [rig_a])
    except api.ResourceConflict:
        pass
    else:
        raise AssertionError('Retained official switch allowed an unsafe lease')
    flow.release(str(first.runtime_id))
    assert official.enable_animation
    api.claim_transforms('empty-spring-test', [rig_a])
    api.release_owner('empty-spring-test')
finally:
    runtime._official_spring_bone, runtime._spring_input_value = original_official, original_input

example = physics.create_example(bpy.context.scene)
example['anchor'].parent = rig_b
bpy.context.view_layer.update()
addon.flow.emit_event(example['tree'], example['controls'], 'Start')
addon.flow.emit_event(example['tree'], example['controls'], 'Grab')
assert physics.service.body_state(example['prop'])['grab_anchor'] == example['anchor'].name
assert physics.release_for_armature(rig_a) == ()
assert physics.service.body_state(example['prop'])['grab_anchor'] is not None
assert physics.release_for_armature(rig_b) == (example['prop'].name,)
assert physics.service.body_state(example['prop'])['grab_anchor'] is None
addon.flow.emit_event(example['tree'], example['controls'], 'Stop')
addon.unregister()
assert not api.claimed_transforms()
print('YUGU_POSE_OWNERSHIP_OK')
