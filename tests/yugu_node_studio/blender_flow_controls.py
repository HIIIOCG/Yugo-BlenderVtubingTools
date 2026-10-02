"""Generated Blender graphs exercising event dispatch and native Apply.

Run: blender --background --factory-startup --python this_file.py
Only pure input sources are controlled; the real flow engine and Apply run.
"""

from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, flow_nodes, nodes, runtime
from yugu_node_studio.constants import SOCKET_TRIGGER, TREE_ID
from yugu_node_studio.data import SpringData
from blender_integration import make_armature


class Layout:
    def __init__(self, records=None):
        self.records = [] if records is None else records

    def column(self, **_kwargs):
        return Layout(self.records)

    row = column

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", "")))

    def prop(self, owner, name, **kwargs):
        assert hasattr(owner, name), name
        self.records.append(("prop", name, kwargs.get("text")))

    def operator(self, name, **kwargs):
        self.records.append(("operator", name, kwargs.get("text")))
        return SimpleNamespace()


def make_face():
    mesh = bpy.data.meshes.new("Flow Control Face")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Flow Control Face", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for name in ("Basis", "A", "B", "C", "D"):
        key = obj.shape_key_add(name=name)
        key.value = 0.0
    return obj


class Graph:
    def __init__(self, name):
        flow.clear()
        self.tree = bpy.data.node_groups.new(name, TREE_ID)
        self.faces = [make_face()]
        self.providers = {}
        self.pulls = []
        self.original = runtime._evaluate_node

        def evaluate(context, node):
            provider = self.providers.get(str(node.runtime_id))
            return provider(context, node) if provider else self.original(context, node)

        runtime._evaluate_node = evaluate

    def close(self):
        flow.clear()
        runtime._evaluate_node = self.original
        bpy.data.node_groups.remove(self.tree)
        for face in self.faces:
            mesh = face.data
            bpy.data.objects.remove(face, do_unlink=True)
            bpy.data.meshes.remove(mesh)

    def node(self, kind):
        return self.tree.nodes.new("YUGUS_NODE_" + kind)

    def link(self, source, output, target, input="Enter"):
        return self.tree.links.new(source.outputs[output], target.inputs[input])

    def signal(self, initial=0.0):
        node = self.node("key_bind")
        state = {"value": initial}
        self.providers[str(node.runtime_id)] = lambda _context, _node: {"Trigger": state["value"]}
        return node, state

    def sink(self, label, key="A", face=None, weight=0.6):
        face = face or self.faces[0]
        source = self.node("expression_to_shapes")
        source.name = "Source " + label
        failure = {"broken": False}

        def value(context, _node):
            if failure["broken"]:
                raise ValueError("Expected source failure")
            self.pulls.append((label, context.now))
            return {"Shape Keys Values": flow.ShapeValues({(face.name, key): weight})}

        self.providers[str(source.runtime_id)] = value
        sink = self.node("apply_shapes")
        sink.objects.add().object = face
        self.link(source, "Shape Keys Values", sink, "Shape Keys Values")
        return sink, source, failure

    def labels(self):
        return [label for label, _now in self.pulls]

    def value(self, key):
        return self.faces[0].data.shape_keys.key_blocks[key].value

    def tick(self, now):
        flow.tick(now)

    def press(self, signal, now):
        signal["value"] = 1.0
        self.tick(now)

    def release(self, signal, now):
        signal["value"] = 0.0
        self.tick(now)


