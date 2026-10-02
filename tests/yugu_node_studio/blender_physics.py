"""Native Studio physics actions, control attribution, ownership and lifecycle."""

from pathlib import Path
import math
import sys

import bpy
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio, output_directory
addon = load_node_studio()
from yugu_node_studio import extensions as api, flow, workflow, scheduler
from yugu_node_studio.modules import physics

with RestrictBlend():
    addon.register()
assert "physics" in api.registered_modules()


def distance(a, b):
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def state(run_id):
    return workflow.status(run_id)["state"]


class Fixture:
    def __init__(self):
        physics.cleanup("test fixture")
        flow.clear()
        self.example = physics.create_example(bpy.context.scene)
        self.tree = self.example["tree"]
        self.world = self.example["world"]
        self.controls = self.example["controls"]
        self.prop = self.example["prop"]
        self.anchor = self.example["anchor"]
        self.world.colliders = None
        self.world.gravity = (0, 0, 0)
        self.world.use_floor = False
        self.world.damping = 0
        self.world.angular_damping = 0
        self.now = 10.
        self.frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)

    def node(self, name):
        return next(link.to_node for link in self.tree.links if link.from_node == self.controls
                    and link.from_socket.name == name)

    def fire(self, name, payload=None):
        return flow.emit_event(self.tree, self.controls, name, payload=payload, now=self.now)

    def tick(self, frames=1):
        for _ in range(frames):
            self.now += 1 / 60
            physics.motion(bpy.context, 1 / 60)
            api.poll(now=self.now)
        assert (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe) == self.frame

    def start(self):
        assert state(self.fire("Start")) == "end"
        assert physics.service.active()
        assert len(api.claimed_transforms()) == len(self.world.bodies.all_objects)

    def close(self):
        physics.cleanup("fixture teardown")
        flow.clear()
        try:
            bpy.data.node_groups.remove(self.tree)
        except ReferenceError:
            pass
        lab = self.example["collection"]
        collections = list(lab.children) + [lab]
        for obj in list(lab.all_objects):
            mesh = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for collection in collections:
            bpy.data.collections.remove(collection)
        assert not api.claimed_transforms()


def commands_and_state():
    fixture = Fixture()
    try:
        original = tuple(fixture.prop.location)
        assert not physics.service.active(), "Example auto-started"
        # Reading values never simulates or creates output.
        values = flow.PullContext(fixture.tree, bpy.context.scene, fixture.now, 1 / 60).evaluate(fixture.example["state"])
        assert values["Active"] == 0 and not physics.service.active()
        fixture.start()
        fixture.node("Impulse").linear = (2, 0, 0)
        fixture.node("Impulse").angular = (0, 0, .2)
        assert state(fixture.fire("Impulse")) == "end"
        before = physics.service.body_state(fixture.prop)
        assert abs(before["velocity"][0] - 2) < 1e-5
        fixture.tick(10)
        assert fixture.prop.location.x > original[0] + .2
        assert state(fixture.fire("Force")) == "end"
        fixture.tick(10)
        assert physics.service.body_state(fixture.prop)["velocity"][0] > 2.2
        assert state(fixture.fire("Clear Force")) == "end"
        assert physics.service.body_state(fixture.prop)["force"] == (0, 0, 0)
        values = flow.PullContext(fixture.tree, bpy.context.scene, fixture.now, 0).evaluate(fixture.example["state"])
        assert values["Active"] == 1 and values["Speed"] > 2
        saved = tuple(fixture.prop.location)
        assert state(fixture.fire("Stop")) == "end"
        assert not physics.service.active() and not api.claimed_transforms()
        fixture.tick(3)
        assert distance(fixture.prop.location, saved) < 1e-6
        assert state(fixture.fire("Reset")) == "end"
        assert distance(fixture.prop.location, original) < 1e-6
        assert physics.service.body_state(fixture.prop)["velocity"] == (0, 0, 0)
        assert not api.claimed_transforms()
    finally:
        fixture.close()


