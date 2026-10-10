# Yugo Node

## Integration in Yugo

This directory contains the Node runtime embedded in the `yugo` extension.
Install and enable Yugo once; its existing controls and the Node controls share
the **Yugo** sidebar tab. Node graph, node, socket and operator identities remain
the imported 0.10.0 identities so existing graphs can still be read.

The integration follows the user's choice to keep the legacy Composer and
`addons/yugo/preset.blend`. Composer membership, Main FX, MToon optimization and
outline baking use those shared legacy implementations and assets. Node no
longer registers a second Composer or material synchronization system.

The built-in Floating UI collection is preserved separately in
`assets/floating_ui.blend`, with its image packed, so it does not replace the
legacy preset or introduce another Comp/MToon template. Its compatibility
importer uses this dedicated library. Chat, keyboard and other nodes retired in
0.10.0 remain inactive compatibility nodes; preserving the asset does not enable
their operators or background callbacks.

Imported sources are DEV `fb1391c` and Tools `659970a`. The migration record and
current validation status are in
[YUGO_NODE_FUSION.md](../../../docs/development/YUGO_NODE_FUSION.md).
The following version notes describe the original Node releases; earlier
standalone-install and separate-Composer instructions are historical.

## Saving live previews and installing MToon shaders

Saving writes the original authored pose, Armature root transform and shape-key
values for channels controlled by Apply. The live preview is restored after the
save, including when writing the file fails. Apply ownership and pending flow
events are retained, and an intentionally paused scheduler stays paused.
Physics still releases its temporary transforms and settings during save;
recording stops while the captured samples remain available for export.

Load and Undo recovery validate saved physics metadata before changing any
channel. Invalid data is retained for diagnosis, with the affected object and
reason reported in Blender's console. Other objects and the scheduler continue
recovering. A failed save-pre cleanup or an unavailable target likewise reports
its error without preventing other Apply targets from restoring their authored
values for the save.

**Optimize MToon** checks its preset assets before modifying the material. If
installation fails, the existing shader and native shade color are restored so
the operation can be retried. A successful repeated installation continues to
apply Shadow Mix only once.

## Button Trigger — manual flow input

Add **FlowControl → Button Trigger**, connect its red **Trigger** output to
an **Enter**, **Reset**, **Reload** or other event input, then press **Trigger**
on the node. Each press sends one event immediately. It does not repeat between
presses or require an Hz Trigger. Muting the node stops its button from firing.

For example, connect **Button Trigger → Flow Sequence → Then 1 / Then 2 / Then 3**
to run several actions in order, or **Button Trigger → Once → End** to run only
on the first press. A second Button Trigger connected to **Once → Reset** allows
it to run again. **Delay** and **Wait Until** continue waiting with the existing
scheduler after the press; additional presses while waiting follow those nodes'
existing behavior. This source carries an event pulse, with no numeric value.

## Shift+A categories

The Add menu follows the node frames: **Inputs**, **Custom Inputs**,
**Math / Mixer**, **FlowControl**, **Outputs**, **Motion Operation**,
**Expression Operation** and **Physics**. Math nodes are directly available
under Math / Mixer, without Float/Vector submenus. Button Trigger, Flow Sequence
and Repeat remain available under FlowControl.

Each **Mixer** shortcut creates the same Mixer node with Type already selected:
**Math / Mixer → Mixer** uses **Float**, **Motion Operation → Mixer** uses
**Humanoid Motion**, and **Expression Operation → Mixer** uses **Expressions**.
You can still change Type on any Mixer to Vector, maps, transforms or Physics
Description; the shortcuts do not create separate node classes.

## String / Value conversion

**Math / Mixer → String to Value** converts a **Text** input to a Float **Value**.
Type text directly into the unlinked input, or connect **String → Text**.
Signed integers, decimals and scientific notation are supported; surrounding
whitespace is ignored. Empty, invalid text, NaN, Infinity and overflowing text
return `0`. The whole text must be numeric: labels, units and comma separators
are not removed. A non-Text connection is rejected.

**Math / Mixer → Value to String** converts a finite Float **Value** to **Text**.
Type a value into the unlinked input, or connect Math, Mixer or another numeric
output. The output keeps the number's precision without fixed decimal rounding;
for example, `2.5` becomes `"2.5"` and `2` becomes `"2.0"`. Very large or small
values may use scientific notation. Invalid types and nonfinite numeric inputs
are rejected, following the Math nodes' finite-number rules.

Links override the manual input without changing it; disconnecting restores the
manual value. Both nodes use the existing pull cache without creating timers or
writing scene data. Muting outputs `0` for String to Value or empty text for
Value to String. Connect **String → String to Value → Math**, or
**Math → Value to String → Meta Value** to use the converted output.

## 0.10.0 — Focus the available nodes

**VMC Receiver** and **iFacialMocap** have separate **Connect** and **Disconnect**
inputs, each with its own button. The buttons remain usable when an input is
wired. Both inputs accept flow events or a numeric value's inactive-to-active
transition. Repeated
Connect pulses keep a healthy receiver with unchanged settings; repeated
Disconnect pulses leave it stopped. After changing an address or port, press
Connect to apply the new settings directly. Connect also replaces a stopped or
failed receiver without a preliminary Disconnect. If the iFacialMocap phone
stream is absent, its Connect button resends the start command immediately;
event pulses retry at most once per second while keeping the listener.
Address inputs accept **Text** (for example, String), and port inputs accept
**Float**. Unlinked inputs remain editable on the node. Linking a value overrides
the manual setting for Connect; removing the link restores the saved manual value.
VMC exposes **Address** and **Port**; iFacialMocap exposes **iPhone Address**
and **Device Port**. Connect reads their current values. Ports are truncated to integers and clamped to 1–65535; invalid
non-finite values or a non-text address report an error.
If the same event is wired directly to both inputs, Disconnect takes precedence.
Older saved receivers gain these inputs while retaining their motion/expression
links. iFacialMocap also retains its **Calibrate** input and button, plus Mirror
and BlendShape Mask settings. Connect is handled before Calibrate when the same
event is wired to both; calibration still requires a received head frame.

