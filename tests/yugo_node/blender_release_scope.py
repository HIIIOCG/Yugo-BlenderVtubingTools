"""0.10 release scope and genuine 0.9.7 saved-graph compatibility.

Run through run_fusion_checks.py in disposable factory-startup Blender. The
fusion bootstrap selects source or the delivered ZIP. A separately prepared
0.9.7 test graph may be passed directly with ``-- --legacy path.blend``.
This test never opens a user file or starts network/keyboard workers.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import bpy

from _bootstrap import ROOT, load_node, output_directory

TREE = "YUGUS_NT_humanoid_motion"
LEGACY_TREE = "ReleaseScopeLegacyGraph"
RETIRED = frozenset({
    "YUGUS_NODE_flow_parallel", "YUGUS_NODE_flow_wait_all",
    "YUGUS_NODE_float_receiver", "YUGUS_NODE_spring_simulation",
    "YUGUS_NODE_yt_chat_source", "YUGUS_NODE_yt_chat_event",
    "YUGUS_NODE_chat_debug", "YUGUS_NODE_chat_list", "YUGUS_NODE_keyword_match",
    "YUGUS_NODE_keyboard_data", "YUGUS_NODE_key_state", "YUGUS_NODE_state_logic",
    "YUGUS_NODE_state_events", "YUGUS_NODE_state_toggle", "YUGUS_NODE_state_value",
    "YUGUS_NODE_control_mode", "YUGUS_NODE_mode_match", "YUGUS_NODE_set_mode",
    "YUGUS_NODE_key_bind", "YUGUS_NODE_keybind_expression",
    "YUGUS_NODE_action_flow", "YUGUS_NODE_flow_entry", "YUGUS_NODE_flow_end",
    "YUGUS_NODE_wait_action", "YUGUS_NODE_test_action", "YUGUS_NODE_test_event",
})

addon = load_node()
_package_directory = None


def layout_snapshot(tree):
    return {
        "nodes": sorted([
            [node.name, node.bl_idname, str(node.runtime_id),
             [[socket.name, socket.bl_idname, socket.identifier] for socket in node.inputs],
             [[socket.name, socket.bl_idname, socket.identifier] for socket in node.outputs]]
            for node in tree.nodes
        ]),
        "links": sorted([
            [link.from_node.name, link.from_socket.identifier,
             link.to_node.name, link.to_socket.identifier]
            for link in tree.links
        ]),
    }


def legacy_fixture(path):
    assert addon.bl_info["version"] == (0, 9, 7), addon.bl_info
    tree = bpy.data.node_groups.new(LEGACY_TREE, TREE)
    tree.use_fake_user = True
    nodes = {kind: tree.nodes.new(kind) for kind in sorted(RETIRED)}
    for kind, node in nodes.items():
        node.name = kind
    receiver = tree.nodes.new("YUGUS_NODE_float_receiver")
    receiver.name = "RetiredEventMustNotWrite"
    receiver.value = 3.0
    receiver.inputs["Value"].default_value = 99.0
    clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
    clock.name = "ActiveClockToRetiredAction"

    def wire(first, output, second, input_name):
        tree.links.new(first.outputs[output], second.inputs[input_name])

    def between(first, output, second, input_name):
        wire(nodes["YUGUS_NODE_" + first], output,
             nodes["YUGUS_NODE_" + second], input_name)

    between("keyboard_data", "Data", "key_state", "Keyboard")
    between("key_state", "State", "state_events", "State")
    between("state_events", "On", "state_toggle", "Toggle")
    between("state_toggle", "State", "state_value", "State")
    between("control_mode", "Mode", "mode_match", "Mode")
    between("control_mode", "Mode", "set_mode", "Mode")
    between("yt_chat_source", "Chat", "yt_chat_event", "Chat")
    between("yt_chat_event", "End", "chat_debug", "Enter")
    between("yt_chat_event", "Text", "chat_debug", "Text")
    between("yt_chat_event", "Text", "keyword_match", "Text")
    between("yt_chat_event", "Text", "chat_list", "Text")
    between("flow_entry", "Start", "flow_end", "Enter")
    wire(clock, "Trigger", nodes["YUGUS_NODE_wait_action"], "Enter")
    wire(nodes["YUGUS_NODE_wait_action"], "End", receiver, "Enter")
    wire(nodes["YUGUS_NODE_test_event"], "Trigger", receiver, "Enter")
    nodes["YUGUS_NODE_wait_action"].seconds = 0.0
    nodes["YUGUS_NODE_test_event"].payload_text = "Saved payload survives"
    nodes["YUGUS_NODE_yt_chat_source"].video = "Saved URL survives"
    nodes["YUGUS_NODE_state_value"].on_value = 17.5
    style = bpy.data.collections.new("ReleaseScopeRetiredChatStyle")
    bpy.context.scene.collection.children.link(style)
    nodes["YUGUS_NODE_chat_list"].inputs["Style"].default_value = style
    nodes["YUGUS_NODE_chat_list"].hide_style = False
    assert not style.hide_viewport and not style.hide_render
    tree["expected_release_layout"] = json.dumps(layout_snapshot(tree), sort_keys=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    print(json.dumps({"legacy_fixture": str(path), "nodes": len(tree.nodes),
                      "links": len(tree.links)}))


def reject(function):
    try:
        function()
    except (ValueError, RuntimeError):
        return
    raise AssertionError("A retired feature remained available")


class ControlsLayout:
    def __init__(self):
        self.operators = []
        self.properties = []

    def box(self):
        return self

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


def availability_and_services():
    assert addon.bl_info["version"] == (0, 10, 0), addon.bl_info
    assert RETIRED == addon.availability.RETIRED_NODE_TYPES
    tree = bpy.data.node_groups.new("ReleaseScopeNewGraph", TREE)
    try:
        advertised = {kind for _title, entries in addon.nodes.NODE_MENU for kind, _label in entries}
        assert not RETIRED & advertised, sorted(RETIRED & advertised)
        assert not {"Chat", "Keyboard & State", "Actions"} & {
            title for title, _entries in addon.nodes.NODE_MENU}
        for kind in sorted(RETIRED):
            cls = bpy.types.Node.bl_rna_get_subclass_py(kind)
            assert cls is not None, f"Missing old-file compatibility class: {kind}"
            assert not cls.poll(tree), kind
            assert not addon.availability.is_available(kind), kind
            reject(lambda kind=kind: tree.nodes.new(kind))
        retained = advertised | {
            "YUGUS_NODE_spring_to_physics", "YUGUS_NODE_rigid_to_physics",
            "YUGUS_NODE_physics_solver", "YUGUS_NODE_apply_object_transform",
            "YUGUS_NODE_mixer",
        }
        for kind in sorted(retained):
            node = tree.nodes.new(kind)
            assert addon.availability.is_available(node), kind
            tree.nodes.remove(node)
        assert "YUGUS_NODE_action_source" in advertised
        assert "YUGUS_NODE_expression_action" in advertised
        assert "YUGUS_NODE_flow_repeat" in advertised
        assert not RETIRED & {kind for kind, _label in addon.workflow_nodes.MENU_ENTRIES}
        menu = ControlsLayout()
        addon.ui.YUGUS_MT_add_physics.draw(SimpleNamespace(layout=menu), bpy.context)
        assert {op.type for op in menu.operators} == {
            "YUGUS_NODE_spring_to_physics", "YUGUS_NODE_rigid_to_physics",
            "YUGUS_NODE_physics_solver"}
        solver = tree.nodes.new("YUGUS_NODE_physics_solver")
        controls = ControlsLayout()
        solver.draw_buttons(bpy.context, controls)
        assert controls.properties == ["enabled"], controls.properties
        assert not controls.operators, controls.operators
        assert [socket.name for socket in solver.inputs] == ["Physics Description", "Reset", "Stop"]
        assert "Play" not in solver.inputs and "Updated" not in solver.outputs
        assert "Simulation Data" in solver.outputs
        for name in ("Reset", "Stop"):
            solver.inputs[name].draw(bpy.context, controls, solver, name)
        assert [op.operation for op in controls.operators] == ["RESET", "STOP"]
        tree.nodes.remove(solver)
        callbacks = set(addon.scheduler._motion_callbacks) | set(addon.scheduler._system_callbacks)
        assert not callbacks & {"chat", "chat_ui_layout", "keyboard_values", "keyboard", "input_values", "float_receivers"}, callbacks
        assert not addon.keyboard._enabled
        assert not addon.input_runtime._enabled
        assert not addon.global_keyboard.is_running()
        unregistered = [cls for module in (addon.chat_nodes, addon.chat_ui_nodes,
                                          addon.workflow_ui, addon.chat_example)
                        for cls in module.CLASSES
                        if issubclass(cls, (bpy.types.Operator, bpy.types.AddonPreferences, bpy.types.Panel))]
        unregistered.append(addon.keyboard.YUGUS_OT_keyboard_listener)
        unregistered.extend((addon.operators.YUGUS_OT_spring_debug_print,
                             addon.flow.YUGUS_OT_flow_spring_reload))
        assert all(not getattr(cls, "is_registered", False) for cls in unregistered)
        return {"retired_types": len(RETIRED), "retained_creation_types": len(retained),
                "scheduler_callbacks": sorted(callbacks)}
    finally:
        bpy.data.node_groups.remove(tree)


def generic_data_and_flow():
    tree = bpy.data.node_groups.new("ReleaseScopeGenericData", TREE)
    local_image = None
    try:
        source = tree.nodes.new("YUGUS_NODE_object_value")
        source.object = bpy.data.objects.get("Cube")
        collection = tree.nodes.new("YUGUS_NODE_collection_value")
        collection.collection = bpy.data.collections.get("Collection")
        assert collection.collection is not None
        image = tree.nodes.new("YUGUS_NODE_image_from_url")
        image.inputs["URL"].default_value = "https://example.invalid/image.png"
        context = addon.runtime.EvaluationContext(tree, bpy.context.scene, 0)
        assert context.evaluate(source)["Object"].name == source.object.name
        assert context.evaluate(collection)["Collection"].name == collection.collection.name
        assert context.evaluate(image)["Image"].url.endswith("image.png")
        image_value = tree.nodes.new("YUGUS_NODE_image_value")
        assert context.evaluate(image_value)["Image"] is None
        local_image = bpy.data.images.new("ValueNodeImage", width=2, height=2)
        image_value.image = local_image
        context.cache.clear()
        assert context.evaluate(image_value)["Image"] == local_image
        assert addon.image_runtime.get(context.evaluate(image_value)["Image"]) == local_image
        image_value.mute = True
        context.cache.clear()
        assert context.evaluate(image_value)["Image"] is None
        meta = tree.nodes.new("YUGUS_NODE_meta_value")
        meta.meta_name = "手動角色"
        meta.author = "作者"
        meta.version = "1.2"
        meta.contact = "contact@example.invalid"
        result = context.evaluate(meta)["Meta"]
        assert result.values["name"] == "手動角色"
        assert result.values["authors"] == ("作者",)
        assert result.values["version"] == "1.2"
        text = tree.nodes.new("YUGUS_NODE_string_value")
        text.value = "文字節點的名稱"
        assert context.evaluate(text)["Text"] == "文字節點的名稱"
        name_link = tree.links.new(text.outputs["Text"], meta.inputs["Name"])
        tree.links.new(text.outputs["Text"], meta.inputs["Author"])
        context.cache.clear()
        assert context.evaluate(meta)["Meta"].values["name"] == "文字節點的名稱"
        assert context.evaluate(meta)["Meta"].values["authors"] == ("文字節點的名稱",)
        tree.links.remove(name_link)
        context.cache.clear()
        assert context.evaluate(meta)["Meta"].values["name"] == "手動角色"
        text.mute = True
        context.cache.clear()
        assert context.evaluate(text)["Text"] == ""
        viewer = tree.nodes.new("YUGUS_NODE_data_viewer")
        tree.links.new(meta.outputs["Meta"], viewer.inputs["Meta"])
        context.evaluate(viewer)
        assert "手動角色" in viewer.summary
        meta.mute = True
        context.cache.clear()
        assert context.evaluate(meta)["Meta"].values == {}
        arithmetic = tree.nodes.new("YUGUS_NODE_math")
        arithmetic.inputs["A"].default_value = 2.0
        arithmetic.inputs["B"].default_value = 5.0
        assert context.evaluate(arithmetic)["Value"] == 7.0
        clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
        sequence = tree.nodes.new("YUGUS_NODE_flow_sequence")
        tree.links.new(clock.outputs["Trigger"], sequence.inputs["Enter"])
        counter = tree.nodes.new("YUGUS_NODE_flow_every_n")
        counter.every = 100
        second_counter = tree.nodes.new("YUGUS_NODE_flow_every_n")
        second_counter.every = 100
        tree.links.new(sequence.outputs["Then 1"], counter.inputs["Enter"])
        tree.links.new(sequence.outputs["Then 2"], second_counter.inputs["Enter"])
        run = addon.flow.emit_event(tree, clock, "Trigger", now=10.0)
        assert addon.flow._events[str(counter.runtime_id)]["count"] == 1, tree.last_error
        assert addon.flow._events[str(second_counter.runtime_id)]["count"] == 1, tree.last_error
        assert addon.workflow.status(run)["state"] == "end", addon.workflow.status(run)
        repeat = tree.nodes.new("YUGUS_NODE_flow_repeat")
        repeat.count = 3
        tree.links.new(clock.outputs["Trigger"], repeat.inputs["Enter"])
        repeated = tree.nodes.new("YUGUS_NODE_flow_every_n")
        repeated.every = 100
        tree.links.new(repeat.outputs["Body"], repeated.inputs["Enter"])
        run = addon.flow.emit_event(tree, clock, "Trigger", now=11.0)
        assert addon.flow._events[str(repeated.runtime_id)]["count"] == 3, tree.last_error
        assert addon.workflow.status(run)["state"] == "end", addon.workflow.status(run)
        assert not addon.image_runtime._records
        return {"generic_object_collection_image": True, "local_image_value": True,
                "manual_meta_value": True, "string_meta_inputs": True, "float_math": 7.0,
                "flow_sequence": "completed", "repeat": "completed"}
    finally:
        addon.flow.clear()
        bpy.data.node_groups.remove(tree)
        if local_image is not None:
            bpy.data.images.remove(local_image)


class InertLayout:
    def __init__(self):
        self.labels = []

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def __getattr__(self, name):
        raise AssertionError(f"Retired UI exposed a control: {name}")


def repeat_parallel_reentry():
    """Repeat retains independent simultaneous invocations after retirement."""
    tree = bpy.data.node_groups.new("RepeatReentry", TREE)
    tree.is_workflow_definition = True
    try:
        clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
        repeat = tree.nodes.new("YUGUS_NODE_flow_repeat")
        repeat.count = 2
        repeat.busy_policy = "PARALLEL"
        delay = tree.nodes.new("YUGUS_NODE_flow_delay")
        delay.seconds = 0.1
        tree.links.new(clock.outputs["Trigger"], repeat.inputs["Enter"])
        tree.links.new(repeat.outputs["Body"], delay.inputs["Enter"])
        runs = [addon.flow.emit_event(tree, clock, "Trigger", now=now) for now in (10.0, 10.05)]
        assert addon.workflow.running_for(tree, repeat) == 2
        assert all(addon.workflow.status(run)["state"] == "running" for run in runs)
        addon.flow.tick(now=10.16)
        assert addon.workflow.running_for(tree, repeat) == 2
        assert [task["iteration"] for task in addon.workflow._tasks.values() if task["kind"] == "repeat"] == [2, 2]
        addon.flow.tick(now=10.27)
        assert all(addon.workflow.status(run)["state"] == "end" for run in runs)
        assert not addon.workflow._tasks
        return {"simultaneous_runs": 2, "iterations_per_run": 2, "busy_policy": "PARALLEL"}
    finally:
        addon.flow.clear()
        bpy.data.node_groups.remove(tree)


def removed_nodes_saved_graph():
    """Round-trip the removed declarations without touching a user's file."""
    removed = {"YUGUS_NODE_float_receiver", "YUGUS_NODE_spring_simulation",
               "YUGUS_NODE_flow_parallel", "YUGUS_NODE_flow_wait_all"}
    retired = addon.availability.RETIRED_NODE_TYPES
    tree = bpy.data.node_groups.new("RemovedNodesCompatibility", TREE)
    tree.use_fake_user = True
    # Permit only fixture construction; normal creation stays blocked.
    addon.availability.RETIRED_NODE_TYPES = retired - removed
    try:
        receiver = tree.nodes.new("YUGUS_NODE_float_receiver")
        spring = tree.nodes.new("YUGUS_NODE_spring_simulation")
        parallel = tree.nodes.new("YUGUS_NODE_flow_parallel")
        join = tree.nodes.new("YUGUS_NODE_flow_wait_all")
    finally:
        addon.availability.RETIRED_NODE_TYPES = retired
    receiver.name = "SavedReceiver"
    receiver.value = 3.25
    receiver.inputs["Value"].default_value = 99.0
    spring.name = "SavedLegacySpring"
    spring.trace_duration = 12.5
    parallel.name = "SavedParallel"
    parallel.branches = 3
    parallel.busy_policy = "RESTART"
    join.name = "SavedWaitAll"
    join.busy_policy = "IGNORE"
    clock = tree.nodes.new("YUGUS_NODE_hz_trigger")
    clock.name = "SavedClock"
    arithmetic = tree.nodes.new("YUGUS_NODE_math")
    arithmetic.inputs["A"].default_value = 2.0
    arithmetic.inputs["B"].default_value = 5.0
    viewer = tree.nodes.new("YUGUS_NODE_data_viewer")
    meta = tree.nodes.new("YUGUS_NODE_meta_value")
    meta.name = "SavedMetaValue"
    meta.meta_name = "Saved Character"
    meta.author = "Saved Author"
    meta.version = "2.0"
    meta.copyright = "Saved Copyright"
    meta.contact = "Saved Contact"
    meta.reference = "Saved Reference"
    meta.show_details = True
    text = tree.nodes.new("YUGUS_NODE_string_value")
    text.name = "SavedStringValue"
    text.value = "Saved Linked Name"
    tree.links.new(text.outputs["Text"], meta.inputs["Name"])
    image_value = tree.nodes.new("YUGUS_NODE_image_value")
    image_value.name = "SavedImageValue"
    image_value.image = bpy.data.images.new("SavedValueNodeImage", width=2, height=2)
    tree.links.new(clock.outputs["Trigger"], receiver.inputs["Enter"])
    tree.links.new(arithmetic.outputs["Value"], receiver.inputs["Value"])
    tree.links.new(spring.outputs["Simulation Data"], viewer.inputs["Simulation Data"])
    tree.links.new(meta.outputs["Meta"], viewer.inputs["Meta"])
    counter = tree.nodes.new("YUGUS_NODE_flow_every_n")
    counter.name = "RetiredBranchesMustNotRun"
    counter.every = 100
    tree.links.new(clock.outputs["Trigger"], parallel.inputs["Enter"])
    for index in (1, 2, 3):
        tree.links.new(parallel.outputs[f"Branch {index}"], join.inputs[f"Branch {index}"])
    tree.links.new(join.outputs["End"], counter.inputs["Enter"])
    expected = layout_snapshot(tree)
    tree_name = tree.name
    temporary_root = output_directory()
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yugo_removed_nodes_", dir=temporary_root) as directory:
        assert Path(directory).resolve().is_relative_to(temporary_root)
        path = Path(directory) / "removed_nodes.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
    tree = bpy.data.node_groups[tree_name]
    receiver = tree.nodes["SavedReceiver"]
    spring = tree.nodes["SavedLegacySpring"]
    parallel = tree.nodes["SavedParallel"]
    join = tree.nodes["SavedWaitAll"]
    saved_image = tree.nodes["SavedImageValue"].image
    try:
        assert layout_snapshot(tree) == expected
        assert receiver.value == 3.25 and receiver.inputs["Value"].default_value == 99.0
        assert spring.trace_duration == 12.5
        assert parallel.branches == 3 and parallel.busy_policy == "RESTART"
        assert join.busy_policy == "IGNORE"
        meta = tree.nodes["SavedMetaValue"]
        assert meta.show_details and meta.meta_name == "Saved Character"
        assert addon.runtime.EvaluationContext(tree, bpy.context.scene, 0).evaluate(meta)["Meta"].values == {
            "name": "Saved Linked Name", "authors": ("Saved Author",), "version": "2.0",
            "copyright": "Saved Copyright", "contact": "Saved Contact", "reference": "Saved Reference"}
        assert saved_image is not None and saved_image.name == "SavedValueNodeImage"
        assert addon.flow.PullContext(tree, bpy.context.scene, 1.0, 0.0).evaluate(tree.nodes["SavedImageValue"])["Image"] == saved_image
        for node in (receiver, spring, parallel, join):
            layout = InertLayout()
            node.draw_buttons(bpy.context, layout)
            assert layout.labels == ["Unavailable in Yugo Node 0.10.0"]
            reject(lambda node=node: addon.runtime.EvaluationContext(tree, bpy.context.scene, 0).evaluate(node))
            reject(lambda node=node: addon.flow.PullContext(tree, bpy.context.scene, 1, 0).evaluate(node))
        for node in (parallel, join):
            reject(lambda node=node: addon.workflow.evaluate(None, node))
            reject(lambda node=node: addon.workflow.receive(None, node, node.inputs[0], ()))
            reject(lambda node=node: addon.flow.emit_event(tree, node, "End", now=1.0))
        reject(lambda: addon.flow.reload_spring(spring))
        reject(lambda: addon.flow.spring_value(spring, None, 1.0, 0.0))
        reject(lambda: addon.runtime.start_spring(spring, bpy.context))
        reject(lambda: addon.spring_trace.start([spring]))
        addon.flow.emit_event(tree, tree.nodes["SavedClock"], "Trigger", now=1.0)
        addon.scheduler.run_motion_once()
        assert receiver.value == 3.25
        assert not addon.flow._springs and not addon.spring_trace._records
        assert not addon.workflow._tasks and not addon.workflow._callbacks
        assert str(tree.nodes["RetiredBranchesMustNotRun"].runtime_id) not in addon.flow._events
        assert layout_snapshot(tree) == expected
        return {"saved_nodes": len(tree.nodes), "saved_links": len(tree.links),
                "properties_preserved": True, "legacy_execution_blocked": True}
    finally:
        addon.flow.clear()
        bpy.data.node_groups.remove(tree)
        if saved_image is not None:
            bpy.data.images.remove(saved_image)


