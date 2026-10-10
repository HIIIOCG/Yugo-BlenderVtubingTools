# Yugo 0.2.2 onboarding qualification

Task [#42](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/42)
owns the integration and release of the follow-up onboarding fixes.

## Candidate and compatibility

- Fetched and chosen main baseline: `cc05fbbf8bc226425d3491e7422237dcbce8a70f`.
- Role: integration; branch: `feature/42-onboarding-help-release`.
- Isolated workspace: `D:/Projects/BCON26/motion_codedev/.workspaces/tasks/42-onboarding-help-release`.
- Main extension: `yugo` 0.2.2, including Node 0.10.0. Studio remains 0.7.0.
- PhysX Tracker 0.4.0 retains the exact imported distribution, 2,408 files
  including its provenance manifest, and 79 Git LFS objects.
- Handbook dependency: [Yugo-Docs PR #4](https://github.com/HIIIOCG/Yugo-Docs/pull/4),
  reviewed and merged at `58ca8ea1bef2cb179750d71438e57e8b20806d45`.
  Its existing Pages workflow must finish and the public guide must be verified
  before the package is published.

Fast Started now creates a connected tracking graph, selects the character,
opens the editor, and starts with outputs paused and receivers disconnected.
It supports VMC body motion and VMC/iFacialMocap face tracking. Body and face
VMC use one receiver. Its explicit Start/Pause controls apply only to the
displayed starter graph, including pinned editors. Invalid setups fail before
mutation; a failed creation retires only its new tree/cache.

Help opens the official handbook. The top Yugo sidebar entry remains visible
with an empty Avatar List. First editor opening creates a paused Action example;
opening an existing graph retains its nodes and current output state. Existing
package, node and operator IDs remain valid; the legacy Help operator ID is kept.

The registration guard still rejects another package owning the graph type
before registering Yugo. Its message and installation instructions now identify
the older separately installed entry and explain that Yugo already includes Node.
The affected consumers are the legacy Yugo lifecycle, embedded Node editor,
onboarding operators, and independent PhysX host bridge. No Studio public API
contract or tracking socket schema is changed.

## Exact checks and resources

All commands ran in the owned workspace. In the commands below, `$TaskPython`
is `C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe`.
The binaries report Blender 5.1.2 and 5.2.0 LTS.

```powershell
& $TaskPython -B scripts/check_version.py --tag v0.2.2
& $TaskPython -B scripts/check_governance.py
& $TaskPython -B scripts/check_task_scope.py
& $TaskPython -B -m unittest discover -s tests/governance -v
git diff --check
git lfs fsck

& $TaskPython -B artifacts/build-main-private.py
& $TaskPython -B scripts/physx_build.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --output dist --require-clean

& $TaskPython -B scripts/run_fusion_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --package dist/yugo-0.2.2.zip --output artifacts/release-final-yugo-51 --vrm-source 'C:/Users/User/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/blender_org/vrm' --test onboarding --test lifecycle --test save_lifecycle --test receiver_reconnect --test release_scope --test visual_compatibility
& $TaskPython -B scripts/run_fusion_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --package dist/yugo-0.2.2.zip --output artifacts/release-final-yugo-52 --vrm-source 'C:/Users/User/AppData/Roaming/Blender Foundation/Blender/5.2/extensions/blender_org/vrm' --test onboarding --test lifecycle --test save_lifecycle --test receiver_reconnect --test release_scope --test visual_compatibility
& $TaskPython -B scripts/physx_run_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --package dist/yugo_physx_tracker-0.4.0.zip --output artifacts/release-final-physx-51
& $TaskPython -B scripts/physx_run_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --package dist/yugo_physx_tracker-0.4.0.zip --output artifacts/release-final-physx-52
& $TaskPython -B artifacts/collect-final-qualification.py
```

The main build wrapper applies `node_environment.isolated_environment` to
`scripts/build.ps1` and runs source/ZIP extension validation and byte verification.
The test runners create separate user/config/extensions/scripts/temp directories
for every test, copy test snapshots, verify source selection, and reject runtime
source changes. No active Blender or installed extension is modified.

| Check | Result |
| --- | --- |
| Main Yugo source and actual ZIP, six selected cases on both Blender versions | 24 passed |
| PhysX source and actual ZIP, lifecycle/native worker/host pipeline on both versions | 12 passed |
| Official VRM schema supplements in each onboarding source/ZIP run | 4 passed |
| Main and PhysX source/ZIP validation and complete file-set/byte identity | Passed |
| Version, governance, task scope, whitespace and LFS object integrity | Passed |
| Governance unit suite | 67 tests, OK; one Windows symlink-privilege skip |
| Handbook build/link/content checks and independent desktop/mobile browser QA | Passed |

The onboarding checks exercise a real clock-driven body Apply, pose ownership,
Pause restoring the baseline pose/releasing ownership, reload revision,
preflight and rollback, shared VMC topology, editor launch/reuse/pinning,
empty-Avatar-List entry, and the Help URL via Blender's real URL operator.

Official VRM 4.5.0 (Blender 5.1) and 4.7.2 (Blender 5.2) source installations were
read-only inputs copied into private fixtures. Their full source maps and hashes
are in each report. Tests construct characters using their actual VRM 0.x/1.0
PropertyGroups. This verifies schemas and expression mappings; it does not
qualify a private avatar, .vrm file importer, live phone/UDP reception, or a
separate facial Apply write. The body test injects typed motion at the receiver
snapshot boundary. No received/private character material was available.

Final reports are under `artifacts/release-final-yugo-51`, `release-final-yugo-52`,
`release-final-physx-51`, and `release-final-physx-52`.
`artifacts/final-qualification.json` collects exact reports, fixture versions,
source/test/tool hashes and package checksums. Runs made before the final
registration-message wording fix remain as non-qualifying history.

## Local packages and publication gate

| Local tested package | Bytes | SHA-256 |
| --- | ---: | --- |
| yugo-0.2.2.zip | 10,415,337 | `30f28b048444137aae1d04e7c934c73edafae62a08714a644670d3e3d2019f76` |
| yugo_physx_tracker-0.4.0.zip | 241,385,867 | `5c1040aa5bed7841e5c6ae98c64393202d0f089d34b9b33939276d6f8f95a8fd` |

ZIP-container checksums can change on the CI build; unpacked source bytes must
match the qualified candidate exactly. Test reports record the baseline HEAD
because they precede the final documentation commit. Integration compares the
final candidate runtime/test/tool bytes against the recorded hashes, then
compares the merged tree with the qualified tree.

The new `v0.2.2` tag uses the existing legacy release workflow. It publishes six
assets: Yugo ZIP/checksum/build report and separate PhysX ZIP/checksum/build
report. The workflow verifies all source bytes and imported provenance from the
exact tagged main commit. After success, verify uploaded states, source commit,
published report file hashes, GitHub asset digests/checksum sidecars, physics
release-note coverage, and public handbook content. Record publication on
task #42; do not infer publication from a local build. Earlier releases remain
immutable.

The original Physics Lab transition limits still apply: manual setup of old
scenes, re-enable Tracker after a host reload, and manually start its worker
after loading a file. Its bridge is private; this release does not claim a
Studio public feature adapter.

