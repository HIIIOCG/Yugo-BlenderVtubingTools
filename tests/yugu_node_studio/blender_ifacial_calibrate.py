"""Calibrate action sockets, rising edges, and fresh same-event data pulls."""

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
from yugu_node_studio import flow, network, nodes, operators
from yugu_node_studio.constants import SOCKET_TRIGGER
from blender_flow_controls import Graph, Layout


def receive(node, head=None):
    # Keep the real receiver snapshot/lock and frame-calibration conversion,
    # supplying captured frames without a device or background socket thread.
    receiver = network.IFacialReceiver("127.0.0.1", 49983, "127.0.0.1", 49986)
    receiver.head = head
    receiver.expressions = {"eyeBlinkLeft": 35.0}
    network._ifacial_receivers[str(node.runtime_id)] = receiver
    return receiver


def close(graph, node):
    network._ifacial_receivers.pop(str(node.runtime_id), None)
    graph.close()


def close_to(values, expected):
    assert all(abs(float(a) - b) < 1e-6 for a, b in zip(values, expected)), (values, expected)


def socket_and_migration_test():
    graph = Graph("Calibrate socket migration")
    source = graph.node("ifacial_source")
    try:
        assert source.inputs[-1].name == "Calibrate"
        assert source.inputs[-1].bl_idname == SOCKET_TRIGGER
        layout = Layout()
        source.inputs[-1].draw(bpy.context, layout, source, "Calibrate")
        assert layout.records == [
            ("label", "Calibrate"),
            ("operator", "yugu_node_studio.ifacial_calibrate", "Calibrate"),
        ], layout.records
        # The old top button must no longer be duplicated in draw_buttons.
        layout = Layout()
        source.draw_buttons(bpy.context, layout)
        assert not any(record[:2] == ("operator", "yugu_node_studio.ifacial_calibrate")
                       for record in layout.records)
        source.inputs.remove(source.inputs["Calibrate"])
        assert nodes.ensure_current_sockets()
        socket = source.inputs["Calibrate"]
        identity = socket.identifier
        key, _signal = graph.signal()
        graph.link(key, "Trigger", source, "Calibrate")
        source.inputs.new(SOCKET_TRIGGER, "Older Input")
        assert nodes.ensure_current_sockets() and nodes.ensure_current_sockets()
        assert source.inputs[-1].name == "Calibrate"
        assert source.inputs[-1].identifier == identity
        assert source.inputs[-1].links[0].from_node == key
    finally:
        close(graph, source)


def sample_and_manual_button_test():
    graph = Graph("Calibrate sample and manual action")
    source = graph.node("ifacial_source")
    try:
        previous = (1.0, 2.0, 3.0)
        source.calibration = previous
        reports = []
        operator = SimpleNamespace(tree_name=graph.tree.name, node_name=source.name,
                                   report=lambda level, message: reports.append((level, message)))
        assert operators.YUGUS_OT_ifacial_calibrate.execute(operator, bpy.context) == {"CANCELLED"}
        close_to(source.calibration, previous)
        assert not network.is_running(str(source.runtime_id)), "Calibration started a receiver"
        receiver = receive(source)
        assert operators.YUGUS_OT_ifacial_calibrate.execute(operator, bpy.context) == {"CANCELLED"}
        close_to(source.calibration, previous)
        assert reports[-1][1] == "No iFacialMocap head frame received yet"
        receiver.head = (15.0, -20.0, 8.0)
        source.connected = True
        assert bpy.ops.yugu_node_studio.ifacial_calibrate(
            tree_name=graph.tree.name, node_name=source.name) == {"FINISHED"}
        close_to(source.calibration, receiver.head)
        assert not source.last_error
        assert network._ifacial_receivers[str(source.runtime_id)] is receiver
        assert receiver.expressions == {"eyeBlinkLeft": 35.0}
    finally:
        close(graph, source)


def numeric_edge_and_failure_test():
    graph = Graph("Calibrate rising edges")
    source = graph.node("ifacial_source")
    try:
        previous = (1.0, 2.0, 3.0)
        source.calibration = previous
        key, signal = graph.signal()
        graph.link(key, "Trigger", source, "Calibrate")
        graph.tick(0)
        graph.press(signal, 1)
        close_to(source.calibration, previous)
        assert "No iFacialMocap head frame" in graph.tree.last_error
        assert "No iFacialMocap head frame" in source.last_error
        assert not network.is_running(str(source.runtime_id))
        receiver = receive(source)
        graph.release(signal, 1.1)
        graph.press(signal, 1.2)
        close_to(source.calibration, previous)
        assert "No iFacialMocap head frame" in graph.tree.last_error
        receiver.head = (10.0, 25.0, -5.0)
        # A held trigger cannot retry the previous failed action automatically.
        graph.tick(1.3)
        close_to(source.calibration, previous)
        graph.release(signal, 1.4)
        graph.press(signal, 1.5)
        close_to(source.calibration, (10.0, 25.0, -5.0))
        assert not source.last_error and not graph.tree.last_error
        receiver.head = (-30.0, 12.0, 6.0)
        graph.tick(1.6)
        graph.tick(1.7)
        close_to(source.calibration, (10.0, 25.0, -5.0))
        graph.release(signal, 1.8)
        graph.press(signal, 1.9)
        close_to(source.calibration, receiver.head)
        assert network._ifacial_receivers[str(source.runtime_id)] is receiver
    finally:
        close(graph, source)


