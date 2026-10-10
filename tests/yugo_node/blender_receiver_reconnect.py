"""Real localhost VMC/iFacial reconnect and receiver-health regressions.

Run with --background --factory-startup; no phone or user scene is required.
All ports are temporary loopback ports and every worker is stopped on exit.
"""
import importlib.util
from pathlib import Path
import socket
import struct
import sys
import time
from unittest.mock import patch

import bpy

from _bootstrap import ROOT, load_node, output_directory

addon = load_node()
_package_directory = None


def temporary_port(kind):
    with socket.socket(socket.AF_INET, kind) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def wait_for(predicate, description):
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(description)


def press(node, action="CONNECT"):
    operator = (bpy.ops.yugu_node_studio.vmc_connection
                if node.bl_idname == "YUGUS_NODE_vmc_source"
                else bpy.ops.yugu_node_studio.source_connection)
    assert operator(tree_name=node.id_data.name, node_name=node.name,
                    action=action) == {"FINISHED"}


def stopped(worker):
    assert worker.thread is None or not worker.thread.is_alive()
    assert worker.socket is None or worker.socket.fileno() == -1


def osc_string(value):
    raw = value.encode() + bytes([0])
    return raw + bytes((-len(raw)) % 4)


def vmc_frame(node, value):
    packet = (osc_string("/VMC/Ext/Blend/Val") + osc_string(",sf")
              + osc_string("Smile") + struct.pack(">f", value))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(packet, ("127.0.0.1", node.port))
    worker = addon.network._vmc_receivers[node.runtime_id]
    wait_for(lambda: worker.snapshot()[1].values.get("smile") == value,
             "VMC packet did not reach the requested connection")


def phone_command(phone, expected):
    phone.settimeout(3.0)
    actual = phone.recvfrom(4096)[0].decode("utf-8")
    assert actual == expected, (actual, expected)


def no_phone_command(phone):
    phone.settimeout(0.05)
    try:
        unexpected = phone.recvfrom(4096)[0]
    except socket.timeout:
        return
    raise AssertionError(f"Unexpected iFacial command: {unexpected!r}")


def phone_frame(node, value):
    worker = addon.network._ifacial_receivers[node.runtime_id]
    with socket.create_connection(("127.0.0.1", node.listen_port), timeout=3.0) as client:
        client.sendall((f"=head#{value},2,3|jawOpen-{value}"
                        + "___iFacialMocap").encode("utf-8"))
        wait_for(lambda: addon.network.ifacial_head(node.runtime_id) == (value, 2.0, 3.0),
                 "iFacial head did not reach the requested connection")
        assert worker.snapshot((0.0, 0.0, 0.0))[1]["jawOpen"] == value
    wait_for(lambda: worker.client is None, "iFacial did not release its TCP client")


def expect_failure(node):
    try:
        addon.flow.source_connection_action(node, "CONNECT")
    except OSError:
        pass
    else:
        raise AssertionError("The injected connection failure was not reported")
    assert not node.connected and node.last_error
    assert not addon.network.is_running(node.runtime_id)
    assert node.runtime_id not in addon.network._vmc_receivers
    assert node.runtime_id not in addon.network._ifacial_receivers


