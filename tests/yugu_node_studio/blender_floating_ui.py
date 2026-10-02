"""Real Studio events, native display, shared ownership and lifecycle recovery."""
import importlib
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio, output_directory

addon = load_node_studio()
from yugu_node_studio import extensions as api, flow, scheduler, workflow, workflow_ui
from yugu_node_studio.modules import floating_ui
from yugu_node_studio.modules.floating_ui import adapter
from yugu_node_studio.modules.floating_ui.service import FloatingUIService, OWNER, ROLE

with RestrictBlend():
    addon.register()
assert "floating_ui" in api.registered_modules()
assert not api.lifecycle_errors(), api.lifecycle_errors()


def tagged():
    return [item for database in (bpy.data.objects, bpy.data.collections,
                                 bpy.data.curves, bpy.data.meshes, bpy.data.materials, bpy.data.images)
            for item in database if item.get(adapter.STUDIO_TAG)]


class Graph:
    def __init__(self):
        self.tree, self.source, self.display = floating_ui.create_example(bpy.context)
        self.display.move_duration = 1
        self.display.max_messages = 3
        self.scene = bpy.context.scene
        self.template, self.anchor = self.display.template, self.display.anchor
        self.now = time.monotonic()

    def fire(self, key="one", text="Immutable message", **extra):
        payload = {"id": key, "text": text, "author": "Viewer", **extra}
        rid = flow.emit_event(self.tree, self.source, "Message", payload, now=self.now)
        assert workflow.status(rid)["state"] == "end", workflow.status(rid)
        return rid

    def service(self):
        return floating_ui.get_service(self.display, self.scene)

    def close(self):
        api.cleanup("test")
        flow.clear()
        bpy.data.node_groups.remove(self.tree)
        assert not api.claimed_transforms(), api.claimed_transforms()
        assert not tagged(), [item.name for item in tagged()]


# Real events complete immediately; native rows retain their copied payload and
# static layout. One shared scheduler animates every display after End.
graph = Graph()
original = {obj.name: (obj.data, obj.matrix_basis.copy()) for obj in graph.template.objects}
end = graph.tree.nodes.new("YUGUS_NODE_test_action")
end.label = "Floating inserted"
end.seconds = 0
graph.tree.links.new(graph.display.outputs["End"], end.inputs["Enter"])
graph.fire()
service = graph.service()
row = service.instances["one"]
assert row.message_object.data.body == "Immutable message"
assert row.message_object.data != service.message_source.data
assert row.objects[service.author_source.name].data.body == "Viewer"
assert service.panel_root.parent == graph.anchor
assert any(item["label"] == "Floating inserted" and item["state"] == "end" for item in workflow.logs)
assert not workflow.running_for(graph.tree, graph.display)
owner = api.node_owner("floating_ui", graph.display, graph.scene)
claims = api.claimed_transforms(owner)
assert {item["session_uid"] for item in claims} == {obj.session_uid for obj in
    (service.panel_root, row.root, *row.objects.values())}
assert graph.template.get(OWNER) is None and graph.anchor.get(OWNER) is None
for obj in graph.template.objects:
    assert obj.data == original[obj.name][0] and obj.matrix_basis == original[obj.name][1]
graph.fire("one", "Duplicate must not replace the snapshot")
assert len(service.instances) == 1 and row.message_object.data.body == "Immutable message"
graph.now += .1
graph.fire("two", "Second")
clock = adapter.time
try:
    adapter.time = SimpleNamespace(monotonic=lambda: graph.now + .5)
    scheduler.run_motion_once()
    halfway = row.root.location.y
    assert math.isclose(halfway, graph.display.spacing * .5, abs_tol=1e-5), halfway
    graph.now += .5
    graph.fire("three", "Interrupted movement")
    assert math.isclose(row.root.location.y, halfway, abs_tol=1e-5)
    adapter.time = SimpleNamespace(monotonic=lambda: graph.now + 1)
    scheduler.run_motion_once()
    assert math.isclose(row.root.location.y, graph.display.spacing * 2, abs_tol=1e-5)
    graph.now += 1
    graph.fire("four", "Bounded queue")
    assert set(service.instances) == {"two", "three", "four"}