Temporarily remove **Chat**, **Keyboard & State** and **Actions** from the Add
menu and node search while those features are unfinished. YouTube Chat also
leaves Sources. Their panels, example builders and connection/listener controls
are unavailable; Chat and keyboard background callbacks are stopped.

Files from earlier versions keep the retired nodes' properties, sockets and
connections as inactive compatibility nodes. They display **Unavailable in
Yugo Node 0.10.0** and cannot evaluate, receive events or start a connection.
Test Event and the older keyboard binding nodes are inactive as well.

**FlowControl → Parallel** and **Wait All** are removed from the Add menu and
node search. Old files retain their properties, sockets and connections as
inactive compatibility nodes. Parallel dispatch and Wait All joining are removed
from the workflow runtime. **Repeat** remains available, including independent
simultaneous invocations through its **When Busy → Parallel** setting.

**Float Receiver** and **Physics → Spring Simulation (Legacy)** are also
removed from the Add menu and node search. Old files retain them as inactive
compatibility nodes, with their saved values and connections intact. The Float
Receiver scheduler, event capture and driver-path controls are removed. The
shared Float socket and numeric/math nodes remain available.

**Custom Inputs → Meta Value** provides manually authored character metadata. Fill in
Name and Author; expand Details for Version, Copyright, Contact and Reference.
Each field accepts a Text connection, which overrides the manually entered
value. **Custom Inputs → String** provides editable text for these inputs and other Text
sockets. Disconnect an input to use its manually entered value again.
Its Meta output can connect to Data Viewer without loading VRM data.
**Custom Inputs → Image** selects an existing Blender image or opens a local image file,
and outputs that image through the shared Image socket. Image From URL remains
available for URL references. These value nodes do not download images or
modify the character's VRM metadata.

**Expression Smooth → Names** also accepts a Text connection from String.
Enter expression names separated by commas; an empty value smooths all
expressions. Linking overrides the saved manual Names value, and disconnecting
restores it. Existing nodes gain the Names input while keeping their values
and connections.

**Spring to YugoPhysics** has a **Preview Collision Spheres** checkbox, off by
default. Enable it to preview the incoming VRM Spring bones' hit radii as
viewport wire spheres at their current segment tails. This displays Spring
hit-radius spheres, not the environment's collider objects. The preview follows
the current armature and pose, scales radii with the armature, and stays round
without applying armature scale twice. Scene length units do not resize the
viewport markers.

The checkbox adds no sockets and works without a Solver connection. It does not
build or advance physics or change the Physics Description. Each converter owns
its own temporary display objects; turning it off, muting, disconnecting Spring
Data or deleting the node clears that display. Loading/Undo and addon shutdown
also clear temporary markers. The checkbox preference is saved in the graph.

Use **Spring to YugoPhysics → YugoPhysics Solver** for Spring physics.
The Solver starts playing automatically when enabled and connected to a valid
**Physics Description**. Its inputs are **Physics Description**, **Reset**, then
**Stop**; Reset and Stop each have a button beside their input. **Play** and
**Updated** sockets, Physics Hz, manual Build/Play/Step controls and the recording
panel are removed from the node. Physics runs internally at 240 Hz by default;
**Object Transforms**, **Bone Transforms** and **Simulation Data** remain outputs.

The Solver checks wiring and Enabled/Mute controls each motion cycle. It audits
authored Physics Description inputs at 2 Hz, so mass, friction, shape, selection,
Spring settings, rest geometry and collider-size edits normally apply within
0.5 seconds. Reset requests an immediate audit. A real boundary-scale change
requests the matching geometry before the next sample; scene-unit/Physics Hz
changes also update immediately. Deleting or renaming a modeled source object
requests an immediate audit, preserving surviving bodies' state and time.
Moving poses do not recompile the model.
Spring conversion reuses unchanged authored geometry, while evaluated attachment
and collider poses are sampled every motion cycle. Changed models retain the
position, rotation, velocity and physical time of compatible bodies and joints.
New bodies start at their source pose. Reset clears simulation history and resumes
playing; Stop releases applied transforms and rigid body settings and stays stopped
until Reset. Disable/mute or disconnect the description to release the world;
reconnecting a valid description starts it again. Invalid descriptions show an
error instead of publishing a partially updated model.

For rigid bodies, use the explicit **Objects List** selection or the converter's
manual list. A real Blender Object copy enters/leaves the world at the next model audit after that upstream
list changes. Creating a copy alone does not select it automatically. Current
Collection compatibility flattens old membership once; it does not follow new
collection members. Geometry Nodes virtual instances do not yet carry independent
physics bindings. Frequent large body lists still require recompilation and may
cause an update delay; the Solver batches the description available at each update.

Connect data to the appropriate Apply node and an external event source such as
**Hz Trigger → Enter** to consume it. For ordered body/Spring application, an
upstream Apply's **End → Enter** can supply the downstream event. The Solver only
publishes data; reading its outputs does not advance time or write scene objects.
Unconnected transform outputs require no presentation interpolation or bone/object
conversion. Reading a transform port computes the current packet once per publication;
connected Bone Transforms maintain their continuous display frames each motion cycle.
Simulation Data observes raw native states independently. Normal Spring playback
refreshes complete native poses once after its last substep; diagnostic observers and
custom rigid damping retain the per-step refresh they require.

Internal Simulation Data and diagnostic trace support remain available, but the
Solver no longer presents recording controls. **Record Both** is unavailable
because the Legacy solver is retired.

Blender Action, Expression Action, Flow Control, generic Data nodes, Motion Delay
and MuJoCo physics remain available. This release retains the 0.9.7 Spring output
fixes. Install **yugo_node-0.10.0.zip** and restart Blender to load the new release.

## 0.9.7 — Smooth Spring output without axial spinning

Spring to YugoPhysics is a conversion node: Spring Mass and Stiffness Scale
controls are removed. It uses the authored Spring settings with a fixed internal
reference mass and the calibrated conversion. Values saved in those old controls
no longer affect the result; existing connections are preserved.

