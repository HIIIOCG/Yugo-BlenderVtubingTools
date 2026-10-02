"""Synthetic vertical feature: values, snapshots, genuine waits and teardown."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio, output_directory
addon = load_node_studio()
from yugu_node_studio import extensions as api, flow, workflow, runtime, scheduler, workflow_ui
from yugu_node_studio.nodes import YUGUS_NodeBase
from yugu_node_studio.constants import TREE_ID, SOCKET_TRIGGER
from _bpy_restrict_state import RestrictBlend


class YUGUS_NODE_api_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_api_value"
    bl_label = "API Value"
    value: bpy.props.FloatProperty(default=7)

    def init(self, _context):
        self._init_base()
        self.outputs.new(SOCKET_TRIGGER, "Value")


class YUGUS_NODE_api_action(api.ActionNodeMixin, YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_api_action"
    bl_label = "API Action"
    seconds: bpy.props.FloatProperty(default=.25)
    timeout_seconds: bpy.props.FloatProperty(default=0)
    fail: bpy.props.BoolProperty(default=False)
    target: bpy.props.PointerProperty(type=bpy.types.Object)
    bad_state: bpy.props.BoolProperty(default=False)

    def init(self, _context):
        self.init_action()
        self.inputs.new(SOCKET_TRIGGER, "Value")
        self.inputs.new('YUGUS_NS_update_event', 'Command')


started, updated, cancelled, retired, commands = [], [], [], [], []


def start(context):
    if context.input_name == 'Command':
        commands.append(context.run_id)
        return api.ActionResult.end()
    payload = context.payload
    started.append((context.run_id, payload))
    payload["nested"]["value"] = 999
    value = context.input("Value", 0)
    if context.node.target:
        context.claim_transforms([context.node.target])
    if context.node.bad_state:
        return api.ActionResult.running({"invalid": context.node})
    if context.node.fail:
        return api.ActionResult.failed("Synthetic failure")
    if context.node.seconds <= 0:
        return api.ActionResult.end()
    return api.ActionResult.running({"deadline": context.now + context.node.seconds,
                                     "value": value, "origin": context.payload})


def update(context, state):
    updated.append((context.now, state["value"], context.payload))
    assert context.payload["nested"]["value"] == 7
    assert state["origin"]["nested"]["value"] == 7
    if context.now < state["deadline"]:
        return api.ActionResult.running(state)
    return api.ActionResult.end()


def cancel(context, state, reason):
    cancelled.append((context.run_id, reason, context.node is None))


def create_spec(policy_inputs=None):
    return api.ModuleSpec(
        "synthetic", label="Synthetic API",
        classes=(YUGUS_NODE_api_value, YUGUS_NODE_api_action),
        evaluators={YUGUS_NODE_api_value.bl_idname: lambda context, node: {"Value": node.value}},
        actions={YUGUS_NODE_api_action.bl_idname: api.ActionSpec(
            start, update, cancel, inputs=('Enter', 'Command'), policy_inputs=policy_inputs, configuration=lambda node: {
                "seconds": node.seconds, "fail": node.fail, "bad": node.bad_state,
                "target": api.object_ref(node.target) if node.target else None})},
        menu_entries=((YUGUS_NODE_api_value.bl_idname, "API Value"), (YUGUS_NODE_api_action.bl_idname, "API Action")),
        cards={YUGUS_NODE_api_action.bl_idname: api.CardSpec("API Action", "Wait for the synthetic action.")},
        cleanup=lambda reason: retired.append(("cleanup", reason)),
        recover=lambda reason: retired.append(("recover", reason)))


with RestrictBlend():
    addon.register()
    bundled_modules = api.registered_modules()
    api.register_module(create_spec())
assert api.registered_modules() == (*bundled_modules, "synthetic")
assert ("Synthetic API", create_spec().menu_entries) in api.menu_entries()
assert any(item[0] == YUGUS_NODE_api_action.bl_idname for item in api.card_entries())
assert retired == [], "Module recovery read scene data during restricted registration"
for spec in (create_spec(), api.ModuleSpec("missing_dependency", dependencies=("absent",))):
    try:
        api.register_module(spec)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid module declarations were accepted")


class Graph:
    def __init__(self):
        flow.clear()
        self.tree = bpy.data.node_groups.new("Public API graph", TREE_ID)
        self.source = self.tree.nodes.new("YUGUS_NODE_test_event")
        self.cancel = self.tree.nodes.new("YUGUS_NODE_test_event")
        self.value = self.tree.nodes.new(YUGUS_NODE_api_value.bl_idname)
        self.action = self.tree.nodes.new(YUGUS_NODE_api_action.bl_idname)
        self.after = self.tree.nodes.new("YUGUS_NODE_test_action")
        self.after.seconds, self.after.label = 0, "Completed extension"
        self.link(self.source, "Trigger", self.action, "Enter")
        self.link(self.cancel, "Trigger", self.action, "Cancel")
        self.data_link = self.link(self.value, "Value", self.action, "Value")
        self.link(self.action, "End", self.after, "Enter")
        for output in ("Failed", "Cancelled"):
            probe = self.tree.nodes.new("YUGUS_NODE_test_action")
            probe.seconds, probe.label = 0, output + " extension"
            self.link(self.action, output, probe, "Enter")

    def link(self, source, output, target, input_name):
        return self.tree.links.new(source.outputs[output], target.inputs[input_name])

    def fire(self, now=0):
        payload = {"nested": {"value": 7}, "event": len(started)}
        rid = flow.emit_event(self.tree, self.source, "Trigger", payload, now=now)
        payload["nested"]["value"] = -1
        return rid

    def close(self):
        flow.clear()
        bpy.data.node_groups.remove(self.tree)


graph = Graph()
context = flow.PullContext(graph.tree, bpy.context.scene, 0, 0)
assert context.evaluate(graph.value)["Value"] == 7
rid = graph.fire()
graph.value.value = 11
assert workflow.status(rid)["state"] == "running"
assert context.evaluate(graph.action)["Running"] == 1
api.poll(now=.1)
assert workflow.status(rid)["state"] == "running"
assert not workflow.logs
api.poll(now=.249)
assert not workflow.logs
api.poll(now=.251)
assert workflow.status(rid)["state"] == "end"
assert len(workflow.logs) == 2 and workflow.logs[-1]["payload"]["nested"]["value"] == 7
assert updated[-1][1] == 7, "Waiting action resampled a changing value"
assert retired == [("recover", "register")]
assert not cancelled
graph.close()

# Policy behavior, genuine synchronous completion, and cancellation notification.
for policy in ("PARALLEL", "IGNORE", "RESTART"):
    graph = Graph()
    graph.action.busy_policy = policy
    before = len(started)
    a, b = graph.fire(), graph.fire(.05)
    assert len(started) - before == (1 if policy == "IGNORE" else 2)
    if policy == "RESTART":
        assert workflow.status(a)["state"] == "cancelled"
    api.poll(now=.31)
    assert workflow.status(b)["state"] == "end"
    graph.close()
graph = Graph()
graph.action.seconds = 0
rid = graph.fire()
assert workflow.status(rid)["state"] == "end" and workflow.logs[-1]["state"] == "end"
graph.close()

api.unregister_module('synthetic')
api.register_module(create_spec(policy_inputs=('Enter',)))
for policy in ('IGNORE', 'RESTART'):
    graph = Graph()
    command = graph.tree.nodes.new('YUGUS_NODE_test_event')
    graph.link(command, 'Trigger', graph.action, 'Command')
    graph.action.busy_policy = policy
    rid = graph.fire()
    before_commands = len(commands)
    control = flow.emit_event(graph.tree, command, 'Trigger', now=.1)
    assert workflow.status(control)['state'] == 'end'
    assert len(commands) == before_commands + 1
    assert workflow.status(rid)['state'] == 'running', 'Command incorrectly applied Enter busy policy'
    api.poll(now=.3)
    assert workflow.status(rid)['state'] == 'end'
    graph.close()

for mode in ("cancel", "mute", "delete", "link", "setting", "clear", "timeout", "bad", "fail"):
    graph = Graph()
    obj = bpy.data.objects.new("Claimed test object", None)
    bpy.context.scene.collection.objects.link(obj)
    graph.action.target = obj
    if mode == "timeout":
        graph.action.timeout_seconds = .1
    if mode == "bad":
        graph.action.bad_state = True
    if mode == "fail":
        graph.action.fail = True
    before = len(cancelled)
    rid = graph.fire()
    if mode in {"bad", "fail"}:
        assert workflow.status(rid)["state"] == "failed"
    else:
        assert len(api.claimed_transforms()) == 1
        if mode == "cancel":
            flow.emit_event(graph.tree, graph.cancel, "Trigger", {"cancel_run_id": rid}, now=.05)
        elif mode == "mute":
            graph.action.mute = True
        elif mode == "delete":
            graph.tree.nodes.remove(graph.action)
        elif mode == "link":
            graph.tree.links.remove(graph.data_link)
        elif mode == "setting":
            graph.action.seconds = .5
        elif mode == "clear":
            flow.clear()
        api.poll(now=.11)
        if mode != "clear":
            assert workflow.status(rid)["state"] == ("failed" if mode == "timeout" else "cancelled"), mode
    if mode in {"cancel", "timeout", "bad", "fail"}:
        output = "Cancelled" if mode == "cancel" else "Failed"
        assert any(item["label"] == output + " extension" and item["state"] == "end" for item in workflow.logs), mode
    assert len(cancelled) == before + 1, mode
    assert not api.claimed_transforms(), mode
    graph.close()
    bpy.data.objects.remove(obj, do_unlink=True)

# Strict event/state JSON and atomic resource claims with rename stability.
for value in ({"bad": float("nan")}, {1: "key"}, {"tuple": (1,)}, {"rna": bpy.context.scene}):
    try:
        api.json_snapshot(value)
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("Non-JSON state was accepted")
a, b = [bpy.data.objects.new(name, None) for name in ("Owner A", "Owner B")]
api.claim_transforms("first", [a])
try:
    api.claim_transforms("second", [b, a])
except api.ResourceConflict:
    pass
else:
    raise AssertionError("Overlapping writers were accepted")
assert not api.claimed_transforms("second"), "Atomic claim partially succeeded"
reference = api.object_ref(a)
a.name = "Renamed owner A"
assert api.resolve_object(reference) == a
api.release_owner("first")
api.claim_transforms("second", [a, b])
assert len(api.claimed_transforms("second")) == 2
api.release_owner("second")
for obj in (a, b):
    bpy.data.objects.remove(obj)

# Existing cards recognize a declared extension action without a second graph.
definition = workflow_ui.new_definition("Public API cards")
view = workflow_ui.cards_from_graph(definition)
node = workflow_ui.add_card(definition, view.chain, YUGUS_NODE_api_action.bl_idname)
assert workflow_ui.cards_from_graph(definition).supported
assert node.bl_idname == YUGUS_NODE_api_action.bl_idname
bpy.data.node_groups.remove(definition)

# Common scheduler order: routing before feature update before action completion.
order = []
scheduler.add_motion("api:first", lambda context, delta: order.append(100), priority=100)
scheduler.add_motion("api:feature", lambda context, delta: order.append(200), priority=200)
scheduler.add_motion("api:last", lambda context, delta: order.append(300), priority=300)
scheduler.run_motion_once()
assert order == [100, 200, 300]
for name in ("api:first", "api:feature", "api:last"):
    scheduler.remove_motion(name)

# Lifecycle callbacks tear down before Undo/Load replaces RNA, recover afterwards.
graph = Graph()
rid = graph.fire()
addon._undo_pre(None)
assert not api.claimed_transforms() and scheduler.is_suspended()
addon._undo_post(None)
assert not scheduler.is_suspended()
assert retired[-2:] == [("cleanup", "undo/redo"), ("recover", "undo/redo")]
graph.close()
graph = Graph()
graph.fire()
filename = output_directory() / "public-api-lifecycle.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(filename))
bpy.ops.wm.open_mainfile(filepath=str(filename))
assert retired[-2:] == [("cleanup", "load"), ("recover", "load")]
assert not workflow.history() and not api.claimed_transforms()
flow.clear()

# Bundled discovery honors dependencies without consumers editing dispatch.
from yugu_node_studio import modules
discovery = output_directory() / "api-discovery"
discovery.mkdir(exist_ok=True)
(discovery / "a_consumer.py").write_text(
    'from ..extensions import ModuleSpec\nSPEC = ModuleSpec("discovered_consumer", dependencies=("discovered_base",))\n', encoding="utf-8")
(discovery / "z_base.py").write_text(
    'from ..extensions import ModuleSpec\ndef create_spec():\n    return ModuleSpec("discovered_base")\n', encoding="utf-8")
original_paths = list(modules.__path__)
modules.__path__[:] = [str(discovery)]
with RestrictBlend():
    api.register()
assert api.registered_modules().index("discovered_base") < api.registered_modules().index("discovered_consumer")
try:
    api.unregister_module("discovered_base")
except ValueError:
    pass
else:
    raise AssertionError("A live dependency was unregistered")
api.unregister_module("discovered_consumer")
api.unregister_module("discovered_base")
modules.__path__[:] = original_paths

# Consumer failures cannot strand another module, callbacks, or resource claims.
def raising(*_args):
    raise RuntimeError("Expected consumer cleanup failure")

api.register_module(api.ModuleSpec("cleanup_survivor", cleanup=lambda reason: retired.append(("survivor", reason)),
                                  motion=lambda context, delta: None))
api.register_module(api.ModuleSpec("cleanup_failure", cleanup=raising, recover=raising,
                                  on_unregister=raising, motion=lambda context, delta: None))
addon._undo_pre(None)
addon._undo_post(None)
assert not scheduler.is_suspended()
assert any(item["source"] == "cleanup_failure.recover" for item in api.lifecycle_errors())
restricted_graph = Graph()
restricted_run = restricted_graph.fire()
cancel_count = len(cancelled)
with RestrictBlend():
    addon.unregister()
    assert len(cancelled) == cancel_count + 1, 'Restricted disable skipped the pending action cancel hook'
    assert cancelled[-1][0] == restricted_run and cancelled[-1][2]
    addon.register()
    addon.unregister()
assert not api.registered_modules()
assert not scheduler._motion_callbacks and not scheduler._system_callbacks
assert ("survivor", "unregister") in retired
assert not hasattr(bpy.types.Scene, "yugu_node_studio_tree")
print("YUGU_PUBLIC_API_OK")
