"""Select and verify Studio source from this checkout for standalone tests."""

from importlib import import_module
import os
from pathlib import Path
import sys
from types import ModuleType


def repository():
    return Path(os.environ.get("NODE_TEST_REPOSITORY", Path(__file__).resolve().parents[2])).resolve()


def load_node_studio():
    sys.dont_write_bytecode = True
    expected = repository() / "addons" / "yugu_node_studio" / "__init__.py"
    assert expected.is_file(), f"Studio source is missing: {expected}"
    sys.path.insert(0, str(expected.parent.parent))
    module = import_module("yugu_node_studio")
    actual = Path(module.__file__).resolve()
    assert actual == expected, f"Wrong Studio source loaded: {actual}; expected {expected}"
    print(f"NODE_TEST_SOURCE_OK: {actual}")
    return module


def load_protocol_source():
    """Load dependency-free parsers without executing the Blender entry point."""
    sys.dont_write_bytecode = True
    folder = repository() / "addons" / "yugu_node_studio"
    name = "_node_protocol_source"
    package = ModuleType(name)
    package.__path__ = [str(folder)]
    sys.modules[name] = package
    for child in ("constants", "protocols"):
        module = import_module(f"{name}.{child}")
        expected = folder / f"{child}.py"
        assert Path(module.__file__).resolve() == expected, f"Wrong parser source: {module.__file__}"
    print(f"NODE_TEST_SOURCE_OK: {folder}")
    return name


def version():
    import tomllib
    return tomllib.loads((repository() / "addons/yugu_node_studio/blender_manifest.toml").read_text(encoding="utf-8"))["version"]


def output_directory():
    folder = Path(os.environ.get("YUGUS_TEST_OUTPUT", repository() / "dist")).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def example_path():
    return output_directory() / f"yugu_node_studio-{version()}-example.blend"