Spring output interpolates between the two latest physical states, including
updates that complete no new physics step. This adds one physical step of
presentation latency and avoids a staircase from uneven update timing. It does
not filter the motion or alter MuJoCo integration. Comparison recordings retain
the original solved states.

Converted Spring bones derive their output rotation from their solved tail
direction. The conversion accounts for the corrected parent frame so removing
an unwanted axial spin does not rotate the child's intended world direction.
Spring roots stay at their animated attachment; corrections within the Spring
subtree cannot move its animated driver. Near a direction reversal, output
frames stay continuous and recover their reference roll at a bounded rate.
Manually authored native ball joints retain their complete rotation.

Comparison JSON still records full native rotations and positions, including
native roll and attachment compliance. Those raw diagnostics differ from the
projected, interpolated graph output and must not be treated as its display pose.

Install the new ZIP and restart Blender, then use **Stop → Build → Play** on the
YugoPhysics Solver to replace its runtime world.

## 0.9.6 — Preserve authored Spring response in MuJoCo

Spring to YugoPhysics now calibrates Stiffness, Drag and Gravity against the
original fixed 60 Hz SpringBone response. Native MuJoCo viscous drag resists
world-space tail movement; relative joint damping previously encouraged tails
to follow the parent. Relaxation rates, finite joint inertia and the authored
gravity/stiffness ratio are converted together. Physics Hz controls integration
quality and cost; it does not rescale the authored parameters.

The conversion uses a reference mass at each chain root. Descendant masses decrease
with depth, with finite lower bounds, to approximate the original independent
tails while retaining one coupled native physics world. Deep tails exert smaller
reaction impulses on rigid bodies. Large-angle motion and collision response
remain approximations; this release's comparison calibration targets springs
without colliders.

Moving inputs now interpolate at each physical substep. A scene read happens
once per update, stationary inputs retain native batching, and input history
preserves pending catch-up time. Pause/Resume excludes paused wall time; Reset
and manual Step restart the input history. Comparison JSON records the applied
interpolated boundary and its physical timestamp for both solvers.

Install this ZIP and restart Blender to replace loaded modules, then use
**Stop → Build → Play** on an existing YugoPhysics Solver. Existing connections
and Legacy Spring behavior are preserved.

## 0.9.5 — Keep shared smoothing consistent between animation branches

Motion Smooth and Expression Smooth now return the last filtered result on a
repeated timestamp or an untimed inspection. These reads preserve filter
velocity and never create filter history. Two independent Hz Trigger branches
can share one Smooth node and receive the same result. Physics Apply validation
also reads the filter without resetting it. Advancing steps keep the existing
smoothing formula, masks and pass-through behavior at zero smoothing.

The Spring comparison export records collider object parents, collections and
whether each object belongs to the current View Layer. A warning identifies
excluded collider sources, whose transforms Blender may stop updating. Keep
collider Empty objects in an enabled collection when disabling model geometry
for a simulation-only test. The recording budget is now 256 MiB, so the
159-bone dual-solver fixture can retain its default ten-second capture.
Recording still has measurement overhead; compare ordinary playback with
recording disabled.

## 0.9.4 — Legacy and MuJoCo Spring comparison recordings

Restore **Physics → Spring Simulation (Legacy)** with its previous VRM 0/1
algorithm, fixed 60 Hz stepping and neutral initialization unchanged. Both
this node and **YugoPhysics Solver** now expose **Simulation Data**, which can
connect to the existing **Data Viewer** to inspect the latest sample. Their
recording controls are built into the simulation nodes.

For a comparison, connect the same **VRM Data: Spring Data** to the Legacy node
and to **Spring to YugoPhysics → YugoPhysics Solver**. Drive the body's animation
through the existing Motion/Apply branch. Press **Record Both** on either solver:
it resets both simulation histories and starts them from one shared clock origin.
The clicked node supplies the duration and recording rate for both recordings,
with the rate limited by the slower solver. The defaults are **10 seconds** and
**60 Hz**. **Record** starts only that node; **Stop Record** ends its comparison
group. The Blender animation timeline is not automatically rewound or started.

Both solvers can record against the same armature without applying their Spring
outputs. Leave those Spring outputs disconnected from bone Apply nodes so neither
solver's pose becomes the other's driving input. Recording advances both solvers
without a Spring Apply branch and temporarily suppresses the official VRM Spring
animation when available. To preview both simulated results visually, use two
model copies with matching skeletons and identical body animation, and apply each
solver only to its own copy.

**Export Comparison JSON** exports the paired recordings together. Samples contain
simulation time, Blender frame, bone head/tail positions, solved local/world
rotations, velocity and collision boundaries. They capture the raw solved state
before Legacy's display interpolation. Positions use metres and quaternion
components use **w, x, y, z** order. Common tail and angular velocities are finite
differences between recorded samples; the first sample has `null` velocity.
Native-only diagnostics are included separately where available.

The JSON also records the actual engine version, original Spring settings,
converted MuJoCo settings, skeleton identity and timing. Both solvers interpolate
their driving boundaries between sampled poses. Legacy's presentation delay is
recorded explicitly so analysis can distinguish input timing from solver behavior.

Recording history lives in memory and is not stored in the `.blend` file. Export
before saving/reloading, Undo or addon shutdown; loading and Undo clear the history.
**Clear Recording** discards the selected comparison group. Recording and JSON
sampling add measurement overhead, so stop recording and disconnect Simulation
Data when measuring normal playback performance.

## 0.9.3 — Stable MuJoCo springs and less playback overhead

Correct VRM stiffness/gravity conversion at the reference 60 Hz and include
descendant loads in articulated spring support. Finite twist inertia, stable
spring feedback and stronger attachment rotation constraints prevent short
hair/sleeve chains from collapsing or exploding when applied directly. Native
bad-position/velocity/acceleration warnings now stop publication instead of
silently accepting MuJoCo's automatic reset.

