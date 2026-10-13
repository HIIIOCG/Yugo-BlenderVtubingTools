"""Keep public release copy concise, versioned, and on the right product route."""

from contextlib import redirect_stderr, redirect_stdout
from fnmatch import fnmatchcase
import io
from pathlib import Path
import re
import shlex
import sys
import tempfile
import tomllib
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_release_notes


RELEASE_BASE = "https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/download"
YUGO_DOWNLOADS = [
    f"{RELEASE_BASE}/v1.2.3/yugo-1.2.3.zip",
    f"{RELEASE_BASE}/v1.2.3/yugo_physx_tracker-4.5.6.zip",
]
STUDIO_DOWNLOADS = [f"{RELEASE_BASE}/studio-v2.3.4/yugu_node_studio-2.3.4.zip"]
CHANGE = "- **Physics Solver** — Fixed an error that prevented simulations from starting."
COMPATIBILITY = "**Compatibility:** Blender 5.1 / 5.2 · Windows x64."


def release_body(version="1.2.3", downloads=None, bullets=None):
    downloads = YUGO_DOWNLOADS if downloads is None else downloads
    bullets = [CHANGE] if bullets is None else bullets
    rows = [
        f"| **[{'Main extension' if index == 0 else 'Optional tracker'}]({url})** | "
        f"{'Main Blender extension' if index == 0 else 'Optional physics tracking'} |"
        for index, url in enumerate(downloads)
    ]
    return "\n".join([
        "## Downloads", "", "| Download | Purpose |", "| --- | --- |", *rows,
        "", f"## What's changed in {version}", "", *bullets, "", COMPATIBILITY, "",
    ])


class ReleaseBodyTests(unittest.TestCase):
    def validate(self, body):
        check_release_notes.validate_body(body, "1.2.3", YUGO_DOWNLOADS)

    def test_two_package_downloads_and_node_changes_pass(self):
        self.validate(release_body())

    def test_one_main_download_without_fabricated_optional_package_passes(self):
        check_release_notes.validate_body(
            release_body("2.3.4", STUDIO_DOWNLOADS), "2.3.4", STUDIO_DOWNLOADS,
        )

    def test_empty_notes_and_old_engineering_body_fail(self):
        old_body = (
            "## macOS and Linux installation\n"
            "- Release publication requires native installation and CPU physics checks.\n"
            "Source commit: abc123\nSHA-256: abc456\n"
        )
        for body in ("", " \n\t", old_body):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.validate(body)

    def test_downloads_must_come_first(self):
        with self.assertRaises(ValueError):
            self.validate("Welcome to this release.\n" + release_body())

    def test_main_package_must_precede_optional_package(self):
        with self.assertRaises(ValueError):
            self.validate(release_body(downloads=list(reversed(YUGO_DOWNLOADS))))

    def test_omitted_or_extra_download_rows_fail(self):
        for downloads in (YUGO_DOWNLOADS[:1], YUGO_DOWNLOADS + [YUGO_DOWNLOADS[0]]):
            with self.subTest(downloads=downloads), self.assertRaises(ValueError):
                self.validate(release_body(downloads=downloads))

    def test_stale_main_optional_and_release_tag_urls_fail(self):
        for old, new in (
            ("yugo-1.2.3.zip", "yugo-1.2.2.zip"),
            ("yugo_physx_tracker-4.5.6.zip", "yugo_physx_tracker-4.5.5.zip"),
            ("/v1.2.3/", "/v1.2.2/"),
            ("/v1.2.3/", "/studio-v1.2.3/"),
        ):
            with self.subTest(replacement=new), self.assertRaises(ValueError):
                self.validate(release_body().replace(old, new))

    def test_wrong_change_version_fails(self):
        with self.assertRaises(ValueError):
            self.validate(release_body(version="1.2.2"))

    def test_source_checksum_and_build_record_links_fail(self):
        unwanted_urls = (
            "https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/archive/refs/tags/v1.2.3.zip",
            YUGO_DOWNLOADS[0] + ".sha256",
            f"{RELEASE_BASE}/v1.2.3/release-build.json",
        )
        for url in unwanted_urls:
            body = release_body(bullets=[f"- **Physics Solver** — Read the [details]({url})."])
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.validate(body)

    def test_installation_and_provenance_cannot_hide_in_change_bullets(self):
        for detail in (
            "Choose Install from Disk to install the ZIP.",
            "Open Edit → Preferences to configure the extension.",
            "Source commit: abc123.",
            "SHA-256: abc456.",
            "Check the build provenance before use.",
        ):
            with self.subTest(detail=detail), self.assertRaises(ValueError):
                self.validate(release_body(bullets=[f"- **Physics Solver** — {detail}"]))

    def test_changes_need_a_named_feature_and_concise_description(self):
        invalid_bullets = (
            [],
            [CHANGE] * 7,
            ["- Improved performance."],
            ["- **Physics Solver**"],
            ["- **Physics Solver** — " + "word " * 41],
        )
        for bullets in invalid_bullets:
            with self.subTest(bullets=bullets), self.assertRaises(ValueError):
                self.validate(release_body(bullets=bullets))

    def test_compatibility_must_be_nonempty_and_last(self):
        for replacement in ("", "**Compatibility:** ", COMPATIBILITY + "\nExtra information."):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                self.validate(release_body().replace(COMPATIBILITY, replacement))


class ReleaseRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="yugo-release-notes-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write_manifest("yugo", "1.2.3")
        self.write_manifest("yugo_physx_tracker", "4.5.6")
        self.write_manifest("yugu_node_studio", "2.3.4")
        self.yugo_path = Path("docs/releases/YUGO_1_2_3_NOTES.md")
        self.studio_path = Path("docs/releases/STUDIO_2_3_4_NOTES.md")
        self.write(self.yugo_path, release_body())
        self.write(self.studio_path, release_body("2.3.4", STUDIO_DOWNLOADS))

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_manifest(self, package_id, version, declared_id=None):
        self.write(
            f"addons/{package_id}/blender_manifest.toml",
            f'id = "{declared_id or package_id}"\nversion = "{version}"\n',
        )

    def test_each_product_selects_its_manifest_version_and_notes(self):
        for product, tag, path in (
            ("yugo", "v1.2.3", self.yugo_path),
            ("studio", "studio-v2.3.4", self.studio_path),
        ):
            with self.subTest(product=product):
                self.assertEqual(check_release_notes.check_release_notes(self.root, product, tag), path)
                self.assertEqual(check_release_notes.check_release_notes(self.root, product), path)

    def test_wrong_product_tag_and_stale_version_fail(self):
        for product, tag in (
            ("yugo", "studio-v1.2.3"), ("yugo", "v1.2.2"),
            ("studio", "v2.3.4"), ("studio", "studio-v2.3.3"),
        ):
            with self.subTest(product=product, tag=tag), self.assertRaises(ValueError):
                check_release_notes.check_release_notes(self.root, product, tag)

    def test_optional_version_comes_from_its_own_manifest(self):
        self.write_manifest("yugo_physx_tracker", "4.5.7")
        with self.assertRaises(ValueError):
            check_release_notes.check_release_notes(self.root, "yugo")
        self.write(self.yugo_path, release_body().replace("tracker-4.5.6.zip", "tracker-4.5.7.zip"))
        self.assertEqual(check_release_notes.check_release_notes(self.root, "yugo"), self.yugo_path)

    def test_studio_does_not_require_optional_manifest(self):
        (self.root / "addons/yugo_physx_tracker/blender_manifest.toml").unlink()
        self.assertEqual(check_release_notes.check_release_notes(self.root, "studio"), self.studio_path)

    def test_studio_rejects_fabricated_optional_download(self):
        self.write(self.studio_path, release_body("2.3.4", STUDIO_DOWNLOADS + [YUGO_DOWNLOADS[1]]))
        with self.assertRaises(ValueError):
            check_release_notes.check_release_notes(self.root, "studio")

    def test_missing_current_notes_do_not_fall_back_to_changelog(self):
        (self.root / self.studio_path).unlink()
        self.write("docs/releases/STUDIO_2_3_4.md", "Internal qualification report.\n")
        self.write("CHANGELOG.md", release_body("2.3.4", STUDIO_DOWNLOADS))
        with self.assertRaises(FileNotFoundError):
            check_release_notes.check_release_notes(self.root, "studio")

    def test_empty_current_notes_fail(self):
        self.write(self.yugo_path, "")
        with self.assertRaises(ValueError):
            check_release_notes.check_release_notes(self.root, "yugo")

    def test_wrong_main_or_optional_manifest_identity_fails(self):
        for package in ("yugo", "yugo_physx_tracker"):
            with self.subTest(package=package):
                path = self.root / f"addons/{package}/blender_manifest.toml"
                original = path.read_text(encoding="utf-8")
                self.write_manifest(package, "1.2.3", declared_id="another_extension")
                with self.assertRaises(ValueError):
                    check_release_notes.check_release_notes(self.root, "yugo")
                path.write_text(original, encoding="utf-8")

    def test_cli_emits_only_checked_relative_path(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(check_release_notes, "ROOT", self.root), \
                mock.patch.object(sys, "argv", ["check_release_notes.py", "--product", "studio", "--tag", "studio-v2.3.4"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(check_release_notes.main(), 0)
        self.assertEqual(stdout.getvalue(), self.studio_path.as_posix() + "\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_failure_does_not_emit_a_publishable_path(self):
        self.write(self.yugo_path, "")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(check_release_notes, "ROOT", self.root), \
                mock.patch.object(sys, "argv", ["check_release_notes.py", "--product", "yugo"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(check_release_notes.main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Release notes check failed:", stderr.getvalue())


class RepositoryPublicationTests(unittest.TestCase):
    def workflow_packages(self):
        packages = {}
        for package_id in ("yugo", "yugo_physx_tracker", "yugu_node_studio"):
            manifest = tomllib.loads(
                (ROOT / "addons" / package_id / "blender_manifest.toml").read_text(encoding="utf-8")
            )
            packages[package_id] = f"{package_id}-{manifest['version']}.zip"
        return packages

    def test_current_product_notes_pass(self):
        for product in ("yugo", "studio"):
            with self.subTest(product=product):
                path = check_release_notes.check_release_notes(ROOT, product)
                self.assertTrue((ROOT / path).is_file())

    def test_workflows_publish_checked_product_notes_without_generated_body(self):
        for workflow, product in (("release.yml", "yugo"), ("node-release.yml", "studio")):
            with self.subTest(workflow=workflow):
                text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
                checked = re.search(
                    r'(?m)^\s*notes=\$\(python -B scripts/check_release_notes\.py '
                    rf'--product {product} --tag "\$RELEASE_TAG"\)\s*$', text,
                )
                self.assertIsNotNone(checked, "Check the matching product and tag before publishing")
                self.assertIn('echo "notes=$notes" >> "$GITHUB_OUTPUT"', text)
                self.assertRegex(text, r"RELEASE_NOTES:\s*\$\{\{\s*steps\.metadata\.outputs\.notes\s*\}\}")
                publish = text[text.index("gh release create"):]
                self.assertIn('--notes-file "$RELEASE_NOTES"', publish)
                self.assertLess(checked.start(), text.index("gh release create"))
                self.assertNotRegex(text, r"--generate-notes|--notes(?:\s|=)")
                self.assertNotRegex(text, r"CHANGELOG\.md|Source commit:|SHA-?256:")

    def test_release_commands_select_only_installable_packages(self):
        packages = self.workflow_packages()
        for workflow, package_ids in (
            ("release.yml", ("yugo", "yugo_physx_tracker")),
            ("node-release.yml", ("yugu_node_studio",)),
        ):
            with self.subTest(workflow=workflow):
                text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
                version = packages[package_ids[0]].removeprefix(package_ids[0] + "-").removesuffix(".zip")
                text = re.sub(r"\$\{\{\s*steps\.metadata\.outputs\.version\s*\}\}", version, text)
                text = re.sub(r"\$\{\{\s*steps\.physx\.outputs\.package\s*\}\}", packages["yugo_physx_tracker"], text)
                text = text.replace("$RELEASE_VERSION", version)
                # Read the shell command's arguments, including assets after options.
                text = re.sub(r"\\\n\s*", " ", text)
                commands = re.findall(r"(?m)^\s*gh release create ([^\n]+)", text)
                self.assertEqual(len(commands), 1)
                package_assignments = re.findall(r"(?m)^\s*package=([^\n]+)", text)
                command = commands[0]
                if package_assignments:
                    command = command.replace("$package", shlex.split(package_assignments[-1])[0])
                arguments = iter(shlex.split(command)[1:])  # Skip the release tag.
                assets = []
                for argument in arguments:
                    if argument in ("--title", "--notes-file"):
                        next(arguments)
                    elif argument == "--verify-tag":
                        continue
                    else:
                        self.assertFalse(argument.startswith("-"), f"Review new release option: {argument}")
                        assets.append(argument)
                self.assertCountEqual(assets, [f"dist/{packages[key]}" for key in package_ids])
                self.assertNotRegex(text, r"gh\s+release\s+upload\b", "Keep additional assets out of public releases")

    def test_checksums_and_provenance_remain_in_90_day_actions_artifacts(self):
        packages = self.workflow_packages()
        for workflow, evidence in (
            ("release.yml", [
                f"dist/{packages['yugo']}.sha256", f"dist/{packages['yugo_physx_tracker']}.sha256",
                "dist/release-build.json", "dist/physx-build.json", "dist/physx-release-build.json",
            ]),
            ("node-release.yml", [
                f"dist/{packages['yugu_node_studio']}.sha256", "dist/studio-build.json",
                "artifacts/studio-release/results.json",
            ]),
        ):
            with self.subTest(workflow=workflow):
                text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
                uploads = re.findall(
                    r"(?ms)^      - uses: actions/upload-artifact@[^\n]+\n(.*?)(?=^      - |\Z)", text,
                )
                self.assertEqual(len(uploads), 1)
                upload = uploads[0]
                self.assertRegex(upload, r"(?m)^\s*if:\s*always\(\)\s*$")
                self.assertRegex(upload, r"(?m)^\s*retention-days:\s*90\s*$")
                path_field = re.search(r"(?m)^([ ]*)path:\s*\|\s*\n", upload)
                self.assertIsNotNone(path_field)
                patterns = []
                for line in upload[path_field.end():].splitlines():
                    if line.strip() and len(line) - len(line.lstrip()) <= len(path_field[1]):
                        break
                    if line.strip():
                        patterns.append(line.strip())
                for path in evidence:
                    self.assertTrue(
                        any(fnmatchcase(path, pattern) for pattern in patterns if not pattern.startswith("!"))
                        and not any(fnmatchcase(path, pattern[1:]) for pattern in patterns if pattern.startswith("!")),
                        f"Actions artifacts must retain {path}",
                    )


if __name__ == "__main__":
    unittest.main()
