"""Check canonical task declarations, including false or widened claims."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from check_pr_scope import compare_declarations, declaration


def body(task=2, role="feature", paths=None):
    data = {"task_id": task, "role": role, "allowed_paths": paths or ["addons/yugu_node_studio/tests/**"]}
    return "```task-scope\n" + json.dumps(data) + "\n```\n"


class Declarations(unittest.TestCase):
    def test_canonical_match_and_order(self):
        pr = body(paths=["docs/**", "tests/**"])
        issue = body(paths=["tests/**", "docs/**"])
        self.assertEqual(compare_declarations(pr, issue)["task_id"], 2)

    def test_widened_scope_and_elevated_role_rejected(self):
        for pr in (body(paths=["**"]), body(role="integration"), body(task=3)):
            with self.subTest(pr=pr), self.assertRaises(ValueError):
                compare_declarations(pr, body())

    def test_missing_duplicate_and_invalid_blocks(self):
        for invalid in ("", body() + body(), "```task-scope\n[]\n```", body(task=True), body(task=0), body(role="admin"), body(paths=["docs/**", "docs/**"])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                declaration(invalid)

    def test_duplicate_json_keys_and_nonstring_role_are_rejected(self):
        for invalid in ('```task-scope\n{"task_id":2,"role":"feature","role":"integration","allowed_paths":["docs/**"]}\n```', body(role=[])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                declaration(invalid)

    def test_product_relocation_requires_matching_canonical_paths(self):
        old_issue = body(paths=["yugu_node_studio/tests/**"])
        relocated_pr = body(paths=["addons/yugu_node_studio/tests/**"])
        with self.assertRaisesRegex(ValueError, "allowed paths differ"):
            compare_declarations(relocated_pr, old_issue)
        self.assertEqual(
            compare_declarations(relocated_pr, relocated_pr)["allowed_paths"],
            ["addons/yugu_node_studio/tests/**"],
        )


if __name__ == "__main__":
    unittest.main()