def snapshot_sink(graph, source, label, key, measurements):
    sink, conversion, _failure = graph.sink(label, key)
    graph.link(source, "Expressions", conversion, "Expressions")

    def values(context, _node):
        motion = context.evaluate(source)["Humanoid Motion"]
        rotation = motion.bones["head"].rotation
        measurement = abs(rotation.x) + abs(rotation.y) + abs(rotation.z)
        measurements.append((label, measurement))
        return {"Shape Keys Values": flow.ShapeValues({(graph.faces[0].name, key): measurement})}

    graph.providers[str(conversion.runtime_id)] = values
    return sink


def sequence_fresh_pull_test():
    graph = Graph("Sequence caches refresh after Calibrate")
    source = graph.node("ifacial_source")
    try:
        receiver = receive(source, (20.0, 30.0, 15.0))
        measurements = []
        before = snapshot_sink(graph, source, "before", "A", measurements)
        after = snapshot_sink(graph, source, "after", "B", measurements)
        key, signal = graph.signal()
        sequence = graph.node("flow_sequence")
        graph.link(key, "Trigger", sequence)
        graph.link(sequence, "Then 1", before)
        graph.link(sequence, "Then 2", source, "Calibrate")
        graph.link(sequence, "Then 3", after)
        graph.tick(0)
        graph.press(signal, 1)
        assert [label for label, _value in measurements] == ["before", "after"]
        assert measurements[0][1] > 0.1, measurements
        assert measurements[1][1] < 1e-6, "After Calibrate reused the source cache"
        assert graph.value("A") > 0.1 and graph.value("B") < 1e-6
        close_to(source.calibration, receiver.head)
        assert not graph.tree.last_error, graph.tree.last_error
    finally:
        close(graph, source)


def fanout_and_clock_test():
    graph = Graph("Calibrate fanout before data pulls")
    source = graph.node("ifacial_source")
    try:
        receiver = receive(source, (10.0, 20.0, 30.0))
        measurements = []
        sink = snapshot_sink(graph, source, "after", "A", measurements)
        key, signal = graph.signal()
        # Link Apply first to prove source-action priority beats link order.
        graph.link(key, "Trigger", sink)
        graph.link(key, "Trigger", source, "Calibrate")
        graph.tick(0)
        graph.press(signal, 1)
        assert measurements[0][1] < 1e-6, measurements
        for link in tuple(source.inputs["Calibrate"].links):
            graph.tree.links.remove(link)
        clock = graph.node("hz_trigger")
        graph.link(clock, "Trigger", source, "Calibrate")
        receiver.head = (-20.0, 7.0, 12.0)
        graph.tick(2)
        graph.tick(2 + 1 / 60)
        close_to(source.calibration, receiver.head)
        assert not graph.tree.last_error, graph.tree.last_error
    finally:
        close(graph, source)


def missing_head_retires_cached_frame_test():
    graph = Graph("Failed Calibrate cannot retain an old cached head frame")
    source = graph.node("ifacial_source")
    try:
        previous = (1.0, 2.0, 3.0)
        source.calibration = previous
        receiver = receive(source, (10.0, 20.0, 30.0))
        pulse = flow._Pulse(graph.tree, bpy.context.scene, 1.0, 1 / 60, set())
        before = pulse.context.evaluate(source)
        assert "head" in before["Humanoid Motion"].bones
        receiver.head = None
        pulse.receive(source.inputs["Calibrate"])
        close_to(source.calibration, previous)
        assert "No iFacialMocap head frame" in graph.tree.last_error
        assert not pulse.context.cache
        after = pulse.context.evaluate(source)
        assert "head" not in after["Humanoid Motion"].bones
        assert after is not before
        assert network._ifacial_receivers[str(source.runtime_id)] is receiver
    finally:
        close(graph, source)


def main():
    addon.register()
    try:
        for test in (socket_and_migration_test, sample_and_manual_button_test,
                     numeric_edge_and_failure_test, sequence_fresh_pull_test,
                     fanout_and_clock_test, missing_head_retires_cached_frame_test):
            test()
            print("IFACIAL_CALIBRATE_PASS: " + test.__name__)
        print("YUGU_IFACIAL_CALIBRATE_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
