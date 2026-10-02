# Yugu Node Studio

## 0.6.0 — Keyboard, modes and action cards

Keyboard input is split into reusable data, state, event and value nodes. On
Windows, **Keyboard Data** listens globally when **Listen** is enabled, including
when another application has focus. Keys still reach that application. Listening
starts explicitly and stops on file load or extension shutdown. A press and
release between updates are retained as separate events.

**Add → Keyboard & State** includes Keyboard Data, Key State, State Logic,
State Events, Toggle, State Value, Control Mode, Mode Match and Set Mode.
Connect **Keyboard Data: Data → Key State: Keyboard**, then
**Key State: State → State Events: State**. **On** and **Off** are events;
**State** and **Value** are current numeric values. State Logic combines conditions;
State Value eases a numeric value without delaying the press/release events.

Control Mode stores named modes. Key State can use its **Mode** output to restrict
bindings to chosen modes. Locking a mode controller discards those bindings'
presses; after unlocking, a still-held key must be released and pressed again.
Bindings using another controller or no controller keep working. Mode Match
provides a condition for If / Gate / Wait Until.

**Action Flow** calls a reusable graph containing Flow Entry, action steps and
Flow End. Its card editor reads the actual graph: step parameters and notes are
stored on the nodes, and reordering cards changes completion links. Structured
parallel and repeated blocks can be edited as cards; other graphs remain
available through the graph editor. A definition only runs when called.

**Wait Action** and **Test Action** finish asynchronously. **Parallel** starts
branches together; **Wait All** joins matching branch completions. **Repeat**
waits for each body iteration to finish before starting the next. Action Flow
supports simultaneous calls, ignore-while-running or restart behavior.
Cancel / Failed stop the affected invocation without cancelling independent
flows. Waiting steps retain the original event payload. Rewiring, deleting or
muting an active step cancels its invocation. Undo and file loading clear pending
runtime work.

The new actions and keyboard states use the same 60 Hz main-thread scheduler as
the existing motion updates. Flow Sequence still dispatches Then 1 / 2 / 3 in
order; connect action **End → Enter** when the next step must wait for completion.
Existing Key Bind nodes load for compatibility; new graphs use the split nodes.

### Try the example

Open `yugu_node_studio-0.6.0-example.blend` after installing the extension, or use
F3 → **Create Keyboard and Action Flow Example**. In the Node Editor's Yugu Studio
sidebar, **Action Flow** provides **Start sample**, **Cancel sample** and
**Select Stage 1** buttons. The sample runs timed test steps, parallel waits,
repeat and an If block without requiring a character or advancing the timeline.

Select the Action Flow node and press **Edit Cards** to edit its list. **Expand
Graph** opens the same underlying definition, and **Return** returns to the
parent graph. Enable **Keyboard Listen** to try the global bindings: **F8**
cycles Idle / Stage 1 / Stage 2, **Ctrl+R** starts only in Stage 1, and **Escape**
cancels the latest running invocation. The Key State nodes let you change them.

## 0.5.2 — Flow Sequence naming

The Sequence flow node is now displayed as **Flow Sequence** in the editor and
Add menu. Its node identifier, sockets and execution behavior are unchanged,
so existing graphs remain compatible.

## 0.5.1 — Triggered iFacialMocap calibration

iFacialMocap has a **Calibrate** trigger input at the bottom, with its manual
**Calibrate** button directly underneath. Connect a Key Bind Trigger or a flow
event to use the latest received head orientation as neutral. A held numeric
Trigger calibrates once on its rising edge. Missing head data leaves the
previous calibration intact and reports an error. Calibration refreshes the
current flow's data cache so later steps use the new neutral orientation.

Keyboard node redesign is being planned; existing Key Bind behavior is retained
in this release.

## 0.5.0 — Flow controls and triggered Reload

The N-panel no longer shows Open Yugu Node Studio Editor. VRM Data and Spring
Simulation each have a **Reload** trigger input at the bottom, with their manual
**Reload** button directly beneath its label. Existing graphs receive these
inputs without removing data links. Connect a Key Bind Trigger to reload once
when its value becomes active; holding the value does not reload repeatedly.
Hz Trigger and other flow event outputs also work with Reload.

**Add → Flow Control** includes:

