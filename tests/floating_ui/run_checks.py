"""Run Floating UI checks in an isolated Blender profile without installing it."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--demo", action="store_true", help="Also generate an editable demo and PNG")
    parser.add_argument("--gui", action="store_true", help="Test real modal playback in a separate hidden GUI process")
    parser.add_argument("--output", type=Path, help="Task-specific artifact directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    binary = args.blender.resolve()
    if not binary.is_file():
        parser.error("--blender must point to an existing Blender executable")
    output = (args.output or root / "artifacts" / "floating-ui-checks").resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile = output / "profile"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    temporary = profile / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(temporary)
    environment["TMP"] = str(temporary)
    for name, relative in (
        ("BLENDER_USER_RESOURCES", "resources"),
        ("BLENDER_USER_CONFIG", "config"),
        ("BLENDER_USER_SCRIPTS", "scripts"),
        ("BLENDER_USER_DATAFILES", "datafiles"),
        ("BLENDER_USER_EXTENSIONS", "extensions"),
    ):
        path = profile / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)

    def run(name, command, artifacts=None, *, hidden=False):
        log = output / (name + ".log")
        if artifacts is not None:
            environment["FLOATING_UI_ARTIFACTS"] = str(output / artifacts)
        with log.open("w", encoding="utf-8") as stream:
            try:
                options = {}
                if hidden and os.name == "nt":
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = subprocess.SW_HIDE
                    options["startupinfo"] = startup
                result = subprocess.run(
                    [str(part) for part in command], cwd=root, env=environment,
                    stdout=stream, stderr=subprocess.STDOUT, timeout=240,
                    check=False,
                    **options,
                )
            except subprocess.TimeoutExpired:
                print(f"FAIL {name}: timed out; see {log}", file=sys.stderr)
                return False
        if result.returncode:
            print(f"FAIL {name}: exit {result.returncode}; see {log}", file=sys.stderr)
            print(log.read_text(encoding="utf-8", errors="replace")[-6000:], file=sys.stderr)
            return False
        if hidden:
            print(f"GUI process exited; evidence: {log}")
        else:
            print(f"PASS {name}: {log}")
        return True

    ok = run("model", [sys.executable, "-B", "-m", "unittest", "discover",
                       "-s", "tests/floating_ui", "-p", "test_model.py", "-v"])
    flags = [binary, "--background", "--factory-startup", "--disable-autoexec",
             "--python-exit-code", "1", "--python"]
    ok = run("blender", flags + [root / "tests/floating_ui/blender_integration.py"],
             "integration") and ok
    if args.demo:
        ok = run("demo", flags + [root / "tests/floating_ui/make_demo.py"], "demo") and ok
    if args.gui:
        gui_path = output / "gui" / "gui_result.json"
        # Retain old evidence, but ensure a previous result cannot mask a crash.
        if gui_path.exists():
            gui_path.replace(gui_path.with_name("gui_result.previous.json"))
        gui_ok = run("gui", [binary, "--factory-startup", "--disable-autoexec",
                             "--python-exit-code", "1", "--python",
                             root / "tests/floating_ui/blender_gui.py"], "gui", hidden=True)
        if gui_ok:
            gui_ok = gui_path.is_file() and json.loads(gui_path.read_text(encoding="utf-8"))["status"] == "passed"
        if not gui_ok:
            print("FAIL gui: modal playback did not produce passing evidence", file=sys.stderr)
        else:
            print(f"PASS gui: {gui_path}")
        ok = gui_ok and ok
    record = {
        "status": "passed" if ok else "failed",
        "blender_executable": str(binary),
        "source": str(root / "addons/yugo_floating_ui"),
        "profile": str(profile),
        "demo_requested": args.demo,
        "gui_requested": args.gui,
    }
    (output / "results.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
