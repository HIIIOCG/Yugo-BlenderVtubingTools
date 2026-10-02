# Studio test fixtures

`preset.sha256` pins the bundled `addons/yugu_node_studio/preset.blend` imported
with Studio 0.6.0. The import preserves the Yugo Contributors' original material
and compositor library under the package's GPL-3.0-or-later declaration. The
package test checks both the tracked source asset and the built ZIP against this
digest; it does not require an old release ZIP or private avatar.

Head-profile reference tests read narrowly selected defaults and pure rotation
functions from the tracked `addons/yugo/` baseline without registering that
package. The Studio lifecycle test deliberately registers that tracked legacy
package to verify independent registration, timers, properties and shutdown.

Character rigs, meshes, shape keys and outline bake input are created by the
tests. The stage-one example is generated into `YUGUS_TEST_OUTPUT` using the
manifest version and then reopened in the paired saved-example test. No user
character or motion files are included.