def declaration_tests():
    graph = Graph("Flow declarations")
    try:
        expected = {
            "flow_if": (["Enter", "Condition"], ["True", "False"]),
            "flow_gate": (["Enter", "Open"], ["End"]),
            "flow_delay": (["Enter", "Reset"], ["End"]),
            "flow_wait_until": (["Enter", "Condition", "Reset"], ["End"]),
            "flow_once": (["Enter", "Reset"], ["End"]),
            "flow_sequence": (["Enter"], ["Then 1", "Then 2", "Then 3"]),
            "flow_every_n": (["Enter", "Reset"], ["End"]),
        }
        menu = dict(nodes.NODE_MENU)["Flow Control"]
        for kind, (inputs, outputs) in expected.items():
            node = graph.node(kind)
            assert [socket.name for socket in node.inputs] == inputs
            assert [socket.name for socket in node.outputs] == outputs
            assert (node.bl_idname, node.bl_label) in menu
            assert not hasattr(node, "enabled")
            for socket in tuple(node.inputs) + tuple(node.outputs):
                if socket.name in {"Condition", "Open"}:
                    assert socket.bl_idname == SOCKET_TRIGGER and socket.default_value == 1.0
                else:
                    assert socket.bl_idname == flow.EVENT
                assert socket.draw_color(bpy.context, node) == (0.85, 0.12, 0.12, 1.0)
            layout = Layout()
            node.draw_buttons(bpy.context, layout)
            assert layout.records[-1] == ("label", node.last_summary)
            if kind == "flow_delay":
                assert node.seconds == 0.5
                assert layout.records[0][:2] == ("prop", "seconds")
            if kind == "flow_every_n":
                assert node.every == 2
                assert layout.records[0][:2] == ("prop", "every")
        assert len(flow_nodes.CLASSES) == 7
        # Reload retains its visible label/button in the last input socket.
        for kind, operator in (("vrm_data", "yugu_node_studio.reload_vrm"),
                               ("spring_simulation", "yugu_node_studio.flow_spring_reload")):
            node = graph.node(kind)
            reload = node.inputs["Reload"]
            identifier = reload.identifier
            graph.tree.nodes.new("NodeReroute")
            assert nodes.ensure_current_sockets() and nodes.ensure_current_sockets()
            assert node.inputs[-1].name == "Reload" and node.inputs[-1].identifier == identifier
            layout = Layout()
            reload.draw(bpy.context, layout, node, "Reload")
            assert layout.records == [("label", "Reload"), ("operator", operator, "Reload")]
    finally:
        graph.close()


def branch_and_gate_tests():
    graph = Graph("If and Gate")
    try:
        clock = graph.node("hz_trigger")
        branch, gate = graph.node("flow_if"), graph.node("flow_gate")
        yes, _, _ = graph.sink("true", "A")
        no, _, _ = graph.sink("false", "B")
        opened, _, _ = graph.sink("gate", "C")
        graph.link(clock, "Trigger", branch)
        graph.link(branch, "True", yes)
        graph.link(branch, "False", no)
        graph.link(clock, "Trigger", gate)
        graph.link(gate, "End", opened)
        gate.inputs["Open"].default_value = 0
        graph.tick(0)
        graph.tick(1 / 60)
        assert graph.labels() == ["true"]
        branch.inputs["Condition"].default_value = 0
        gate.inputs["Open"].default_value = 1
        graph.tick(2 / 60)
        assert graph.labels() == ["true", "false", "gate"]
        assert graph.value("A") > 0.59, "Unselected branch released its held value"
        gate.inputs["Open"].default_value = 0
        graph.tick(3 / 60)
        assert graph.labels()[-1] == "false" and graph.value("C") > 0.59
        gate.mute = True
        graph.tick(4 / 60)
        assert graph.value("C") == 0, "Muted event path retained ownership"
    finally:
        graph.close()


def alternative_branch_target_test():
    graph = Graph("If alternatives share one target")
    try:
        clock = graph.node("hz_trigger")
        branch = graph.node("flow_if")
        yes, _, _ = graph.sink("true alternative", "A", weight=0.2)
        no, _, _ = graph.sink("false alternative", "A", weight=0.8)
        graph.link(clock, "Trigger", branch)
        graph.link(branch, "True", yes)
        graph.link(branch, "False", no)
        graph.tick(0)
        for index, (condition, expected) in enumerate(((1, 0.2), (0, 0.8), (1, 0.2)), 1):
            branch.inputs["Condition"].default_value = condition
            graph.tick(index / 60)
            assert abs(graph.value("A") - expected) < 1e-6
            assert not graph.tree.last_error, graph.tree.last_error
        assert graph.labels() == ["true alternative", "false alternative", "true alternative"]
        clock.mute = True
        graph.tick(4 / 60)
        assert graph.value("A") == 0, "If hand-off lost the original target value"
    finally:
        graph.close()


