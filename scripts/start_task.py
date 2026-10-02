#!/usr/bin/env python3
"""Create a new, isolated task branch/worktree without fetching or resetting data."""

import argparse
import json
from pathlib import Path
import sys

from task_scope import ROLES, ScopeError, start_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Positive GitHub issue number")
    parser.add_argument("--slug", required=True, help="Short lowercase task name with hyphens")
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--scope", required=True, action="append", help="Allowed repository path or glob; repeatable")
    parser.add_argument("--workspace-root", required=True, type=Path, help="Absolute parent outside the source repository")
    parser.add_argument("--base", default="origin/main", help="Existing local base revision (default: origin/main); never fetches")
    args = parser.parse_args()
    try:
        record = start_task(
            Path.cwd(), task_id=args.task, slug=args.slug, role=args.role,
            scopes=args.scope, workspace_root=args.workspace_root, base=args.base,
        )
    except (OSError, ScopeError) as exc:
        print(f"Task creation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("Task created. Open a new chat in the assigned workspace and run check_task_scope.py before delivery.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