finally:
    adapter.time = clock

# Invalid input routes Failed and leaves the valid prior queue intact.
before = tuple(service.instances)
for payload in ({"text": 7}, {"text": "Bad local image", "avatar_path": str(output_directory() / "missing-avatar.png")}):
    rid = flow.emit_event(graph.tree, graph.source, "Message", payload, now=graph.now)
    assert workflow.status(rid)["state"] == "failed", workflow.status(rid)
    assert tuple(service.instances) == before
assert not api.lifecycle_errors()
graph.close()
print("FLOATING_NATIVE_QUEUE_OK")

graph = Graph()
rid = flow.emit_event(graph.tree, graph.source, "Message", {
    "text": "Bad first image", "avatar_path": str(output_directory() / "missing-avatar.png")}, now=graph.now)
assert workflow.status(rid)["state"] == "failed" and graph.service() is None
assert not tagged() and not api.claimed_transforms()
graph.close()

# The source button emits a new UUID each time; local avatar files are copied
# into per-row shaders without mutating the template's placeholder image.
graph = Graph()
image = bpy.data.images.new("Test generated avatar", 2, 2)
image.pixels = [1, .15, .1, 1] * 4
avatar_path = output_directory() / "floating-avatar.png"
image.filepath_raw, image.file_format = str(avatar_path), "PNG"
image.save()
graph.source.text, graph.source.author, graph.source.avatar_path = "Button message", "Tester", str(avatar_path)
first = floating_ui.send_message(graph.source, graph.scene, now=graph.now)
second = floating_ui.send_message(graph.source, graph.scene, now=graph.now)
assert workflow.payload(first)["id"] != workflow.payload(second)["id"]
assert len(graph.service().instances) == 2
template_image = graph.service().icon_source.active_material.node_tree.nodes["Avatar"].image
for row in graph.service().instances.values():
    copied = row.icon_object.active_material.node_tree.nodes["Avatar"].image
    assert copied != template_image and Path(copied.filepath).resolve() == avatar_path.resolve()
assert bpy.ops.yugu_node_studio.floating_ui_clear(tree_name=graph.tree.name, node_name=graph.display.name) == {"FINISHED"}
assert graph.service() is None and not api.claimed_transforms()
graph.fire()
clear = graph.tree.nodes.new(adapter.SOURCE_ID)
graph.tree.links.new(clear.outputs["Message"], graph.display.inputs["Clear"])
flow.emit_event(graph.tree, clear, "Message", {}, now=graph.now)
assert graph.service() is None and not tagged()
graph.close()
print("FLOATING_SOURCE_CLEAR_OK")

# Appending an explicitly chosen local Collection uses the same display action
# and retires its previous configuration without modifying the preset source.
graph = Graph()
graph.fire()
preset, _parts = floating_ui.make_example_template("Floating portable preset")
preset_path = output_directory() / "floating-template.blend"
bpy.data.libraries.write(str(preset_path), {preset})
graph.display.template_path, graph.display.collection_name = str(preset_path), preset.name
assert graph.service() is None
assert bpy.ops.yugu_node_studio.floating_ui_import(
    tree_name=graph.tree.name, node_name=graph.display.name) == {"FINISHED"}
assert graph.display.template != preset and graph.display.template.library is None
graph.fire("imported", "Imported native template")
assert graph.service().instances["imported"].message_object.data.body == "Imported native template"
assert next(obj for obj in preset.objects if obj.get(ROLE) == "message").data.body == "Your message appears here"
graph.close()
print("FLOATING_PRESET_IMPORT_OK")

