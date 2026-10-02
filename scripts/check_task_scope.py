#!/usr/bin/env python3
"""Validate changed Git paths against a task record or explicit CI declaration.

This is a cooperation check, not an operating-system sandbox. CI declarations
must be cross-checked against the canonical GitHub issue by the PR workflow.
"""

import argparse
import json
from pathlib import Path
import sys

from task_scope import (
    ROLES, ScopeError, changed_paths, read_task_record, repository_root,
    resolve_commit, validate_changed_paths, validate_scopes, verify_repository,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true", help="Use a workflow-verified explicit declaration instead of local metadata")
    parser.add_argument("--base", help="Required with --ci: existing local base revision")
    parser.add_argument("--role", choices=ROLES, help="Required with --ci: declared task role")
    parser.add_argument("--scope", action="append", help="Required with --ci: allowed path or glob; repeatable")
    args = parser.parse_args()
    try:
        repo = repository_root(Path.cwd())
        verify_repository(repo)
        if args.ci:
            if not args.base or not args.role or not args.scope:
                raise ScopeError("--ci requires --base, --role, and at least one --scope.")
            base_sha = resolve_commit(repo, args.base)
            role = args.role
            scopes = validate_scopes(args.scope)
        else:
            if args.base or args.role or args.scope:
                raise ScopeError("Local checks must use .dev/task.json; overrides require --ci.")
            record = read_task_record(repo)
            base_sha = record["base_sha"]
            role = record["role"]
            scopes = record["allowed_paths"]
        checked = validate_changed_paths(repo, changed_paths(repo, base_sha), role, scopes)
    except (OSError, ScopeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "role": role, "base_sha": base_sha, "changed_paths": checked}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
