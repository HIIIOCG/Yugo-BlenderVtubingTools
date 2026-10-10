"""Load the selected tracker and the complete repository Yugo host only."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import tomllib

import bpy

sys.dont_write_bytecode = True
ROOT = Path(os.environ["PHYSX_TEST_REPOSITORY"]).resolve(strict=True)
TRACKER = Path(os.environ["PHYSX_TEST_TRACKER"]).resolve(strict=True)
OUTPUT = Path(os.environ["PHYSX_TEST_OUTPUT"]).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)


def module(name, directory):
    expected = (directory / "__init__.py").resolve(strict=True)
    assert name not in sys.modules, f"Unexpected preloaded package: {name}"
    spec = importlib.util.spec_from_file_location(name, expected,
                                                submodule_search_locations=[str(directory)])
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    assert Path(result.__file__).resolve() == expected
    return result


def load():
    manifest = tomllib.loads((TRACKER / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert manifest["id"] == "yugo_physx_tracker", manifest
    yugo = module("yugo", ROOT / "addons/yugo")
    host = yugo.node
    assert Path(host.__file__).resolve() == ROOT / "addons/yugo/node/__init__.py"
    addon = module("yugo_physx_tracker", TRACKER)
    assert tuple(addon.bl_info["version"]) == tuple(map(int, manifest["version"].split(".")))
    print("PHYSX_TEST_SOURCE_OK: " + json.dumps({"host": yugo.__file__,
          "node": host.__file__, "tracker": addon.__file__, "version": manifest["version"]}), flush=True)
    return yugo, host, addon


def finish(name, **checks):
    result = {"ok": True, "blender": bpy.app.version_string,
              "mode": os.environ["PHYSX_TEST_MODE"], "tracker": str(TRACKER), **checks}
    (OUTPUT / "checks.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PHYSX_" + name.upper() + "_PASS " + json.dumps(result), flush=True)


def wait_for(predicate, tick=None, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if tick is not None:
            tick()
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise TimeoutError(f"Condition did not complete after {seconds:g} seconds")


def assert_clean(addon, host):
    assert not addon._registered
    assert not addon.runtime._worlds
    assert not addon.bridge._bindings and addon.bridge._token is None
    assert addon.nodes._numeric_ui is None and addon.nodes._point_ui is None
    assert not bpy.app.timers.is_registered(addon.runtime.tick)
    assert not bpy.app.timers.is_registered(addon._deferred_migrate)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        assert addon._invalidate not in handlers
    assert addon._migrate not in bpy.app.handlers.load_post
    assert addon.physics_inputs._geometry_changed not in bpy.app.handlers.depsgraph_update_post
    assert not addon.physics_inputs._tracked_meshes
    assert not addon.physics_inputs._mesh_signatures
    assert addon.data.PHYSICS not in host.nodes.NUMERIC_PARAMETERS