Constant-force spring steps run in native batches. Rotation output avoids
unnecessary world-matrix conversions, boundary sampling skips a redundant
forward solve before stepping, and recovery markers/VRM animation switches are
written only when ownership changes. Solver status text refreshes at 2 Hz;
simulation and transform output keep their configured frequency. No rewiring
is needed. **Stop → Build → Play** rebuilds an existing world with the fix.

## 0.9.2 — Motion Delay

Add **Motion Operation → Motion Delay** between a motion source and its
downstream motion processing or Apply graph. **Delay Seconds** sets a live
delay from 0 to 10 seconds and accepts a linked Float. For example, 0.5 outputs
the motion sampled half a second earlier. Position and rotation interpolate
between samples; the armature root is delayed together with the bone channels.
The first pose is held while the buffer fills, and 0 seconds passes through.

Motion is sampled only when a downstream trigger pulls it with an advancing
timestamp. UI previews and repeated reads do not advance the buffer. **Reset**
clears the history; changing the source, disconnecting Motion Delay,
file loading and Undo also start with fresh history. This node uses the existing
Humanoid Motion socket and does not change the Blender timeline.

## 0.9.1 — Reuse a compatible loaded MuJoCo runtime

The loader now accepts CPU MuJoCo **3.3.7 or 3.5.0**. If Blender or another
extension has already imported either supported version, Yugo reuses that same
module and its existing allocation hooks. The bundled fallback remains 3.3.7.
The Yugo Physics panel reports the engine version actually used by each world.

The loader validates the required CPU APIs and verifies that the Python binding
version matches the native library before accepting it. Failed initialization
does not become a cached successful engine. It does not unload or replace a
live runtime belonging to another extension.

## 0.9.0 — Shared MuJoCo physics and one Mixer

The new **Physics** menu contains **Spring to YugoPhysics**, **Rigid Body to
YugoPhysics**, and **YugoPhysics Solver**. MuJoCo 3.3.7 CPU is bundled and is
the only engine offered by these nodes. This release targets Windows x64,
Blender 5.1 / 5.2, and uses Blender's existing NumPy. It does not require the
separate Ragdoll/PhysX addons or include humanoid models or controllers.

Select objects explicitly using **Objects List**, then connect it to **Rigid
Body to YugoPhysics**. The converter reads Blender's authored Active/Passive
settings: Active non-Animated bodies are dynamic; Passive and Animated bodies
follow the scene as kinematic collision boundaries. VRM **Spring Data** connects
to **Spring to YugoPhysics**. Both converters output **Physics Description**.
Use **Math / Mixer → Mixer**, Type **Physics Description**, to merge any number of inputs
into one world. Repeated identical entries are deduplicated; conflicting object
or bone bindings are rejected.

Connect the mixed description to **YugoPhysics Solver**. In the current 0.10.0
interface, a valid description starts the shared fixed-step world automatically.
Model changes update it automatically while retaining surviving simulation state.
Rigid bodies and Spring joints interact through native contacts in that same
world. Reset returns dynamic bodies and joints to their initial state and resumes
playing; Stop releases the world until Reset. Reading data never advances it.

To write objects, connect **Object Transforms → Output → Apply Transform** and
an external event such as **Hz Trigger → Enter**. Apply Transform is a generic sink for world-space object
position/rotation, preserving authored scale and handling object parents. It
validates the complete packet before writing, rejects locked channels, drivers,
constraints, unsupported delta transforms and competing owners, and can also
consume non-physics Object Transforms. For springs, connect **Bone Transforms**
to the existing **Bone Transforms to Armature**, and an external **Enter** event.
Official VRM Spring animation is suppressed only while its result is applied.

Explicitly applied dynamic objects temporarily leave Blender's native rigid
body simulation. Unselected objects keep their authored settings. Stop,
disconnection, disabled/muted nodes, file loading, Undo and addon shutdown
restore the owned transforms and rigid body flags. Saving stops physics first,
so temporary takeover flags cannot become the saved object's authored settings.
**Yugo Physics** panels in the Node Editor and View3D list every solver, its
state, model contents, elapsed time and applied object count.

**Mixer** replaces the separate Float, Vector, Humanoid Motion, Bone Transform,
Expressions, Bone Map and Expression Map mixer menu entries and adds Physics
Description and Object Transforms types. Switching Type preserves socket IDs
and existing wires. Connected inactive sockets remain visible and produce an
explicit error. Map/physics types merge; transform/motion/value types retain
their appropriate blending algorithms. Old mixer and Spring Simulation node
IDs remain loadable for existing files; their legacy solvers are not offered
in the new Add menu.

Initial physics support is Box, Sphere, Capsule and Cylinder rigid bodies and
VRM 0/1 Spring chains on one armature per world. VRM 1 uses a directly connected
linear joint list; its last listed joint is the endpoint, not another simulated
segment. VRM 0 can include descendant branches. Mesh/Convex Hull/Cone collisions,
Blender rigid body constraints, VRM Center compensation, Inside/Plane colliders,
negative/sheared physics scale and nonuniform Spring armature scale produce
explicit errors. Spring's dimensionless VRM stiffness/drag are converted to
MuJoCo torsional stiffness/damping using joint mass and segment length; the
result is native MuJoCo physics rather than identical VRM Verlet motion.
Rigid restitution and damping are translated to MuJoCo contact/drag parameters,
so their response is not numerically identical to Blender Bullet.

Different solver worlds do not share contact impulses. Blender object identity
is a binding between a description/result and its object; mutual collision
requires both bodies in the same MuJoCo world. No cross-engine bridge or
separate Physics-based Motion Processor is introduced in this release.

## 0.8.8 — Restore neutral Spring initialization

Spring Simulation now initializes its simulated bones from their neutral pose,
matching the early Yugu Node start/reset behavior. Reload, reconnect, and addon
restart cannot turn the previous simulation's bent hair/clothing output into a
new preferred pose. Animated external parents retain their current transforms.
The neutral reference is constructed without writing the Armature during a
data pull; the downstream Apply node remains the pose writer.