| Node | Behavior |
| --- | --- |
| If | Sends Enter to True or False according to Condition. |
| Gate | Passes Enter to End while Open is active. |
| Delay | Sends End after Seconds. Additional Enter pulses are ignored while waiting. |
| Wait Until | Arms on Enter, then sends End once Condition becomes active. |
| Once | Passes the first Enter; Reset allows it to pass again. |
| Flow Sequence | Runs Then 1, Then 2, Then 3 in that order. |
| Every N | Sends End after every N Enter pulses; Reset clears the count. |

Condition and Open accept Key Bind values or their unlinked 0–1 sliders; positive
values are true. Delay and Wait Until have Reset inputs to cancel their wait.
Enter and Reset accept event pulses or the rising edge of a numeric Trigger.
Waiting uses the existing scheduler without adding another timer. Muting,
disconnecting, deleting, undoing or loading a file clears pending runtime work.
If/Gate branches retain their last applied values between accepted pulses;
disconnecting or disabling an Apply restores its captured values.

For a single shortcut that reloads both sources, connect **Key Bind → Flow Sequence
Enter**, **Then 1 → VRM Data Reload**, and **Then 2 → Spring Simulation Reload**.
For a one-time startup reload, use **Hz Trigger → Once → Flow Sequence**. Keep the
regular Body Apply → End → Spring Apply route for ordered continuous motion.

## 0.4.8 — Head Rotation Profile presets

Head Rotation Profile adds a **Preset** dropdown: **Normal**, **Live2D**, and
**Custom**. Normal uses Yugo 0.1.29's non-inverted profile; Live2D uses its inverted
profile, including the negative body multipliers. Selecting either fills all
Head / Neck / Chest / Spine values. Manual edits switch to Custom, and existing
custom values are preserved on upgrade.

Pitch / Yaw / Roll now apply to their corresponding canonical motion axes and
match the original Yugo iFacialMocap profile output, including mixed rotations.

## 0.4.7 — Spring output without Weight

Spring Simulation outputs its full simulated Bone Rotation and no longer has a
Weight control. Connect **Bone Rotation Mix** downstream to control its influence.
Existing data links remain compatible; saved Spring Weight values are ignored.
The smooth fixed-step simulation from 0.4.6 remains in place.

## 0.4.6 — Smooth moving Spring simulation

Spring Simulation now samples moving parents, the armature, centers and colliders
along the interval between pulls. Catch-up physics steps use the corresponding
intermediate pose instead of applying the latest pose to every overdue step.
This prevents timer jitter from producing sudden kicks while the character moves.

Output rotations interpolate between the last two fixed physics steps, adding
one **1/60-second** presentation delay. Pulls with no due step keep continuous
output instead of holding or returning an empty packet. The solver still runs
at 60 Hz internally, and only the connected Apply writes the result. Existing
graphs and Spring settings work without rewiring. After upgrading, press
**Reload Spring** once to rebuild any running simulation.

## 0.4.5 — Correct expression flow boundary

**Expressions to ShapeKeysValues** has only Expressions / Expression Map inputs
and a Shape Keys Values output. It runs on downstream demand, not its own events.
**ShapeKeysValues to Objects List** retains the red Enter / End sockets.
Upgrade removes the converter's accidental 0.4.3–0.4.4 event sockets, bypassing
their connected event chain while preserving data links. No new timer is added.

## 0.4.4 — Input-local target selection

VRM Data and BoneRotations to Armature now show their Armature picker beneath
the corresponding input label. ShapeKeysValues to Objects List shows its object
rows and Add Object button beneath Objects List. Connected inputs disable their
manual controls. Value nodes without target inputs keep their own selection UI.
Only layout changes; saved selections, links and evaluation remain unchanged.

## 0.4.3 — Flow UI

- Objects is displayed as **Objects List**, including VRM Data and target sockets.
  Internal socket identifiers remain unchanged for saved-file compatibility.
- **Expressions to ShapeKeysValues** retains VRM Expression Map conversion and
  adds **Enter / End**. Connect Hz Trigger → converter Enter → converter End →
  ShapeKeysValues to Objects List Enter; connect the values wire separately.
  End fires only after successful conversion. Existing pull-only data paths remain supported.
- ShapeKeysValues to Objects List also has Enter / End. Event sockets are red
  and sorted first. Bone Rotation sockets are gray.
- Bone Rotation Mix now uses only Base, Overlay and Factor. Old Mask / Bone Map
  sockets and their links are removed during migration; source nodes remain.