def delay_tests():
    graph = Graph("Delay deadlines and Reset")
    try:
        enter, signal = graph.signal()
        reset, reset_signal = graph.signal()
        delay = graph.node("flow_delay")
        sink, _, _ = graph.sink("delay")
        link = graph.link(enter, "Trigger", delay)
        graph.link(reset, "Trigger", delay, "Reset")
        graph.link(delay, "End", sink)
        graph.tick(0)
        graph.press(signal, 1)
        graph.release(signal, 1.1)
        graph.press(signal, 1.25)  # A second Enter cannot extend/queue this deadline.
        graph.tick(1.499)
        assert graph.labels() == []
        graph.tick(1.5)
        assert graph.pulls == [("delay", 1.5)]
        graph.tick(1.8)
        assert len(graph.pulls) == 1
        graph.release(signal, 1.9)
        graph.press(signal, 2)
        graph.press(reset_signal, 2.25)
        graph.tick(2.5)
        assert len(graph.pulls) == 1, "Reset failed to cancel pending Delay"
        graph.release(signal, 2.6)
        graph.release(reset_signal, 2.6)
        delay.seconds = 0
        graph.press(signal, 3)
        assert graph.pulls[-1] == ("delay", 3)
        delay.seconds = 0.5
        graph.release(signal, 3.1)
        graph.press(signal, 4)
        delay.mute = True
        graph.tick(4.1)
        graph.tick(4.5)
        assert len(graph.pulls) == 2, "Muted Delay still completed"
        delay.mute = False
        graph.release(signal, 4.6)
        graph.press(signal, 5)
        graph.tree.links.remove(link)
        graph.tick(5.6)
        assert len(graph.pulls) == 2, "Disconnected Delay still completed"
    finally:
        graph.close()
    graph = Graph("Delay Reset event at the exact deadline")
    try:
        enter, _signal = graph.signal(initial=1)
        delay = graph.node("flow_delay")
        clock = graph.node("hz_trigger")
        clock.hz = "8"
        counter = graph.node("flow_every_n")
        counter.every = 4
        sink, _, _ = graph.sink("deadline")
        graph.link(enter, "Trigger", delay)
        graph.link(clock, "Trigger", counter)
        graph.link(counter, "End", delay, "Reset")
        graph.link(delay, "End", sink)
        graph.tick(0)  # Zero is a valid armed timestamp.
        for now in (0.125, 0.25, 0.375, 0.5, 0.625):
            graph.tick(now)
        assert not graph.pulls, "Event Reset at Delay deadline lost to pending completion"
    finally:
        graph.close()


def wait_tests():
    graph = Graph("Wait Until polling and Reset")
    try:
        enter, signal = graph.signal()
        reset, reset_signal = graph.signal()
        condition, condition_signal = graph.signal()
        wait = graph.node("flow_wait_until")
        sink, _, _ = graph.sink("wait")
        graph.link(enter, "Trigger", wait)
        graph.link(reset, "Trigger", wait, "Reset")
        graph.link(condition, "Trigger", wait, "Condition")
        graph.link(wait, "End", sink)
        graph.tick(0)
        graph.press(signal, 1)
        graph.tick(1.2)
        assert not graph.pulls
        condition_signal["value"] = 1
        graph.tick(1.3)  # Condition is polled even without another Enter.
        assert graph.pulls == [("wait", 1.3)]
        graph.tick(1.4)
        assert len(graph.pulls) == 1
        graph.release(signal, 1.5)
        graph.press(signal, 2)
        assert graph.pulls[-1] == ("wait", 2), "True condition should pass immediately"
        condition_signal["value"] = 0
        graph.release(signal, 2.1)
        graph.press(signal, 3)
        graph.press(reset_signal, 3.1)
        condition_signal["value"] = 1
        graph.tick(3.2)
        assert len(graph.pulls) == 2, "Reset Wait Until emitted End"
    finally:
        graph.close()
    graph = Graph("Wait Reset while its condition becomes true")
    try:
        enter, _signal = graph.signal(initial=1)
        condition, condition_signal = graph.signal()
        clock = graph.node("hz_trigger")
        clock.hz = "8"
        wait = graph.node("flow_wait_until")
        sink, _, _ = graph.sink("ready reset")
        graph.link(enter, "Trigger", wait)
        graph.link(condition, "Trigger", wait, "Condition")
        graph.link(clock, "Trigger", wait, "Reset")
        graph.link(wait, "End", sink)
        graph.tick(0)
        condition_signal["value"] = 1
        graph.tick(0.125)
        assert not graph.pulls, "Ready Wait Until emitted End before its Event Reset"
    finally:
        graph.close()


def once_and_counter_tests():
    for kind in ("flow_once", "flow_every_n"):
        graph = Graph(kind)
        try:
            enter, signal = graph.signal()
            reset, reset_signal = graph.signal()
            control = graph.node(kind)
            if kind == "flow_every_n":
                control.every = 3
            sink, _, _ = graph.sink(kind)
            graph.link(enter, "Trigger", control)
            graph.link(reset, "Trigger", control, "Reset")
            graph.link(control, "End", sink)
            graph.tick(0)
            for index in range(3):
                graph.press(signal, 0.1 + index * 0.2)
                graph.tick(0.15 + index * 0.2)
                graph.release(signal, 0.2 + index * 0.2)
            assert len(graph.pulls) == 1, (kind, graph.pulls)
            graph.press(reset_signal, 0.7)
            assert len(graph.pulls) == 1, "Reset emitted End"
            graph.release(reset_signal, 0.8)
            for index in range(3):
                graph.press(signal, 0.9 + index * 0.2)
                graph.release(signal, 1.0 + index * 0.2)
            assert len(graph.pulls) == 2, (kind, "Reset did not rearm", graph.pulls)
            if kind == "flow_every_n":
                control.every = 1
                graph.press(signal, 1.5)
                assert len(graph.pulls) == 3
        finally:
            graph.close()