This restores the relevant early Spring behavior inside the current addon.
Humanoid position/rotation output, Ragdoll integration and other current node
features remain available. The 0.8.7 read/clock guards and fixed 60 Hz simulation
are retained. Historical Studio 0.6.0 and renamed Yugo Node 0.7.0 already shared
their Spring solver; the earlier `yugu_node` addon differed in start/reset and
timer policy. This release adopts its neutral initialization, rather than its
policy of dropping overdue simulation steps.

## 0.8.7 — Spring Simulation clock isolation

Spring Simulation advances only when a downstream trigger supplies an advancing
timestamp. Untimed graph reads and UI previews return the latest presentation
without creating a solver or changing its input samples, tail history or clock.
Duplicate and older timestamps also leave pending simulation time untouched;
the next advancing pull resumes catch-up. Nonfinite clock values are rejected.

The VRM 0/1 Spring force formulas, gravity, collider response and 60 Hz solver
calibration are unchanged. This fixes preview/trigger interference that could
advance hair and clothing against the wrong driving pose. Use Reload Spring to
discard an existing simulation history when retesting. Ragdoll remains a
separate addon and keeps its existing controls and physical parameters.

## 0.8.6 — Common Float and Vector nodes

This release implements the first 10 common numeric nodes from the local node
plan. Add **Custom Inputs → Float / Vector** for constants (including animated values).
Add **Math & Conversion → Float** for Math, Clamp, Map Range and Mix Float;
add **Math & Conversion → Vector** for Combine XYZ, Separate XYZ, Vector Math
and Mix Vector.

Math supports Add, Subtract, Multiply, Divide, Multiply Add, Minimum, Maximum
and Absolute. Vector Math supports Add, Subtract, Scale, Dot Product, Cross
Product, Length, Distance and Normalize. Dot Product, Length and Distance use
the Float **Value** output; the other operations use **Vector**. Switching an
operation preserves sockets and links; an inactive output returns zero of its
type. Float and Vector constants and their socket values have no 0–1 limit.

Motion Smooth / Weight / Mix, Expression Smooth / Weight / Mix, and Bone
Transform Mix now accept Float inputs for Smooth, Weight or Factor. Unlinked
inputs edit and evaluate the original saved RNA property, including its
animation and driver path. Linking overrides only the evaluated value;
disconnecting restores the original property. Most consumer parameters clamp
to 0–1; Expression Weight retains its original −5 to 5 range. Float Receiver
remains the bridge from computed values to native Blender drivers.

Divide by zero returns 0; normalizing a zero vector returns (0, 0, 0). Clamp
accepts reversed bounds. A zero-width Map Range source returns To Min; reversed
ranges are supported. Map Range and both Mix nodes clamp their interpolation
factor by default; disabling clamp allows extrapolation. Wrong input types,
nonfinite inputs and overflowing results raise evaluation errors.

Math nodes are pure pulls: they create no timers and do not write the scene.
Only downstream consumers such as Float Receiver perform their existing
actions. `yugo-common-math-demo.blend` demonstrates all 10 node types and
computed X/Y object drivers.

Compare, component-wise vector Multiply / Divide, advanced scalar functions,
rotation / space conversion, and numeric inputs for RagDoll remain in the
local plan for a later batch. The complete category reorganization and public
extension registry also remain planned.

## 0.8.5 — Keyword Match

**Add → Chat → Keyword Match** compares any Text input with a Keyword input.
Choose Contains (default), Equals, Starts With or Ends With. Case Sensitive is
off by default. Literal Unicode text is supported, including Chinese and
canonically equivalent accented characters; regular expressions are not used.
An empty Text or Keyword always returns false. Spaces remain meaningful.

Connect **YT Chat Event: End → Keyword Match: Enter** and **Text → Text**,
then enter a keyword such as `你好`. **Matched** triggers only for matching
messages; **Not Matched** triggers for the other messages. One Enter chooses
exactly one branch. **Result** is a Boolean value usable by If, Gate or State
Logic. The **Text / Keyword** data outputs retain that event's original inputs,
including across Wait and Action Flow, even after newer chat messages arrive or
the settings are edited. Each buffered YouTube message is judged independently.

Without Enter, reading Result performs a pure comparison of the current inputs
and emits no events. The node has no timer, scene update or object creation.
The YouTube example includes a `你好` Keyword Match → Chat Debug preview while
the existing Chat List continues displaying all messages. This release retains
the updated preset and Text Width 100 / User Name Width 10 defaults from 0.8.4.

## 0.8.4 — Updated Floating UI preset and user name width

New Chat Lists use **User Name Width = 10**. Text Width remains 100; explicitly
saved custom widths are preserved. The bundled `preset.blend` is the user's
updated Floating UI asset. Import Floating UI uses this new preset for fresh
imports; previously imported, editable templates remain in the scene.

## 0.8.3 — Import Floating UI, avatars and font measurement

Install `yugo_node-0.8.3.zip`, then add **Chat → Chat List** and press
**Import Floating UI**. The complete bundled Floating UI collection is appended
into the current scene. Style/Parent bind to its actual UI_Style/UI_Transform,
and Text, User Name, Panel, Avatar Object and Avatar Material bind to
ChatText/UserName/ChatPanel/UserIcon/UserPanel. Repeating import on the same node
reuses its asset, including after renaming or reopening the file. Existing
same-named scene assets are retained. Unlink Style and Parent before importing.

**Hide Style**, enabled by default, changes only the effective Style collection's
viewport/render visibility. Generated messages live under UI_3D in a separate
collection and remain visible. Turn Hide Style off to edit the template, then
use Refresh Style or leave Sync Style enabled to apply changes.

New Chat Lists use **Text Width = 100**, in Blender's native text-frame width
units. This is a width, not a percentage. Zero retains the template's text box;
explicitly saved custom widths are preserved. Nodes without a saved width use
the new default. Name Width remains independent.
Long CJK, URLs and emoji are measured with the actual font before wrapping.

