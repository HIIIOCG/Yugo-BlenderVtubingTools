"""Real Blender tests for exclusive physics ownership and action semantics.

Run in a separate --factory-startup background process with task-local profiles.
All test objects are synthetic; no live avatar or received original is touched.
"""

from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
from yugo_physics_lab import service


def close(left, right, tolerance=1e-4):
    return (Vector(left) - Vector(right)).length <= tolerance


def expect_error(action, text):
    try:
        action()
    except (ValueError, RuntimeError) as error:
        assert text.lower() in str(error).lower(), (text, str(error))
    else:
        raise AssertionError(f"Expected error containing {text!r}")


class Fixture:
    def __init__(self, count=1):
        service.clear()
        self.scene = bpy.context.scene
        self.objects = []
        self.meshes = []
        self.collections = []
        self.bodies = self.collection("Physics test bodies")
        self.colliders = self.collection("Physics test colliders")
        self.left = self.empty("Physics test left anchor", (0, 0, 3))
        self.right = self.empty("Physics test right anchor", (3, 0, 3))
        self.items = [self.empty(f"Physics test body {index}", (index * 2, 0, 3), self.bodies)
                      for index in range(count)]
        self.settings = SimpleNamespace(bodies=self.bodies, colliders=self.colliders,
            left_anchor=self.left, right_anchor=self.right, gravity=(0, 0, 0),
            use_floor=False, radius=.3, mass=1., damping=0., angular_damping=0.,
            friction=.6, restitution=.25, max_speed=30., grab_radius=1.5,
            grab_strength=120., grab_damping=22., grab_max_force=250.,
            recall_strength=80., recall_damping=18., recall_max_force=200.,
            recall_timeout=8., recall_hold=True, recall_orientation=True)
        bpy.context.view_layer.update()

    def collection(self, name):
        collection = bpy.data.collections.new(name)
        self.scene.collection.children.link(collection)
        self.collections.append(collection)
        return collection

    def empty(self, name, position, collection=None):
        obj = bpy.data.objects.new(name, None)
        (collection or self.scene.collection).objects.link(obj)
        obj.location = position
        self.objects.append(obj)
        return obj

    def cube(self, name, position, scale):
        mesh = bpy.data.meshes.new(name + " mesh")
        coordinates = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        mesh.from_pydata(coordinates, [], [(0, 1, 3, 2), (4, 6, 7, 5),
                                         (0, 4, 5, 1), (2, 3, 7, 6),
                                         (0, 2, 6, 4), (1, 5, 7, 3)])
        obj = bpy.data.objects.new(name, mesh)
        self.colliders.objects.link(obj)
        obj.location = position
        obj.scale = scale
        self.meshes.append(mesh)
        self.objects.append(obj)
        bpy.context.view_layer.update()
        return obj

    def start(self):
        return service.start(self.scene, self.settings)

    def advance(self, frames):
        for _index in range(frames):
            service.update(1 / 60)
            assert service.active(), service.status()

    def close(self):
        service.clear()
        for obj in self.objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        for mesh in self.meshes:
            bpy.data.meshes.remove(mesh)
        for collection in self.collections:
            bpy.data.collections.remove(collection)


def timeline_and_force_test():
    fixture = Fixture()
    try:
        body = fixture.items[0]
        body["yugo_physics_mass"] = 2.
        body["yugo_physics_radius"] = .4
        frame = fixture.scene.frame_current
        fixture.start()
        expect_error(fixture.start, "already running")
        service.impulse(body, (2, 0, 0), (0, 0, 1))
        assert close(service.body_state(body)["velocity"], (1, 0, 0))
        service.force(body, (2, 0, 0))
        fixture.advance(30)
        first = service.body_state(body)
        fixture.advance(30)
        second = service.body_state(body)
        assert second["velocity"][0] > first["velocity"][0] + .45
        assert second["angular_velocity"][2] > 1
        assert second["position"][0] > 1
        assert fixture.scene.frame_current == frame, "Physics advanced the Blender timeline"
        service.force(body, (0, 0, 0))
        saved_velocity = service.body_state(body)["velocity"]
        fixture.advance(10)
        assert close(service.body_state(body)["velocity"], saved_velocity, .001)
        current = body.matrix_world.copy()
        service.stop()
        service.update(1)
        assert not service.active() and close(body.matrix_world.translation, current.translation)
        service.reset()
        assert not service.active() and close(body.matrix_world.translation, (0, 0, 3))
        assert close(service.body_state(body)["velocity"], (0, 0, 0))
        assert close(service.body_state(body)["angular_velocity"], (0, 0, 0))
        assert close(service.body_state(body)["force"], (0, 0, 0))
    finally:
        fixture.close()


