#!/usr/bin/env python3
"""Validate repository governance documents and package exclusions offline."""
from __future__ import annotations

from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md", "README.md", "CONTRIBUTING.md",
    "docs/development/PARALLEL_DEVELOPMENT.md",
    "docs/development/NODE_BASELINE.md", "docs/development/TEST_MATERIALS.md",
    "docs/development/CHAT_STARTERS.md", "docs/architecture/NODE_SYSTEM.md",
    "docs/legacy/YUGO_0_1_26.md", ".github/ISSUE_TEMPLATE/development_task.yml",
    ".github/pull_request_template.md",
    "scripts/start_task.py", "scripts/check_task_scope.py", "scripts/task_scope.py",
    "scripts/check_pr_scope.py", "scripts/check_governance.py",
    ".github/workflows/governance.yml",
    "addons/yugo/__init__.py", "addons/yugo/blender_manifest.toml",
    "addons/yugo/preset.blend", "addons/yugo/README.md", "addons/yugo/LICENSE",
    "addons/yugo/CHANGELOG.md", "addons/yugu_node_studio/README.md",
    "tests/governance/test_task_scope.py", "tests/governance/test_pr_scope.py",
    "tests/yugo/_bootstrap.py", "scripts/run_blender_tests.py",
)


def main() -> int:
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing management file: {name}")
    for name in REQUIRED:
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == ".blend":
            continue
        content = path.read_text(encoding="utf-8")
        if not content.endswith("\n"):
            errors.append(f"Missing final newline: {name}")
        if any(line.rstrip() != line for line in content.splitlines()):
            errors.append(f"Trailing whitespace: {name}")
        if path.suffix != ".md":
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            target = target.strip().strip("<>")
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0])
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                errors.append(f"Broken local link in {name}: {target}")
    for path in ROOT.glob("*.py"):
        errors.append(f"Addon Python must live under addons/, not repository root: {path.name}")
    for name in ("blender_manifest.toml", "preset.blend", "composing", "ifacialmocap", "keybinds", "springbone", "vmc"):
        if (ROOT / name).exists():
            errors.append(f"Legacy product files must live in addons/yugo/: {name}")
    if (ROOT / "addons/yugo/LICENSE").read_bytes() != (ROOT / "LICENSE").read_bytes():
        errors.append("Standalone addon license must match the repository license")
    with (ROOT / "addons/yugo/blender_manifest.toml").open("rb") as stream:
        excluded = tomllib.load(stream).get("build", {}).get("paths_exclude_pattern", [])
    for name in ("/docs/", "/AGENTS.md", "/scripts/", "/tests/"):
        if name not in excluded:
            errors.append(f"Legacy package must exclude {name}")
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    if "```task-scope" not in template:
        errors.append("PR template is missing task-scope declaration")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Governance files, local links and legacy package exclusions pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
