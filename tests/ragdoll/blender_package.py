"""Exercise the actual extracted extension ZIP, without a source fallback."""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_demo import make_character, make_graph


def maximum_difference(saved, armature):
    return max(abs(old[row][column] - armature.pose.bones[name].matrix_basis[row][column])
               for name, old in saved.items() for row in range(4) for column in range(4))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args(argv)
    assert args.package.is_file(), args.package
    extraction_parent = ROOT / "artifacts" / "package-check"
    extraction_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zip-", dir=extraction_parent) as temporary:
        module_root = Path(temporary) / "yugo_ragdoll"
        module_root.mkdir()
        with zipfile.ZipFile(args.package) as archive:
            names = archive.namelist()
            assert "blender_manifest.toml" in names
            assert "__init__.py" in names and "solver.py" in names and "service.py" in names
            assert all(not name.endswith(".pyc") and "__pycache__" not in name for name in names)
            for info in archive.infolist():
                destination = (module_root / info.filename).resolve()
                assert destination.is_relative_to(module_root.resolve()), info.filename
            archive.extractall(module_root)
        assert "yugo_ragdoll" not in sys.modules, "Package was imported before extraction"
        sys.path.insert(0, str(Path(temporary)))
        addon = importlib.import_module("yugo_ragdoll")
        assert Path(addon.__file__).resolve().parent == module_root.resolve()
        addon.register()
        from yugo_ragdoll import preview, service, solver
        for module in (preview, service, solver):
            assert Path(module.__file__).resolve().parent == module_root.resolve()
        armature = make_character("Extracted ZIP Character")
        tree, source, ragdoll, apply = make_graph(armature)
        bpy.context.scene.frame_set(42, subframe=0.25)
        saved = {bone.name: bone.matrix_basis.copy() for bone in armature.pose.bones}
        start = preview.start(tree, apply)
        assert start["status"] not in {"FAILED", "CANCELLED", "STOPPED"}, start
        preview.hit(tree, ragdoll)
        for _ in range(30):
            snapshot = preview.step(tree, apply, 1.0 / 60)
        assert snapshot["status"] not in {"FAILED", "CANCELLED", "STOPPED"}, snapshot
        assert maximum_difference(saved, armature) > 1e-4, "Extracted ZIP did not move the character"
        assert bpy.context.scene.frame_current == 42
        assert abs(bpy.context.scene.frame_subframe - 0.25) < 1e-6
        first = preview.read(tree, apply)
        second = preview.read(tree, apply)
        assert first == second, "Reading Motion advanced simulation"
        preview.stop(tree, apply)
        assert maximum_difference(saved, armature) < 1e-6, "Stop failed to restore original pose"
        assert apply.status in {"STOPPED", "CANCELLED"}
        addon.unregister()
        addon.register()
        addon.unregister()
        print("RAGDOLL_PACKAGE_OK", args.package)


if __name__ == "__main__":
    main()
