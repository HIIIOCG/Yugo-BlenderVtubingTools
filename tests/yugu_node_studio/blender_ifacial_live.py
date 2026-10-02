"""Run with Blender to verify the complete iFacialMocap TCP source path."""

from __future__ import annotations

from pathlib import Path
import socket
import sys
import time

import bpy


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio
from yugu_node_studio import network, runtime
from yugu_node_studio.constants import ARKIT_BLENDSHAPES, TREE_ID, normalize_name
from yugu_node_studio.protocols import (
    IFACIAL_FRAME_DELIMITER,
    IFACIAL_START_COMMAND,
    IFACIAL_STOP_COMMAND,
)


def available_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def wire_name(name):
    if name.endswith("Left"):
        return name[:-4] + "_L"
    if name.endswith("Right"):
        return name[:-5] + "_R"
    return name


def main():
    yugu_node_studio.register()
    tree = bpy.data.node_groups.new("iFacial Live Test", TREE_ID)
    node = tree.nodes.new("YUGUS_NODE_ifacial_source")
    node.address = "127.0.0.1"
    node.listen_address = "127.0.0.1"
    node.listen_port = available_tcp_port()

    command_receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    command_receiver.settimeout(2.0)
    command_receiver.bind(("127.0.0.1", 0))
    node.device_port = command_receiver.getsockname()[1]
    sender = None
    try:
        network.start_ifacial(
            node.runtime_id,
            node.address,
            node.device_port,
            node.listen_address,
            node.listen_port,
        )
        command, _address = command_receiver.recvfrom(2048)
        assert command.decode("utf-8") == IFACIAL_START_COMMAND

        values = {
            name: (0.5 if index == 0 else float((index * 7) % 101))
            for index, name in enumerate(ARKIT_BLENDSHAPES)
        }
        fields = [
            f"{wire_name(name)}&{value}" for name, value in values.items()
        ]
        fields.append("=head#5,10,15,0,0,0")
        payload = ("|".join(fields) + "|" + IFACIAL_FRAME_DELIMITER).encode(
            "utf-8"
        )
        sender = socket.create_connection(("127.0.0.1", node.listen_port), timeout=2.0)
        cuts = (17, 193, 509, len(payload))
        start = 0
        for end in cuts:
            sender.sendall(payload[start:end])
            start = end

        deadline = time.monotonic() + 2.0
        while network.status(node.runtime_id)["age"] is None:
            assert time.monotonic() < deadline, network.status(node.runtime_id)
            time.sleep(0.01)

        source = runtime._ifacial_source_values(node, 1.0 / 60.0)
        expressions = source["Expressions"].values
        assert len(expressions) == len(ARKIT_BLENDSHAPES), len(expressions)
        for name, percentage in values.items():
            output = expressions[normalize_name(name)]
            assert abs(output - percentage / 100.0) < 1.0e-8, (
                name,
                percentage,
                output,
            )
        assert abs(node.blendshape_masks[0].raw_value - 0.5) < 1.0e-8
        assert abs(node.blendshape_masks[0].output_value - 0.005) < 1.0e-8

        network.stop(node.runtime_id)
        stop_command, _address = command_receiver.recvfrom(2048)
        assert stop_command.decode("utf-8") == IFACIAL_STOP_COMMAND
        print("YUGUS_NODE_IFACIAL_LIVE_OK")
    finally:
        if sender is not None:
            sender.close()
        network.stop(node.runtime_id)
        command_receiver.close()
        yugu_node_studio.unregister()


if __name__ == "__main__":
    main()
