"""Verify live receivers respect Blender's online-access preference."""

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yugo.ifacialmocap import runtime as ifacialmocap_runtime
from yugo.vmc import runtime as vmc_runtime


class FakeLayer:
    runtime_id = ""


def expect_offline_error(callback, label):
    try:
        callback()
    except RuntimeError as error:
        assert "Online access is disabled" in str(error), str(error)
    else:
        raise AssertionError(f"{label} started while Blender was offline")


def main():
    assert not bpy.app.online_access
    expect_offline_error(
        lambda: vmc_runtime.start(None, None, FakeLayer(), None),
        "VMC",
    )
    expect_offline_error(
        lambda: ifacialmocap_runtime.start(None, FakeLayer(), None),
        "iFacialMocap",
    )
    print("YUGO_NETWORK_PERMISSION_OK")


if __name__ == "__main__":
    main()