def sequence_and_shared_pull_tests():
    graph = Graph("Sequence order")
    try:
        key, signal = graph.signal()
        sequence = graph.node("flow_sequence")
        one, _, _ = graph.sink("1", "A")
        nested, _, _ = graph.sink("1 child", "D")
        two, _, _ = graph.sink("2", "B")
        three, _, _ = graph.sink("3", "C")
        graph.link(key, "Trigger", sequence)
        graph.link(sequence, "Then 1", one)
        graph.link(one, "End", nested)
        graph.link(sequence, "Then 2", two)
        graph.link(sequence, "Then 3", three)
        graph.tick(0)
        graph.press(signal, 1)
        assert graph.labels() == ["1", "1 child", "2", "3"], graph.pulls
        assert not graph.tree.last_error, graph.tree.last_error
    finally:
        graph.close()
    graph = Graph("One source shared by a pulse")
    try:
        face = make_face()
        graph.faces.append(face)
        key, signal = graph.signal()
        sequence = graph.node("flow_sequence")
        first, source, _ = graph.sink("shared", "A")
        second = graph.node("apply_shapes")
        second.objects.add().object = face

        def shared(context, _node):
            graph.pulls.append(("shared", context.now))
            return {"Shape Keys Values": flow.ShapeValues({
                (graph.faces[0].name, "A"): 0.6, (face.name, "B"): 0.7})}

        graph.providers[str(source.runtime_id)] = shared
        graph.link(source, "Shape Keys Values", second, "Shape Keys Values")
        graph.link(key, "Trigger", sequence)
        graph.link(sequence, "Then 1", first)
        graph.link(sequence, "Then 2", second)
        graph.tick(0)
        graph.press(signal, 1)
        assert graph.labels() == ["shared"], "Pulse fanout evaluated the same data twice"
        assert graph.value("A") > 0.59 and face.data.shape_keys.key_blocks["B"].value > 0.69
    finally:
        graph.close()


def numeric_edge_tests():
    graph = Graph("Numeric Enter rising edges")
    try:
        key, signal = graph.signal(initial=1)
        sink, _, _ = graph.sink("edge")
        link = graph.link(key, "Trigger", sink)
        graph.tick(0)
        for now in (0.1, 0.2, 0.3):
            graph.tick(now)
        assert len(graph.pulls) == 1, "Held numeric trigger repeated"
        signal["value"] = 1e-8
        graph.tick(0.4)
        graph.press(signal, 0.5)
        assert len(graph.pulls) == 2
        graph.tree.links.remove(link)
        graph.tick(0.6)
        assert graph.value("A") == 0, "Disconnected Apply retained ownership"
        graph.link(key, "Trigger", sink)
        graph.tick(0.7)
        assert len(graph.pulls) == 3, "Reconnected high trigger did not rearm"
        sink.enabled = False
        graph.tick(0.8)
        assert graph.value("A") == 0
        sink.enabled = True
        graph.tick(0.9)
        assert len(graph.pulls) == 4, "Re-enabled numeric trigger did not rearm"
    finally:
        graph.close()


def end_failure_and_ownership_tests():
    graph = Graph("End depends on successful Apply")
    try:
        clock = graph.node("hz_trigger")
        first, _, failure = graph.sink("first", "A")
        second, _, _ = graph.sink("second", "B")
        graph.link(clock, "Trigger", first)
        graph.link(first, "End", second)
        graph.tick(0)
        graph.tick(1 / 60)
        assert graph.labels() == ["first", "second"]
        failure["broken"] = True
        graph.tick(2 / 60)
        assert graph.labels() == ["first", "second"], "Failed Apply emitted End"
        assert graph.value("A") == 0 and graph.value("B") == 0
        assert "Expected source failure" in graph.tree.last_error
    finally:
        graph.close()
    graph = Graph("Conflicting Apply ownership")
    try:
        clock = graph.node("hz_trigger")
        first, _, _ = graph.sink("owner1", "A")
        second, _, _ = graph.sink("owner2", "A")
        graph.link(clock, "Trigger", first)
        graph.link(clock, "Trigger", second)
        graph.tick(0)
        graph.tick(1 / 60)
        assert "already controls" in second.last_summary
        first.enabled = False
        graph.tick(2 / 60)
        assert second.last_summary == "Applied 1 values" and graph.value("A") > 0.59
    finally:
        graph.close()


