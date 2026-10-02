"""Shared validation for isolated Git task workspaces.

These checks validate Git changes against a declared task. They are cooperation
rules, not an operating-system sandbox or an authorization mechanism.
"""

from __future__ import annotations

import fnmatch
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable


EXPECTED_REPOSITORY = "HIIIOCG/Yugo-BlenderVtubingTools"
ROLES = ("docs", "feature", "core", "integration")
TASK_RECORD = ".dev/task.json"
NODE_ROOT = "yugu_node_studio"
NODE_SHARED_FILES = frozenset(
    f"{name}.py"
    for name in (
        "data", "constants", "nodes", "runtime", "flow", "workflow",
        "scheduler", "__init__", "targets",
        "input_nodes", "workflow_nodes", "flow_nodes",
    )
)
LEGACY_PACKAGES = frozenset(
    ("composing", "ifacialmocap", "keybinds", "springbone", "vmc")
)


class ScopeError(Exception):
    """An unsafe or inconsistent task declaration."""


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ScopeError(message or f"Git command failed: {' '.join(args)}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode("utf-8", "surrogateescape").strip()


def repository_root(location: Path) -> Path:
    root = git_text(location, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def verify_repository(repo: Path) -> None:
    origin = git_text(repo, "remote", "get-url", "origin")
    accepted = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"HIIIOCG/Yugo-BlenderVtubingTools(?:\.git)?/?",
        origin,
        flags=re.IGNORECASE,
    )
    if not accepted:
        raise ScopeError(
            f"Expected origin for github.com/{EXPECTED_REPOSITORY}; origin does not match."
        )


def resolve_commit(repo: Path, revision: str) -> str:
    if not revision or "\x00" in revision or "\n" in revision or "\r" in revision:
        raise ScopeError("The base must be an existing local Git revision.")
    try:
        return git_text(
            repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
        )
    except ScopeError as exc:
        raise ScopeError(
            f"Base revision {revision!r} is unavailable locally. "
            "The coordinator must fetch or select the base before starting the task."
        ) from exc


def normalize_path(value: str, *, pattern: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ScopeError("Paths must be nonempty repository-relative strings.")
    if "\\" in value or ":" in value or any(ord(char) < 32 for char in value):
        raise ScopeError(f"Use portable repository-relative paths with '/' separators: {value!r}.")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ScopeError(f"Absolute paths, empty segments, and traversal are forbidden: {value!r}.")
    if any(part.casefold() in (".git", ".dev") for part in parts):
        raise ScopeError(f"Git internals and local task records cannot be task scope: {value!r}.")
    if pattern:
        if any("**" in part and part != "**" for part in parts):
            raise ScopeError("A recursive '**' wildcard must occupy an entire path segment.")
        if "[" in value or "]" in value:
            raise ScopeError("Scope patterns support '*', '?', and complete '**' segments only.")
    elif "*" in value or "?" in value:
        raise ScopeError(f"A changed file path cannot contain wildcard characters: {value!r}.")
    return value


def validate_scopes(scopes: Iterable[str]) -> list[str]:
    result = list(dict.fromkeys(normalize_path(scope, pattern=True) for scope in scopes))
    if not result:
        raise ScopeError("Declare at least one allowed path with --scope.")
    return result


def scope_matches(path: str, scope: str) -> bool:
    """Match segments: '*' never crosses '/', while '**' matches any depth."""
    path_parts = path.split("/")
    scope_parts = scope.split("/")

    @lru_cache(maxsize=None)
    def match(path_index: int, scope_index: int) -> bool:
        if scope_index == len(scope_parts):
            return path_index == len(path_parts)
        item = scope_parts[scope_index]
        if item == "**":
            return any(
                match(next_index, scope_index + 1)
                for next_index in range(path_index, len(path_parts) + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], item)
            and match(path_index + 1, scope_index + 1)
        )

    return match(0, 0)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_filesystem_path(repo: Path, relative: str) -> None:
    path = repo
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ScopeError(f"Changed path uses a symbolic link: {relative}.")
    if not is_within(path.resolve(), repo.resolve()):
        raise ScopeError(f"Changed path resolves outside the assigned workspace: {relative}.")


def required_role(path: str) -> str | None:
    lowered = path.casefold()
    parts = lowered.split("/")
    if (
        parts[-1] == "agents.md"
        or parts[0] == "scripts"
        or lowered.startswith(".github/workflows/")
        or lowered in ("blender_manifest.toml", "changelog.md")
        or lowered in (f"{NODE_ROOT}/blender_manifest.toml", f"{NODE_ROOT}/changelog.md")
    ):
        return "integration"
    if (
        (len(parts) == 1 and lowered.endswith(".py"))
        or parts[0] in LEGACY_PACKAGES
        or parts[0] == "core"
        or (len(parts) > 1 and parts[0] == NODE_ROOT and parts[1] == "core")
        or (
            len(parts) == 2
            and parts[0] == NODE_ROOT
            and parts[1] in NODE_SHARED_FILES
        )
    ):
        return "core"
    return None


def is_documentation(path: str) -> bool:
    lowered = path.casefold()
    parts = lowered.split("/")
    return (
        parts[-1] in ("readme.md", "contributing.md", "security.md")
        or lowered.startswith(("docs/", "templates/", ".github/issue_template/"))
        or (len(parts) > 1 and parts[0] == NODE_ROOT and parts[1] == "docs")
        or lowered == ".github/pull_request_template.md"
        or lowered.startswith(".github/pull_request_template/")
    )


def validate_changed_paths(
    repo: Path, paths: Iterable[str], role: str, allowed_paths: list[str]
) -> list[str]:
    if role not in ROLES:
        raise ScopeError(f"Unknown task role: {role!r}.")
    scopes = validate_scopes(allowed_paths)
    paths = sorted(set(paths))
    git_symlinks = set()
    if paths:
        for item in git(repo, "ls-files", "--stage", "-z").stdout.split(b"\x00"):
            if item.startswith(b"120000 "):
                git_symlinks.add(item.split(b"\t", 1)[1].decode("utf-8", "surrogateescape"))
    checked = []
    failures = []
    for original in paths:
        try:
            path = normalize_path(original)
            validate_filesystem_path(repo, path)
            if path in git_symlinks:
                raise ScopeError("changed path is a Git symbolic link, including core.symlinks=false checkouts")
            requirement = required_role(path)
            if requirement == "integration" and role != "integration":
                raise ScopeError("governance or release files require the integration role")
            if requirement == "core" and role not in ("core", "integration"):
                raise ScopeError("shared Node or legacy runtime files require core or integration")
            if role == "docs" and not is_documentation(path):
                raise ScopeError("the docs role may change documentation and templates only")
            if not any(scope_matches(path, scope) for scope in scopes):
                raise ScopeError("outside the task's declared allowed_paths")
            checked.append(path)
        except ScopeError as exc:
            failures.append(f"{original}: {exc}")
    if failures:
        raise ScopeError("Task scope rejected:\n" + "\n".join(f"- {item}" for item in failures))
    return checked


def parse_diff_paths(output: bytes) -> set[str]:
    items = output.decode("utf-8", "surrogateescape").split("\x00")
    result = set()
    index = 0
    while index < len(items) and items[index]:
        status = items[index]
        index += 1
        if status.startswith("U"):
            raise ScopeError("Resolve Git merge conflicts before checking task scope.")
        count = 2 if status.startswith(("R", "C")) else 1
        for _ in range(count):
            if index >= len(items) or not items[index]:
                raise ScopeError("Could not parse Git's changed-path output.")
            result.add(items[index])
            index += 1
    return result


def changed_paths(repo: Path, base_sha: str) -> set[str]:
    paths = set()
    for arguments in (
        ("diff", "--name-status", "-z", "--find-renames", base_sha, "HEAD", "--"),
        ("diff", "--name-status", "-z", "--find-renames", "--cached", "--"),
        ("diff", "--name-status", "-z", "--find-renames", "--"),
    ):
        paths.update(parse_diff_paths(git(repo, *arguments).stdout))
    paths.update(
        item
        for item in git(repo, "ls-files", "--others", "--exclude-standard", "-z")
        .stdout.decode("utf-8", "surrogateescape").split("\x00")
        if item
    )
    return paths


def _unique_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScopeError(f"Duplicate key in task record: {key}.")
        result[key] = value
    return result


def read_task_record(repo: Path) -> dict:
    record_path = repo / TASK_RECORD
    validate_record_path = repo / ".dev"
    if (
        validate_record_path.is_symlink()
        or record_path.is_symlink()
        or (hasattr(validate_record_path, "is_junction") and validate_record_path.is_junction())
    ):
        raise ScopeError("Local task records must be ordinary files inside the assigned workspace.")
    if not record_path.is_file():
        raise ScopeError(f"Missing {TASK_RECORD}; create a task worktree with scripts/start_task.py.")
    if record_path.stat().st_size > 65536:
        raise ScopeError("The local task record is unexpectedly large.")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_keys)
    except (UnicodeError, ValueError) as exc:
        raise ScopeError(f"Invalid task record: {exc}.") from exc
    if (
        not isinstance(record, dict)
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
    ):
        raise ScopeError("Expected a task record with schema_version 1.")
    if record.get("repository") != EXPECTED_REPOSITORY:
        raise ScopeError("The task record belongs to a different repository.")
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[1-9][0-9]*", task_id):
        raise ScopeError("The task record requires a positive GitHub issue number as task_id.")
    role = record.get("role")
    if role not in ROLES:
        raise ScopeError("The task record has an invalid role.")
    if record.get("status") != "active":
        raise ScopeError("The task record is not active.")
    workspace = record.get("workspace")
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise ScopeError("The task record requires an absolute workspace path.")
    if Path(workspace).resolve() != repo.resolve():
        raise ScopeError("This is not the exact workspace assigned in the task record.")
    branch = git_text(repo, "branch", "--show-current")
    expected_branch = record.get("branch")
    if not branch or branch in ("main", "master"):
        raise ScopeError("Run local task checks from an assigned task branch, not main or detached HEAD.")
    if (
        not isinstance(expected_branch, str)
        or not re.fullmatch(rf"feature/{task_id}-[a-z0-9]+(?:-[a-z0-9]+)*", expected_branch)
        or branch != expected_branch
    ):
        raise ScopeError("The checked-out branch does not match the assigned task branch.")
    base_sha = record.get("base_sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", base_sha):
        raise ScopeError("The task record requires an immutable base commit SHA.")
    if resolve_commit(repo, base_sha) != base_sha:
        raise ScopeError("The task record's base commit is not available.")
    if git(repo, "merge-base", "--is-ancestor", base_sha, "HEAD", check=False).returncode:
        raise ScopeError("The assigned base commit is not an ancestor of this task branch.")
    scopes = record.get("allowed_paths")
    if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
        raise ScopeError("The task record requires an allowed_paths list.")
    record["allowed_paths"] = validate_scopes(scopes)
    for field in ("dependencies", "resources"):
        if not isinstance(record.get(field), list):
            raise ScopeError(f"The task record requires a {field} list.")
    return record


