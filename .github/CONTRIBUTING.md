# Contributing

All developers and AI chats must first read [AGENTS.md](../AGENTS.md), the [parallel development workflow](ai/PARALLEL_DEVELOPMENT.md), and the [Node architecture](../docs/architecture/NODE_SYSTEM.md). Each task has one responsible chat, one branch, one isolated working copy, and one focused PR.

## Starting a task

1. Choose a task from [GitHub Issues](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues) whose prerequisites are satisfied. The [task starting guide](ai/CHAT_STARTERS.md) provides the chat sequence and commands.
2. The coordinating role confirms the responsible chat, role, allowed paths, shared interfaces, and test resources, and records the task assignment.
3. The responsible chat runs `git fetch origin` itself and verifies the official remote and latest `origin/main`. Do not discard existing work to synchronize, or ask the project owner to click Pull in GitHub Desktop first.
4. Use `scripts/start_task.py` to create an isolated worktree, or have the coordinating role create and document an equivalent isolated environment.
5. Verify the worktree's absolute path and branch. All tools must explicitly use that path.

Task #2 imported the qualified Studio 0.6.0 baseline through [PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15), following the structural cleanup in #8. The qualified public interfaces for #3 were merged through [PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18); their contract is documented in [PUBLIC_API.md](../docs/architecture/PUBLIC_API.md). The 0.7.0 release candidate for #16 now includes Floating UI, Prop Physics, and the native Ragdoll Motion adapter. Consult the integration records for complete candidate validation and release status. The official source is in `addons/yugu_node_studio/`, and tests are in `tests/yugu_node_studio/`. Use the repository worktree as the sole editable source. The original `motion_codedev` source is a read-only reference; do not maintain manual two-way synchronization.

Keep the recorded baseline during development. Fetch again when adopting newly merged dependencies, explicitly integrate them, update the baseline, and rerun affected checks. The integration role must obtain the latest mainline itself before validating a merge candidate. Idle chats and the project owner's desktop folders do not automatically synchronize when other chats update their working copies.

## Validation and delivery

- Run `python -B scripts/check_task_scope.py` to check committed, staged, unstaged, and untracked changes.
- When changing governance or entry-point documents, run `python -B scripts/check_governance.py` to check required files, links, and packaging exclusions.
- For Studio, run `python -B scripts/node_check_version.py`. For legacy Yugo, run `python -B scripts/check_version.py`. Then run the checks relevant to the change. Use `-B` to avoid generating Python caches.
- When changing isolation tools, run `python -B -m unittest discover -s tests/governance -p "test_*.py"`.
- The legacy smoke-test entry point is `python -B scripts/run_blender_tests.py --blender "<absolute path to the Blender executable>"`. Use `--list` to see available tests, and record any VRM or GUI checks that were not scheduled as not run.
- When changing an add-on, use Blender 5.1+ and validate the source, build, ZIP package, and affected functionality. Use task-specific copies of test profiles, output, and scenes.
- Studio's standalone build entry point is `scripts/node_build.py`, its test entry point is `scripts/node_run_checks.py`, and its functional tests are in `tests/yugu_node_studio/`. Both tools use a task-specific Blender profile and output. The legacy Windows build entry point, `scripts/build.ps1`, and `scripts/check_version.py` continue to handle only `addons/yugo/`; legacy tests are in `tests/yugo/`.
- Use the PR template and include the task ID, role, allowed paths, dependencies, tests actually run, and compatibility impact. List failed checks as issues; do not count skipped checks as passed.
- The coordinating role updates to the latest mainline, validates the actual merge candidate, and then merges PRs in sequence. Regular feature chats must not create official tags or overwrite existing Releases.

Studio's PowerShell build and full validation commands:

```powershell
python -B scripts/node_check_version.py
python -B scripts/node_build.py --blender "<absolute path to the Blender executable>"
$StudioVersion = python -B scripts/node_check_version.py --print-version
python -B scripts/node_run_checks.py --blender "<absolute path to the Blender executable>" --package "dist/yugu_node_studio-$StudioVersion.zip" --output artifacts/node-checks
python -B scripts/node_run_checks.py --list
```

Build validation checks the source, ZIP package, complete deployment file list, and file contents. Tests verify the repository source actually loaded and the generated examples. For checks requiring the official VRM extension or manual global-keyboard acceptance testing, explicitly record the prerequisites and checks not run according to the [Node baseline](../docs/development/NODE_BASELINE.md). Studio tags use `studio-v<version>`. The integration role handles official releases after verifying the routing according to the [Studio release workflow](../docs/development/STUDIO_RELEASE.md). The legacy `v*` release route remains in place.

Do not commit generated ZIP packages, Python caches, Blender recovery files, personal profiles, `.dev/`, working copies, or local connection settings. Required version-controlled `.blend` assets and test fixtures may be committed, but their purpose must be explained.

Handle motions, characters, and Blender test files according to [test material preparation and handoff](../docs/development/TEST_MATERIALS.md). Keep originals read-only and use your own copies for testing. Keep large or private materials locally or in designated shared storage; do not upload them on your own.

Bug reports should include the product and version, Blender version, operating system, reproduction steps, and relevant error output. Remove private avatar and network information.

By contributing, you agree that your contribution is licensed under GPL-3.0-or-later.
