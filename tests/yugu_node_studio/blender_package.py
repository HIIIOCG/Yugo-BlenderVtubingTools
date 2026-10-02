"""Validate the delivered zip in an isolated import namespace, not the source tree."""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import zipfile

import bpy
from _bpy_restrict_state import RestrictBlend

root = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
version = tomllib.loads((root / "addons/yugu_node_studio/blender_manifest.toml").read_text(encoding="utf-8"))["version"]
archive = Path(os.environ.get("NODE_PACKAGE_PATH", root / f"dist/yugu_node_studio-{version}.zip")).resolve()
expected_preset = (Path(__file__).resolve().parent / "fixtures/preset.sha256").read_text(encoding="utf-8").split()[0]
assert hashlib.sha256((root / "addons/yugu_node_studio/preset.blend").read_bytes()).hexdigest() == expected_preset
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
    names = package.namelist()
    assert "preset.blend" in names and "blender_manifest.toml" in names
    assert not any(name.startswith("tests/") or "__pycache__" in name for name in names)
    for name in names:
        if name.endswith(".py"):
            ast.parse(package.read(name).decode("utf-8-sig"), filename=name)
    assert hashlib.sha256(package.read("preset.blend")).hexdigest() == expected_preset
    assert tomllib.loads(package.read("blender_manifest.toml").decode("utf-8"))["version"] == version
    with tempfile.TemporaryDirectory(prefix="yugu-studio-package-") as folder:
        package.extractall(folder)
        spec = importlib.util.spec_from_file_location("packaged_studio", Path(folder) / "__init__.py", submodule_search_locations=[folder])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert Path(module.__file__).resolve() == Path(folder).resolve() / "__init__.py"
        assert ".".join(map(str, module.bl_info["version"])) == version
        print(f"NODE_TEST_SOURCE_OK: {Path(module.__file__).resolve()}")
        with RestrictBlend():
            module.register()
        assert bpy.ops.yugu_node_studio.new_graph() == {"FINISHED"}
        tree = bpy.context.scene.yugu_node_studio_tree
        for _menu, entries in module.nodes.NODE_MENU[5:]:
            for node_type, _label in entries:
                tree.nodes.new(node_type)
        sample, definition = module.workflow_ui.build_demo("Packaged stage one")
        assert module.workflow_ui.cards_from_graph(definition).supported
        start = next(node for node in sample.nodes if node.label == "Start sample")
        run_id = module.flow.emit_event(sample, start, "Trigger", {"source": "package"}, now=0)
        for tick in range(1, 51):
            module.flow.tick(tick * .1)
        assert module.workflow.status(run_id)["state"] == "end"
        assert any(item["payload"].get("source") == "package" for item in module.workflow.logs)
        for node_type in ("YUGUS_NODE_vrm_data", "YUGUS_NODE_spring_simulation"):
            node = tree.nodes.new(node_type)
            assert node.inputs[-1].name == "Reload"
        ifacial = tree.nodes.new("YUGUS_NODE_ifacial_source")
        assert ifacial.inputs[-1].name == "Calibrate"
        module.nodes.ensure_current_sockets()
        assert all(node.inputs[-1].name == "Reload" for node in tree.nodes
                   if node.bl_idname in {"YUGUS_NODE_vrm_data", "YUGUS_NODE_spring_simulation"})
        assert ifacial.inputs[-1].name == "Calibrate"
        assert bpy.ops.yugu_node_studio.composer_reload() == {"FINISHED"}
        assert bpy.context.scene.yugus_composing.initialized
        outline_material = module.studio.outline_bake._append_outline_material()
        assert outline_material.name.startswith("Black_OutlineFast")
        bpy.data.materials.remove(outline_material)
        with RestrictBlend():
            module.unregister()
        assert not hasattr(bpy.types.Scene, "yugu_node_studio_tree")
print("YUGU_STUDIO_PACKAGE_OK")
