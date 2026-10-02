"""Validate safe runtime shutdown around Blender file replacement."""

from pathlib import Path
import sys

import bpy


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo
from yugo import camera, scheduler, undo_safety
from yugo.keybinds import runtime as keybinds_runtime
from yugo.vmc import runtime as vmc_runtime


class ExplodingVmcTarget:
    def clear(self):
        raise AssertionError("File loading must not write through old VMC RNA")


class FakeKeybindTarget:
    def __init__(self):
        self.reset_arguments = []

    def close(self, *, reset=True):
        self.reset_arguments.append(reset)


def main():
    yugo.register()
    receiver = vmc_runtime._Receiver("127.0.0.1", 0)
    fake_keybind = FakeKeybindTarget()
    try:
        receiver.targets["old-layer"] = ExplodingVmcTarget()
        vmc_runtime._receivers[receiver.key] = receiver
        vmc_runtime._layer_receivers["old-layer"] = receiver
        keybinds_runtime._targets[1] = fake_keybind
        camera._owner = object()
        camera._runtime_scene = bpy.context.scene

        motion_ticks = []
        scheduler.add_motion(
            "test_load_pause",
            lambda _context, _delta: motion_ticks.append(1),
            priority=50,
        )

        undo_safety._load_pre(None)
        assert scheduler.is_suspended()
        assert receiver.socket is None
        assert not vmc_runtime._receivers
        assert not vmc_runtime._layer_receivers
        assert fake_keybind.reset_arguments == [False]
        assert not keybinds_runtime._targets
        assert camera._owner is None
        assert camera._runtime_scene is None

        scheduler._motion_timer()
        assert not motion_ticks

        undo_safety._load_post(None)
        assert not scheduler.is_suspended()
        scheduler._motion_timer()
        assert motion_ticks == [1]
        scheduler.remove_motion("test_load_pause")
        print("Yugo file-load runtime safety test passed")
    finally:
        yugo.unregister()


main()
