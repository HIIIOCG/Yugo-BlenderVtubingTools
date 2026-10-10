# Fusion validation

These checks load the complete `yugo` entry point, then expose its `node` child
to the existing DEV 0.10.0 regressions. Registration and shutdown therefore
exercise both legacy and Node runtime together. The imported regression test
assertions, classes and functions are preserved; only package loading,
test-output locations and two physics module imports were adapted.

Run from the task worktree with Python 3.11+ and Blender 5.1 or newer:

```powershell
python -B scripts/run_fusion_checks.py --list
python -B scripts/run_fusion_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" --output artifacts/fusion-source-51
python -B scripts/run_fusion_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --package dist/yugo-0.1.26.zip --output artifacts/fusion-zip-52
```

Use a fresh output directory for each invocation. Providing a ZIP runs source
and ZIP checks by default; `--mode package` selects only ZIP checks. Repeat
`--test lifecycle` or another listed name to select checks. Camera and further
legacy regressions are available as optional selections.

The runner verifies the legacy preset checksum, exact package file list and
bytes before running any ZIP test. Every process gets separate Blender user
directories, temporary files and dependency caches. Its test script and
bootstrap are copied into that process's output directory. Success requires
zero exit status, the actual source assertion and the test's completion marker;
ordinary process exit alone does not count as passed. Logs and `results.json`
remain in the selected output directory.

`blender_lifecycle.py` checks the frozen 121 Node/Socket/Tree class inventory,
one `Yugo` sidebar category, shared legacy composer/material/outline assets,
save/load of a linked graph and shared avatar, two complete package reloads,
registration failure cleanup/retry, and rejection of simultaneous historical
Studio registration in either order. The historical Studio conflict test uses
the separately preserved `addons/yugu_node_studio` source even in ZIP mode.

Receiver reconnect checks use real temporary localhost UDP/TCP ports; they do
not require a phone. Physics uses the bundled official MuJoCo wheels. Composing,
MToon and outline checks use native/synthetic data and the legacy preset.
Official VRM extension integration, actual character appearance, keyboard
hardware and sidebar visual layout require their separate fixtures or manual
acceptance; this runner does not report those checks as passed.
