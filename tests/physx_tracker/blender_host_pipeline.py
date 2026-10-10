"""Native learned physics through real fused host Bind/Apply at a fixed frame."""
import time

import bpy
from _bootstrap import OUTPUT, TRACKER, assert_clean, finish, load, wait_for

yugo, host, addon = load()
yugo.register()
addon.register()
clients = []


def basis(rig):
    return {bone.name: tuple(float(value) for row in bone.matrix_basis for value in row)
            for bone in rig.pose.bones}


def difference(first, second):
    return max(abs(a - b) for name in first for a, b in zip(first[name], second[name]))


def snapshot(obj):
    return {'matrix': [float(value) for row in obj.matrix_world for value in row],
            'vertices': [tuple(vertex.co) for vertex in obj.data.vertices],
            'rigid_body': (obj.rigid_body.type, obj.rigid_body.mass, obj.rigid_body.collision_shape)}


def wire(first, output, second, input_name):
    return tree.links.new(first.outputs[output], second.inputs[input_name])


def press(target, action):
    # Invoke the host event router, including real links and the installed addon adapter.
    old = tuple(trigger.outputs['Trigger'].links)
    for link in old:
        tree.links.remove(link)
    wire(trigger, 'Trigger', target, action)
    run_id = host.flow.emit_event(tree, trigger, 'Trigger')
    assert not tree.last_error, (run_id, tree.last_error, target.status)


def active_state(node):
    world = addon.runtime._worlds.get(node.runtime_id)
    assert world is not None, node.status
    return world


def pump():
    addon.runtime.tick()
    assert physics.runtime_id in addon.runtime._worlds, physics.status
    assert bpy.context.scene.frame_current == fixed_frame
    assert not tree.last_error, tree.last_error


try:
    scene = bpy.context.scene
    scene.frame_set(17)
    fixed_frame = scene.frame_current
    bpy.ops.mesh.primitive_cube_add(size=.15, location=(4., 0., .8))
    cube = bpy.context.object
    cube.name = 'Read-only rigid input'
    bpy.ops.rigidbody.object_add()
    cube.rigid_body.type = 'ACTIVE'
    cube.rigid_body.collision_shape = 'BOX'
    scene.rigidbody_world.enabled = False
    original_cube = snapshot(cube)
    tree = bpy.data.node_groups.new('Native PhysX host pipeline', addon.data.TREE)
    tree.use_fake_user = True
    from yugo_physx_tracker.demo import create
    mapping, actor, tracker, physics = create(bpy.context, tree)
    source = mapping.armature
    original_source = basis(source)
    original_object = tuple(value for row in source.matrix_world for value in row)
    trigger = tree.nodes.new('YUGUS_NODE_button_trigger')
    rigid = tree.nodes.new(addon.data.RIGID_BODIES)
    wire(rigid, 'Rigid Bodies', physics, 'Rigid Bodies')
    bind = tree.nodes.new('YUGUS_NODE_motion_to_rotation')
    apply = tree.nodes.new('YUGUS_NODE_apply_rotation')
    apply.armature = source
    wire(physics, 'Humanoid Motion', bind, 'Motion')
    wire(mapping, 'Bone Map', bind, 'Bone Map')
    wire(bind, 'Bone Transform', apply, 'Bone Transform')
    # First run with Apply disconnected: native preview must not write source data.
    press(physics, 'Play')
    world = active_state(physics)
    client = world['client']
    clients.append(client)
    assert str(TRACKER / 'backend/worker.py') in list(map(str, client.process.args))
    wait_for(lambda: world['state'].get('seconds', 0.) > .2, tick=pump)
    assert world['state']['learned_policy_loaded']
    assert difference(original_source, basis(source)) < 1e-8
    assert tuple(value for row in source.matrix_world for value in row) == original_object
    assert snapshot(cube) == original_cube
    motion = addon.bridge.context(tree, scene).evaluate(physics)['Humanoid Motion']
    assert isinstance(motion, host.data.HumanoidMotion)
    assert motion.bones['hips'].location is not None
    assert all(value.location is None for name, value in motion.bones.items() if name != 'hips')
    # A real Updated event owns/applies the typed bone packet without manual Apply.
    wire(physics, 'Updated', apply, 'Enter')
    wait_for(lambda: difference(original_source, basis(source)) > .001, tick=pump)
    assert apply.runtime_id in host.flow._owners
    typed = addon.bridge.context(tree, scene).evaluate(bind)['Bone Transform']
    assert isinstance(typed, host.flow.BoneTransforms) and typed.armature_name == source.name
    assert snapshot(cube) == original_cube
    press(physics, 'Play')
    wait_for(lambda: not world['state']['running'], tick=pump)
    for _ in range(20):
        pump()
        time.sleep(.01)
    paused_seconds = world['state']['seconds']
    for _ in range(10):
        pump()
        time.sleep(.01)
    assert world['state']['seconds'] == paused_seconds
    press(physics, 'Generate Targets')
    assert len(addon.reference_display.points(physics.reference_collection, physics.runtime_id)) == 11
    press(physics, 'Clear Targets')
    assert not addon.reference_display.points(physics.reference_collection, physics.runtime_id)
    press(physics, 'Reset')
    wait_for(lambda: world['state']['seconds'] == 0., tick=pump)
    host.flow.release(apply.runtime_id)
    assert apply.runtime_id not in host.flow._owners
    assert difference(original_source, basis(source)) < 1e-6
    saved = OUTPUT / 'native-paused.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    client.process.wait(timeout=8)
    assert client.closed and client.process.returncode == 0
    assert not addon.runtime._worlds and not host.flow._owners
    tree = bpy.data.node_groups['Native PhysX host pipeline']
    physics = next(node for node in tree.nodes if node.bl_idname == addon.data.PHYSICS)
    actor = next(node for node in tree.nodes if node.bl_idname == addon.data.SKELETON)
    trigger = next(node for node in tree.nodes if node.bl_idname == 'YUGUS_NODE_button_trigger')
    assert not addon.runtime.play_requested(physics)
    assert scene is not bpy.context.scene
    scene = bpy.context.scene
    assert scene.frame_current == fixed_frame
    # Starting after a saved load is a deliberate event; it launches one fresh worker.
    press(physics, 'Play')
    fresh = active_state(physics)
    clients.append(fresh['client'])
    wait_for(lambda: fresh['state'].get('ready') and fresh['state'].get('seconds', 0.) > .1, tick=pump)
    assert fresh['client'].process.pid != client.process.pid
    addon.unregister()
    for candidate in clients:
        candidate.process.wait(timeout=8)
        assert candidate.closed and candidate.process.returncode == 0
    assert_clean(addon, host)
    yugo.unregister()
    assert not host.flow._owners
    finish('host_pipeline', generated_skeleton=True, private_avatar_fixture=False,
           real_linked_play_pause_reset=True, worker_from_selected_package=True,
           typed_motion_bind_apply=True, updated_event_applies_pose=True,
           source_unchanged_without_apply=True, rigid_source_unchanged=True,
           apply_owner_released=True, fixed_frame=fixed_frame,
           load_stops_worker_and_requires_manual_start=True,
           unload_workers_returncode=[candidate.process.returncode for candidate in clients])
finally:
    addon.unregister()
    yugo.unregister()
    for candidate in clients:
        candidate.close()
        candidate.process.wait(timeout=8)
