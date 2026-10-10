# Yugo 0.2.1 and PhysX Tracker 0.4.0 qualification

Task [#40](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/40)
is the integration and release task for the handbook fixes and the independent
physics add-on update requested by the user.

## Sources and release scope

- Fetched main before starting: `6da97ae7ed2eb538c13ab3f9042b0f875d8e2f73`.
- Qualified PhysX import [PR #39](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/39)
  was merged with head `da89570460942ccc888a0cd20822f7f416c19605`.
- Chosen task baseline: `84b72ae6bce193fda0e936877a1107c0f418886f`.
- Isolated workspace:
  `D:/Projects/BCON26/motion_codedev/.workspaces/tasks/40-handbook-physx-release`.
- Main extension: `yugo`, version `0.2.1`, retaining the embedded Node `0.10.0`.
- Separate extension: `yugo_physx_tracker`, version `0.4.0`; the imported
  distribution and its recorded 2,407 upstream files remain unchanged.
- Handbook prerequisite:
  [Yugo-Docs #1](https://github.com/HIIIOCG/Yugo-Docs/issues/1). Its Getting Started
  content and Compositing labels must be deployed before the release is published.

Only the public Compositing wording, documentation links, release metadata, and
the release route change in Yugo. Existing package, node, operator, and property
identifiers are retained. Studio is not assigned a version by this release.

## Candidate verification

All commands ran in the task workspace. `$TaskPython` below is
`C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe`.
Blender builds and tests used task-private user resources, configuration,
extensions, scripts, logs, and temporary files. The user's running Blender and
the original development snapshots were not used or changed.

```powershell
& $TaskPython -B scripts/check_version.py --tag v0.2.1
& $TaskPython -B scripts/check_governance.py
& $TaskPython -B scripts/check_task_scope.py
git diff --check
git lfs fsck
```

These checks passed. Main and PhysX source/ZIP extension validation and complete
file-set/byte comparisons passed. The release workflow's two Python provenance
generators and five shell blocks passed syntax checks; the generators were also
executed against the actual built ZIPs. Local YAML parser validation was not
available; successful GitHub Actions execution is required for publication.

```powershell
& ./scripts/build.ps1 -BlenderPath 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' -PythonPath $TaskPython -OutputDirectory dist
& $TaskPython -B scripts/physx_build.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --output dist --require-clean

& $TaskPython -B scripts/run_fusion_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --package dist/yugo-0.2.1.zip --output artifacts/release-yugo-51 --test lifecycle --test release_scope --test composing --test visual_compatibility
& $TaskPython -B scripts/run_fusion_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --package dist/yugo-0.2.1.zip --output artifacts/release-yugo-52 --test lifecycle --test release_scope --test composing --test visual_compatibility

& $TaskPython -B scripts/physx_run_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe' --package dist/yugo_physx_tracker-0.4.0.zip --output artifacts/release-physx-51
& $TaskPython -B scripts/physx_run_checks.py --blender 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --package dist/yugo_physx_tracker-0.4.0.zip --output artifacts/release-physx-52
```

The main build command was launched with `node_environment.isolated_environment`
applied by a task-local subprocess wrapper. The other entry points isolate their
own processes. The two Blender binaries report 5.1.2 and 5.2.0 LTS.

| Qualification | Result |
| --- | --- |
| Yugo source and actual ZIP: lifecycle, release scope, compositor, visual compatibility, both Blender versions | 16 checks passed |
| PhysX source and actual ZIP: lifecycle, native PhysX/ONNX worker, fused Yugo host pipeline, both Blender versions | 12 checks passed |
| PhysX import identity and all upstream byte/hash records | Passed |
| Both ZIPs' complete source file sets and bytes | Passed |
| Git LFS local object integrity | Passed |

The test reports were created before the final documentation commit and record
the baseline HEAD alongside the tested source hashes. Integration must compare
the final candidate's runtime and test bytes against those recorded hashes.
The release workflow then builds from the tagged main commit, verifies both
packages, and writes that exact source commit into the published provenance.

Reports remain under the task's `artifacts/release-yugo-51`, `release-yugo-52`,
`release-physx-51`, `release-physx-52`, and `workflow-validation` directories.
`artifacts/physx-release-fingerprints.json` covers 2,528 host, tracker, test, and
tool files; they were unchanged during the tests.

## Published assets and verification gate

The `v0.2.1` tag follows the legacy Yugo release route, which now also publishes
the independently qualified PhysX ZIP. It does not put PhysX inside `yugo`.
Required release assets are:

- `yugo-0.2.1.zip`, its `.zip.sha256`, and `release-build.json`.
- `yugo_physx_tracker-0.4.0.zip`, its `.zip.sha256`, and `physx-release-build.json`.

The main package retains the 50 MiB upper bound. PhysX has a separate
200–300 MiB bound, Windows x64 manifest check, 2,408-file check, and 79-object
Git LFS check. The provenance includes every source file hash and the original
242,419,870-byte distribution's SHA-256:
`3b3b636b1a32304138030a338db4226dbd1f773748cd4af7cf1251bbc81bc48e`.

The locally qualified main ZIP is 10,410,219 bytes. The qualified PhysX ZIP is
241,385,867 bytes with SHA-256
`70f1f586933f47ed3b4f3e0f24dd69a6494a7ed92a3e5d29379dc1bbc171673d`.
ZIP container checksums can differ after rebuilding from a checkout with new
file timestamps. Published source manifests must match the qualified file
bytes, and the asset digests must match their checksum sidecars.

Do not describe this document as proof of publication. Task #40 records the
final merged/tagged commit, workflow result, deployed handbook check, and
published release/asset verification after those actions actually complete.
Existing `v0.2.0` and its assets are immutable.

## Limits and compatibility

Physics Lab scenes are not converted automatically. PhysX 0.4.0 requires
disable/re-enable after reloading the Yugo host; its worker must be started
manually after loading a file. Its host bridge is private and is not a Studio
public-API adapter.

GPU execution, GUI drawing, real-person VMC/VRM fixtures, VRM spring-output
interaction, live topology/contact editing, and host hot reload were not tested
for this release. The broader Yugo suite was already qualified on the baseline;
this task re-ran the checks affected by its presentation and release changes.
The initial local full Git LFS re-download was not completed due to low network
throughput. Publication requires the Actions checkout and pinned-source verifier
to retrieve and validate all payloads; it must not treat that earlier probe as a
complete download/hash pass.