def start_task(
    source: Path, *, task_id: str, slug: str, role: str,
    scopes: list[str], workspace_root: Path, base: str = "origin/main",
) -> dict:
    repo = repository_root(source)
    verify_repository(repo)
    if not re.fullmatch(r"[1-9][0-9]*", task_id):
        raise ScopeError("--task must be a positive GitHub issue number.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 64:
        raise ScopeError("--slug must use lowercase letters, digits, and single hyphens (64 characters max).")
    if role not in ROLES:
        raise ScopeError(f"Unknown task role: {role!r}.")
    allowed_paths = validate_scopes(scopes)
    if not workspace_root.is_absolute():
        raise ScopeError("--workspace-root must be an absolute parent directory outside the source repository.")
    parent = workspace_root.resolve()
    workspace = parent / f"{task_id}-{slug}"
    if is_within(parent, repo) or is_within(workspace, repo):
        raise ScopeError("Task workspaces must be outside the source repository.")
    git_common = Path(git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if is_within(parent, git_common) or is_within(workspace, git_common):
        raise ScopeError("Task workspaces must be outside Git's internal directory.")
    worktrees = git(repo, "worktree", "list", "--porcelain", "-z").stdout
    for field in worktrees.decode("utf-8", "surrogateescape").split("\x00"):
        if field.startswith("worktree "):
            active_workspace = Path(field[len("worktree "):]).resolve()
            if is_within(parent, active_workspace) or is_within(workspace, active_workspace):
                raise ScopeError("The requested parent is inside an existing Git worktree.")
    if os.path.lexists(workspace):
        raise ScopeError(f"Workspace path already exists; choose a new task or slug: {workspace}.")
    branch = f"feature/{task_id}-{slug}"
    if git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise ScopeError(f"Branch already exists; existing branches are never reset: {branch}.")
    base_sha = resolve_commit(repo, base)
    if git(repo, "ls-tree", "-r", "--name-only", base_sha, "--", ".dev").stdout:
        raise ScopeError("The base tracks .dev files; local task metadata must never be versioned.")
    record = {
        "schema_version": 1,
        "repository": EXPECTED_REPOSITORY,
        "task_id": task_id,
        "role": role,
        "branch": branch,
        "workspace": str(workspace),
        "base_sha": base_sha,
        "allowed_paths": allowed_paths,
        "dependencies": [],
        "resources": [],
        "status": "active",
    }
    parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(workspace), base_sha)
    try:
        local_dir = workspace / ".dev"
        local_dir.mkdir()
        (local_dir / ".gitignore").write_text("*\n", encoding="utf-8")
        (workspace / TASK_RECORD).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if git(workspace, "check-ignore", "-q", TASK_RECORD, check=False).returncode:
            raise ScopeError("Git is not ignoring the local task record.")
    except (OSError, ScopeError) as exc:
        raise ScopeError(
            f"Worktree created at {workspace}, but task metadata setup failed: {exc}. "
            "The existing worktree and branch were preserved for coordinator recovery."
        ) from exc
    return record