def grab_release_and_true_completion():
    fixture = Fixture()
    try:
        fixture.start()
        assert state(fixture.fire("Grab")) == "end"
        assert physics.service.body_state(fixture.prop)["grab_anchor"] == fixture.anchor.name
        fixture.anchor.location.x += 1
        bpy.context.view_layer.update()
        fixture.tick(20)
        before = physics.service.body_state(fixture.prop)
        assert before["position"][0] > -.6
        assert state(fixture.fire("Release")) == "end"
        released = physics.service.body_state(fixture.prop)
        assert released["grab_anchor"] is None
        assert released["velocity"] == before["velocity"]
        assert released["angular_velocity"] == before["angular_velocity"]
        rid = fixture.fire("Attract", {"label": "original payload"})
        assert state(rid) == "running", "Attract End emitted when force was installed"
        fixture.tick(1)
        assert state(rid) == "running"
        for _ in range(480):
            fixture.tick()
            if state(rid) != "running":
                break
        assert state(rid) == "end", workflow.status(rid)
        value = physics.service.body_state(fixture.prop)
        assert value["recall_state"] == "settled"
        assert value["recall_elapsed"] >= fixture.node("Attract").settle_time
        assert distance(value["position"], value["home_position"]) <= .04
        assert workflow.status(rid)["payload"] == {"label": "original payload"}
        fixture.tick(20)
        assert physics.service.body_state(fixture.prop)["recall_hold"]
        fixture.fire("Stop Attraction")
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
    finally:
        fixture.close()


def cancel_restart_parallel_and_tokens():
    fixture = Fixture()
    try:
        fixture.start()
        node = fixture.node("Attract")
        first = fixture.fire("Attract")
        assert state(first) == "running"
        # Ignore does not create a second physical return.
        ignored = fixture.fire("Attract")
        assert state(ignored) == "end" and state(first) == "running"
        node.busy_policy = "RESTART"
        # The core cancels a configuration-changed invocation before Restart.
        api.poll(now=fixture.now)
        assert state(first) == "cancelled"
        first = fixture.fire("Attract")
        second = fixture.fire("Attract")
        assert state(first) == "cancelled" and state(second) == "running"
        # A distinct node cannot falsely share the same body's asynchronous End.
        competitor = fixture.tree.nodes.new(physics.YUGUS_NODE_physics_attract.bl_idname)
        competitor.target = fixture.prop
        fixture.tree.links.new(fixture.world.outputs["World"], competitor.inputs["World"])
        fixture.tree.links.new(fixture.controls.outputs["Attract"], competitor.inputs["Enter"])
        # World topology change is caught first; restart a clean captured world.
        fixture.tick()
        assert state(second) in {"cancelled", "failed"}
        fixture.start()
        fixture.fire("Attract")
        assert competitor.last_summary.startswith("Failed"), competitor.last_summary
        fixture.tree.links.remove(next(link for link in fixture.tree.links if link.to_node == competitor and link.to_socket.name == "Enter"))
        fixture.tick()
        assert physics.service.active()
        rid = fixture.fire("Attract")
        # Cancel only selected original run through the established public event.
        cancel_source = fixture.tree.nodes.new(physics.YUGUS_NODE_physics_controls.bl_idname)
        fixture.tree.links.new(cancel_source.outputs["Stop Attraction"], node.inputs["Cancel"])
        # Adding an input cancels the watched action, without stopping the world.
        api.poll(now=fixture.now)
        assert state(rid) == "cancelled"
        assert physics.service.active()
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        rid = fixture.fire("Attract")
        flow.emit_event(fixture.tree, cancel_source, "Stop Attraction", {"target_run_id": rid}, now=fixture.now)
        assert state(rid) == "cancelled" and physics.service.active()
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        # Grab replaces this body's control; its pending attract reports Cancelled.
        rid = fixture.fire("Attract")
        fixture.fire("Grab")
        fixture.tick()
        assert state(rid) == "cancelled"
        assert physics.service.body_state(fixture.prop)["grab_anchor"] == fixture.anchor.name
    finally:
        fixture.close()


