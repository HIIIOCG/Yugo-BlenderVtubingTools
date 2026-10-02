"""Exercise real Git worktrees and scope boundaries without network or Blender."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from task_scope import (  # noqa: E402
    EXPECTED_REPOSITORY, ScopeError, changed_paths, git, git_text,
    normalize_path, scope_matches, start_task, validate_changed_paths,
)


class PatternTests(unittest.TestCase):
    def test_wildcards_respect_directory_boundaries(self):
        self.assertTrue(scope_matches("docs/guide.md", "docs/*.md"))
        self.assertFalse(scope_matches("docs/sub/guide.md", "docs/*.md"))
        self.assertTrue(scope_matches("docs/guide.md", "docs/**/*.md"))
        self.assertTrue(scope_matches("docs/sub/deep/guide.md", "docs/**/*.md"))
        self.assertTrue(scope_matches("docs/sub/guide.md", "docs/**"))
        self.assertFalse(scope_matches("README.md", "docs/**"))

    def test_rejects_nonportable_and_traversal_scopes(self):
        for scope in (
            "../outside", "/outside", "C:/outside", "docs/../outside",
            "docs\\outside", ".git/config", ".dev/task.json", "docs//guide.md",
            "docs/**.py", "docs/[ab].md", "docs/guide.md\n",
        ):
            with self.subTest(scope=scope), self.assertRaises(ScopeError):
                normalize_path(scope, pattern=True)


class GitTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="yugu-task-scope-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "source"
        self.repo.mkdir()
        git(self.repo, "init", "--initial-branch=main")
        self.write(self.repo, ".gitignore", ".dev/\n")
        self.write(self.repo, "README.md", "Original readme\n")
        self.write(self.repo, "docs/intro.md", "Original introduction\n")
        self.write(self.repo, "docs/old.md", "Original rename candidate\n")
        self.write(self.repo, "__init__.py", "# Legacy registration\n")
        self.write(self.repo, "composing/runtime.py", "# Legacy runtime\n")
        self.write(self.repo, "yugu_node_studio/runtime.py", "# Shared Node runtime\n")
        self.write(self.repo, "yugu_node_studio/nodes.py", "# Shared Node registry\n")
        self.write(self.repo, "yugu_node_studio/features/sway/runtime.py", "# Feature runtime\n")
        git(self.repo, "add", ".")
        self.commit(self.repo, "Initial fixture")
        git(self.repo, "remote", "add", "origin", f"https://github.com/{EXPECTED_REPOSITORY}.git")
        self.base = git_text(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.counter = 100

    @staticmethod
    def write(repo: Path, relative: str, text: str):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def commit(repo: Path, message: str):
        git(
            repo, "-c", "user.name=Task Scope Fixture", "-c", "user.email=task-scope@example.invalid",
            "commit", "-m", message,
        )

    def new_task(self, *, role="docs", scopes=None):
        self.counter += 1
        record = start_task(
            self.repo, task_id=str(self.counter), slug="fixture", role=role,
            scopes=scopes or ["docs/**"], workspace_root=self.root / "tasks",
        )
        return Path(record["workspace"]), record

    def checker(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "check_task_scope.py"), *args],
            cwd=repo, text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )

    def assertRejected(self, repo: Path, message: str, *args: str):
        result = self.checker(repo, *args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        return result

    def test_bootstrap_cli_creates_isolated_workspace_and_ignored_record(self):
        result = subprocess.run(
            [
                sys.executable, "-B", str(SCRIPTS / "start_task.py"),
                "--task", "7", "--slug", "new-docs", "--role", "docs",
                "--scope", "docs/**", "--scope", "README.md",
                "--workspace-root", str(self.root / "tasks"),
            ],
            cwd=self.repo, text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = self.root / "tasks" / "7-new-docs"
        record = json.loads((worker / ".dev/task.json").read_text(encoding="utf-8"))
        self.assertEqual(record["branch"], "feature/7-new-docs")
        self.assertEqual(record["base_sha"], self.base)
        self.assertEqual(record["allowed_paths"], ["docs/**", "README.md"])
        self.assertEqual(record["dependencies"], [])
        self.assertEqual(record["resources"], [])
        self.assertEqual(git_text(worker, "branch", "--show-current"), record["branch"])
        self.assertEqual(git_text(worker, "status", "--porcelain"), "")
        self.assertEqual(git(worker, "check-ignore", "-q", ".dev/task.json", check=False).returncode, 0)
        self.assertEqual(git_text(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(git_text(self.repo, "status", "--porcelain"), "")

    def test_checker_combines_committed_staged_unstaged_and_untracked(self):
        worker, _ = self.new_task()
        self.write(worker, "docs/committed.md", "Committed addition\n")
        git(worker, "add", "docs/committed.md")
        self.commit(worker, "Allowed committed addition")
        self.write(worker, "docs/staged.md", "Staged addition\n")
        git(worker, "add", "docs/staged.md")
        self.write(worker, "docs/intro.md", "Unstaged edit\n")
        self.write(worker, "docs/untracked.md", "Untracked addition\n")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["changed_paths"],
            ["docs/committed.md", "docs/intro.md", "docs/staged.md", "docs/untracked.md"],
        )

    def test_checks_staged_changes_even_if_working_copy_cancels_them(self):
        worker, _ = self.new_task()
        self.write(worker, "README.md", "Out-of-scope staged content\n")
        git(worker, "add", "README.md")
        self.write(worker, "README.md", "Original readme\n")
        self.assertRejected(worker, "README.md")

    def test_out_of_scope_for_every_git_change_kind(self):
        for kind in ("committed", "staged", "unstaged", "untracked"):
            with self.subTest(kind=kind):
                worker, _ = self.new_task()
                path = "other.md" if kind == "untracked" else "README.md"
                self.write(worker, path, "Outside task scope\n")
                if kind in ("committed", "staged"):
                    git(worker, "add", path)
                if kind == "committed":
                    self.commit(worker, "Outside scope")
                self.assertRejected(worker, path)

    def test_rejects_wrong_branch(self):
        worker, _ = self.new_task()
        git(worker, "checkout", "-b", "feature/999-unassigned")
        self.assertRejected(worker, "does not match the assigned task branch")

    def test_rejects_detached_head(self):
        worker, _ = self.new_task()
        git(worker, "checkout", "--detach")
        self.assertRejected(worker, "not main or detached HEAD")

    def test_rejects_main_even_when_task_record_claims_it(self):
        worker, _ = self.new_task()
        git(worker, "checkout", "--ignore-other-worktrees", "main")
        self.assertRejected(worker, "not main or detached HEAD")

    def test_rejects_wrong_workspace(self):
        worker, record = self.new_task()
        record["workspace"] = str(self.repo)
        self.write(worker, ".dev/task.json", json.dumps(record))
        self.assertRejected(worker, "not the exact workspace")

    def test_rejects_wrong_repository(self):
        worker, _ = self.new_task()
        git(worker, "remote", "set-url", "origin", "https://github.com/other/project.git")
        self.assertRejected(worker, "Expected origin")

    def test_repository_mismatch_does_not_log_url_credentials(self):
        worker, _ = self.new_task()
        git(worker, "remote", "set-url", "origin", "https://example:FAKE_SECRET@github.com/other/project.git")
        result = self.assertRejected(worker, "Expected origin")
        self.assertNotIn("FAKE_SECRET", result.stderr)

    def test_rejects_mutable_or_missing_base_record(self):
        worker, record = self.new_task()
        record["base_sha"] = "HEAD"
        self.write(worker, ".dev/task.json", json.dumps(record))
        self.assertRejected(worker, "immutable base commit SHA")

    def test_local_overrides_are_rejected(self):
        worker, _ = self.new_task()
        self.assertRejected(worker, "overrides require --ci", "--scope", "**")

    def test_feature_cannot_change_governance_or_release_even_with_wide_scope(self):
        for path in (
            "AGENTS.md", "docs/AGENTS.md", ".github/workflows/check.yml",
            "scripts/new_tool.py", "blender_manifest.toml", "CHANGELOG.md",
            "yugu_node_studio/blender_manifest.toml", "yugu_node_studio/CHANGELOG.md",
        ):
            with self.subTest(path=path):
                worker, _ = self.new_task(role="feature", scopes=["**"])
                self.write(worker, path, "Protected content\n")
                self.assertRejected(worker, "require the integration role")

    def test_feature_cannot_change_shared_or_legacy_runtime(self):
        for path in (
            "__init__.py", "composing/runtime.py", "vmc/new_feature.py",
            "yugu_node_studio/runtime.py", "yugu_node_studio/nodes.py",
            "yugu_node_studio/input_nodes.py", "yugu_node_studio/workflow_nodes.py",
            "yugu_node_studio/flow_nodes.py",
            "yugu_node_studio/core/new_registry.py",
        ):
            with self.subTest(path=path):
                worker, _ = self.new_task(role="feature", scopes=["**"])
                self.write(worker, path, "# Protected runtime content\n")
                self.assertRejected(worker, "require core or integration")

    def test_feature_can_have_its_own_runtime_and_package_init(self):
        worker, _ = self.new_task(role="feature", scopes=["yugu_node_studio/features/sway/**"])
        self.write(worker, "yugu_node_studio/features/sway/runtime.py", "# Feature edit\n")
        self.write(worker, "yugu_node_studio/features/sway/__init__.py", "# Feature registration\n")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docs_role_cannot_change_feature_code_even_with_declared_scope(self):
        worker, _ = self.new_task(role="docs", scopes=["yugu_node_studio/features/**"])
        self.write(worker, "yugu_node_studio/features/sway/runtime.py", "# Feature edit\n")
        self.assertRejected(worker, "documentation and templates only")

    def test_docs_role_can_change_declared_node_readme_and_docs(self):
        worker, _ = self.new_task(role="docs", scopes=["yugu_node_studio/README.md", "yugu_node_studio/docs/**"])
        self.write(worker, "yugu_node_studio/README.md", "Node documentation\n")
        self.write(worker, "yugu_node_studio/docs/guide.md", "Node guide\n")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_integration_still_requires_declared_scope(self):
        worker, _ = self.new_task(role="integration", scopes=["docs/**"])
        self.write(worker, "AGENTS.md", "Governance edit\n")
        self.assertRejected(worker, "outside the task's declared allowed_paths")

    def test_integration_can_change_declared_governance(self):
        worker, _ = self.new_task(role="integration", scopes=["AGENTS.md"])
        self.write(worker, "AGENTS.md", "Governance edit\n")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_core_can_change_declared_shared_files(self):
        worker, _ = self.new_task(role="core", scopes=["yugu_node_studio/runtime.py"])
        self.write(worker, "yugu_node_studio/runtime.py", "# Shared runtime edit\n")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rename_checks_old_and_new_paths(self):
        worker, _ = self.new_task(scopes=["docs/new.md"])
        git(worker, "mv", "docs/old.md", "docs/new.md")
        paths = changed_paths(worker, self.base)
        self.assertEqual(paths, {"docs/old.md", "docs/new.md"})
        self.assertRejected(worker, "docs/old.md")
        worker, _ = self.new_task(scopes=["docs/old.md"])
        git(worker, "mv", "docs/old.md", "README-renamed.md")
        self.assertRejected(worker, "README-renamed.md")

    def test_committed_rename_checks_both_paths(self):
        worker, _ = self.new_task(scopes=["docs/new.md"])
        git(worker, "mv", "docs/old.md", "docs/new.md")
        self.commit(worker, "Rename outside source scope")
        self.assertRejected(worker, "docs/old.md")

    def test_deletion_requires_scope(self):
        worker, _ = self.new_task(scopes=["docs/intro.md"])
        git(worker, "rm", "docs/old.md")
        self.assertRejected(worker, "docs/old.md")
        worker, _ = self.new_task(scopes=["docs/**"])
        git(worker, "rm", "docs/old.md")
        result = self.checker(worker)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed_paths"], ["docs/old.md"])

    def test_protected_deletion_is_rejected(self):
        worker, _ = self.new_task(role="feature", scopes=["**"])
        git(worker, "rm", "yugu_node_studio/runtime.py")
        self.assertRejected(worker, "require core or integration")

    def test_changed_symlink_is_rejected_when_supported(self):
        worker, _ = self.new_task()
        target = self.root / "outside.md"
        target.write_text("External file\n", encoding="utf-8")
        try:
            (worker / "docs/linked.md").symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        self.assertRejected(worker, "symbolic link")

    def test_git_symlink_mode_is_rejected_even_without_os_symlink_support(self):
        worker, _ = self.new_task()
        # A symlink checkout can be an ordinary text file on Windows. Its Git
        # index mode still records that it is a link and must be checked.
        target = "../outside.md\n"
        blob = subprocess.run(
            ["git", "-C", str(worker), "hash-object", "-w", "--stdin"],
            input=target, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        self.write(worker, "docs/linked.md", target)
        git(worker, "update-index", "--add", "--cacheinfo", "120000", blob, "docs/linked.md")
        self.assertRejected(worker, "Git symbolic link")

    def test_validate_paths_rejects_traversal_even_with_wide_scope(self):
        with self.assertRaisesRegex(ScopeError, "traversal"):
            validate_changed_paths(self.repo, ["../outside.md"], "integration", ["**"])

    def test_bootstrap_refuses_parent_inside_source(self):
        with self.assertRaisesRegex(ScopeError, "outside the source repository"):
            start_task(
                self.repo, task_id="9", slug="collision", role="docs",
                scopes=["docs/**"], workspace_root=self.repo / "workspaces",
            )
        self.assertFalse((self.repo / "workspaces").exists())

    def test_bootstrap_refuses_parent_inside_other_worktree(self):
        worker, _ = self.new_task()
        with self.assertRaisesRegex(ScopeError, "inside an existing Git worktree"):
            start_task(
                self.repo, task_id="9", slug="collision", role="docs",
                scopes=["docs/**"], workspace_root=worker / "workspaces",
            )

    def test_bootstrap_preserves_colliding_directory(self):
        collision = self.root / "tasks/9-collision"
        collision.mkdir(parents=True)
        self.write(collision, "keep.txt", "Keep my data\n")
        with self.assertRaisesRegex(ScopeError, "already exists"):
            start_task(
                self.repo, task_id="9", slug="collision", role="docs",
                scopes=["docs/**"], workspace_root=self.root / "tasks",
            )
        self.assertEqual((collision / "keep.txt").read_text(encoding="utf-8"), "Keep my data\n")
        self.assertEqual(git(self.repo, "show-ref", "--verify", "--quiet", "refs/heads/feature/9-collision", check=False).returncode, 1)

    def test_bootstrap_preserves_colliding_branch(self):
        git(self.repo, "branch", "feature/9-collision")
        with self.assertRaisesRegex(ScopeError, "Branch already exists"):
            start_task(
                self.repo, task_id="9", slug="collision", role="docs",
                scopes=["docs/**"], workspace_root=self.root / "tasks",
            )
        self.assertEqual(git_text(self.repo, "rev-parse", "feature/9-collision"), self.base)
        self.assertFalse((self.root / "tasks/9-collision").exists())

    def test_missing_base_is_reported_without_network_or_workspace_creation(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(ScopeError, "unavailable locally"):
            self.new_task()
        self.assertFalse((self.root / "tasks").exists())

    def test_bootstrap_rejects_wrong_origin(self):
        git(self.repo, "remote", "set-url", "origin", str(self.root / "another-repo"))
        with self.assertRaisesRegex(ScopeError, "Expected origin"):
            self.new_task()

    def test_bootstrap_validates_task_slug_and_absolute_parent(self):
        for task_id, slug, parent in (
            ("../1", "safe", self.root / "tasks"),
            ("1", "../escape", self.root / "tasks"),
            ("1", "Uppercase", self.root / "tasks"),
            ("1", "safe", Path("relative-parent")),
        ):
            with self.subTest(task=task_id, slug=slug), self.assertRaises(ScopeError):
                start_task(
                    self.repo, task_id=task_id, slug=slug, role="docs",
                    scopes=["docs/**"], workspace_root=parent,
                )

    def test_ci_requires_explicit_complete_declaration(self):
        self.assertRejected(self.repo, "requires --base, --role", "--ci")

    def test_ci_supports_detached_head_and_explicit_scope(self):
        worker, _ = self.new_task()
        git(worker, "checkout", "--detach")
        self.write(worker, "docs/intro.md", "CI docs edit\n")
        result = self.checker(worker, "--ci", "--base", self.base, "--role", "docs", "--scope", "docs/**")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed_paths"], ["docs/intro.md"])


if __name__ == "__main__":
    unittest.main()