## 0.4.1 — Bone Rotation Mix

Add **Humanoid Motion → Bone Rotation Mix** between rotation sources and
**BoneRotations to Armature**. Inputs: **Base**, **Overlay**, optional
**Mask** and **Bone Map**; output: **Bone Rotation**. Factor 0–1 uses quaternion
SLERP, not Euler averaging or additive rotation. Base-only bones are preserved;
Overlay-only bones blend from identity (local rest rotation).

Humanoid Mask requires a matching Bone Map to resolve native bone names.
Non-humanoid bones (for example hair) use Factor without humanoid masking.
Both sources must refer to the same Armature. Spring ownership and target
restrictions are preserved. Evaluation remains pull-driven by downstream Apply;
the Mix has no timer and does not write poses itself.

For body-dependent Spring physics, keep the body Apply before the Spring pull
using its End trigger. Mixing data alone does not evaluate/apply the body first.

Version **0.4.0** is an independent variant based on **yugu_node 0.8.0**,
with **Yugo 0.1.29** MToon Optimize, Composer, bundled preset, and keyboard
behavior. It does not require Yugo to be installed. Its extension ID, node
types, operators, settings, and timers are separate from the original.

Install `yugu_node_studio-0.6.0.zip` through Blender's Install from Disk.
Use the **Yugu Studio** sidebar in either the 3D View or Studio node editor.
Existing original yugu_node graphs are not automatically converted; create a
Studio graph to use this variant. Keep the original extension for older graphs.
Multiple Apply nodes can drive disjoint bones on the same character. Overlapping
writers are rejected. Do not also drive those targets from the original add-on.

## Studio material and compositor tools

- **MToon Optimize** works on selected objects, choosing the bundled Alpha/Diff
  material setup and applying Shadow Mix and backface settings as in 0.1.29.
  Native MToon properties are synchronized to the custom shader at 2 Hz.
- **Outline Apply** is an optional checkbox in **Optimize**. Turn off MToon
  Optimize to run outlines alone. It uses the original Yugo outline bake:
  selected MToon outline modifiers become independent meshes with the bundled
  Black_OutlineFast material, copied armature modifiers/weights and driven
  Shape Keys. After successful baking, source outline modifiers are removed
  and source MToon outline preview is disabled. Save before applying; the
  operator supports Undo. No mesh decimation is performed.
- **Composer** has its own character list. Add armatures manually, or use
  **Characters from Graph** to copy references from the current Studio graph.
  Enable each character's checkbox to include its meshes in the Cryptomatte.
- **Main FX** provides Scene Color Blend, Ambient Occlusion, Rim Light,
  Separated Rim, Secondary Shadow, Separated Shadows, Gradient Color, Dark
  Bloom, and Light Bloom. All enabled characters share these scene settings.
  **Reload Composer** rebuilds the owned setup while preserving effect values.

As in Yugo, enabling Composer connects its Main FX to the scene's compositor
output. Save your scene first if you have an existing compositing setup. The
two extensions must not both manage the same scene's final compositor output.

Yugu Node Studio is an independent Blender 5.1+ Extension for building VRM Humanoid
motion graphs. Every motion source emits canonical VRM Humanoid names. Graph
operations therefore run before a character's Blender bone names are resolved.

VRM data is treated as configuration, not as a live runtime layer. The **VRM
Data** node reads Humanoid mappings, expression bindings, Spring Bone settings,
and Meta only when its Character changes or **Reload** is pressed. Runtime
evaluation consumes the resulting snapshot and writes native Blender Pose Bone
and Shape Key values.

## Trigger flow (0.4.0)

Character Output's automatic update rate is removed. Old Character Output nodes
remain readable but are retired and do not run. Existing 0.1.0 graphs must be
rewired to the new nodes; no old links or nodes are automatically deleted.

1. Open **Yugu Studio**, create a graph, and select the character in **VRM Data**.
2. Connect **Humanoid Motion** and **Bone Map** to **Motion to Bone Rotation**.
   Motion Type currently offers VRM Humanoid; future formats can be added here.
3. Connect its **Bone Rotation** to **BoneRotations to Armature**.
   The packet carries the target armature from Bone Map. Only rotations are
   written: root-object motion, bone locations, and scales are not applied.
