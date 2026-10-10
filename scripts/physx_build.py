"""Verify the pinned PhysX import and build an installable CPU extension ZIP."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

from node_environment import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addons/yugo_physx_tracker"
PINNED_ARCHIVE = "3b3b636b1a32304138030a338db4226dbd1f773748cd4af7cf1251bbc81bc48e"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_import() -> tuple[dict, dict]:
    manifest = tomllib.loads((ADDON / "blender_manifest.toml").read_text(encoding="utf-8"))
    provenance = json.loads((ADDON / "IMPORT_MANIFEST.json").read_text(encoding="utf-8"))
    if (provenance["archive_sha256"] != PINNED_ARCHIVE or provenance["version"] != "0.4.0"
            or manifest["id"] != provenance["package_id"] or manifest["version"] != provenance["version"]):
        raise ValueError("The package identity differs from the qualified 0.4.0 import.")
    info = next(ast.literal_eval(node.value) for node in ast.parse((ADDON / "__init__.py").read_text()).body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "bl_info"
                                                        for target in node.targets))
    if info["version"] != tuple(map(int, manifest["version"].split("."))):
        raise ValueError("bl_info and manifest versions disagree.")
    overrides = provenance.get("overrides", {})
    for name, record in provenance["files"].items():
        path = (ADDON / name).resolve()
        if not path.is_relative_to(ADDON.resolve()):
            raise ValueError("Invalid import manifest path: " + name)
        expected = overrides.get(name, record)
        if not path.is_file() or path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Imported file is missing or changed: " + name + ". Run git lfs pull for native payloads.")
    if (ADDON / "vendor/ovphysx/plugins/PhysXGpu_64.dll").exists():
        raise ValueError("The CPU package unexpectedly includes the excluded PhysX GPU library.")
    return manifest, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    binary = args.blender.resolve(strict=True)
    if not args.blender.is_absolute() or not binary.is_file():
        parser.error("--blender must be an absolute executable path.")
    manifest, provenance = verify_import()
    changes = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "addons/yugo_physx_tracker"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if args.require_clean and changes:
        raise ValueError("A clean committed source is required.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "physx-build"
    logs.mkdir(exist_ok=True)
    environment = isolated_environment(logs / "profile")
    archive = output / f"{manifest['id']}-{manifest['version']}.zip"
    commands = ([binary, "--command", "extension", "validate", ADDON],
                [binary, "--command", "extension", "build", "--source-dir", ADDON, "--output-dir", output],
                [binary, "--command", "extension", "validate", archive])
    for index, command in enumerate(commands):
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=environment, capture_output=True, timeout=300)
        (logs / f"extension-{index}.log").write_bytes(result.stdout + b"\n" + result.stderr)
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).decode("utf-8", errors="replace"))
    expected = {path.relative_to(ADDON).as_posix() for path in ADDON.rglob("*") if path.is_file()
                and not any(part in {"__pycache__", ".git", "tests", "reports"} for part in path.relative_to(ADDON).parts)
                and path.suffix not in {".pyc", ".pyo"}}
    with zipfile.ZipFile(archive) as package:
        names = [name for name in package.namelist() if not name.endswith("/")]
        if package.testzip() or len(names) != len(set(names)) or set(names) != expected:
            raise ValueError("Built ZIP file set/integrity differs from the verified source.")
        for name in names:
            if hashlib.sha256(package.read(name)).hexdigest() != digest(ADDON / name):
                raise ValueError("Built ZIP bytes differ from source: " + name)
    tracked = subprocess.run(["git", "ls-files", "-z", "--", "addons/yugo_physx_tracker"],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    committed = {name.removeprefix("addons/yugo_physx_tracker/") for name in tracked if name}
    if args.require_clean and expected - committed:
        raise ValueError("The ZIP contains ignored or untracked runtime files.")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    checksum = digest(archive)
    archive.with_suffix(".zip.sha256").write_text(checksum + "  " + archive.name + "\n", encoding="utf-8")
    record = {"package": str(archive), "source": str(ADDON), "version": manifest["version"],
              "source_commit": revision, "source_dirty": bool(changes or expected - committed),
              "source_archive_sha256": provenance["archive_sha256"], "files": len(expected),
              "bytes": archive.stat().st_size, "sha256": checksum, "source_validate": "passed",
              "zip_validate": "passed", "import_hashes": "passed", "byte_comparison": "passed"}
    (output / "physx-build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