addon.register()
try:
    tree = bpy.data.node_groups.new("ReceiverReconnectTest", "YUGUS_NT_humanoid_motion")
    vmc = tree.nodes.new("YUGUS_NODE_vmc_source")
    vmc.address = "127.0.0.1"
    vmc.port = temporary_port(socket.SOCK_DGRAM)
    press(vmc)
    worker = addon.network._vmc_receivers[vmc.runtime_id]
    assert addon.network.status(vmc.runtime_id)["running"]
    assert addon.network.status(vmc.runtime_id)["age"] is None
    press(vmc)  # A healthy receiver remains valid before its first packet.
    assert addon.network._vmc_receivers[vmc.runtime_id] is worker
    vmc_frame(vmc, 0.25)
    press(vmc)
    assert addon.network._vmc_receivers[vmc.runtime_id] is worker
    assert worker.snapshot()[1].values["smile"] == 0.25

    vmc.port = temporary_port(socket.SOCK_DGRAM)
    press(vmc)
    replacement = addon.network._vmc_receivers[vmc.runtime_id]
    assert replacement is not worker, "VMC Connect ignored the edited port"
    assert replacement.port == vmc.port
    stopped(worker)
    vmc_frame(vmc, 0.75)
    worker = replacement
    vmc.address = "0.0.0.0"
    addon.flow._Pulse(tree, bpy.context.scene, 10.0, 0.0, set()).receive(vmc.inputs["Connect"])
    replacement = addon.network._vmc_receivers[vmc.runtime_id]
    assert replacement is not worker and replacement.address == "0.0.0.0"
    stopped(worker)
    vmc_frame(vmc, 0.5)
    worker = replacement
    vmc.address = " 0.0.0.0 "
    press(vmc)
    assert addon.network._vmc_receivers[vmc.runtime_id] is worker

    # Unexpected socket failure leaves the old registry entry, but it is not live.
    worker.socket.close()
    wait_for(lambda: not worker.thread.is_alive(), "VMC worker did not exit after socket failure")
    assert not addon.network.is_running(vmc.runtime_id)
    assert not addon.network.status(vmc.runtime_id)["running"]
    press(vmc)
    assert addon.network._vmc_receivers[vmc.runtime_id] is not worker
    stopped(worker)
    vmc_frame(vmc, 1.0)

    # A real bind failure closes the newly created socket and remains retryable.
    captured = []
    original_vmc_start = addon.network.VMCReceiver.start
    def capture_vmc_start(receiver):
        captured.append(receiver)
        original_vmc_start(receiver)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reservation.bind(("127.0.0.1", 0))
        vmc.address = "127.0.0.1"
        vmc.port = reservation.getsockname()[1]
        with patch.object(addon.network.VMCReceiver, "start", capture_vmc_start):
            expect_failure(vmc)
        stopped(captured[-1])
    captured = []
    with patch.object(addon.network.VMCReceiver, "start", capture_vmc_start), \
         patch.object(addon.network.threading.Thread, "start",
                      side_effect=OSError("Injected VMC thread startup failure")):
        expect_failure(vmc)
    assert vmc.last_error == "Injected VMC thread startup failure"
    stopped(captured[-1])
    press(vmc)
    assert vmc.connected and not vmc.last_error
    vmc_frame(vmc, 0.125)
    worker = addon.network._vmc_receivers[vmc.runtime_id]
    press(vmc, "DISCONNECT")
    stopped(worker)
    press(vmc, "DISCONNECT")

    phone = tree.nodes.new("YUGUS_NODE_ifacial_source")
    phone.address = "127.0.0.1"
    phone.listen_address = "127.0.0.1"
    phone.listen_port = temporary_port(socket.SOCK_STREAM)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as first_phone, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as second_phone:
        first_phone.bind(("127.0.0.1", 0))
        second_phone.bind(("127.0.0.1", 0))
        phone.device_port = first_phone.getsockname()[1]
        press(phone)
        phone_command(first_phone, addon.network.IFACIAL_START_COMMAND)
        worker = addon.network._ifacial_receivers[phone.runtime_id]
        press(phone)  # Retry a missing first handshake without restarting the listener.
        phone_command(first_phone, addon.network.IFACIAL_START_COMMAND)
        assert addon.network._ifacial_receivers[phone.runtime_id] is worker
        assert addon.network.status(phone.runtime_id)["running"]
        assert addon.network.status(phone.runtime_id)["age"] is None
        for now in (10.0, 10.1):
            addon.flow._Pulse(tree, bpy.context.scene, now, 0.0, set()).receive(phone.inputs["Connect"])
        no_phone_command(first_phone)  # Fast event pulses cannot flood the phone.
        time.sleep(1.05)
        addon.flow._Pulse(tree, bpy.context.scene, 11.2, 0.0, set()).receive(phone.inputs["Connect"])
        phone_command(first_phone, addon.network.IFACIAL_START_COMMAND)
        addon.flow._Pulse(tree, bpy.context.scene, 11.3, 0.0, set()).receive(phone.inputs["Connect"])
        no_phone_command(first_phone)
        with socket.create_connection(("127.0.0.1", phone.listen_port), timeout=3.0) as client:
            client.sendall(b"=head#11,2,3|jawOpen-11___iFacialMocap")
            wait_for(lambda: addon.network.ifacial_head(phone.runtime_id) == (11.0, 2.0, 3.0),
                     "iFacial did not receive the initial phone frame")
            press(phone)  # An active TCP client is already streaming.
            no_phone_command(first_phone)
            assert addon.network._ifacial_receivers[phone.runtime_id] is worker
        wait_for(lambda: worker.client is None, "iFacial did not release disconnected client")
        press(phone)  # Phone restart/TCP loss only needs a new START handshake.
        phone_command(first_phone, addon.network.IFACIAL_START_COMMAND)
        assert addon.network._ifacial_receivers[phone.runtime_id] is worker
        phone_frame(phone, 12.0)
        assert addon.network.ifacial_head(phone.runtime_id) == (12.0, 2.0, 3.0)

        phone.device_port = second_phone.getsockname()[1]
        press(phone)
        phone_command(first_phone, addon.network.IFACIAL_STOP_COMMAND)
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        replacement = addon.network._ifacial_receivers[phone.runtime_id]
        assert replacement is not worker, "iFacial Connect ignored the edited device port"
        assert replacement.device_port == phone.device_port
        stopped(worker)
        worker = replacement
        phone_frame(phone, 22.0)

        # Each connection parameter must take effect on one Connect action.
        for name, value, frame in (
                ("address", "localhost", 33.0),
                ("listen_address", "0.0.0.0", 44.0),
                ("listen_port", temporary_port(socket.SOCK_STREAM), 55.0)):
            setattr(phone, name, value)
            addon.flow._Pulse(tree, bpy.context.scene, frame, 0.0, set()).receive(phone.inputs["Connect"])
            phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
            phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
            replacement = addon.network._ifacial_receivers[phone.runtime_id]
            assert replacement is not worker, f"iFacial Connect ignored the edited {name}"
            stopped(worker)
            worker = replacement
            phone_frame(phone, frame)
        phone.address = " localhost "
        phone.listen_address = " 0.0.0.0 "
        phone.mirror = not phone.mirror
        phone.calibration = (1.0, 2.0, 3.0)
        phone.name = "Renamed Phone"
        tree.name = "Renamed Receiver Graph"
        press(phone)
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        assert addon.network._ifacial_receivers[phone.runtime_id] is worker

        worker.socket.close()
        wait_for(lambda: not worker.thread.is_alive(), "iFacial worker did not exit after socket failure")
        assert not addon.network.is_running(phone.runtime_id)
        assert not addon.network.status(phone.runtime_id)["running"]
        press(phone)
        phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        replacement = addon.network._ifacial_receivers[phone.runtime_id]
        assert replacement is not worker
        stopped(worker)
        worker = replacement
        phone_frame(phone, 66.0)

        captured = []
        original_phone_start = addon.network.IFacialReceiver.start
        def capture_phone_start(receiver):
            captured.append(receiver)
            original_phone_start(receiver)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            reservation.bind(("127.0.0.1", 0))
            reservation.listen(1)
            phone.listen_address = "127.0.0.1"
            phone.listen_port = reservation.getsockname()[1]
            with patch.object(addon.network.IFacialReceiver, "start", capture_phone_start):
                expect_failure(phone)
            stopped(worker)
            stopped(captured[-1])
            phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
            phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        captured = []
        with patch.object(addon.network.IFacialReceiver, "start", capture_phone_start), \
             patch.object(addon.network.threading.Thread, "start",
                          side_effect=OSError("Injected iFacial thread startup failure")):
            expect_failure(phone)
        assert phone.last_error == "Injected iFacial thread startup failure"
        stopped(captured[-1])
        phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        press(phone)
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        phone_frame(phone, 77.0)

        # Failure after listener/worker startup must also close all resources.
        press(phone, "DISCONNECT")
        phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        captured = []
        def partially_started_phone(receiver):
            captured.append(receiver)
            original_phone_start(receiver)
            raise OSError("Injected failure after iFacial listener startup")
        with patch.object(addon.network.IFacialReceiver, "start", partially_started_phone):
            expect_failure(phone)
        stopped(captured[-1])
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        press(phone)
        phone_command(second_phone, addon.network.IFACIAL_START_COMMAND)
        assert phone.connected and not phone.last_error
        phone_frame(phone, 88.0)
        worker = addon.network._ifacial_receivers[phone.runtime_id]
        press(phone, "DISCONNECT")
        phone_command(second_phone, addon.network.IFACIAL_STOP_COMMAND)
        stopped(worker)
        press(phone, "DISCONNECT")
    print("RECEIVER_RECONNECT_OK", bpy.app.version_string, addon.bl_info["version"])
finally:
    addon.unregister()