# Independent displays retain separate queues and claims. The template/anchor
# remain available to other writers; generated objects reject competing claims.
graph = Graph()
other = graph.tree.nodes.new(adapter.DISPLAY_ID)
other.template, other.anchor = graph.template, graph.anchor
graph.tree.links.new(graph.source.outputs["Message"], other.inputs["Enter"])
graph.fire()
second_service = floating_ui.get_service(other, graph.scene)
assert second_service is not graph.service() and len(second_service.instances) == 1
api.claim_transforms("external-anchor", [graph.anchor])
try:
    api.claim_transforms("conflicting-generated-writer", [graph.service().panel_root])
except api.ResourceConflict:
    pass
else:
    raise AssertionError("Another writer claimed Studio generated objects")
api.release_owner("external-anchor")
graph.display.spacing = 2
assert graph.service() is None and floating_ui.get_service(other, graph.scene) is second_service
graph.close()
print("FLOATING_OWNERSHIP_OK")

# A completed insertion has no ticket to watch its graph. The persistent display
# must still reconcile settings, direct/upstream wires, mute, and manual removal.
for mode in ("settings", "direct_link", "upstream_link", "upstream_mute", "output_link", "mute", "node", "object", "template"):
    graph = Graph()
    reroute = None
    if mode.startswith("upstream"):
        graph.tree.links.remove(graph.tree.links[0])
        reroute = graph.tree.nodes.new("NodeReroute")
        upstream = graph.tree.links.new(graph.source.outputs["Message"], reroute.inputs[0])
        graph.tree.links.new(reroute.outputs[0], graph.display.inputs["Enter"])
    graph.fire()
    service = graph.service()
    if mode == "settings":
        graph.display.max_messages = 2
    elif mode == "direct_link":
        graph.tree.links.remove(graph.tree.links[0])
    elif mode == "upstream_link":
        graph.tree.links.remove(upstream)
    elif mode == "upstream_mute":
        graph.source.mute = True
    elif mode == "output_link":
        tail = graph.tree.nodes.new("YUGUS_NODE_flow_end")
        graph.tree.links.new(graph.display.outputs["End"], tail.inputs["Enter"])
    elif mode == "mute":
        graph.display.mute = True
    elif mode == "node":
        graph.tree.nodes.remove(graph.display)
    elif mode == "object":
        bpy.data.objects.remove(service.instances["one"].message_object, do_unlink=True)
    else:
        graph.template.objects.unlink(service.message_source)
    scheduler.run_motion_once()
    assert service.closed and not api.claimed_transforms(), mode
    assert not tagged(), mode
    graph.close()
print("FLOATING_RECONCILIATION_OK")

# Cards edit and execute the same real Display node. The F3 operator creates a
# normal Studio graph and a wholly generated, editable example layout.
definition = workflow_ui.new_definition("Floating cards")
view = workflow_ui.cards_from_graph(definition)
card = workflow_ui.add_card(definition, view.chain, adapter.DISPLAY_ID)
assert card.bl_idname == adapter.DISPLAY_ID and workflow_ui.cards_from_graph(definition).supported
bpy.data.node_groups.remove(definition)
assert bpy.ops.yugu_node_studio.floating_ui_example() == {"FINISHED"}
tree = bpy.context.scene.yugu_node_studio_tree
source = next(node for node in tree.nodes if node.bl_idname == adapter.SOURCE_ID)
display = next(node for node in tree.nodes if node.bl_idname == adapter.DISPLAY_ID)
floating_ui.send_message(source, bpy.context.scene)
assert len(floating_ui.get_service(display, bpy.context.scene).instances) == 1
api.cleanup("test")
bpy.data.node_groups.remove(tree)
print("FLOATING_EDITOR_OK")