def grab_release_recall_test():
    fixture = Fixture(2)
    try:
        fixture.start()
        first, second = fixture.items
        assert service.grab(fixture.left) == first.name
        expect_error(lambda: service.grab(fixture.left), "already holding")
        fixture.left.location.x = 2
        bpy.context.view_layer.update()
        fixture.advance(20)
        held = service.body_state(first)
        assert held["position"][0] > .4, held
        velocity = held["velocity"]
        assert service.release(fixture.left) == (first.name,)
        assert close(service.body_state(first)["velocity"], velocity), "Release discarded velocity"
        position = service.body_state(first)["position"]
        service.recall(first)
        assert close(service.body_state(first)["position"], position), "Recall teleported at start"
        fixture.advance(300)
        state = service.body_state(first)
        assert state["recall_state"] == "settled", state
        assert close(state["position"], state["home_position"], .08), state
        service.cancel_recall(first)
        assert service.body_state(first)["recall_state"] == "idle"
        # A separate anchor can own another body without sharing velocity state.
        fixture.right.location = second.matrix_world.translation
        bpy.context.view_layer.update()
        assert service.grab(fixture.right) == second.name
        service.reset()
        assert service.active()
        assert all(service.body_state(item)["grab_anchor"] is None for item in fixture.items)
    finally:
        fixture.close()


def floor_and_box_collision_test():
    fixture = Fixture()
    try:
        fixture.settings.gravity = (0, 0, -9.81)
        fixture.settings.use_floor = True
        fixture.settings.floor_height = 0.
        floor = fixture.cube("Physics test elevated box", (0, 0, 1), (2, 2, .2))
        floor.rotation_euler.z = .3
        bpy.context.view_layer.update()
        fixture.start()
        fixture.advance(300)
        height = service.body_state(fixture.items[0])["position"][2]
        assert height >= 1.45, height
        assert fixture.scene.frame_current == 1
        floor.location.x += 1
        bpy.context.view_layer.update()
        previous = fixture.items[0].matrix_world.copy()
        service.update(1 / 60)
        assert not service.active() and "static collider moved" in service.status()
        assert close(fixture.items[0].matrix_world.translation, previous.translation)
    finally:
        fixture.close()


def writer_identity_and_scene_test():
    fixture = Fixture()
    extra_scene = None
    try:
        fixture.start()
        body = fixture.items[0]
        body.location.x = 9
        bpy.context.view_layer.update()
        service.update(1 / 60)
        assert not service.active() and "writer" in service.status()
        assert close(body.matrix_world.translation, (9, 0, 3)), "Guard overwrote user edit"
        service.reset()
        fixture.start()
        original_name = body.name
        bpy.data.objects.remove(body, do_unlink=True)
        replacement = fixture.empty(original_name, (7, 0, 3), fixture.bodies)
        bpy.context.view_layer.update()
        service.update(1 / 60)
        assert not service.active() and "replaced" in service.status()
        assert close(replacement.matrix_world.translation, (7, 0, 3))
        service.clear()
        fixture.items = [replacement]
        fixture.start()
        extra_scene = bpy.data.scenes.new("Physics test other scene")
        bpy.context.window.scene = extra_scene
        service.update(1 / 60)
        assert not service.active() and "Scene changed" in service.status()
        bpy.context.window.scene = fixture.scene
        service.clear()
        expect_error(service.reset, "No physics home")
        assert service.body_state(replacement) is None
    finally:
        if extra_scene is not None:
            bpy.context.window.scene = fixture.scene
            bpy.data.scenes.remove(extra_scene)
        fixture.close()