4. Connect expressions and **Expression Map** to **VRM Expression to Shape Keys
   Values**, then its output to **ShapeKeysValues to Objects**. Native
   mesh/key targets come from the map. Binary and VRM expression overrides are
   resolved before morph weights are accumulated. Material binds are not part
   of this shape-key-only route.
5. Connect **Hz Trigger → Enter** on BoneRotations to Armature, or
   **Hz Trigger → Trigger** on ShapeKeysValues to Objects, choose **8 / 12 / 24 /
   30 / 60 Hz**. Hz Trigger lives in **Add → Flow Control**, has no Active
   checkbox, and runs automatically when connected to an enabled Apply. A clock can drive
   multiple Apply nodes; separate clocks can drive different branches.
6. For physics: **VRM Data Spring Data → Spring Simulation → Bone Rotation →
   another BoneRotations to Armature**. Connect a clock to that Apply.
   Spring has no independent Start/Stop or output FPS control.

No connected trigger source, muted source, disabled Apply, or muted Apply means
no upstream evaluation. Data converters do not write to Blender. Apply nodes
are the only character writers. A pulse pulls dependencies lazily, caching shared
nodes for that pulse. Key Bind outputs remain numeric action weights; flow event
inputs can also use their inactive-to-active transition to start a pulse.

Stopping/disconnecting a branch restores its captured rotations or shape-key
values; rotation-only output never resets locations/scales. Opening a saved file
resumes connected clocks automatically. Disable Apply or mute/disconnect the Hz
Trigger to pause a route. Network receiver Connect controls remain independent.

## Explicit targets and End flow (0.4.0)

Pulse counters, Live Flow, per-link telemetry and monitor redraw work are removed.

- **BoneRotations to Armature** has an inline Armature selector and an
  **Armatures** input (linked input wins). Its event input is now **Enter**.
  **End** fires only after a successful application and dependency-graph update;
  connect it to another Bone Enter or Shape Trigger for ordered execution.
  Disabled, muted, failed, or cyclic upstream branches do not fire End.
- **ShapeKeysValues to Objects** has an **Objects** input and a manual list with
  Add/Remove, plus an optional Blender Collection. The Shape Keys Values socket
  is gray. Linked targets override the list. Any object with actual Shape Keys
  is supported (including Mesh/Lattice); unsupported objects are skipped.
  Existing mapped object names keep their own per-object values. Other selected
  objects receive matching Shape Key names. Conflicting source values for the
  same key name are rejected rather than chosen arbitrarily. Basis is never
  driven. With no explicit target selection, both Apply nodes retain the older
  mapping-target behavior.
- **VRM Data** accepts an **Armature** input and outputs **Armatures** and
  **Objects** in addition to its existing data. It requires exactly one input
  armature. **Reload** rescans descendants and objects using an Armature modifier
  targeting that rig. It does not scan character membership per trigger.
  After connecting/changing an upstream Armature selection, press Reload.
  Inline Character selection still reloads immediately.
- New **Data → Armature** and **Data → Objects** value nodes share these target
  sockets. Since 0.4.2 Objects uses only manually added objects without
  duplicates; legacy Collection selections are flattened into the list once.
  An empty linked list stays empty; it never falls back to another
  character.
- Old Bone Apply Trigger links are renamed in place to Enter. Target sockets and
  VRM Data list outputs are added during socket migration without deleting links.
  Bone rotations are already Blender-local: another target rig must use compatible
  bone names/rest axes. Spring output must still target its simulated armature.

## Spring time and diagnostics

Physics is calibrated at fixed **1/60-second internal steps**, independent of
the chosen output Hz. Each pull accumulates actual monotonic elapsed time and
executes all due steps. Thus 8 Hz alternates seven/eight physics steps per pull;
24 Hz alternates two/three; 60 Hz normally executes one. A long UI stall is
limited to 240 steps per pull for responsiveness, retaining remaining time debt
instead of dropping it. The node displays the pending debt.

Static-boundary tests run 120 physical steps in two seconds at every offered Hz
and yield equivalent rotations. Moving parents, centers and colliders are sampled
at pull time and interpolated at each internal step. Output rotations interpolate
with one fixed step of latency to absorb uneven callback timing. Lower output
rates still display fewer poses, and rapid motion between samples cannot be
recovered exactly; trajectories under differently sampled motion may differ.