# Real Undo/Redo replaces the saved RNA: no runtime lease or generated row may
# survive either direction, while template and graph remain editable.
graph = Graph()
tree_name = graph.tree.name
bpy.context.preferences.edit.use_global_undo = True
assert bpy.ops.ed.undo_push(message="Floating before message") == {"FINISHED"}
graph.fire()
assert tagged() and api.claimed_transforms()
assert bpy.ops.ed.undo_push(message="Floating after message") == {"FINISHED"}
assert bpy.ops.ed.undo() == {"FINISHED"}
assert not tagged() and not api.claimed_transforms()
assert bpy.ops.ed.redo() == {"FINISHED"}
assert not tagged() and not api.claimed_transforms()
tree = bpy.data.node_groups[tree_name]
source = next(node for node in tree.nodes if node.bl_idname == adapter.SOURCE_ID)
display = next(node for node in tree.nodes if node.bl_idname == adapter.DISPLAY_ID)
floating_ui.send_message(source, bpy.context.scene)
assert floating_ui.get_service(display, bpy.context.scene)
api.cleanup("test")
bpy.data.node_groups.remove(tree)
print("FLOATING_UNDO_REDO_OK")

# Recovery is Studio-specific: a standalone PR #12 preview (generic OWNER tag)
# coexists across Undo/Redo, actual file load, and restricted re-registration.
graph = Graph()
preview = FloatingUIService(graph.scene, graph.template, anchor=graph.anchor)
preview.push({"id": "standalone", "text": "Keep standalone preview"}, now=graph.now)
preview_root_name = preview.panel_root.name
preview_output_name = preview.output.name
graph.fire()
addon._undo_pre(None)
assert not tagged() and not api.claimed_transforms() and scheduler.is_suspended()
addon._undo_post(None)
assert not scheduler.is_suspended() and preview.is_valid()
graph.fire("after-undo")
filename = output_directory() / "floating-lifecycle.blend"
tree_name, display_name, source_name = graph.tree.name, graph.display.name, graph.source.name
bpy.ops.wm.save_as_mainfile(filepath=str(filename))
bpy.ops.wm.open_mainfile(filepath=str(filename))
assert not tagged() and not api.claimed_transforms()
assert bpy.data.objects.get(preview_root_name) and bpy.data.collections.get(preview_output_name)
assert not bpy.data.objects[preview_root_name].get(adapter.STUDIO_TAG)
tree = bpy.data.node_groups[tree_name]
source, display = tree.nodes[source_name], tree.nodes[display_name]
floating_ui.send_message(source, bpy.context.scene)
assert floating_ui.get_service(display, bpy.context.scene)
assert tagged()
with RestrictBlend():
    addon.unregister()
assert not api.claimed_transforms() and tagged(), "Restricted cleanup must retain only tagged saved data for recovery"
with RestrictBlend():
    addon.register()
assert tagged(), "First-enable recovery must wait for scene access"
scheduler.run_motion_once()
assert not tagged() and not api.claimed_transforms()
assert bpy.data.objects.get(preview_root_name) and bpy.data.collections.get(preview_output_name)
assert not api.lifecycle_errors(), api.lifecycle_errors()
tree = bpy.data.node_groups[tree_name]
source, display = tree.nodes[source_name], tree.nodes[display_name]
floating_ui.send_message(source, bpy.context.scene)
assert floating_ui.get_service(display, bpy.context.scene)
addon.unregister()
assert not tagged() and not api.claimed_transforms()
with RestrictBlend():
    addon = importlib.reload(addon)
    addon.register()
api = importlib.import_module("yugu_node_studio.extensions")
floating_ui = importlib.import_module("yugu_node_studio.modules.floating_ui")
scheduler = importlib.import_module("yugu_node_studio.scheduler")
scheduler.run_motion_once()
assert not api.lifecycle_errors()
tree = bpy.data.node_groups[tree_name]
source, display = tree.nodes[source_name], tree.nodes[display_name]
floating_ui.send_message(source, bpy.context.scene)
assert len(floating_ui.get_service(display, bpy.context.scene).instances) == 1
with RestrictBlend():
    addon.unregister()
assert not api.claimed_transforms()
print("YUGU_FLOATING_UI_OK")