def old_graph(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))
    tree = bpy.data.node_groups[LEGACY_TREE]
    expected = json.loads(tree["expected_release_layout"])
    actual = layout_snapshot(tree)
    assert actual == expected, {
        "changed_nodes": [[before[0], before[3:], after[3:]]
                          for before, after in zip(expected["nodes"], actual["nodes"])
                          if before != after],
        "links_preserved": actual["links"] == expected["links"],
    }
    retired_nodes = [node for node in tree.nodes if node.bl_idname in RETIRED]
    assert len(retired_nodes) == len(RETIRED)
    assert tree.nodes["YUGUS_NODE_test_event"].payload_text == "Saved payload survives"
    assert tree.nodes["YUGUS_NODE_yt_chat_source"].video == "Saved URL survives"
    assert tree.nodes["YUGUS_NODE_state_value"].on_value == 17.5
    # RNA update callbacks must also be inert for a saved/animated Chat List.
    chat_list = tree.nodes["YUGUS_NODE_chat_list"]
    style = bpy.data.collections["ReleaseScopeRetiredChatStyle"]
    visibility = (style.hide_viewport, style.hide_render)
    chat_list.hide_style = True
    assert (style.hide_viewport, style.hide_render) == visibility
    chat_list.hide_style = False
    assert (style.hide_viewport, style.hide_render) == visibility
    for node in retired_nodes:
        layout = InertLayout()
        node.draw_buttons(bpy.context, layout)
        assert layout.labels, node.bl_idname
        assert not addon.flow._enabled(node), node.bl_idname
        reject(lambda node=node: addon.runtime.EvaluationContext(tree, bpy.context.scene, 0).evaluate(node))
        reject(lambda node=node: addon.flow.PullContext(tree, bpy.context.scene, 1, 0).evaluate(node))
        for output in node.outputs:
            if output.bl_idname == addon.flow.EVENT:
                reject(lambda node=node, output=output: addon.flow.emit_event(tree, node, output.name, now=1.0))
    source = tree.nodes["YUGUS_NODE_yt_chat_source"]
    reject(lambda: addon.chat_runtime.connect(source, api_key="test"))
    reject(lambda: addon.chat_runtime.attach_transport(source, SimpleNamespace(start=lambda: None)))
    receiver = tree.nodes["RetiredEventMustNotWrite"]
    for now in (1.0, 1.1, 2.0):
        addon.flow.tick(now=now)
    assert receiver.value == 3.0, receiver.value
    assert not addon.chat_runtime._sessions
    assert not addon.global_keyboard.is_running()
    assert layout_snapshot(tree) == expected
    # Availability does not rewrite the saved graph's mute settings.
    assert all(not node.mute for node in retired_nodes)
    return {"legacy_nodes": len(tree.nodes), "legacy_links": len(tree.links),
            "retired_data_event_and_connection_guarded": True, "saved_values_preserved": True,
            "retired_rna_callback_inert": True}


