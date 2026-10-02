# Repository instructions for every development chat

Read [CONTRIBUTING.md](CONTRIBUTING.md),
[the parallel development rules](docs/development/PARALLEL_DEVELOPMENT.md), and
[the Node system boundaries](docs/architecture/NODE_SYSTEM.md) before editing.
The human user's explicit instructions remain authoritative. These rules do not
require a new approval for routine work already authorized by the user.

## Product and source of truth

- GitHub Issues and pull requests track tasks, dependencies, validation, and
  integration. Repository files hold the rules and architecture decisions.
- The legacy `yugo` extension is in `addons/yugo/`; its tests are in
  `tests/yugo/`. Repository governance tests are in `tests/governance/`. Preserve its
  package ID, existing releases, and working runtime while preparing the Node
  transition.
- Yugu Node Studio is the intended direction for new development.
  `addons/yugu_node_studio/` currently contains an explanatory README only.
  Task #2 imports the real source after repository-layout task #8 is merged;
  Node tests belong in `tests/yugu_node_studio/`. Do not add fake package code,
  describe planned modules as implemented, or start the import in an old layout.
  Tasks #3 and #4 still wait for the qualified #2 baseline.
- Do not synchronize or overwrite live files in another checkout or the shared
  `motion_codedev/yugo*` folders. After the Node import, Git task workspaces become
  the development source of truth; the former source folders are reference
  snapshots until an explicit migration task retires them.

## Start and own a task

- Each implementation chat owns one task ID, one branch, and one absolute
  workspace path. A separate branch in the same directory is not isolation.
- Each active chat fetches its own remote and verifies the latest `origin/main`
  before starting a task or adopting a newly merged dependency. Record and keep
  the task's chosen baseline during development; update it deliberately and
  re-run affected checks. `start_task.py` does not fetch. Do not require the
  human to pull another checkout or use their GitHub Desktop folder as a shared
  workspace. Idle chats do not synchronize until they resume work.
- Use `scripts/start_task.py` for new tasks. Read the resulting `.dev/task.json`
  and check the actual workspace and branch before making changes. A task must
  state its role, allowed paths, baseline commit, dependencies, and resource
  needs. The initial governance setup is an integration task.
- Do not implement a feature or core change in `main`, in another chat's
  workspace, or in Blender's shared installed extension directory.
- Use the smallest suitable allowed-path list. Do not widen it or change role
  to make a failing scope check pass. Record a genuine scope change in the task
  and PR, and coordinate its dependencies with the integration role.
- `docs` owns documentation tasks; `feature` owns a bounded implementation;
  `core` owns shared contracts and lifecycle; `integration` owns task setup,
  integration order, migration, and release operations.

## Edit and validate

- Preserve unrelated staged, unstaged, and untracked changes. Never reset,
  clean, restore, force-push, delete a workspace, or move another task's branch
  to clear an obstruction.
- Run `scripts/check_task_scope.py` before handing off a task. Check tracked,
  staged, unstaged, and untracked paths; do not hide changes in ignored folders.
- Core/API, socket/schema, scheduler, lifecycle, registration, and release
  changes must identify affected consumers and enter the integration order.
  Two isolated workspaces can still introduce incompatible behavior.
- Run checks appropriate to the change and report exact commands, results,
  baseline/commit, and prerequisites. Never call an unrun, skipped, or failing
  integration test passed.
- Use task-specific Blender user/config/script directories, logs, generated
  packages, copied `.blend` fixtures, and network ports. Reserve unavoidable
  shared resources in the task record; wait for their recorded owner to finish.
  Never install into or reload the user's active Blender session for a routine
  task test.
- Follow [the test material guide](docs/development/TEST_MATERIALS.md): treat
  received originals as read-only, test copies, and record the exact material
  version with the tested commit. Do not automatically commit local/private
  material or report cloud tests that lacked their required fixtures as passed.

## Hand off, integrate, and release

- Submit one focused pull request per task with Task ID, Role, Allowed paths,
  dependencies, validation, and compatibility notes. Task work finishes with a
  reviewable PR; it does not itself change the shared runtime.
- The integration role processes dependent/shared changes in order, resolves
  conflicts in its own workspace, and verifies the exact candidate against the
  latest fetched `main`. Re-run affected checks if that candidate changes.
- Only the integration role performs authorized merges, assigns product
  versions, updates release metadata, creates release tags, or publishes
  packages. Task branches must not invent competing version bumps.
- A `v*` tag currently triggers the legacy release workflow. Keep existing
  release tags and assets immutable; a corrected release gets a new version.
  Node package/tag routing must be established by the migration/release task
  before publishing a Node release.
- Agent instructions and scope checks are coordination gates, not an operating
  system sandbox. GitHub branch protection and an atomic resource/lease service
  are not guaranteed by these files; do not claim they are active without
  checking the repository settings or implementing the service.
