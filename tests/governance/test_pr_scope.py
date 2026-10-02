"""Check canonical task declarations, including false or widened claims."""
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_pr_scope
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


class MainCLI(unittest.TestCase):
    HEAD = "a" * 40
    BASE = "b" * 40
    NUMBER = 21

    def event(self):
        return {
            "action": "synchronize",
            "repository": {"full_name": check_pr_scope.REPOSITORY},
            "pull_request": {"number": self.NUMBER, "state": "open", "merged": False,
                             "body": body(), "head": {"sha": self.HEAD},
                             "base": {"sha": self.BASE}},
        }

    def issue(self, state="open"):
        return {"number": 2, "state": state, "body": body()}

    def merged_pull(self):
        return {"number": self.NUMBER, "state": "closed", "merged": True,
                "head": {"sha": self.HEAD}}

    def invoke(self, event, replies, *, checker_returncode=0, event_from_env=False, token="test-only-token"):
        responses = []
        for reply in replies:
            if isinstance(reply, BaseException):
                responses.append(reply)
            else:
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8")
                responses.append(response)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            event_path = folder / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            policy_root = folder / "trusted-policy"
            argv = ["check_pr_scope.py", "--policy-root", str(policy_root)]
            environment = {} if token is None else {"GITHUB_TOKEN": token}
            if event_from_env:
                environment["GITHUB_EVENT_PATH"] = str(event_path)
            else:
                argv.extend(["--event", str(event_path)])
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(check_pr_scope.sys, "argv", argv), \
                 mock.patch.dict(check_pr_scope.os.environ, environment, clear=True), \
                 mock.patch.object(check_pr_scope, "urlopen", side_effect=responses) as fetch, \
                 mock.patch.object(check_pr_scope.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], checker_returncode)) as run, \
                 mock.patch.object(check_pr_scope.sys, "stdout", stdout), \
                 mock.patch.object(check_pr_scope.sys, "stderr", stderr):
                result = check_pr_scope.main()
            return result, stdout.getvalue(), stderr.getvalue(), fetch, run, policy_root.resolve()

    def assert_requests(self, fetch, paths):
        self.assertEqual(fetch.call_count, len(paths))
        for call, path in zip(fetch.call_args_list, paths):
            request = call.args[0]
            self.assertEqual(request.full_url, f"https://api.github.com/repos/{check_pr_scope.REPOSITORY}/{path}")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-token")
            self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
            self.assertEqual(call.kwargs, {"timeout": 30})

    def assert_rejected(self, result):
        code, stdout, stderr, _, run, _ = result
        self.assertEqual(code, 1)
        self.assertNotIn("SKIP", stdout)
        self.assertIn("ERROR", stderr)
        run.assert_not_called()

    def test_completed_matching_head_skips_path_checker(self):
        event = self.event()
        event["pull_request"].update(state="closed", merged=True)
        result = self.invoke(event, [self.issue("closed"), self.merged_pull()])
        code, stdout, stderr, fetch, run, _ = result
        self.assertEqual(code, 0, stderr)
        self.assertIn("SKIP", stdout)
        self.assertEqual(stderr, "")
        run.assert_not_called()
        self.assert_requests(fetch, ["issues/2", f"pulls/{self.NUMBER}"])

    def test_open_event_uses_live_merge_after_issue_closes(self):
        # The job began on an open event; its Issue closed while CI was pending.
        result = self.invoke(self.event(), [self.issue("closed"), self.merged_pull()])
        code, stdout, stderr, fetch, run, _ = result
        self.assertEqual(code, 0, stderr)
        self.assertIn("SKIP", stdout)
        run.assert_not_called()
        self.assert_requests(fetch, ["issues/2", f"pulls/{self.NUMBER}"])

    def test_closed_issue_rejects_live_open_or_unmerged_pull(self):
        for state, merged in (("open", False), ("open", True), ("closed", False),
                              ("closed", None), ("closed", 1), ("closed", "true")):
            with self.subTest(state=state, merged=merged):
                live = self.merged_pull()
                live.update(state=state, merged=merged)
                self.assert_rejected(self.invoke(self.event(), [self.issue("closed"), live]))

    def test_live_pull_identity_and_head_must_match(self):
        for change in ({"number": self.NUMBER + 1}, {"number": str(self.NUMBER)},
                       {"head": {"sha": "c" * 40}}, {"head": {}},
                       {"head": None}, {"head": []}, {"head": self.HEAD}):
            with self.subTest(change=change):
                live = self.merged_pull()
                live.update(change)
                self.assert_rejected(self.invoke(self.event(), [self.issue("closed"), live]))

    def test_matching_but_invalid_head_is_not_completion_evidence(self):
        for head in ("", "a" * 39, "g" * 40, None):
            with self.subTest(head=head):
                event, live = self.event(), self.merged_pull()
                event["pull_request"]["head"] = {"sha": head}
                live["head"] = {"sha": head}
                self.assert_rejected(self.invoke(event, [self.issue("closed"), live]))

    def test_invalid_event_pull_number_cannot_skip(self):
        for number in (None, "21", True, 0, -1):
            with self.subTest(number=number):
                event = self.event()
                event["pull_request"]["number"] = number
                live = self.merged_pull()
                live["number"] = number
                self.assert_rejected(self.invoke(event, [self.issue("closed"), live]))

    def test_live_pull_api_failure_or_invalid_json_rejects(self):
        url = f"https://api.github.com/repos/{check_pr_scope.REPOSITORY}/pulls/{self.NUMBER}"
        for failure in (HTTPError(url, 403, "Forbidden", None, None), URLError("offline"),
                        b"not JSON", b"", [], None, "not an object", {}):
            with self.subTest(failure=failure):
                self.assert_rejected(self.invoke(self.event(), [self.issue("closed"), failure]))

    def test_completed_head_does_not_reapply_declaration_comparison(self):
        issue = self.issue("closed")
        issue["body"] = body(role="integration", paths=["docs/**"])
        code, stdout, stderr, fetch, run, _ = self.invoke(
            self.event(), [issue, self.merged_pull()])
        self.assertEqual(code, 0, stderr)
        self.assertIn("SKIP", stdout)
        run.assert_not_called()
        self.assert_requests(fetch, ["issues/2", f"pulls/{self.NUMBER}"])

    def test_invalid_pr_declaration_cannot_use_completed_head_skip(self):
        event = self.event()
        event["pull_request"]["body"] = "no task declaration"
        result = self.invoke(event, [])
        self.assert_rejected(result)
        result[3].assert_not_called()

    def test_active_issue_runs_trusted_checker_and_propagates_exit_code(self):
        paths = ["tests/**", "docs/**"]
        issue = self.issue()
        issue["body"] = body(paths=paths)
        event = self.event()
        event["pull_request"]["body"] = body(paths=list(reversed(paths)))
        for returncode in (0, 7):
            with self.subTest(returncode=returncode):
                code, stdout, stderr, fetch, run, policy_root = self.invoke(
                    event, [issue], checker_returncode=returncode, event_from_env=True)
                self.assertEqual(code, returncode)
                self.assertNotIn("SKIP", stdout)
                self.assertEqual(stderr, "")
                self.assert_requests(fetch, ["issues/2"])
                run.assert_called_once_with(
                    [sys.executable, "-B", str(policy_root / "scripts/check_task_scope.py"),
                     "--ci", "--base", self.BASE, "--role", "feature",
                     "--scope", "tests/**", "--scope", "docs/**"], check=False)

    def test_active_issue_still_rejects_widened_or_different_role(self):
        for pr_body in (body(paths=["**"]), body(role="integration")):
            with self.subTest(body=pr_body):
                event = self.event()
                event["pull_request"]["body"] = pr_body
                result = self.invoke(event, [self.issue()])
                self.assert_rejected(result)
                self.assert_requests(result[3], ["issues/2"])

    def test_completed_check_still_requires_canonical_issue_identity(self):
        for change in ({"number": 3}, {"pull_request": {"url": "a pull, not an Issue"}}):
            with self.subTest(change=change):
                issue = self.issue("closed")
                issue.update(change)
                result = self.invoke(self.event(), [issue])
                self.assert_rejected(result)
                self.assert_requests(result[3], ["issues/2"])

    def test_missing_token_or_wrong_repository_never_fetches(self):
        result = self.invoke(self.event(), [], token=None)
        self.assert_rejected(result)
        result[3].assert_not_called()
        event = self.event()
        event["repository"]["full_name"] = "different/repository"
        result = self.invoke(event, [])
        self.assert_rejected(result)
        result[3].assert_not_called()


if __name__ == "__main__":
    unittest.main()
