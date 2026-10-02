#!/usr/bin/env python3
"""Check release metadata without importing Blender modules."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = ROOT / "addons" / "yugo"
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_bl_info() -> dict[str, object]:
    module = ast.parse((ADDON_ROOT / "__init__.py").read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
    fail("__init__.py does not define a literal bl_info dictionary")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Git tag to compare, for example v0.1.14")
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="Print only the validated manifest version",
    )
    args = parser.parse_args()

    manifest_path = ADDON_ROOT / "blender_manifest.toml"
    with manifest_path.open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)

    version = str(manifest.get("version", ""))
    if not SEMVER.fullmatch(version):
        fail(f"manifest version is not valid semantic versioning: {version!r}")

    bl_info = read_bl_info()
    legacy_version = bl_info.get("version")
    stable_version = version.split("+", 1)[0].split("-", 1)[0]
    expected_legacy = tuple(int(part) for part in stable_version.split("."))
    if legacy_version != expected_legacy:
        fail(f"bl_info version {legacy_version!r} does not match manifest {version}")

    manifest_blender = tuple(
        int(part) for part in str(manifest["blender_version_min"]).split(".")
    )
    if bl_info.get("blender") != manifest_blender:
        fail("bl_info Blender version does not match blender_version_min")

    if args.tag and args.tag != f"v{version}":
        fail(f"tag {args.tag!r} must be v{version}")

    tagline = str(manifest.get("tagline", ""))
    if not tagline or len(tagline) > 64 or tagline.endswith((".", "!", "?")):
        fail("tagline must be 1-64 characters and have no ending punctuation")

    for permission, reason in manifest.get("permissions", {}).items():
        if (
            not reason
            or len(reason) > 64
            or str(reason).endswith((".", "!", "?"))
        ):
            fail(
                f"permission {permission!r} needs a 1-64 character reason "
                "without punctuation"
            )

    for required in ("__init__.py", "blender_manifest.toml", "LICENSE", "README.md"):
        if not (ADDON_ROOT / required).is_file():
            fail(f"required file is missing: {required}")

    for python_file in ADDON_ROOT.rglob("*.py"):
        try:
            ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
        except (SyntaxError, UnicodeError) as error:
            fail(f"could not parse {python_file.relative_to(ADDON_ROOT)}: {error}")

    forbidden = [
        path.relative_to(ADDON_ROOT)
        for path in ADDON_ROOT.rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    if forbidden:
        fail("generated Python cache files are present: " + ", ".join(map(str, forbidden)))

    if args.print_version:
        print(version)
    else:
        print(f"Yugo {version} metadata is consistent")


if __name__ == "__main__":
    main()
