"""Check relocated guidance is required and remains outside addon packages."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_governance


class GuidanceLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="yugo-guidance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in check_governance.REQUIRED:
            self.write(name, "Fixture\n")
        self.exclusions = ["/.github/", "/docs/", "/AGENTS.md", "/scripts/", "/tests/"]
        self.write_manifest()
        self.write("LICENSE", "License fixture\n")
        self.write("addons/yugo/LICENSE", "License fixture\n")
        self.write(".github/pull_request_template.md", "```task-scope\n{}\n```\n")
        self.write("AGENTS.md", "Read [guidance](.github/ai/PARALLEL_DEVELOPMENT.md).\n")
        self.write(".github/CONTRIBUTING.md", "Read [rules](../AGENTS.md).\n")
        self.write(".github/ai/CHAT_STARTERS.md", "See [tests](../../docs/development/TEST_MATERIALS.md).\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_manifest(self):
        values = ", ".join(f'"{item}"' for item in self.exclusions)
        self.write("addons/yugo/blender_manifest.toml", f"[build]\npaths_exclude_pattern = [{values}]\n")

    def check(self):
        errors = io.StringIO()
        with mock.patch.object(check_governance, "ROOT", self.root), redirect_stderr(errors), redirect_stdout(io.StringIO()):
            result = check_governance.main()
        return result, errors.getvalue()

    def test_relocated_guidance_with_correct_relative_links_passes(self):
        self.assertEqual(self.check(), (0, ""))

    def test_old_root_policy_does_not_replace_required_github_policy(self):
        (self.root / ".github/CONTRIBUTING.md").unlink()
        self.write("CONTRIBUTING.md", "Old location\n")
        result, errors = self.check()
        self.assertEqual(result, 1)
        self.assertIn("Missing management file: .github/CONTRIBUTING.md", errors)

    def test_link_using_old_guide_directory_is_rejected(self):
        self.write(".github/ai/CHAT_STARTERS.md", "See [tests](TEST_MATERIALS.md).\n")
        result, errors = self.check()
        self.assertEqual(result, 1)
        self.assertIn("Broken local link in .github/ai/CHAT_STARTERS.md", errors)

    def test_github_guidance_must_be_excluded_from_legacy_package(self):
        self.exclusions.remove("/.github/")
        self.write_manifest()
        result, errors = self.check()
        self.assertEqual(result, 1)
        self.assertIn("Legacy package must exclude /.github/", errors)


if __name__ == "__main__":
    unittest.main()