def unsafe_selection_test():
    fixture = Fixture()
    try:
        body = fixture.items[0]
        body.parent = fixture.left
        expect_error(fixture.start, "no parent")
        body.parent = None
        constraint = body.constraints.new("COPY_LOCATION")
        expect_error(fixture.start, "constraints")
        body.constraints.remove(constraint)
        body.driver_add("location", 0)
        expect_error(fixture.start, "animation or drivers")
        body.driver_remove("location", 0)
        body.animation_data_clear()
        fixture.colliders.objects.link(body)
        expect_error(fixture.start, "different objects")
        fixture.colliders.objects.unlink(body)
        fixture.settings.left_anchor = body
        expect_error(fixture.start, "anchors must be outside")
        fixture.settings.left_anchor = fixture.left
        body["yugo_physics_mass"] = 0
        expect_error(fixture.start, "positive")
        del body["yugo_physics_mass"]
        fixture.start()
        body.constraints.new("COPY_LOCATION")
        service.update(1 / 60)
        assert not service.active() and "constraints" in service.status()
    finally:
        fixture.close()
    fixture = Fixture(33)
    try:
        expect_error(fixture.start, "at most 32")
    finally:
        fixture.close()


def collider_geometry_guard_test():
    fixture = Fixture()
    try:
        collider = fixture.cube("Physics test editable box", (0, 0, 0), (1, 1, 1))
        fixture.start()
        fixture.advance(1)
        collider.data.vertices[0].co.x = -2
        collider.data.update()
        bpy.context.view_layer.update()
        before = fixture.items[0].matrix_world.translation.copy()
        service.update(1 / 60)
        assert not service.active() and "bounding box changed" in service.status(), service.status()
        assert close(fixture.items[0].matrix_world.translation, before)
    finally:
        fixture.close()


def native_rigid_body_guard_test():
    fixture = Fixture()
    try:
        native = fixture.cube("Physics test native rigid body", (0, 0, 3), (.3, .3, .3))
        fixture.colliders.objects.unlink(native)
        fixture.bodies.objects.link(native)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        native.select_set(True)
        bpy.context.view_layer.objects.active = native
        bpy.ops.rigidbody.object_add()
        assert native.rigid_body is not None
        expect_error(fixture.start, "native rigid body")
        bpy.ops.rigidbody.object_remove()
        fixture.scene.collection.objects.link(native)
        fixture.bodies.objects.unlink(native)
        fixture.settings.left_anchor = native
        bpy.ops.rigidbody.object_add()
        expect_error(fixture.start, "anchors must not have native")
        bpy.ops.rigidbody.object_remove()
    finally:
        fixture.close()


def dependency_handler_writer_guard_test():
    fixture = Fixture()
    body = fixture.items[0]

    def competing_writer(_scene, _depsgraph):
        body.location.x = 5.0

    try:
        fixture.start()
        bpy.app.handlers.depsgraph_update_post.append(competing_writer)
        service.impulse(body, (1, 0, 0))
        service.update(1 / 60)
        assert not service.active(), "Competing dependency handler was accepted as the physics writer"
        assert "another writer" in service.status(), service.status()
        assert close(body.matrix_world.translation, (5, 0, 3))
        # Reset must also report the conflict, rather than adopting its output.
        expect_error(service.reset, "another writer")
        assert not service.active() and "another writer" in service.status()
    finally:
        if competing_writer in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(competing_writer)
        fixture.close()


def main():
    print("PHYSICS_LAB_TEST_SOURCE: " + str(Path(service.__file__).resolve()))
    assert Path(service.__file__).resolve() == ROOT / "addons/yugo_physics_lab/service.py"
    for test in (timeline_and_force_test, grab_release_recall_test, floor_and_box_collision_test,
                 writer_identity_and_scene_test, unsafe_selection_test, collider_geometry_guard_test,
                 native_rigid_body_guard_test, dependency_handler_writer_guard_test):
        test()
        print("PHYSICS_SERVICE_PASS: " + test.__name__)
    assert not service.active()
    print("PHYSICS_LAB_SERVICE_OK")


if __name__ == "__main__":
    main()
