# Yugo 0.2.3 platform qualification

Task #44 owns the integration and publication of macOS and Linux support.
The baseline is `f65dc569463125adbeb96f61d006be7d1e422ed5` (Yugo 0.2.2).
Package ID `yugo`, bundled Node identities and saved graphs remain unchanged.

## Candidate and platform boundaries

The candidate bundles official CPython 3.13 MuJoCo 3.3.7 and GLFW 2.10.2
distributions for Windows x64, Linux x64 and macOS Apple Silicon. Blender
5.1.2 and 5.2.0 are the native qualification targets. Blender 5.0 and later
do not officially support Intel macOS; Blender 4.5 compatibility is a separate
ABI/API qualification, not a wheel-only change.

The independent ProtoMotions / PhysX Tracker 0.4.0 import remains Windows x64
and retains its exact payloads and import provenance. This task does not port
OVPhysX or claim macOS/Linux support for that separate ZIP.

## Validation route

`scripts/yugo_platform_check.py --blender <absolute-binary> --build-only
--output dist` validates the source, builds one universal ZIP, validates it
and compares every ZIP member against the source. `platform-build.json`
records the source commit, ZIP checksum and all source file hashes.

`.github/workflows/yugo-platforms.yml` sends that exact archive to six native
runners: Windows, Linux and macOS Apple Silicon, each with Blender 5.1.2 and
5.2.0. Each runner executes:

```text
python -B -m unittest discover -s tests/yugo_node -p test_mujoco_dependency.py
python -B scripts/yugo_platform_check.py --blender <absolute-binary> --package <same-zip> --platform <native-platform> --output artifacts/platform
python -B scripts/run_fusion_checks.py --blender <absolute-binary> --package <same-zip> --test lifecycle --test onboarding --test physics_live --output artifacts/fusion
```

The installation check uses task-specific resources/config/scripts/extensions,
cache and temporary directories. It creates a private extension repository,
installs/enables the package through Blender's extension CLI, restarts Blender
and verifies the actual `bl_ext.user_default.yugo` module and source bytes.
It checks the Preferences version, registered Node tree/panels, editor opening,
default paused graph, and a real CPU MuJoCo model advancing under gravity.
No `MUJOCO_GL=disable` override substitutes for the ordinary import path.
The fusion checks cover both source dependency fallback and the delivered ZIP.

The release workflow reuses this qualification workflow and cannot publish
until every native matrix job passes. Its publication job downloads the same
qualified archive, checks its commit/checksum and compares all source bytes
before creating a new release. Existing tags and release assets are immutable.

## Evidence and limits

The frozen local candidate passed 19 dependency unit checks and real installed
extension checks on Windows x64 with Blender 5.1.2 and 5.2.0. Each version also
passed lifecycle, onboarding and live physics in both source and package modes
(12 fusion executions). The same local ZIP was used for both versions:
SHA-256 `a05322a4f56260fb27e2a5cc2520085329612dee2f816e3f30ee239991c2ed1a`.
Task-owned evidence is in `artifacts/wheels/unit-checks.json`,
`artifacts/final-platform-51/`, `artifacts/final-platform-52/`,
`artifacts/final-fusion-51/results.json` and
`artifacts/final-fusion-52/results.json`. The commands are the validation route
above, with the task's absolute Blender binary paths and `windows-x64`.

Qualification is pending until the native CI results are recorded in task #44
and its PR. Windows execution or static wheel metadata alone does not establish
Mac/Linux runtime success. Run artifacts retain commands, exit codes, engine
paths, platform/architecture, installed source paths and ZIP checksums.

These automated checks do not constitute live phone tracking, a private avatar
test, or inspection of the original tester's Blender installation. They do not
qualify global operating-system keyboard capture or the separate PhysX worker.
