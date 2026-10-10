"""Live YugoPhysics regressions in a disposable factory-startup Blender.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1.
The fusion runner selects complete source or the delivered ZIP via bootstrap.
No user scene, running Blender session, or persistent settings are changed.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import bpy
import numpy as np

from _bootstrap import ROOT, load_node, output_directory

TREE = "YUGUS_NT_humanoid_motion"

addon = load_node()
_package_directory = None


class ControlsLayout:
    def __init__(self):
        self.operators, self.properties = [], []

    def row(self, **_kwargs):
        return self

    def label(self, **_kwargs):
        pass

    def prop(self, _node, name, **_kwargs):
        self.properties.append(name)

    def operator(self, identifier, **kwargs):
        operator = SimpleNamespace(identifier=identifier, text=kwargs.get("text", ""))
        self.operators.append(operator)
        return operator


def body(name, location, *, passive=False, size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.rigidbody.object_add()
    obj.rigid_body.type = "PASSIVE" if passive else "ACTIVE"
    obj.rigid_body.collision_shape = "BOX"
    obj.rigid_body.linear_damping = 0.0
    obj.rigid_body.angular_damping = 0.0
    obj.rigid_body.friction = 0.5
    return obj


def wire(tree, first, output, second, input_name):
    return tree.links.new(first.outputs[output], second.inputs[input_name])


def state(solver):
    result = addon.physics._worlds.get(str(solver.runtime_id))
    assert result is not None, (solver.state_status, solver.last_summary)
    assert result["status"] == "Playing", (solver.state_status, solver.last_summary)
    return result


def audit_model(solver, now):
    """Force the model check for isolated state-transfer cases.

    Ordinary model polling is 2 Hz; its timing is covered separately by
    blender_solver_model_rate.py rather than these same-time rebuild cases.
    """
    return addon.physics._synchronize(solver, bpy.context.scene, now)


def free_state(world, obj):
    identity = next(item.body_id for item in world.description.bodies if item.object_name == obj.name)
    body_id = world._rigid_ids[identity]
    joint = int(world.model.body_jntadr[body_id])
    qpos, qvel = int(world.model.jnt_qposadr[joint]), int(world.model.jnt_dofadr[joint])
    return (world.data.qpos[qpos:qpos + 7].copy(), world.data.qvel[qvel:qvel + 6].copy(),
            world.tick, float(world.data.time))


def same_state(before, after):
    np.testing.assert_allclose(after[0], before[0], rtol=0, atol=1e-10)
    np.testing.assert_allclose(after[1], before[1], rtol=0, atol=1e-10)
    assert after[2:] == before[2:], (before[2:], after[2:])


def live_world():
    scene = bpy.context.scene
    scene.use_gravity = True
    scene.gravity = (0.0, 0.0, -9.81)
    cube = body("LiveFallingBody", (0, 0, 10))
    boundary = body("LiveMovingBoundary", (10, 0, 0), passive=True)
    tree = bpy.data.node_groups.new("PhysicsLive", TREE)
    objects = tree.nodes.new("YUGUS_NODE_objects_value")
    objects.objects.add().object = cube
    objects.objects.add().object = boundary
    converter = tree.nodes.new(addon.physics.RIGID)
    solver = tree.nodes.new(addon.physics.SOLVER)
    clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
    apply = tree.nodes.new(addon.object_transforms.APPLY)
    wire(tree, objects, "Objects", converter, "Objects")
    description_link = wire(tree, converter, "Physics Description", solver, "Physics Description")
    wire(tree, solver, "Object Transforms", apply, "Object Transforms")
    apply_link = wire(tree, clock, "Trigger", apply, "Enter")
    try:
        assert [socket.name for socket in solver.inputs] == ["Physics Description", "Reset", "Stop"], [socket.name for socket in solver.inputs]
        assert [socket.name for socket in solver.outputs] == [
            "Object Transforms", "Bone Transforms", "Simulation Data"]
        central = ControlsLayout()
        solver.draw_buttons(bpy.context, central)
        assert central.properties == ["enabled"], central.properties
        assert not central.operators, central.operators
        inline = ControlsLayout()
        for name in ("Reset", "Stop"):
            solver.inputs[name].draw(bpy.context, inline, solver, name)
        assert [op.operation for op in inline.operators] == ["RESET", "STOP"]
        assert all(op.tree_name == tree.name and op.node_name == solver.name
                   for op in inline.operators)

        addon.physics.tick(now=0.0)
        first = state(solver)
        original_world = first["world"]
        assert original_world.tick == 0
        addon.physics.tick(now=0.25)
        assert state(solver)["world"] is original_world
        falling = free_state(original_world, cube)
        assert falling[0][2] < 9.8 and falling[1][2] < -2.0, falling

        # Explicit model checks read descriptions; unchanged content and poses do
        # not compile another world or discard the body's velocity.
        for _ in range(3):
            audit_model(solver, 0.25)
            addon.physics.tick(now=0.25)
            assert state(solver)["world"] is original_world
        same_state(falling, free_state(original_world, cube))
        boundary.location.x += 1.0
        bpy.context.view_layer.update()
        addon.physics.tick(now=0.25)
        assert state(solver)["world"] is original_world
        same_state(falling, free_state(original_world, cube))

        initial_revision = state(solver)["description"].revision
        for index in range(1, 9):
            boundary.rotation_euler = (0.13 * index, 0.27 * index, 0.41 * index)
            bpy.context.view_layer.update()
            audit_model(solver, 0.25)
            addon.physics.tick(now=0.25)
            current = state(solver)
            before_fields = asdict(next(item for item in original_world.description.bodies if item.object_name == boundary.name))
            after_fields = asdict(next(item for item in current["description"].bodies if item.object_name == boundary.name))
            difference = {key: (value, after_fields[key]) for key, value in before_fields.items()
                          if value != after_fields[key] and key not in {"position", "quaternion"}}
            assert current["world"] is original_world, (index, difference)
            assert current["description"].revision == initial_revision
            same_state(falling, free_state(original_world, cube))

        boundary.scale = (1.25, 1.25, 1.25)
        bpy.context.view_layer.update()
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        scaled = state(solver)
        assert scaled["world"] is not original_world and original_world.closed
        assert scaled["description"].revision != initial_revision
        np.testing.assert_allclose(next(item.size for item in scaled["description"].bodies
                                       if item.object_name == boundary.name), (0.625,) * 3, atol=1e-6)
        same_state(falling, free_state(scaled["world"], cube))
        original_world = scaled["world"]

        cube.rigid_body.mass = 2.5
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        changed = state(solver)
        assert changed["world"] is not original_world and original_world.closed
        same_state(falling, free_state(changed["world"], cube))
        assert next(item for item in changed["description"].bodies
                    if item.object_name == cube.name).mass == 2.5

        # Failed compilation keeps the working world and caches that exact
        # model failure. A different valid revision can recover immediately.
        backend = sys.modules[addon.__name__ + ".physics_backend"]
        native_world = backend.World
        attempts = []
        def reject_compile(*_args, **_kwargs):
            attempts.append(True)
            raise ValueError("Injected native compile failure")
        backend.World = reject_compile
        try:
            cube.rigid_body.mass = 3.0
            for _ in range(3):
                audit_model(solver, 0.25)
                addon.physics.tick(now=0.25)
                assert state(solver)["world"] is changed["world"]
                same_state(falling, free_state(changed["world"], cube))
            assert len(attempts) == 1, attempts
            assert "Injected native compile failure" in solver.last_summary
        finally:
            backend.World = native_world
        cube.rigid_body.mass = 3.25
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        changed = state(solver)
        same_state(falling, free_state(changed["world"], cube))
        assert not changed.get("input_error")

        # Node and graph display names are editable without stopping runtime.
        stable = changed["world"]
        tree.name, solver.name = "PhysicsLiveRenamed", "LiveSolverRenamed"
        addon.physics.tick(now=0.25)
        assert state(solver)["world"] is stable
        same_state(falling, free_state(stable, cube))

        # A real Blender object copy is eligible only after it reaches the
        # explicit Objects List. Its spawn pose belongs to its new body.
        copy = cube.copy()
        copy.name = "LiveNewInstance"
        scene.collection.objects.link(copy)
        copy.location = (3, 0, 10)
        bpy.context.view_layer.update()
        stable = changed["world"]
        addon.physics.tick(now=0.25)
        assert state(solver)["world"] is stable
        assert copy.name not in state(solver)["world"].snapshot()["object_names"]
        objects.objects.add().object = copy
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        with_copy = state(solver)["world"]
        assert with_copy is not stable and stable.closed
        same_state(falling, free_state(with_copy, cube))
        np.testing.assert_allclose(free_state(with_copy, copy)[0][:3], (3, 0, 10), atol=1e-10)
        objects.objects.remove(len(objects.objects) - 1)
        bpy.data.objects.remove(copy, do_unlink=True)
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        without_copy = state(solver)["world"]
        assert without_copy is not with_copy and with_copy.closed
        same_state(falling, free_state(without_copy, cube))

        # Newly inserted passive geometry participates in actual native contact.
        floor = body("LiveInsertedFloor", (0, 0, 8), passive=True)
        objects.objects.add().object = floor
        audit_model(solver, 0.25)
        addon.physics.tick(now=0.25)
        same_state(falling, free_state(state(solver)["world"], cube))
        start_tick = state(solver)["world"].tick
        start_height = free_state(state(solver)["world"], cube)[0][2]
        for index in range(1, 91):
            if index <= 12:
                # Recompile every update; active simulation must still advance.
                cube.rigid_body.mass += 0.01
                if index % 3 == 0:
                    spawned = cube.copy()
                    spawned.name = f"LiveContinuousSpawn{index}"
                    scene.collection.objects.link(spawned)
                    spawned.location = (20 + index, 0, 10)
                    objects.objects.add().object = spawned
                    bpy.context.view_layer.update()
            if index <= 12:
                audit_model(solver, 0.25 + index / 60)
            addon.physics.tick(now=0.25 + index / 60)
            if index <= 12:
                current = state(solver)["world"]
                assert current.tick == start_tick + 4 * index, (current.tick, start_tick, index)
                assert free_state(current, cube)[0][2] < start_height
        contact = state(solver)["world"]
        assert contact.data.ncon > 0, contact.snapshot()
        assert 8.9 < free_state(contact, cube)[0][2] < 9.1, free_state(contact, cube)
        scene.gravity = (0, 0, -5)
        addon.physics.tick(now=1.75)
        np.testing.assert_allclose(state(solver)["world"].model.opt.gravity, (0, 0, -5))

        # External event sources consume data independently of Solver outputs.
        addon.flow.emit_event(tree, clock, "Trigger", now=1.75)
        assert str(apply.runtime_id) in addon.object_transforms._owners, apply.last_summary
        assert not cube.rigid_body.enabled and cube.rigid_body.kinematic
        assert abs(cube.location.z - 9.0) < 0.1

        def button(operation):
            assert bpy.ops.yugu_node_studio.physics_action(
                tree_name=tree.name, node_name=solver.name, operation=operation) == {"FINISHED"}

        # Copying an already applied body also copies its recovery markers.
        # The copy keeps its own birth pose when this same Apply acquires it.
        owned_copy = cube.copy()
        owned_copy.name = "LiveCopyOfAppliedBody"
        scene.collection.objects.link(owned_copy)
        owned_copy.location = (4, 0, 12)
        objects.objects.add().object = owned_copy
        bpy.context.view_layer.update()
        audit_model(solver, 1.75)
        addon.physics.tick(now=1.75)
        assert owned_copy.name in state(solver)["world"].snapshot()["object_names"]
        addon.flow.emit_event(tree, clock, "Trigger", now=1.75)
        assert owned_copy.name in addon.physics._rigid_owners
        assert owned_copy.name in addon.object_transforms._owners[str(apply.runtime_id)]["objects"]
        assert not owned_copy.rigid_body.enabled and owned_copy.rigid_body.kinematic

        # Another Apply/world may acquire a new copy without releasing the
        # source's live ownership or borrowing its original authored transform.
        foreign_copy = cube.copy()
        foreign_copy.name = "LiveCopyInAnotherWorld"
        scene.collection.objects.link(foreign_copy)
        foreign_copy.location = (6, 0, 14)
        foreign_converter = tree.nodes.new(addon.physics.RIGID)
        foreign_converter.objects.add().object = foreign_copy
        foreign_solver = tree.nodes.new(addon.physics.SOLVER)
        foreign_apply = tree.nodes.new(addon.object_transforms.APPLY)
        foreign_clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
        wire(tree, foreign_converter, "Physics Description", foreign_solver, "Physics Description")
        wire(tree, foreign_solver, "Object Transforms", foreign_apply, "Object Transforms")
        wire(tree, foreign_clock, "Trigger", foreign_apply, "Enter")
        bpy.context.view_layer.update()
        addon.physics.tick(now=1.75)
        state(foreign_solver)
        addon.flow.emit_event(tree, foreign_clock, "Trigger", now=1.75)
        assert addon.physics._rigid_owners[cube.name]["apply"] == str(apply.runtime_id)
        assert addon.physics._rigid_owners[foreign_copy.name]["apply"] == str(foreign_apply.runtime_id)
        assert not cube.rigid_body.enabled and not foreign_copy.rigid_body.enabled
        assert bpy.ops.yugu_node_studio.physics_action(
            tree_name=tree.name, node_name=foreign_solver.name, operation="STOP") == {"FINISHED"}
        np.testing.assert_allclose(foreign_copy.location, (6, 0, 14), atol=1e-6)
        assert foreign_copy.rigid_body.enabled and not foreign_copy.rigid_body.kinematic
        assert not cube.rigid_body.enabled and cube.rigid_body.kinematic
        assert str(apply.runtime_id) in addon.object_transforms._owners
        for temporary_node in (foreign_solver, foreign_apply, foreign_clock, foreign_converter):
            tree.nodes.remove(temporary_node)
        bpy.data.objects.remove(foreign_copy, do_unlink=True)

        button("STOP")
        assert owned_copy.rigid_body.enabled and not owned_copy.rigid_body.kinematic
        np.testing.assert_allclose(owned_copy.location, (4, 0, 12), atol=1e-6)
        objects.objects.remove(len(objects.objects) - 1)
        bpy.data.objects.remove(owned_copy, do_unlink=True)
        assert str(solver.runtime_id) not in addon.physics._worlds
        assert cube.rigid_body.enabled and not cube.rigid_body.kinematic
        np.testing.assert_allclose(cube.location, (0, 0, 10), atol=1e-6)
        for now in (2.0, 2.1):
            addon.physics.tick(now=now)
            assert str(solver.runtime_id) not in addon.physics._worlds
        button("RESET")
        assert state(solver)["world"].tick == 0
        addon.physics.tick(now=3.0)
        addon.physics.tick(now=3.1)
        assert state(solver)["world"].tick > 0

        # Socket pulses have exactly the same Stop/Reset semantics as clicks.
        tree.links.remove(apply_link)
        stop_link = wire(tree, clock, "Trigger", solver, "Stop")
        addon.flow.emit_event(tree, clock, "Trigger", now=3.1)
        addon.physics.tick(now=3.2)
        assert str(solver.runtime_id) not in addon.physics._worlds
        tree.links.remove(stop_link)
        reset_link = wire(tree, clock, "Trigger", solver, "Reset")
        addon.flow.emit_event(tree, clock, "Trigger", now=3.2)
        assert state(solver)["world"].tick == 0
        tree.links.remove(reset_link)
        wire(tree, clock, "Trigger", apply, "Enter")
        addon.physics.tick(now=4.0)
        addon.physics.tick(now=4.1)
        addon.flow.emit_event(tree, clock, "Trigger", now=4.1)
        assert str(apply.runtime_id) in addon.object_transforms._owners
        solver.enabled = False
        addon.physics.tick(now=4.2)
        assert str(solver.runtime_id) not in addon.physics._worlds
        assert str(apply.runtime_id) not in addon.object_transforms._owners
        assert cube.rigid_body.enabled and not cube.rigid_body.kinematic
        np.testing.assert_allclose(cube.location, (0, 0, 10), atol=1e-6)
        solver.enabled = True
        addon.physics.tick(now=5.0)
        state(solver)
        tree.links.remove(description_link)
        addon.physics.tick(now=5.1)
        assert str(solver.runtime_id) not in addon.physics._worlds
        return {"auto_play": True, "unchanged_revision_no_compile": True,
                "mass_and_real_instance_continuity": True, "continuous_spawn_advances_time": True,
                "rename_preserves_world": True, "compile_failure_and_recovery": True,
                "copied_leases_preserve_each_apply": True,
                "inserted_native_contact": True,
                "button_and_event_stop_reset": True, "external_apply": True,
                "disable_and_disconnect_cleanup": True}
    finally:
        addon.physics.clear()
        addon.flow.clear()
        bpy.data.node_groups.remove(tree)


def spring_transfer():
    """Exercise retained Spring native states against a real Blender armature."""
    World = importlib.import_module(addon.__name__ + ".physics_backend").World
    describe_spring = importlib.import_module(addon.__name__ + ".physics_inputs").describe_spring
    rig_data = bpy.data.armatures.new("LiveSpringRig")
    rig = bpy.data.objects.new("LiveSpringRig", rig_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = rig_data.edit_bones.new("Tail")
    bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.location = (20, 0, 0)
    data = addon.data.SpringData("0.0", ({"joints": ({"bone": "Tail"},),
        "stiffness": 1.0, "drag_force": 0.5, "gravity_dir": (0, 0, -1),
        "gravity_power": 0.2, "hit_radius": 0.0},), (), (), rig.name)
    description = describe_spring(data, bpy.context.scene)
    before = World(description)
    after = None
    try:
        before.update_boundaries(bpy.context.scene)
        before.step(40)
        assert np.linalg.norm(before.data.qvel) > 1e-5
        chain = description.springs[0]
        joint = replace(chain.joints[0], stiffness=chain.joints[0].stiffness * 1.1)
        updated = replace(description, springs=(replace(chain, joints=(joint,)),))
        after = World(updated)
        after.update_boundaries(bpy.context.scene)
        after.transfer_state_from(before)
        np.testing.assert_allclose(after.data.qpos, before.data.qpos, rtol=0, atol=1e-10)
        np.testing.assert_allclose(after.data.qvel, before.data.qvel, rtol=0, atol=1e-10)
        assert after.tick == before.tick and after.data.time == before.data.time
        after.step(1)
        assert after.tick == 41 and np.all(np.isfinite(after.data.qpos))
        return {"spring_position_velocity_time_retained": True}
    finally:
        before.close()
        if after is not None:
            after.close()
        bpy.data.objects.remove(rig, do_unlink=True)
        bpy.data.armatures.remove(rig_data)


def saved_socket_migration():
    tree = bpy.data.node_groups.new("PhysicsSocketMigration", TREE)
    tree.use_fake_user = True
    solver = tree.nodes.new(addon.physics.SOLVER)
    solver.name = "MigratedSolver"
    reset = tree.nodes.new("YUGUS_NODE_hz_trigger")
    reset.name = "ResetSource"
    stop = tree.nodes.new("YUGUS_NODE_hz_trigger")
    stop.name = "StopSource"
    play = tree.nodes.new("YUGUS_NODE_hz_trigger")
    apply = tree.nodes.new(addon.object_transforms.APPLY)
    # Recreate old order while keeping the old Reset/Stop RNA socket IDs.
    solver.inputs.new(addon.flow.EVENT, "Play")
    solver.inputs.move(3, 1)
    solver.inputs.move(3, 2)
    solver.outputs.new(addon.flow.EVENT, "Updated")
    solver.outputs.move(3, 0)
    wire(tree, reset, "Trigger", solver, "Reset")
    wire(tree, stop, "Trigger", solver, "Stop")
    wire(tree, play, "Trigger", solver, "Play")
    wire(tree, solver, "Updated", apply, "Enter")
    wire(tree, solver, "Object Transforms", apply, "Object Transforms")
    identifiers = {name: solver.inputs[name].identifier for name in ("Reset", "Stop")}
    output_id = solver.outputs["Object Transforms"].identifier
    addon.nodes.ensure_current_sockets()
    assert [socket.name for socket in solver.inputs] == ["Physics Description", "Reset", "Stop"], [socket.name for socket in solver.inputs]
    assert "Updated" not in solver.outputs and "Play" not in solver.inputs
    assert all(solver.inputs[name].identifier == identifier for name, identifier in identifiers.items())
    assert len(tree.links) == 3
    temporary_root = output_directory()
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yugo_live_saved_", dir=temporary_root) as directory:
        path = Path(directory) / "physics_sockets.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
    tree = bpy.data.node_groups["PhysicsSocketMigration"]
    solver = tree.nodes["MigratedSolver"]
    assert [socket.name for socket in solver.inputs] == ["Physics Description", "Reset", "Stop"], [socket.name for socket in solver.inputs]
    assert {name: solver.inputs[name].identifier for name in identifiers} == identifiers
    assert solver.outputs["Object Transforms"].identifier == output_id
    assert {(link.from_node.name, link.to_socket.name) for link in tree.links
            if link.to_node == solver} == {("ResetSource", "Reset"), ("StopSource", "Stop")}
    assert len(tree.links) == 3
    bpy.data.node_groups.remove(tree)
    return {"reset_stop_links_and_ids_saved": True, "obsolete_play_updated_removed": True}


addon.register()
try:
    metrics = {"live": live_world(), "spring": spring_transfer(), "migration": saved_socket_migration()}
    print("YUGO PHYSICS LIVE PASSED " + json.dumps(metrics, sort_keys=True))
finally:
    addon.unregister()
    assert not addon.physics._worlds and not addon.physics._rigid_owners
    assert not addon.object_transforms._owners
    assert not addon.scheduler._motion_callbacks and not addon.scheduler._system_callbacks
    if _package_directory is not None:
        _package_directory.cleanup()