def no_chat_background_work():
    original = (addon.chat_runtime.maintenance, addon.chat_runtime.tick)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Active Flow called retired Chat background work")

    addon.chat_runtime.maintenance = addon.chat_runtime.tick = forbidden
    try:
        addon.flow.tick(now=3.0)
        addon.scheduler.run_motion_once()
        addon.scheduler.run_system_once()
    finally:
        addon.chat_runtime.maintenance, addon.chat_runtime.tick = original
    assert not addon.global_keyboard.is_running()


addon.register()
try:
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--write-legacy" in arguments:
        legacy_fixture(Path(arguments[arguments.index("--write-legacy") + 1]).resolve())
    else:
        metrics = {"declarations": availability_and_services(), "generic": generic_data_and_flow(),
                   "repeat_reentry": repeat_parallel_reentry()}
        if "--legacy" in arguments:
            metrics["legacy"] = old_graph(Path(arguments[arguments.index("--legacy") + 1]).resolve())
        metrics["removed_nodes"] = removed_nodes_saved_graph()
        no_chat_background_work()
        metrics["retired_background_work"] = "absent"
        print("YUGO RELEASE SCOPE PASSED " + json.dumps(metrics, sort_keys=True))
finally:
    addon.unregister()
    assert not addon.scheduler._motion_callbacks and not addon.scheduler._system_callbacks
    assert not addon.global_keyboard.is_running()