def timeout_and_other_body_survives_cancel():
    fixture = Fixture()
    try:
        other = bpy.data.objects.new("Other Studio Physics Prop", None)
        fixture.world.bodies.objects.link(other)
        other.location = (3, 0, .85)
        bpy.context.view_layer.update()
        fixture.start()
        physics.service.impulse(other, (1, 0, 0))
        node = fixture.node("Attract")
        node.strength = 0
        node.seconds = .05
        fixture.node("Impulse").linear = (5, 0, 0)
        fixture.fire("Impulse")
        rid = fixture.fire("Attract")
        fixture.tick(5)
        assert state(rid) == "failed", workflow.status(rid)
        assert "timed out" in workflow.status(rid)["error"]
        assert physics.service.active()
        assert physics.service.body_state(other)["position"][0] > 3
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        node.seconds = 3
        rid = fixture.fire("Attract")
        before = physics.service.body_state(other)
        fixture.fire("Stop Attraction")
        fixture.tick(2)
        assert state(rid) == "cancelled" and physics.service.active()
        after = physics.service.body_state(other)
        assert after["position"][0] > before["position"][0]
        assert after["velocity"] == before["velocity"]
    finally:
        fixture.close()


def ownership_and_invalidation():
    fixture = Fixture()
    try:
        other = bpy.data.objects.new("Atomic claim second prop", None)
        fixture.world.bodies.objects.link(other)
        other.location = (4, 0, 1)
        bpy.context.view_layer.update()
        api.claim_transforms("competing writer", [other])
        rid = fixture.fire("Start")
        assert state(rid) == "failed" and not physics.service.active()
        assert len(api.claimed_transforms("competing writer")) == 1
        assert len(api.claimed_transforms()) == 1, "Start partially claimed the first prop"
        api.release_owner("competing writer")
        fixture.start()
        fixture.prop.location.x = 8
        bpy.context.view_layer.update()
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
        assert abs(fixture.prop.location.x - 8) < 1e-6, "Overwrote competing user edit"
        fixture.start()
        rid = fixture.fire("Attract")
        fixture.world.mute = True
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
        assert state(rid) in {"failed", "cancelled"}
        fixture.world.mute = False
        fixture.start()
        fixture.world.mass = 2
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
        fixture.start()
        rid = fixture.fire("Attract")
        fixture.node("Attract").strength += 1
        api.poll(now=fixture.now)
        assert state(rid) == "cancelled" and physics.service.active()
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        # Removing the World node retires its persistent claims at the next cycle.
        fixture.tree.nodes.remove(fixture.world)
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
    finally:
        fixture.close()


def action_mute_delete_and_target_replacement():
    fixture = Fixture()
    try:
        fixture.start()
        node = fixture.node("Attract")
        rid = fixture.fire("Attract")
        node.mute = True
        api.poll(now=fixture.now)
        assert state(rid) == "cancelled" and physics.service.active()
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        node.mute = False
        rid = fixture.fire("Attract")
        original_name = fixture.prop.name
        fixture.prop.name += " renamed"
        lifecycle_error_count = len(api.lifecycle_errors())
        api.cancel_node(node, "renamed target")
        assert state(rid) == "cancelled"
        assert len(api.lifecycle_errors()) == lifecycle_error_count
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
        fixture.prop.name = original_name
        fixture.start()
        rid = fixture.fire("Attract")
        fixture.tree.nodes.remove(node)
        api.poll(now=fixture.now)
        assert state(rid) == "cancelled" and physics.service.active()
        assert physics.service.body_state(fixture.prop)["recall_state"] == "idle"
        name = fixture.prop.name
        bpy.data.objects.remove(fixture.prop, do_unlink=True)
        replacement = bpy.data.objects.new(name, None)
        fixture.world.bodies.objects.link(replacement)
        replacement.location = (8, 2, 7)
        bpy.context.view_layer.update()
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms()
        assert distance(replacement.location, (8, 2, 7)) < 1e-6
    finally:
        fixture.close()


