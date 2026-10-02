# Studio Ragdoll Motion

Press F3 → **Create Studio Ragdoll Example** and open its generated Studio graph.
The original 21-bone fixture requires no avatar or VRM add-on. Press Start in the
controls, then Hit. Pose Source → Ragdoll Motion → Apply Ragdoll uses Studio's
tree, actions, shared scheduler and exclusive armature ownership. Reading Pose
or Status never starts or advances simulation. Saved graphs do not auto-start.

Enter stays Running until the grounded/stable gate and recovery blend actually
finish. Hit adds impulse to the existing body, including during recovery, and
ends once momentum is installed. It cancels an earlier Recover invocation while
the main session continues. Recover waits for actual completion; Cancel can use
the core target_run_id payload to select one invocation. Stop releases the pose
writer and restores captured transforms still owned by it. A completed session
retains its lease so Hit can resume from its current pose; Stop releases it.
The busy policy applies to Enter invocations. Hit, Recover and Stop remain
available while Enter is waiting, including with Ignore or Restart selected.

The Pose values contain native world-space bone endpoints and armature identity.
They use an additive socket type, because Studio's existing Native Pose socket
contains local transforms. Solver/status values are copied snapshots; no RNA is
retained in action tickets. Graph/source/settings changes retire the session.
Impulse and Hit Bone can change during a run and take effect on the next Hit.

Whole-armature claims arbitrate against existing Apply/native/spring pose writers.
After a successful capture, props held by configured physics anchors parented to
that armature are released before its first step. Unrelated prop anchors continue.
Load, Undo/Redo and unregister retire sessions and claims; restricted teardown
discards numerical state without accessing unavailable Blender data.

The solver is copied unchanged from [PR17](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/17)
at `f274f0e89aef5fb2eeb2b755bad58d1726c3b14a`. The service is the same qualified
source with the integration fix that lets `_restore` retire sessions when Blender
temporarily hides `bpy.data.objects` during extension disable. Its SHA-256 is
`ED055075AB62373FA192C60DD7AAB2BC4347D145B8CE6DD05A217E8E2ECC33CB`.
The original procedural 21-bone example is copied from that PR's test fixture.
The model supports up to
128 selected bones, world-space impulse, bone length/attachment/swing constraints
and ground contact. Recovery is a kinematic blend, not an IK get-up animation or
collision-preserving force recall. Animated/IK-driven, linked, constrained,
mirrored, sheared or nonuniformly scaled rigs are rejected. No preview timer,
independent node tree or lifecycle handlers are imported.
