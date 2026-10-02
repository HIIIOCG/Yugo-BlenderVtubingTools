# Ragdoll Motion Node

Task #14 provides an articulated ragdoll feature and an explicit native Blender
node preview. It starts from the character's current evaluated pose, accepts a
world-space impulse on a selected bone, falls under gravity, and waits for ground
contact and a continuous stable interval before blending to the source pose.
A new hit during recovery keeps the current pose and velocity and resumes physics.

The source baseline is `193c33c83416d52cfc1b4184eee1feaa7b17d708`.
Studio source import (#2) and public extension contracts (#3) are still prerequisites
for the **formal Studio adapter**. This preview uses its own declared node tree and
socket IDs and does not change Studio graphs or its workflow/scheduler internals.
The public service owns no timer; its caller advances time.

## Try the feature / 試用

Install the locally built `yugo_ragdoll-0.1.0.zip` using Blender's Install from Disk.
Open the original generated `yugo-ragdoll-motion-demo.blend`, select the Ragdoll Motion
Preview editor and its demo node group. The original avatar fixtures are generated
from Python and need no downloaded character or VRM dependency.

```text
Pose Source ──Pose──> Ragdoll Motion ──Motion──> Apply Ragdoll
Hz Trigger ───────────────────────────Tick───> Apply Ragdoll
```

1. In **Apply Ragdoll**, press **Play**. The finite real-time preview runs while
   Blender's animation timeline stays stopped. **Start** only captures the pose;
   **Step** advances one tick manually.
2. In **Ragdoll Motion**, choose **Hit bone**, edit **Impulse**, then press
   **Apply / Add Impulse**. Repeated hits use the existing body, including during
   recovery. Impulse axes use world coordinates, with Z pointing up.
3. Enable **Recover when grounded and stable** for automatic recovery, or press
   **Recover to Source Pose** to request it. Both requests wait for Minimum wait,
   ground contact, Stable speed and Stable duration. Timeout reports failure.
4. **Pause** stops the preview clock and retains the pose. **Stop / Restore**
   releases the writer and restores its captured source pose. A competing writer's
   newer bone transforms are preserved.

For your own armature, select it and use F3 → **Create Ragdoll Motion Preview**.
Choose the Armature in Pose Source. Leave Bones empty to use native VRM humanoid
body mappings when available, or deform bones otherwise; a comma-separated list
selects bones explicitly. Connected ancestors are included automatically because
Blender does not allow an independently translated connected child bone.
No VRM Add-on is required for ordinary armatures.
The preview captures a static source pose. Animated/IK-driven targets require the
future common ownership handoff and pose-source adapter.

Changing graph links, selected armature, mapping or physics settings cancels the
current preview. Edit those settings before starting again. Hit bone and Impulse
can be edited during a run. Saving, loading, Undo/Redo and unregister release the
preview runtime; opening a saved file does not automatically start physics.

## Physics and boundaries

The endpoint PBD/XPBD model maintains bone length, parent attachment offsets and
parent/child swing limits. Bone-segment radii collide with a horizontal ground.
The pure solver additionally supports static spheres and axis-aligned boxes;
the node preview currently exposes ground only. The model preserves the captured
axial twist when writing Blender matrices; it does not simulate torsion.

For per-bone adjustments, use the **Data Bone** custom properties
`ragdoll_mass` (kg), `ragdoll_radius` (metres), `ragdoll_limit_min` and
`ragdoll_limit_max` (degrees). Omitted properties use size-based mass/radius and
humanoid name-based swing defaults. Invalid values are rejected before acquiring
a pose writer. Editing these values during a run releases that preview.
The automatic limits are unsigned swing cones: lower arms/legs allow up to
145 degrees, spine/chest/neck/head up to 70 degrees, upper legs at least
35 degrees from the hips direction. Custom signed hinges require an explicitly
supplied rest-world hinge axis through `JointLimit` in the solver API. Endpoint
directions cannot determine anatomical twist or the character's forward axis.

Iterative constraints are followed by an exact forward projection so native
connected Blender bones have identical joint positions and fixed lengths.
Final conservative contact correction can translate a whole root component,
which may lift another limb away from the ground. This is a bounded articulated
model, with no claim of equivalent behavior to Blender's Bullet rigid-body joints.

Motion uses 120 Hz fixed substeps. Trigger rates 8, 12, 24, 30, 60 and 120 Hz
change the sampling frequency, while physical time is integrated through the same
fixed steps. Long stalls beyond the bounded per-update budget discard excess time
and expose `dropped_time`, preventing an unbounded catch-up freeze. Reading a pose
or status does not step or start simulation.
With automatic recovery disabled, physics continues until a recovery request or
cancellation; Timeout protects requested recovery, including the grounded/stable
wait. The pure 12-bone solver benchmark over 300 updates at 60 Hz measured mean
5.713 ms and p95 6.016 ms per update, excluding Blender IO and UI.

Recovery is a kinematic pose blend after the grounded/stable gate; it is not an IK
get-up animation or a collision-preserving force recall. The current pose goal can
be supplied through the service to connect a future animation/controller adapter.
No active pose tracking, self-collision, arbitrary mesh contacts, moving obstacle
contacts, prop/seat collision, grabbing, navigation or locomotion is included.
Those interactions require #3 ownership and action completion integration and
the relevant prop/controller modules. The prior requested automatic release of
hand-held props must be called by that integration before full-body takeover.

The service accepts at most 128 mapped bones and rejects linked/read-only data,
existing animation/drivers/constraints, mirrored/sheared/nonuniform transforms,
Edit Mode, zero-length bones and duplicate pose writers. It checks object/scene
identity, bone topology/settings and external transforms around updates. Session
state contains copied values and identifiers rather than retained Blender RNA.

## Public service contract

`addons/yugo_ragdoll/service.py`:

| Operation | Behavior |
| --- | --- |
| `start(armature, bone_names=None, settings=None, owner="preview")` | Capture current pose, reserve armature, return session ID |
| `hit(id, bone, impulse)` | Add world impulse to current state and restart recovery gate |
| `update(id, dt)` | Advance fixed steps and write native bone matrices |
| `read(id)` | Validate and copy JSON snapshot, without advancing |
| `set_target(id, target_pose)` | Supply complete next recovery goal |
| `recover(id, target_pose=None)` | Request gated recovery |
| `stop(id, restore=True)` | Cancel and release, selectively restore owned bone matrices |
| `cleanup()` | Release sessions and bounded status cache |

Pose snapshots use `{bone_name: {"head": [x,y,z], "tail": [x,y,z]}}` in world
coordinates, alongside `status`, `elapsed`, `error`, `session_id` and `owner`.
Target poses require the same bone set and original bone lengths. The states are
`PHYSICS`, `RECOVERING`, `COMPLETED`, `FAILED` and `CANCELLED`. Completion means
the recovery goal has been reached, not that a single update occurred. Timeout
and target loss fail and release the writer; cancellation never emits completion.
Snapshot fields `contact`, `speed`, `since_hit`, `settled_elapsed` and
`dropped_time` expose the recovery gate and time budget.

Future Studio integration must use a single public evaluator/update cache keyed
by sampling time, the common scheduler, native pose socket types, ownership for
every armature resource and explicit hit/recover/cancel action events. It must
not call private `_runs`, `_tasks` or `_Pulse`. All lifecycle cleanup belongs in
that common contract; the finite preview clock is removed when integrating.

## Reproduce validation

Use the bundled Python or another Python 3.11+ interpreter:

```powershell
python -B -m unittest discover -s tests/ragdoll -p "test_*.py"
python -B tests/ragdoll/run_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
python -B tests/ragdoll/run_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --gui
python -B scripts/check_task_scope.py
python -B scripts/check_version.py
```

The runner uses isolated per-test Blender resource/config/script/extensions and
temporary directories. Generated ZIPs, scenes, logs and personal profiles stay
in ignored task directories; the feature does not install into or reload the
user's active Blender session. Package tests load the extracted ZIP explicitly.

Blender pose conversion follows [PoseBone matrix semantics](https://docs.blender.org/api/5.2/bpy.types.PoseBone.html)
and [Bone.convert_local_to_pose](https://docs.blender.org/api/5.2/bpy.types.Bone.html).
The finite preview uses Blender's [application timer API](https://docs.blender.org/api/5.2/bpy.app.timers.html).
Exact validation results and tested commit are recorded in the task's PR.
The generated humanoid covers native bone output; a user's actual VRM avatar and
its humanoid mapping have not been exercised as a fixture in this task.