**Reload Spring** discards its history and rebuilds from the current pose on the
next pull; it does not write/reset bones immediately. **Print Spring Debug**
includes pending time, step count, cached map, rotations and solver chains, both
in the console and in the **Yugu Spring Debug** text block. Official VRM spring
animation is suspended only while an Apply node owns that spring output, and
restored when the owner releases it.

## Editable character maps

**Bone Map Value** always contains every VRM Humanoid bone, grouped in a stable
Body, Head, Legs, Arms, Left Fingers, and Right Fingers order. Select a Blender
Armature, then choose the corresponding Blender bone in each row. There are no
add/remove controls; blank bone fields are ignored. **Bone Map Mix** keeps Base
rows when the matching Overlay field is blank and replaces them when Overlay
provides a bone.

**Expression Map Value** stores a list of expressions. Each expression contains
its name, binary mode, blink and mouth overrides, Morph Target binds, and
Material Color binds. **Expression Map Mix** replaces a Base expression when
Overlay contains the same normalized name; expressions found only on Overlay
are added. These map values use the same sockets as VRM Data, so they can be
connected directly to Blender Character Output and Expression Action.

## Reusable motion masks

**Humanoid Mask** is a data-only node with no motion input. Connect its **Mask**
output to one or more **Motion Mix**, **Motion Smooth**, or **Motion Weight**
nodes. Each region slider controls the corresponding canonical Humanoid bones.
For Motion Smooth it scales that region's smoothing amount; for Motion Weight
it multiplies the node's global Weight. An unconnected Mask input behaves as a
full-strength mask.

## Legacy Key Bind compatibility

Key Bind follows Yugo 0.1.29: unassigned key by default, exact Ctrl/Shift
matching, **Hold**, **Trigger** (toggle), **Solo**, **On Value**, **Binary**,
**Smooth**, and **Exclusive / Eyes / Mouth** masks. Binary bypasses easing;
otherwise Smooth uses the same exponential ease-in/ease-out as 0.1.29.

Each graph is one shortcut group: Solo suppresses other Key Bind outputs;
otherwise the first active Exclusive binding wins. Suppressed toggles remain
latched and resume when the exclusive/solo binding ends. Eyes/Mouth locks
affect only the expression branch feeding VRM Expression to Shape Keys Values, preserving the
expression selected by the active Expression Action. They do not mask motion.

Keyboard listening starts automatically and covers Blender windows without a
manual Listen/Run button. Key release works even if Ctrl/Shift was released
first. Losing focus clears held keys; loading a file, undo, or disabling Studio
clears shortcut runtime state. Enter/Escape confirmation stays available to
Blender. Avoid assigning the same shortcut in multiple enabled add-ons.

**Key Bind** outputs a reusable **Trigger** value. It can drive multiple nodes:

- Connect it to **Blender Action** to enable or weight that motion source.
- Connect it to **Expression Action**, along with **Expression Map** from a
  reloaded VRM Data node or editable Expression Map node, to drive one
  expression selected from the character's dropdown list.

An unconnected Blender Action Trigger defaults to full strength, preserving the
timeline-driven behavior of earlier graphs.

## Source monitors

VMC, Blender Action, and Key Bind source nodes have an expandable **Monitor**.
It shows the latest canonical bone transforms, action frame, or key trigger
already evaluated by the graph. Opening a monitor does not re-read VRM
configuration or create another network receiver.

The **iFacialMocap** source instead contains a collapsible **BlendShape Mask**
with all 52 standard ARKit channels. Each row shows the received percentage as
**Data Raw**, editable **Weight** and **Smooth** controls, and the normalized
**Output** emitted from the Expressions socket. Additional channels received
from the phone are appended automatically. The internal TCP listen address and
port use iFacialMocap defaults and are intentionally hidden from the node. The
**Mirror** option mirrors head yaw and roll, matching the original Yugo motion
layer behavior.

## Standardized motion

The `VRM Humanoid Motion` socket carries canonical names such as `hips`,
`head`, `leftUpperArm`, and `rightIndexDistal`. It never carries target-specific
Blender bone names. Blender Actions are converted through the source Bone Map;
VMC and iFacialMocap are converted from Unity coordinates before entering the
graph. **Motion to Bone Rotation** resolves Blender-local rotations through the
cached Bone Map. **VRM Expression to Shape Keys Values** resolves native morph
targets through Expression Map. Only the corresponding triggered Apply nodes
write to Blender; Spring is a pull-driven Bone Rotation source.
