#!/usr/bin/env python3
"""Match a PR declaration to its canonical Issue and run trusted scope policy."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen

REPOSITORY = "HIIIOCG/Yugo-BlenderVtubingTools"
ROLES = {"docs", "feature", "core", "integration"}
BLOCK = re.compile(r"^```task-scope[ \t]*\r?\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate declaration key: {key}")
        result[key] = value
    return result


def declaration(body: str) -> dict:
    matches = BLOCK.findall(body)
    if len(matches) != 1:
        raise ValueError("Exactly one task-scope JSON block is required")
    data = json.loads(matches[0], object_pairs_hook=unique_keys)
    if not isinstance(data, dict):
        raise ValueError("Task declaration must be an object")
    if type(data.get("task_id")) is not int or data["task_id"] < 1:
        raise ValueError("task_id must be a positive GitHub Issue number")
    if not isinstance(data.get("role"), str) or data["role"] not in ROLES:
        raise ValueError("Invalid task role")
    paths = data.get("allowed_paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p for p in paths):
        raise ValueError("allowed_paths must be a nonempty string list")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate allowed paths")
    return {"task_id": data["task_id"], "role": data["role"], "allowed_paths": paths}


def compare_declarations(pr_body: str, issue_body: str) -> dict:
    pr = declaration(pr_body)
    issue = declaration(issue_body)
    if pr["task_id"] != issue["task_id"] or pr["role"] != issue["role"]:
        raise ValueError("PR task/role differs from canonical GitHub Issue")
    if set(pr["allowed_paths"]) != set(issue["allowed_paths"]):
        raise ValueError("PR allowed paths differ from canonical GitHub Issue")
    return issue


def github_object(path: str, token: str) -> dict:
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "yugo-task-scope"},
    )
    with urlopen(request, timeout=30) as response:
        data = json.loads(response.read(1048576).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GitHub API response must be an object")
    return data


def same_head_already_merged(pull: dict, token: str) -> bool:
    """Confirm completion live; the queued event can still describe an open PR."""
    number = pull.get("number")
    head = pull.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if type(number) is not int or number < 1:
        raise ValueError("A positive PR number is required to confirm completion")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("A verified PR head SHA is required to confirm completion")
    current = github_object(f"pulls/{number}", token)
    current_head = current.get("head")
    return (
        type(current.get("number")) is int and current["number"] == number
        and current.get("state") == "closed" and current.get("merged") is True
        and isinstance(current_head, dict) and current_head.get("sha") == head_sha
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--policy-root", type=Path)
    args = parser.parse_args()
    try:
        if args.event is None:
            raise ValueError("GitHub pull_request event file is required")
        event = json.loads(args.event.read_text(encoding="utf-8"))
        if event.get("repository", {}).get("full_name") != REPOSITORY:
            raise ValueError("Unexpected repository")
        pull = event.get("pull_request")
        if not isinstance(pull, dict):
            raise ValueError("Only pull_request events are accepted")
        scope = declaration(pull.get("body") or "")
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN is required to read the canonical Issue")
        issue = github_object(f"issues/{scope['task_id']}", token)
        if issue.get("pull_request") or issue.get("number") != scope["task_id"]:
            raise ValueError("Canonical task must be a GitHub Issue")
        if issue.get("state") != "open":
            if issue.get("state") == "closed" and same_head_already_merged(pull, token):
                print(f"SKIP: PR #{pull['number']} at {pull['head']['sha']} is already merged; "
                      "the canonical task is complete")
                return 0
            raise ValueError("Canonical task must be open during development")
        scope = compare_declarations(pull.get("body") or "", issue.get("body") or "")
        policy_root = (args.policy_root or Path(__file__).resolve().parents[1]).resolve()
        checker = policy_root / "scripts/check_task_scope.py"
        base_sha = pull.get("base", {}).get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise ValueError("A verified PR base SHA is required")
        command = [sys.executable, "-B", str(checker), "--ci", "--base", base_sha, "--role", scope["role"]]
        for path in scope["allowed_paths"]:
            command.extend(["--scope", path])
        return subprocess.run(command, check=False).returncode
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