def saved_scene_and_lifecycle():
    fixture = Fixture()
    try:
        fixture.start()
        rid = fixture.fire("Attract")
        addon._undo_pre(None)
        assert scheduler.is_suspended() and not physics.service.active()
        assert not workflow.history() and not api.claimed_transforms()
        addon._undo_post(None)
        assert not scheduler.is_suspended() and not physics.service.active()
        fixture.start()
        rid = fixture.fire("Attract")
        path = output_directory() / "studio-physics-saved.blend"
        names = {key: value.name for key, value in fixture.example.items()}
        pose = tuple(fixture.prop.location)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        assert not physics.service.active() and not api.claimed_transforms()
        assert not workflow.history()
        assert distance(bpy.data.objects[names["prop"]].location, pose) < 1e-6
        fixture.example = {key: (bpy.data.node_groups[value] if key == "tree" else
                                bpy.data.collections[value] if key == "collection" else
                                bpy.data.objects[value] if key in {"prop", "anchor", "floor"} else
                                bpy.data.node_groups[names["tree"]].nodes[value]) for key, value in names.items()}
        fixture.tree, fixture.world = fixture.example["tree"], fixture.example["world"]
        fixture.controls, fixture.prop = fixture.example["controls"], fixture.example["prop"]
        fixture.anchor = fixture.example["anchor"]
        fixture.start()
        fixture.fire("Attract")
        lifecycle_error_count = len(api.lifecycle_errors())
        with RestrictBlend():
            addon.unregister()
        assert not physics.service.active() and not api.claimed_transforms()
        assert len(api.lifecycle_errors()) == lifecycle_error_count, api.lifecycle_errors()
        with RestrictBlend():
            addon.register()
        assert not physics.service.active()
        # Class registration replaces RNA wrappers; reacquire every native handle.
        fixture.example = {key: (bpy.data.node_groups[value] if key == "tree" else
                                bpy.data.collections[value] if key == "collection" else
                                bpy.data.objects[value] if key in {"prop", "anchor", "floor"} else
                                bpy.data.node_groups[names["tree"]].nodes[value]) for key, value in names.items()}
        fixture.tree, fixture.world = fixture.example["tree"], fixture.example["world"]
        fixture.controls, fixture.prop = fixture.example["controls"], fixture.example["prop"]
        fixture.anchor = fixture.example["anchor"]
    finally:
        fixture.close()


def native_nodes_and_upstream_rewire():
    fixture = Fixture()
    try:
        fixture.tree.nodes.new("NodeFrame")
        reroute = fixture.tree.nodes.new("NodeReroute")
        fixture.tree.links.remove(next(link for link in fixture.tree.links
                                      if link.to_node == fixture.world and link.to_socket.name == "Start"))
        upstream = fixture.tree.links.new(fixture.controls.outputs["Start"], reroute.inputs[0])
        fixture.tree.links.new(reroute.outputs[0], fixture.world.inputs["Start"])
        fixture.start()
        fixture.tick(2)
        assert physics.service.active(), "Native Frame/Reroute stopped the World"
        fixture.tree.links.remove(upstream)
        fixture.tree.links.new(fixture.controls.outputs["Impulse"], reroute.inputs[0])
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms(), "Upstream rewiring kept the persistent World"
        fixture.tree.links.remove(next(link for link in fixture.tree.links if link.to_node == reroute))
        fixture.tree.links.new(fixture.controls.outputs["Start"], reroute.inputs[0])
        fixture.start()
        fixture.controls.mute = True
        fixture.tick()
        assert not physics.service.active() and not api.claimed_transforms(), "Muted upstream source kept the World"
    finally:
        fixture.close()


for test in (commands_and_state, grab_release_and_true_completion, cancel_restart_parallel_and_tokens,
             timeout_and_other_body_survives_cancel, ownership_and_invalidation, native_nodes_and_upstream_rewire,
             action_mute_delete_and_target_replacement, saved_scene_and_lifecycle):
    test()
    print("YUGU_PHYSICS_PASS:", test.__name__)
with RestrictBlend():
    addon.unregister()
assert not physics.service.active() and not api.claimed_transforms()
print("YUGU_PHYSICS_OK")
