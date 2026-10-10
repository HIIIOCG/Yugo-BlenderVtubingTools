"""Verify Yugo fusion source and the actual ZIP in isolated Blender processes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
import zipfile

from node_environment import ROOT, isolated_environment

TESTS = ROOT / "tests/yugo_node"
sys.path.insert(0, str(TESTS))
from _bootstrap import LEGACY_PRESET_SHA256, extract_package

CHECKS = {
    "lifecycle": ("fusion", "FUSION_LIFECYCLE_OK"),
    "release_scope": ("fusion", "YUGO RELEASE SCOPE PASSED"),
    "node_menu": ("fusion", "NODE_MENU_OK"),
    "save_lifecycle": ("fusion", "YUGO SAVE LIFECYCLE PASSED"),
    "receiver_reconnect": ("fusion", "RECEIVER_RECONNECT_OK"),
    "physics_live": ("fusion", "YUGO PHYSICS LIVE PASSED"),
    "visual_compatibility": ("fusion", "FUSION_VISUAL_COMPATIBILITY_OK"),
    "math_ops": ("python", "Ran 17 tests"),
    "scheduler": ("legacy", "Yugo dual scheduler test passed"),
    "load_safety": ("legacy", "Yugo file-load runtime safety test passed"),
    "composing": ("legacy", "Yugo Composing UI smoke test passed"),
    "outline_preset": ("legacy", "YUGO_OUTLINE_PRESET_TEST_OK"),
    "mtoon_optimize": ("legacy", "YUGO_MTOON_CUSTOM_TEST_OK"),
    "outline_bake": ("legacy", "YUGO_OUTLINE_BAKE_TEST_OK"),
    "camera": ("legacy", "CAM_MANAGER_TEST_OK"),
    "object_optimize": ("legacy", "YUGO_OBJECT_OPTIMIZE_TEST_OK"),
    "vmc_hidden_armature": ("legacy", "YUGO_VMC_HIDDEN_ARMATURE_TEST_OK"),
    "vmc_stale_layer": ("legacy", "YUGO_VMC_STALE_LAYER_SAFE_OK"),
}
DEFAULT = tuple(list(CHECKS)[:14])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_files():
    addon = ROOT / "addons/yugo"
    return {path.relative_to(addon).as_posix(): path for path in addon.rglob("*")
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__"
                                         for part in path.relative_to(addon).parts)
            and path.suffix not in {".pyc", ".zip"}
            and re.search(r"\.blend(?:[1-9]\d*|@.*)$", path.name) is None}


def verify_artifact(package, output):
    files = runtime_files()
    assert digest(files["preset.blend"]) == LEGACY_PRESET_SHA256, "Legacy preset changed"
    manifest = tomllib.loads(files["blender_manifest.toml"].read_text(encoding="utf-8"))
    assert manifest["id"] == "yugo", manifest
    result = {"source": str(ROOT / "addons/yugo"), "version": manifest["version"],
              "preset_sha256": LEGACY_PRESET_SHA256,
              "source_files_sha256": {name: digest(path) for name, path in sorted(files.items())}}
    result["source_commit"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                                       text=True, timeout=30).strip()
    if package:
        extracted = extract_package(package, output / "preflight/extracted")
        with zipfile.ZipFile(package) as archive:
            entries = [name for name in archive.namelist() if not name.endswith("/")]
            assert set(entries) == set(files), (
                "ZIP differs from source", sorted(set(files) - set(entries)), sorted(set(entries) - set(files)))
            for name in entries:
                assert archive.read(name) == files[name].read_bytes(), f"ZIP content differs: {name}"
        assert digest(extracted / "preset.blend") == LEGACY_PRESET_SHA256
        result.update(package=str(package), package_sha256=digest(package),
                      package_file_set="passed", package_byte_comparison="passed", safe_extraction="passed")
    (output / "artifact.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_check(binary, package, mode, name, folder, timeout):
    kind, marker = CHECKS[name]
    folder.mkdir(parents=True)
    snapshot = folder / "test-snapshot"
    snapshot.mkdir()
    shutil.copy2(TESTS / "_bootstrap.py", snapshot / "_bootstrap.py")
    shutil.copy2(TESTS / "node_class_inventory.json", snapshot / "node_class_inventory.json")
    script_name = "test_math_ops.py" if kind == "python" else f"blender_{name}.py"
    original = (ROOT / "tests/yugo" if kind == "legacy" else TESTS) / script_name
    shutil.copy2(original, snapshot / script_name)
    environment = isolated_environment(folder)
    environment["FUSION_TEST_REPOSITORY"] = str(ROOT)
    environment["FUSION_TEST_OUTPUT"] = str(folder / "output")
    if mode == "package":
        environment["FUSION_TEST_PACKAGE"] = str(package)
    else:
        environment.pop("FUSION_TEST_PACKAGE", None)
    script = snapshot / script_name
    command = ([sys.executable, "-B", str(script)] if kind == "python" else
               [str(binary), "--background", "--factory-startup", "--disable-autoexec",
                "--python-exit-code", "1", "--python-expr",
                f"import sys, runpy, bpy\nsys.dont_write_bytecode = True\n"
                f"sys.path.insert(0, {str(snapshot)!r})\n"
                f"bpy.context.preferences.filepaths.file_preview_type = 'NONE'\n"
                f"runpy.run_path({str(script)!r}, run_name='__main__')"])
    record = {"mode": mode, "test": name, "command": command, "script_sha256": digest(script),
              "bootstrap_sha256": digest(snapshot / "_bootstrap.py"),
              "class_inventory_sha256": digest(snapshot / "node_class_inventory.json"),
              "log": str(folder / "test.log")}
    options = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
    try:
        process = subprocess.run(command, cwd=folder, env=environment, timeout=timeout,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", **options)
        log = process.stdout
        source_marker = "FUSION_MATH_SOURCE_OK:" if kind == "python" else "FUSION_TEST_SOURCE_OK:"
        passed = (process.returncode == 0 and marker in log and source_marker in log
                  and "Traceback (most recent call last):" not in log)
        if kind == "fusion":
            passed = passed and "FUSION_NODE_SOURCE_OK:" in log
        record.update(status="passed" if passed else "failed", returncode=process.returncode)
        if not passed:
            record["reason"] = "Nonzero exit, traceback, missing source assertion or completion marker"
    except subprocess.TimeoutExpired as error:
        log = error.stdout or b""
        if isinstance(log, bytes):
            log = log.decode("utf-8", errors="replace")
        record.update(status="failed", reason=f"Timed out after {timeout:g} seconds")
    except OSError as error:
        log = str(error)
        record.update(status="failed", reason="Could not launch isolated test")
    Path(record["log"]).write_text(log, encoding="utf-8")
    print(f"{mode}/{name}: {record['status']}", flush=True)
    if record["status"] != "passed":
        print(log[-6000:], flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--package", type=Path, help="Exact Yugo fusion ZIP; enables source and ZIP checks")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--test", action="append", choices=tuple(CHECKS), help="Select checks; repeat as needed")
    parser.add_argument("--mode", action="append", choices=("source", "package"))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for name, (kind, _marker) in CHECKS.items():
            print(f"{name}: {kind}; {'default' if name in DEFAULT else 'optional'}; native/synthetic data")
        return 0
    if args.blender is None or not args.blender.is_absolute() or not args.blender.is_file():
        parser.error("--blender must name an existing absolute Blender executable")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a positive finite number")
    package = args.package.resolve(strict=True) if args.package else None
    modes = list(dict.fromkeys(args.mode or (["source", "package"] if package else ["source"])))
    if "package" in modes and package is None:
        parser.error("--mode package requires --package")
    binary = args.blender.resolve()
    version = subprocess.check_output([str(binary), "--version"], text=True, encoding="utf-8", timeout=30).splitlines()[0]
    match = re.match(r"Blender (\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (5, 1):
        parser.error(f"Blender 5.1+ is required; found {version}")
    default_folder = ROOT / "artifacts" / ("fusion-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    output = (args.output or default_folder).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"repository": str(ROOT), "blender": str(binary), "version": version, "tests": []}
    try:
        report["artifact"] = verify_artifact(package, output)
    except (AssertionError, OSError, zipfile.BadZipFile) as error:
        report.update(status="failed", artifact_error=str(error))
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Artifact verification failed: {error}", flush=True)
        return 1
    for mode in modes:
        for name in dict.fromkeys(args.test or DEFAULT):
            record = run_check(binary, package, mode, name, output / mode / name, args.timeout)
            report["tests"].append(record)
            report["status"] = "passed" if all(item["status"] == "passed" for item in report["tests"]) else "failed"
            (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    final_files = {name: digest(path) for name, path in runtime_files().items()}
    if final_files != report["artifact"]["source_files_sha256"]:
        report.update(status="failed", source_changed_during_run=True)
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Source changed during the run; this result cannot qualify the final candidate.", flush=True)
    print(f"Results: {output / 'results.json'}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
