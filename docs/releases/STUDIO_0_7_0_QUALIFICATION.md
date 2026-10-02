# Studio 0.7.0 integration qualification

Task #16 integrates the qualified Studio import (PR15), public API (PR18),
Floating UI (PR12), prop physics (PR13), and ragdoll (PR17). The adopted GitHub
main baseline is `b6bc88f8ec612749c46cadb794b31a838722224f`.

The final Studio runtime is commit `35adb49222abd1d9c7ddc52237cc3f4441b1484f`.
The later `945bbd7` commit changes only the standalone preview callback regression
and subprocess cache isolation; its Studio deployment files are identical.
Release notes and this qualification record do not change the runtime.

## Qualified installable ZIP

`scripts/node_build.py --require-clean` validated both source and actual ZIP,
checked the complete deployment file set and compared every file with committed
source bytes. It rejected untracked/ignored runtime files. The qualified ZIP has
52 entries, 412804 bytes, SHA-256
`e679ac986c9208846f9b85f9efefdd420a45366d2f85b41f4e657dfbd21027ae`.
Build provenance records the exact commit, clean state and every file checksum.
A ZIP rebuilt from merged GitHub main gets its own checksum/provenance;
ZIP timestamps can change its archive checksum without changing deployed bytes.

| Check | Blender 5.1.2 | Blender 5.2.0 LTS |
| --- | --- | --- |
| Full portable suite | 31 Blender scripts + 5 pure test entrypoints passed | 31 Blender scripts + 5 pure test entrypoints passed |
| Actual extracted ZIP | All three modules, native actions, restricted enable/disable passed | All three modules, native actions, restricted enable/disable passed |
| Genuine GUI scheduler | Passed | Passed |
| Actual GUI Undo/Redo while ragdoll runs | Runtime stopped, no claims, no automatic restart | Runtime stopped, no claims, no automatic restart |
| Saved Studio 0.6 graph fixture | IDs, sockets, links and action flow preserved | IDs, sockets, links and action flow preserved |

The GUI launcher seeds only its disposable preferences, uses an actual extracted
ZIP, fresh result JSON and screenshots, and rejects Python tracebacks, RNA drawing
errors or scheduler retries. Screenshots and complete logs were inspected.
The stationary timeline stays at frame42/subframe0.25 while message rows scroll,
props fall and settle home, and the original 21-bone ragdoll uses its unchanged
defaults. A hit during recovery keeps the session and current pose; Enter reaches
End only after COMPLETED; the earlier Recover ticket becomes Cancelled. Explicit
Cancel releases the rig. These checks use real timers, without manual stepping.

Local evidence under the Task16 checkout: `artifacts/studio-final-build/`,
`artifacts/studio-final-51/`, `artifacts/studio-final-52/`,
`artifacts/studio-final-gui-51/`, `artifacts/studio-final-gui-52/`.
Earlier GUI runs containing socket callback errors are superseded by these final
runs; functional completion alone was insufficient for GUI acceptance.

## Integration regressions and retained sources

- Floating UI model/service and physics solver/service retain the upstream
  source bytes. Generated message ownership, immutable payloads, native template
  append, text/avatar isolation, upstream rewiring, save/load and actual Undo
  are covered by their native regressions.
- Ragdoll solver and original rig fixture retain PR17 source bytes. Its bundled
  service matches the qualified standalone service, with the restricted-data
  restoration guard. Standalone preview and bundled sockets now register
  independently, with native drawing callback regressions.
- Whole-rig ownership bridges core bone Apply, native pose and spring writers.
  Tests preserve disjoint-bone Apply behavior, reject conflicts atomically, guard
  spring initialization before mutation, preserve identity across rename, and
  avoid writing same-name replacements. Successful ragdoll capture releases
  props held by configured anchors parented to that rig.
- `ActionSpec.policy_inputs` preserves the existing default policy and allows
  Enter-only policies. Hit/Recover/Stop remain usable with Ignore/Restart;
  independent adversarial reproduction passed.
- Standalone ragdoll full checks passed after the restricted and drawing fixes:
  solver, service, preview, restricted lifecycle, build, validate, actual ZIP and
  demo. Its separate actual-ZIP GUI passed strict complete-log inspection,
  same-session repeated hit, stable recovery, Pause, Stop/restore and cleanup.

## Explicit limits and release gate

Three optional/diagnostic Studio scripts are not counted as passing regressions:
the shape diagnostic and two tests requiring a separately provisioned official
VRM extension. Standalone protocol tests skip two mathutils cases; the Blender
protocol script passes those checks. Actual user VRM avatars and global physical
keyboard capture were not exercised. Synthesized official spring flags do not
substitute for an official VRM fixture. Ragdoll limits remain in its module README.

The `studio-v*` workflow verifies version/tag/main ancestry, clean source, the
actual ZIP and full portable suite before creating a new immutable release.
Legacy `v*` routing remains separate. No Studio tag or release is published by
this qualification; the user tests the GitHub-main package before release.
