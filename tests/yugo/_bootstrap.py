"""Load the legacy package from this checkout, never another installed copy."""

from importlib import import_module
import os
from pathlib import Path
import sys


def load_yugo():
    # Blender's embedded Python can ignore PYTHONDONTWRITEBYTECODE.
    sys.dont_write_bytecode = True
    repository = Path(
        os.environ.get("YUGO_TEST_REPOSITORY", Path(__file__).resolve().parents[2])
    ).resolve()
    expected = repository / "addons" / "yugo" / "__init__.py"
    if not expected.is_file():
        raise AssertionError(f"Legacy test source is missing: {expected}")
    sys.path.insert(0, str(expected.parent.parent))
    module = import_module("yugo")
    actual = Path(module.__file__).resolve()
    assert actual == expected, f"Wrong Yugo package loaded: {actual}; expected {expected}"
    print(f"YUGO_TEST_SOURCE_OK: {actual}")
    return module
