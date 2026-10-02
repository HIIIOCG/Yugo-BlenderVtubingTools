#!/usr/bin/env python3
"""Run legacy Blender tests in separate processes and task-local profiles.

Example: python -B scripts/run_blender_tests.py --blender /absolute/blender --task 8
The default smoke group uses native/synthetic data and needs no VRM Add-on.
VRM integration tests require a separately provisioned extension fixture; this
runner records them as not run rather than using the user's installed add-ons.
"""

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
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
TEST_DIRECTORY = ROOT / "tests" / "yugo"
SMOKE = ("scheduler", "load_safety", "composing", "outline_preset")
# A process exiting normally is insufficient for tests using deferred UI timers.
SUCCESS = {
    "camera": "CAM_MANAGER_TEST_OK",
    "composing": "Yugo Composing UI smoke test passed",
    "empty_layer": "YUGO_EMPTY_LAYER_DEFAULT_OK",
    "layer_reorder_runtime": "YUGO_LIVE_LAYER_REORDER_OK",
    "load_safety": "Yugo file-load runtime safety test passed",
    "mtoon_optimize": "YUGO_MTOON_CUSTOM_TEST_OK",
    "object_optimize": "YUGO_OBJECT_OPTIMIZE_TEST_OK",
    "outline_bake": "YUGO_OUTLINE_BAKE_TEST_OK",
    "outline_multi_material": "YUGO_OUTLINE_MULTI_MATERIAL_TEST_OK",
    "outline_preset": "YUGO_OUTLINE_PRESET_TEST_OK",
    "outline_vrm_integration": "YUGO_OUTLINE_VRM_INTEGRATION_TEST_OK",
    "scheduler": "Yugo dual scheduler test passed",
    "spring_event_loop": "YUGO_SPRING_EVENT_LOOP_WRITEBACK_OK",
    "spring_post_mix": "YUGO_SPRING_MOTION_LAYER_INTEGRATION_OK",
    "view_window": "YUGO_VIEW_WINDOW_OK",
    "vmc_hidden_armature": "YUGO_VMC_HIDDEN_ARMATURE_TEST_OK",
    "vmc_stale_layer": "YUGO_VMC_STALE_LAYER_SAFE_OK",
}
UI_TESTS = frozenset(("spring_event_loop", "view_window"))
VRM_TESTS = frozenset(("outline_multi_material", "outline_vrm_integration"))