def reload_edge_tests():
    graph = Graph("Reload numeric rising edges")
    rig = make_armature()
    try:
        key, signal = graph.signal()
        data = graph.node("vrm_data")
        data.armature = rig
        spring = graph.node("spring_simulation")
        # A connected closed Gate keeps the spring demanded while preventing
        # simulation pulls, so history cleanup cannot mask Reload edge behavior.
        gate = graph.node("flow_gate")
        gate.inputs["Open"].default_value = 0
        sink = graph.node("apply_rotation")
        sink.armature = rig
        graph.link(key, "Trigger", gate)
        graph.link(gate, "End", sink)
        graph.link(spring, "Bone Rotation", sink, "Bone Rotation")
        graph.link(key, "Trigger", data, "Reload")
        graph.link(key, "Trigger", spring, "Reload")
        revision = data.cache_revision
        graph.tick(0)
        graph.press(signal, 1)
        assert data.cache_revision == revision + 1
        uid = str(spring.runtime_id)
        flow._last_eval[uid] = 123
        for now in (1.1, 1.2, 1.3):
            graph.tick(now)
        assert data.cache_revision == revision + 1
        assert flow._last_eval.get(uid) == 123, "Held Reload cleared history repeatedly"
        graph.release(signal, 1.4)
        graph.press(signal, 1.5)
        assert data.cache_revision == revision + 2
        assert uid not in flow._last_eval, "Second Reload did not clear spring history"
    finally:
        graph.close()
        armature = rig.data
        bpy.data.objects.remove(rig, do_unlink=True)
        bpy.data.armatures.remove(armature)


def sequence_reload_before_apply_test():
    graph = Graph("Sequence reloads before Spring Apply")
    rig = make_armature()
    try:
        key, signal = graph.signal()
        sequence = graph.node("flow_sequence")
        data = graph.node("vrm_data")
        data.armature = rig
        spring = graph.node("spring_simulation")
        sink = graph.node("apply_rotation")
        sink.armature = rig
        snapshot = SpringData("1.0", ({"name": "Hair", "center": "head", "collider_groups": (),
            "joints": tuple({"bone": name, "hit_radius": 0.0, "stiffness": 0.5,
                             "gravity_power": 0.5, "gravity_dir": (0, 0, -1), "drag_force": 0.3}
                            for name in ("hairRoot", "hairTip"))},), (), (), rig.name)
        revisions = []

        def source(_context, _node):
            revisions.append(data.cache_revision)
            return {"Spring Data": snapshot}

        graph.providers[str(data.runtime_id)] = source
        graph.link(key, "Trigger", sequence)
        graph.link(sequence, "Then 1", data, "Reload")
        graph.link(sequence, "Then 2", sink)
        graph.link(data, "Spring Data", spring, "Spring Data")
        graph.link(spring, "Bone Rotation", sink, "Bone Rotation")
        revision = data.cache_revision
        graph.tick(0)
        graph.press(signal, 1)
        uid = str(spring.runtime_id)
        first_solver = flow._springs[uid]["solver"]
        assert revisions == [revision + 1] and not graph.tree.last_error, graph.tree.last_error
        graph.release(signal, 1.1)
        graph.press(signal, 1.2)
        assert revisions == [revision + 1, revision + 2]
        assert flow._springs[uid]["solver"] is not first_solver, "Sequence Apply reused solver history from before Reload"
        assert not graph.tree.last_error, graph.tree.last_error
    finally:
        graph.close()
        armature = rig.data
        bpy.data.objects.remove(rig, do_unlink=True)
        bpy.data.armatures.remove(armature)


def main():
    addon.register()
    try:
        for test in (declaration_tests, branch_and_gate_tests, alternative_branch_target_test, delay_tests, wait_tests,
                     once_and_counter_tests, sequence_and_shared_pull_tests, numeric_edge_tests,
                     end_failure_and_ownership_tests, reload_edge_tests, sequence_reload_before_apply_test):
            test()
            print("FLOW_CONTROL_PASS: " + test.__name__)
        print("YUGU_FLOW_CONTROLS_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