**Add → Data → Image From URL** has URL input and Image output. For YouTube
avatars, connect **YT Chat Event: Avatar URL → Image From URL: URL**, then
**Image → Chat List: Avatar**. Keep the usual End/Text/Author Name connections
to Enter/Text/User Name. Avatar also accepts a native Blender Image directly.

Each message retains its own avatar input and receives an independent copy of
the selected UserPanel material. Other messages and the template keep their
images. Downloads run asynchronously; a pending or failed image keeps the
template avatar. Completed images update the corresponding message on Blender's
main thread, are cached by URL and packed into the .blend on save. Image From URL
shows status and offers Retry. Enable Blender's online access for downloads.
The bundled default avatar is also loaded and packed when importing the style.
YouTube supplies the author image through
[profileImageUrl](https://developers.google.com/youtube/v3/live/docs/liveChatMessages#authorDetails.profileImageUrl);
saved images use Blender's
[packed data](https://docs.blender.org/manual/en/latest/files/blend/packed_data.html).

The existing 60 Hz scheduler updates scrolling motion. Idle messages retain
their clones and skip repeated wrapping/font measurement; template checks remain
limited to twice per second. Native text measurement uses a temporary isolated
font scene rather than reevaluating the character scene for each width probe.
Scrolling still changes only Layout Empty position, leaving Message Empty
animation intact. Scene complexity continues to affect actual frame rate.

Blender 5.1.2 / 5.2.0 checks cover imports, name collisions, saved-file recovery,
independent asynchronous avatars, image error/retry/cancellation, idle updates
and isolated font geometry. Image transport tests use fixtures. Live YouTube
access requires a valid API key and an active broadcast; this release's checks
do not claim a live YouTube session.

## 0.8.2 — Floating Chat List

Continue from 0.8.1 with **Add → Chat → Chat List**. Each Enter copies the
selected Style collection, writes Text and User Name, and emits End once the
message is ready. Connect YT Chat Event End/Text/Author Name to Chat List
Enter/Text/User Name. Any other source can provide the same text inputs.

Style defaults to `UI_Style`; Parent defaults to `UI_Transform`. Select the
template's text objects under Settings if they have different names. Object
and Collection nodes under Add → Data can also feed the Parent and Style inputs.
The complete collection, including nested collections and extra decorations, is
copied. Internal parents, modifier/constraint targets and object driver targets
are remapped to the copies. Every FONT has separate text data; mesh geometry and
materials stay shared with the template.

Each message has a Layout Empty below Parent, then a Message Empty of display
size **0.05**, with scale 1. Place native object animation on the Message Empty.
The list moves only the Layout Empty, preserving the message's own position,
rotation and scale. Message Empty output identifies the message created by that
specific event, including after Wait or Action Flow. An evicted message's
reference resolves as unavailable instead of switching to a newer message.

Settings include Max Messages (default 5), Gap, Scroll Seconds (default 0.25),
Direction, Text Width, Name Width, Fit Panel and Sync Style. The list grows toward local -Y by default.
On overflow, the oldest message is removed and surviving messages slide toward
the first slot. Bounds include all cloned geometry, text and names, including
wrapped/multiline text. Long unspaced CJK and URLs receive measured line breaks
using the actual font; the original input is retained on the Layout Empty. Text
Width or Name Width 0 preserves the corresponding template text box. Fit Panel
grows the selected Panel Object downward while retaining its top edge. Disable
Fit Panel to retain a custom fixed background shape.

With Sync Style enabled, template object layout, text/font settings and copied
decorations refresh within 0.5 seconds while each message retains its content.
Shared mesh/material edits apply directly. Refresh Style applies the template
immediately. This does not overwrite the animation Empty. Clear or Reset removes
this node's generated messages; template objects, Parent, user-added objects and
externally used text data are retained. Generated messages survive save/load,
Undo and disabling the add-on. A copied Chat List starts its own independent list.

**Test Message** creates a sample without a YouTube connection, even when the
text inputs are linked, and sends End to any connected actions. The distributed
`yugo_node-0.8.2-example.blend` includes three sample rows and an ALL-event Chat
List branch alongside the existing message/payment/membership previews. Its
editable UI_Style collection is hidden in the example's viewport/render; unhide
it to edit the template. The user's original `preset.blend` is unchanged.

Chat List and the earlier chat/float/workflow/bone-transform checks pass in
Blender 5.1.2 and 5.2.0. Live YouTube access still needs a valid API key and an
active broadcast. Avatar images remain the objects/materials in the template;
this version does not download profile images.

## 0.8.1 — Connected Hips and root position

Install `yugo_node-0.8.1.zip` alongside the existing `yugo_ragdoll-0.4.0.zip`.
Humanoid Motion already carries bone position and rotation. Blender ignores a
connected bone's own location, so Bone Transforms to Armature now permanently
disconnects the mapped Hips when applying its transform. Its parent and rest
head/tail stay intact; disabling Apply restores the original pose channels but
leaves Hips disconnected. Bone Map identifies Hips, including custom bone names,
and Bone Transform Mix preserves this mapping.

Other connected-bone positions are forwarded through their movable ancestor.
Nonzero connected-bone positions that ask the same ancestor to move in different
directions are incompatible with that bone connection. Apply reports the conflict
before writing any channel; independent positioning requires a movable joint.

Motion to Bone Transform also retains the Armature root position and rotation.
Bone Transform Mix blends these sparse channels, and Apply owns/restores them
independently of the pose-bone channels. A root position is an Armature object's
local location, matching the existing Action/VMC root convention; a bone position
remains its rest-relative pose location. Missing channels do not claim ownership.

Existing node graphs, mappings and saved ragdoll controls remain compatible.
Keep Hz Trigger connected to the Apply node's Enter input so edits reach the
character.

Blender 5.1.2 / 5.2.0 checks include the actual saved avatar (362 Hips descendants
and 155 sampled visible mesh vertices), repeated 60 Hz pulls, a separate target
armature, native sphere dragging, Undo/cancel, and installed ZIP extension loading.
The first Apply preserves selection, active object, pose channels and the user's
mode while disconnecting Hips; the saved source avatar file is not modified by
these tests.

## 0.8.0 — Bone Transform, continuing 0.7.1

Install `yugo_node-0.8.0.zip`. This release continues the 0.7.1 codebase and retains
its YouTube Chat, Connect inputs, Float Receiver and Floating UI features.
Use `yugo_ragdoll-0.3.0.zip` alongside it for viewport ragdoll controls.

The bone pipeline now carries **position and rotation** as native data:

**Humanoid Motion + Bone Map → Motion to Bone Transform → Bone Transform Mix
(optional) → Bone Transforms to Armature**. Connect Hz Trigger to Apply's Enter.
Spring Simulation also outputs Bone Transform, with only its simulated rotation
channel present.

| Previous node/socket | Current name |
| --- | --- |
| Motion to Bone Rotation | Motion to Bone Transform |
| Bone Rotation Mix | Bone Transform Mix |
| BoneRotations to Armature | Bone Transforms to Armature |
| Bone Rotation socket | Bone Transform socket |

Position is the rest-relative local pose translation used by Blender's
`PoseBone.location`; it is not the bone head's absolute world coordinate.
Rotation is a local pose quaternion. Missing channels remain absent: an explicit
zero position resets a controlled position to zero, while an absent position
leaves it outside that source's control. Scale is not part of this packet.

Conversion preserves every mapped position provided by Humanoid Motion, including
hips. Mix interpolates position linearly and rotation with quaternion SLERP, and
preserves Base channels that Overlay does not provide. Data conversion and mixing
do not write to the character. Apply validates all targets, values and ownership
before writing, and captures/restores position and rotation independently when
output stops, is disabled, muted or disconnected.

Existing saved graphs migrate their socket and default node names in place,
retaining connections, settings and custom names/labels. Serialized Blender type
IDs and legacy Python rotation aliases remain available for compatibility; newly
created nodes and the Add menu use Bone Transform names. Save the graph after
updating to retain the new display names.

Blender 5.1 / 5.2 checks cover native Transform conversion/Mix/Apply, independent
channel ownership and restoration, legacy saved graph migration, and actual
viewport Hips dragging through the 60 Hz Apply graph. The 0.7.1 chat Connect and
Float Receiver regressions and packaged preset checks also pass. The historical
monolithic integration test stops at a retired Blender Output activation
assertion; the unchanged released 0.7.1 zip fails at that same assertion.

## 0.7.1 — Connect inputs, chat Trigger and Float Receiver

Install `yugo_node-0.7.1.zip`. Open `yugo_node-0.7.1-example.blend` or use
F3 → **Create YouTube Chat Example**. The example retains the current preset's
Floating UI collection and includes a Super Chat Amount → Float Receiver branch.

**YouTube Chat** now has **Live URL**, **API Key** and **Connect** inputs.
An unlinked Live URL can be entered below its socket; a linked value overrides
the manual setting. An unlinked API Key uses the masked local field, then the
default key from extension preferences if the local field is blank. A linked
key is used exactly as supplied, including an empty value. Local key fields are
not saved to the .blend; linked values follow their source node's persistence.

**Connect** accepts an event or the rising edge of a numeric value. The Connect
button remains available. An unchanged active connection is reused, so a held
value or repeated events do not repeatedly restart the network worker. Terminal
errors are not retried by repeated Connect events; the button can retry them.

**Trigger** emits when new messages are buffered. Connect **Trigger → YT Chat
Event: Enter**, and **Chat → Chat**. The internal existing 60 Hz scheduler
delivers these notifications on Blender's main thread; it does not emit a 60 Hz
stream while idle. Bounded batches continue on subsequent ticks while the
connected readers make progress and still have unread messages. Blocked routes
do not cause permanent repeating Trigger events. External Hz Trigger polling
remains supported.

YT Chat Event no longer has an **Event Type** output. **Amount**, **Months** and
**Gift Count** are unrestricted gray Float sockets. Updating existing saved
graphs preserves their numeric wires.

**Add → Data → Float Receiver** works with numeric data throughout the graph.
It has **Value** and optional **Enter** inputs and **Value / End** outputs.

- Connect only Value to sample continuously at 60 Hz. With Enter connected,
  reception happens only on Enter and End follows each successful reception.
- **Received Value** is a saved, animatable Blender property. Each event's output
  retains its own value across waiting/parallel actions; the visible property
  shows the most recently received value.
- Right-click **Received Value → Copy as New Driver**, then right-click a target
  numeric Blender property → **Paste Driver**.
- Right-click the node → **Copy Driver Data Path** copies a path such as
  `nodes["Float Receiver"].value`. For a driver variable of type Single
  Property, choose the NodeTree containing the receiver as its ID and paste
  that data path. **Copy Full Data Path** is also available.

The driver feature was tested against real object location drivers, negative
values and values above 1, renaming, concurrent delayed events, saved files and
Blender 5.1 / 5.2. Live YouTube calls still need a real key and active room.

## 0.7.0 — YouTube live chat

Install `yugo_node-0.7.0.zip` through Blender's **Install from Disk**.
This release continues Yugu Node Studio 0.6.0. The visible name is **Yugo Node**;
the extension ID and existing node/socket IDs stay compatible with saved graphs.

**Add → Chat** includes **YouTube Chat**, **YT Chat Event**, and **Chat Debug**.
F3 → **Create YouTube Chat Example** creates three ready-wired branches, or open
`yugo_node-0.7.0-example.blend` after installation. The example and bundled preset
preserve the current local `preset.blend`, including its `floating ui` collection.

1. Create a Google Cloud API key with **YouTube Data API v3** enabled.
2. Set the key using **API Key** on YouTube Chat, or in the extension preferences.
   The key is saved in Blender preferences, never in the node graph or `.blend`.
3. Enter a currently live video's URL or video ID and press **Connect**.
4. Connect **YouTube Chat: Chat → YT Chat Event: Chat**.
5. Connect **Hz Trigger (60): Trigger → YT Chat Event: Enter**.
6. Select an Event mode. Connect **End → Chat Debug: Enter**, **Text → Text**,
   and **Author Name → Author Name** to preview received messages in the node.

The connection source has only a Chat data output. It does not emit events.
Only **Enter** reads queued messages. **End** fires once for each new matching
message, and never fires for an empty read. Outputs on the Event node include
Text, Author Name/ID, Message ID, Time, Avatar URL, Event Type and relevant
amount/currency or membership fields. Unused paid/member fields are hidden by mode.

Modes: **Message**, **Super Chat**, **Super Sticker**, **New Member / Upgrade**,
**Member Milestone**, **Membership Gift**, **Gift Membership Received**, and
**All Supported Events**. Ordinary free channel subscriptions are not exposed
as events by YouTube's live chat API; paid membership joins are separate.

The 60 Hz rate is Blender's local pull rate. A background receiver uses the
official REST `liveChatMessages.list` API, follows `pollingIntervalMillis`, and
keeps its continuation token through transient reconnections. It does not send
60 requests per second. Reception latency depends on YouTube's update interval;
API quota applies. No external Python packages are required.

Recent history is skipped on initial connection by default. Enable **Include
Recent History** before Connect to process it. Message IDs are deduplicated.
Each Event node has an independent read cursor, so multiple modes or multiple
listeners do not consume each other's messages. Each Enter examines up to
**Max Messages / Enter** buffered items (default 64); remaining items are read
on later Enter pulses. The buffer holds 2048 items; an overrun is reported as
missed messages, rather than silently losing them.

Each emitted event contains an immutable message snapshot. Place YT Chat Event
in the main graph, with **End → Action Flow: Enter** when calling reusable flows.
YT Chat Event inside an Action Flow definition, Repeat body or Parallel branch
is rejected with a placement message. Wait Action and parallel Action Flow calls
preserve their own Text/Author outputs. Use Action Flow's **Parallel** policy when
every chat event should run; Ignore/Restart intentionally ignore or cancel
overlapping calls. Disconnecting the source stops new reception while
already-triggered effects can finish their snapshots. Muting/deleting an active
Event node cancels its pending work. Mode changes and switching an existing
listener to another source skip preexisting buffered items. Loading, undo and
extension shutdown stop chat workers and clear runtime state; press Connect again.

Reference: [YouTube chat messages](https://developers.google.com/youtube/v3/live/docs/liveChatMessages),
[REST reception](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/list).

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

**Add → FlowControl** includes:

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

Spring Simulation outputs its full simulated Bone Transform and no longer has a
Weight control. Connect **Bone Transform Mix** downstream to control its influence.
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

VRM Data and Bone Transforms to Armature now show their Armature picker beneath
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
  and sorted first. Bone Transform sockets are gray.
- Bone Transform Mix now uses only Base, Overlay and Factor. Old Mask / Bone Map
  sockets and their links are removed during migration; source nodes remain.

## 0.4.1 — Bone Transform Mix

Add **Humanoid Motion → Bone Transform Mix** between rotation sources and
**Bone Transforms to Armature**. Inputs: **Base**, **Overlay**, optional
**Mask** and **Bone Map**; output: **Bone Transform**. Factor 0–1 uses quaternion
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
- **Composer** has its own character list. Add armatures with **+** and remove
  them with **−**. Enable each character's checkbox or **Enable YugoComp** to
  include its meshes in the Cryptomatte. Enabling initializes Main FX on first
  use and rebuilds missing compositor data while preserving saved effect values.
  In the 3D View sidebar, **Show in Viewport** switches that viewport to Rendered
  shading and sets Compositor to **Always**, showing Main FX outside camera view.
- **Main FX** provides Scene Color Blend, Ambient Occlusion, Rim Light,
  Separated Rim, Secondary Shadow, Separated Shadows, Gradient Color, Dark
  Bloom, and Light Bloom. All enabled characters share these scene settings.

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
2. Connect **Humanoid Motion** and **Bone Map** to **Motion to Bone Transform**.
   Motion Type currently offers VRM Humanoid; future formats can be added here.
3. Connect its **Bone Transform** to **Bone Transforms to Armature**.
   The packet carries the target armature from Bone Map. Only rotations are
   written: root-object motion, bone locations, and scales are not applied.
4. Connect expressions and **Expression Map** to **VRM Expression to Shape Keys
   Values**, then its output to **ShapeKeysValues to Objects**. Native
   mesh/key targets come from the map. Binary and VRM expression overrides are
   resolved before morph weights are accumulated. Material binds are not part
   of this shape-key-only route.
5. Connect **Hz Trigger → Enter** on Bone Transforms to Armature, or
   **Hz Trigger → Trigger** on ShapeKeysValues to Objects, choose **8 / 12 / 24 /
   30 / 60 Hz**. Hz Trigger lives in **Add → FlowControl**, has no Active
   checkbox, and runs automatically when connected to an enabled Apply. A clock can drive
   multiple Apply nodes; separate clocks can drive different branches.
6. For physics: **VRM Data Spring Data → Spring Simulation → Bone Transform →
   another Bone Transforms to Armature**. Connect a clock to that Apply.
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

- **Bone Transforms to Armature** has an inline Armature selector and an
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
- New **Custom Inputs → Armature** and **Custom Inputs → Objects** value nodes share these target
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
port remain hidden, with defaults of `0.0.0.0` and `49986`. The
**Mirror** option mirrors head yaw and roll, matching the original Yugo motion
layer behavior.

## Standardized motion

The `VRM Humanoid Motion` socket carries canonical names such as `hips`,
`head`, `leftUpperArm`, and `rightIndexDistal`. It never carries target-specific
Blender bone names. Blender Actions are converted through the source Bone Map;
VMC and iFacialMocap are converted from Unity coordinates before entering the
graph. **Motion to Bone Transform** resolves Blender-local rotations through the
cached Bone Map. **VRM Expression to Shape Keys Values** resolves native morph
targets through Expression Map. Only the corresponding triggered Apply nodes
write to Blender; Spring is a pull-driven Bone Transform source.