def test_name(value: str) -> str:
    name = value.removesuffix(".py").removeprefix("blender_")
    if name not in SUCCESS:
        raise argparse.ArgumentTypeError(f"Unknown test {value!r}; use --list")
    return name


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def isolation_environment(folder: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["YUGO_TEST_REPOSITORY"] = str(ROOT)
    profile = folder / "profile"
    environment["BLENDER_USER_RESOURCES"] = str(profile)
    for kind in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES"):
        directory = profile / kind.lower()
        directory.mkdir(parents=True)
        environment[f"BLENDER_USER_{kind}"] = str(directory)
    temporary = folder / "tmp"
    temporary.mkdir()
    for name in ("TEMP", "TMP", "TMPDIR"):
        environment[name] = str(temporary)
    environment["YUGO_TEST_FIXTURES_DIR"] = str(folder / "fixtures")
    return environment


def run_test(blender: Path, name: str, folder: Path, fixtures: list[Path],
             timeout: float, ui: bool) -> dict:
    folder.mkdir()
    copies = folder / "test-snapshot"
    copies.mkdir()
    for source in TEST_DIRECTORY.glob("*.py"):
        shutil.copy2(source, copies / source.name)
    material_copies = folder / "fixtures"
    material_copies.mkdir()
    for source in fixtures:
        shutil.copy2(source, material_copies / source.name)
    output = folder / "output"
    output.mkdir()
    environment = isolation_environment(folder)
    script = copies / f"blender_{name}.py"
    command = [str(blender)]
    if name not in UI_TESTS:
        command.append("--background")
    command.extend(("--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                    "--python", str(script)))
    result = {"test": name, "command": command, "cwd": str(output),
              "script_sha256": file_hash(script), "log": str(folder / "blender.log")}
    reason = None
    if name in VRM_TESTS:
        reason = "Requires a VRM Add-on fixture registered in an isolated profile; not provisioned"
    elif name in UI_TESTS and not ui:
        reason = "Requires --ui for a separate Blender UI process"
    if reason:
        result.update(status="not_run", reason=reason)
        return result
    launch_options = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        launch_options["startupinfo"] = startup
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=output, env=environment, timeout=timeout,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 **launch_options)
        log = process.stdout
        passed = (process.returncode == 0 and SUCCESS[name] in log
                  and "YUGO_TEST_SOURCE_OK:" in log
                  and "Traceback (most recent call last):" not in log)
        result.update(status="passed" if passed else "failed", returncode=process.returncode)
        if not passed:
            result["reason"] = "Process failed, source assertion failed, or completion marker missing"
    except subprocess.TimeoutExpired as error:
        log = error.stdout or b""
        if isinstance(log, bytes):
            log = log.decode("utf-8", errors="replace")
        result.update(status="failed", reason=f"Timed out after {timeout:g} seconds")
    except OSError as error:
        log = str(error)
        result.update(status="failed", reason=f"Could not launch isolated Blender: {error}")
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    Path(result["log"]).write_text(log, encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, help="Absolute Blender executable path")
    parser.add_argument("--task", help="Task ID; defaults to this workspace's .dev/task.json")
    parser.add_argument("--tests", nargs="+", type=test_name, default=list(SMOKE),
                        help="Test names or blender_*.py filenames; defaults to the smoke group")
    parser.add_argument("--list", action="store_true", help="List tests and prerequisites")
    parser.add_argument("--ui", action="store_true", help="Allow separate UI test processes")
    parser.add_argument("--fixture", action="append", type=Path, default=[],
                        help="Absolute material file to copy into each test's fixtures directory")
    parser.add_argument("--timeout", type=float, default=120, help="Per-process timeout in seconds")
    args = parser.parse_args()
    if args.list:
        for name in SUCCESS:
            requirements = (["UI"] if name in UI_TESTS else []) + (["VRM fixture"] if name in VRM_TESTS else [])
            print(f"{name}: {', '.join(requirements) or 'native/synthetic data'}")
        return 0
    if args.blender is None or not args.blender.is_absolute() or not args.blender.is_file():
        parser.error("--blender must name an existing absolute executable path")
    blender = args.blender.resolve()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a positive finite number")
    task = args.task
    if task is None:
        record = ROOT / ".dev" / "task.json"
        if record.is_file():
            task = str(json.loads(record.read_text(encoding="utf-8"))["task_id"])
    if task is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", task):
        parser.error("Provide a task ID using letters, digits, underscores, dots or hyphens")
    fixtures = []
    for fixture in args.fixture:
        if not fixture.is_absolute() or not fixture.is_file():
            parser.error("Each --fixture must name an existing absolute file")
        fixtures.append(fixture.resolve())
    if len({path.name.casefold() for path in fixtures}) != len(fixtures):
        parser.error("Fixture filenames must be distinct")
    try:
        version_output = subprocess.check_output([str(blender), "--version"], timeout=30,
                                                 text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        parser.error(f"Could not read Blender version: {error}")
    version = version_output.splitlines()[0]
    match = re.match(r"Blender (\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (5, 1):
        parser.error(f"Blender 5.1 or newer is required; found {version}")
    tests = list(dict.fromkeys(args.tests))
    for name in tests:
        if not (TEST_DIRECTORY / f"blender_{name}.py").is_file():
            parser.error(f"Missing test script: {name}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output_root = ROOT / "artifacts" / task / run_id
    output_root.mkdir(parents=True)
    summary = {"blender": str(blender), "version": version, "repository": str(ROOT),
               "addon_source": str(ROOT / "addons" / "yugo"),
               "fixtures": [{"source": str(path), "sha256": file_hash(path)} for path in fixtures],
               "tests": []}
    try:
        summary["commit"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                                     text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        summary["commit"] = None
    print(f"{version}; isolated run: {output_root}", flush=True)
    for name in tests:
        result = run_test(blender, name, output_root / name, fixtures, args.timeout, args.ui)
        summary["tests"].append(result)
        (output_root / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: {result['status']} ({result.get('reason', result.get('duration_seconds'))})", flush=True)
    print(f"Results: {output_root / 'results.json'}")
    if any(item["status"] == "failed" for item in summary["tests"]):
        return 1
    return 2 if any(item["status"] == "not_run" for item in summary["tests"]) else 0


if __name__ == "__main__":
    sys.exit(main())
