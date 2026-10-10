"""Load the complete fusion source or delivered ZIP in each isolated process."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tomllib
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("FUSION_TEST_REPOSITORY", Path(__file__).resolve().parents[2])).resolve()
LEGACY_PRESET_SHA256 = "a477e4417bf89877346beab2b720ddfd8959bf72f82b475631718372d691e119"
_root = None
_directory = None


def output_directory():
    path = Path(os.environ.get("FUSION_TEST_OUTPUT", ROOT / "artifacts/fusion-local")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_package(archive_path, destination):
    """Reject escaping, duplicate and symbolic-link entries before extraction."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen = set()
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None, "ZIP integrity failed"
        for member in archive.infolist():
            name = member.filename
            assert name and "\\" not in name and ":" not in name, name
            assert not name.startswith("/"), name
            target = (destination / name).resolve()
            assert target.is_relative_to(destination), name
            assert not stat.S_ISLNK(member.external_attr >> 16), name
            assert name.casefold() not in seen, f"Duplicate ZIP entry: {name}"
            seen.add(name.casefold())
        archive.extractall(destination)
    return destination


def package_directory():
    global _directory
    if _directory is None:
        package = os.environ.get("FUSION_TEST_PACKAGE")
        _directory = (extract_package(Path(package).resolve(strict=True), output_directory() / "extracted")
                      if package else ROOT / "addons/yugo")
        manifest = tomllib.loads((_directory / "blender_manifest.toml").read_text(encoding="utf-8"))
        assert manifest["id"] == "yugo", manifest
        assert hashlib.sha256((_directory / "preset.blend").read_bytes()).hexdigest() == LEGACY_PRESET_SHA256
    return _directory


def load_root():
    global _root
    if _root is None:
        expected = package_directory() / "__init__.py"
        assert expected.is_file(), expected
        existing = sys.modules.get("yugo")
        if existing is not None:
            assert Path(existing.__file__).resolve() == expected, existing.__file__
            _root = existing
        else:
            spec = importlib.util.spec_from_file_location("yugo", expected,
                                                         submodule_search_locations=[str(expected.parent)])
            _root = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = _root
            spec.loader.exec_module(_root)
        assert Path(_root.__file__).resolve() == expected
        mode = "package" if os.environ.get("FUSION_TEST_PACKAGE") else "source"
        print(f"FUSION_TEST_SOURCE_OK: {mode}: {expected}", flush=True)
    return _root


def load_yugo():
    root = load_root()
    print(f"YUGO_TEST_SOURCE_OK: {root.__file__}", flush=True)
    return root


class NodeUnderFusion:
    """Keep original Node regression assertions while testing root lifecycle."""
    def __init__(self, root):
        self.root = root
        self.node = root.node

    def register(self):
        return self.root.register()

    def unregister(self):
        return self.root.unregister()

    def __getattr__(self, name):
        return getattr(self.node, name)


def load_node():
    root = load_root()
    expected = package_directory() / "node/__init__.py"
    assert Path(root.node.__file__).resolve() == expected, root.node.__file__
    print(f"FUSION_NODE_SOURCE_OK: {expected}", flush=True)
    return NodeUnderFusion(root)


def load_math_ops():
    source = package_directory() / "node/math_ops.py"
    spec = importlib.util.spec_from_file_location("fusion_math_ops_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(f"FUSION_MATH_SOURCE_OK: {source}", flush=True)
    return module
