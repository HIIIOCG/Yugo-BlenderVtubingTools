"""Build and check the ragdoll module in task-specific Blender profiles."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "addons" / "yugo_ragdoll"
TESTS = ROOT / "tests" / "ragdoll"
DEFAULT_BLENDER = Path("C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")


def isolated_environment(run_root, label):
    profile = run_root / "profiles" / label
    directories = {
        "BLENDER_USER_RESOURCES": profile / "resources",
        "BLENDER_USER_CONFIG": profile / "config",
        "BLENDER_USER_SCRIPTS": profile / "scripts",
        "BLENDER_USER_DATAFILES": profile / "datafiles",
        "BLENDER_USER_EXTENSIONS": profile / "extensions",
        "TEMP": profile / "temp",
        "TMP": profile / "temp",
    }
    environment = os.environ.copy()
    for variable, directory in directories.items():
        directory.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(directory)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run_command(command, run_root, label, timeout=120):
    result = subprocess.run([str(value) for value in command], cwd=ROOT,
                            env=isolated_environment(run_root, label),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    log = run_root / (label + ".log")
    log.write_text(result.stdout, encoding="utf-8")
    print(label + ": " + ("passed" if result.returncode == 0 else "FAILED") + " (" + str(log) + ")", flush=True)
    if result.returncode:
        print(result.stdout[-12000:], flush=True)
        raise RuntimeError(label + " exited " + str(result.returncode))
    return result.stdout


def background_test(blender, script, run_root, label, arguments=()):
    command = [blender, "--background", "--factory-startup", "--disable-autoexec",
               "--python-exit-code", "1", "--python", script]
    if arguments:
        command += ["--", *arguments]
    return run_command(command, run_root, label)


def powershell_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def hidden_gui_test(blender, run_root):
    if sys.platform != "win32":
        raise RuntimeError("The optional hidden GUI test currently requires Windows")
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("PowerShell is required for the hidden GUI test")
    report = run_root / "live-timer-report.json"
    stdout = run_root / "live-timer-stdout.log"
    stderr = run_root / "live-timer-stderr.log"
    arguments = ["--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                 "--python", str(TESTS / "blender_live_timer.py"), "--", "--report", str(report)]
    # Start-Process does not quote arguments that contain spaces for its child.
    child_arguments = ["\"" + value + "\"" if " " in value else value for value in arguments]
    script = (
        "$ragdollProcess = Start-Process -FilePath " + powershell_quote(blender) +
        " -ArgumentList @(" + ",".join(powershell_quote(value) for value in child_arguments) + ")" +
        " -WorkingDirectory " + powershell_quote(ROOT) +
        " -WindowStyle Hidden -PassThru -RedirectStandardOutput " + powershell_quote(stdout) +
        " -RedirectStandardError " + powershell_quote(stderr) + "; " +
        "if ($ragdollProcess.WaitForExit(45000)) { exit $ragdollProcess.ExitCode } " +
        "else { Stop-Process -Id $ragdollProcess.Id; throw 'Dedicated ragdoll test process timed out' }"
    )
    run_command([powershell, "-NoProfile", "-NonInteractive", "-Command", script], run_root, "live-timer", timeout=55)
    if not report.is_file():
        raise RuntimeError("Live timer did not write its report")
    result = json.loads(report.read_text(encoding="utf-8"))
    if result.get("status") != "passed":
        raise RuntimeError("Live timer failed: " + json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--gui", action="store_true", help="Run a separate hidden, self-closing GUI timer test")
    args = parser.parse_args()
    blender = args.blender.resolve()
    if not blender.is_file():
        parser.error("Blender executable does not exist: " + str(blender))
    manifest = tomllib.loads((SOURCE / "blender_manifest.toml").read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = ROOT / "artifacts" / ("checks-" + stamp + "-" + str(os.getpid()))
    run_root.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "run_root": str(run_root), "blender": str(blender),
              "package_id": manifest["id"], "version": manifest["version"], "gui": "not requested"}
    try:
        run_command([sys.executable, "-B", "-m", "unittest", "discover", "-s", TESTS, "-p", "test_*.py"], run_root, "solver")
        background_test(blender, TESTS / "blender_service.py", run_root, "service")
        background_test(blender, TESTS / "blender_preview.py", run_root, "preview")
        background_test(blender, TESTS / "blender_restricted_lifecycle.py", run_root, "restricted-lifecycle")
        run_command([blender, "--background", "--factory-startup", "--disable-autoexec", "--command", "extension",
                     "build", "--source-dir", SOURCE, "--output-dir", run_root], run_root, "build")
        package = run_root / (manifest["id"] + "-" + manifest["version"] + ".zip")
        if not package.is_file():
            raise RuntimeError("Blender build did not create " + str(package))
        run_command([blender, "--background", "--factory-startup", "--disable-autoexec", "--command", "extension",
                     "validate", package], run_root, "validate")
        background_test(blender, TESTS / "blender_package.py", run_root, "package", ["--package", package])
        demo = run_root / "yugo-ragdoll-motion-demo.blend"
        background_test(blender, TESTS / "build_demo.py", run_root, "demo", ["--output", demo])
        if args.gui:
            report["gui"] = hidden_gui_test(blender, run_root)
        report.update(status="passed", package=str(package), demo=str(demo))
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        (run_root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
