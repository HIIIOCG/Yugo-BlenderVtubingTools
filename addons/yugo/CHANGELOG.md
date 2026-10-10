# Changelog

All notable changes to Yugo are documented here. Version numbers follow
[Semantic Versioning](https://semver.org/).

## [0.2.3] - 2026-10-10

### macOS and Linux installation

- Yugo now includes official CPU MuJoCo and GLFW dependencies for Windows x64,
  Linux x64, and macOS Apple Silicon, with Blender 5.1 and 5.2.
- Source loading selects dependencies for the current OS and architecture, so
  native files from other platforms cannot overwrite the active runtime.
- Release publication requires native installation, restart, Node editor, and
  CPU physics checks for the same ZIP on each supported platform.
- Update older Yugo installations through Install from Disk, then restart and
  verify that the enabled Yugo entry shows 0.2.3.

The separate ProtoMotions / PhysX Tracker 0.4.0 archive remains Windows x64.
Its native runtime is independent of Yugo's built-in MuJoCo physics. Intel Macs
require separate Blender 4.5 compatibility work because Blender 5.1 and 5.2
support Apple Silicon on macOS.

## [0.2.2] - 2026-10-10

### Getting started fixes

- **Fast Started** now creates a connected Yugo Node graph for the selected
  character and supported VMC / iFacialMocap tracking sources. VMC face and
  body share one receiver. The setup opens the editor and shows the next steps.
- Added **Start Outputs** and **Pause Outputs** controls. A new starter begins
  with receivers disconnected and outputs paused; set your connection details,
  connect the receiver, then start outputs.
- **Help** now opens the official [Yugo Handbook](https://hiiiodigital.com/Yugo-Docs/).
- **Open Yugo Node Editor** is at the top of the Yugo sidebar, including when
  the Avatar List is empty. The first editor opening creates a visible example;
  opening an existing graph keeps its content and synchronizes its selection.
- Simplified installation instructions: install and enable **Yugo**, which
  already includes Yugo Node. Only users who previously installed a separate
  Yugo Node or Yugu Node Studio need to disable that older entry.
- Updated the handbook with the exact button locations and a guided first setup.

### Downloads

- Install `yugo-0.2.2.zip` through **Preferences > Get Extensions > Install from Disk**.
- The optional ProtoMotions / **PhysX Tracker 0.4.0** is also supplied as the
  separate `yugo_physx_tracker-0.4.0.zip` (about 242 MB), with its SHA-256 checksum
  and build provenance. Install it alongside Yugo when needed.

## [0.2.1] - 2026-10-10

### Documentation and interface fixes

- README links now open the official [Yugo Handbook](https://hiiiodigital.com/Yugo-Docs/).
- Getting Started explains installation, creating a graph, choosing a VRM
  character, and connecting a first VMC tracking input.
- Corrected **Compositing** in the handbook and Blender panel. Existing graph,
  operator, and saved-property identifiers remain compatible.

### Physics add-on update

- Replaced the standalone **Yugo Physics Lab** with the ProtoMotions-based
  **PhysX Tracker 0.4.0**, including the CPU PhysX runtime and learned ONNX policy.
- The physics add-on is a separate install: download
  `yugo_physx_tracker-0.4.0.zip` (about 242 MB) and install it alongside Yugo.
- Native tracking, worker controls, character output, registration, and saved
  file lifecycle are qualified on Blender 5.1.2 and 5.2.0 LTS.
- Existing Physics Lab scenes require manual setup in the new add-on. After a
  Yugo host reload, disable and re-enable PhysX Tracker; after loading a file,
  start its worker manually when needed.

### Downloads

- `yugo-0.2.1.zip`: Yugo with the integrated Node 0.10.0 runtime.
- `yugo_physx_tracker-0.4.0.zip`: optional independent physics add-on.
- Both packages target Windows x64 and Blender 5.1/5.2, and include SHA-256
  checksums and source/build provenance reports. Install the ZIPs through
  **Preferences > Get Extensions > Install from Disk**.
- Yugo Node Studio is a separate package and is not versioned by this release.

## [0.2.0] - 2026-10-10

### Added

- The complete current DEV Yugo Node 0.10.0 runtime in the Yugo extension:
  motion and expression sources, graph processing, values and math, workflow
  controls, VRM Spring physics, and MuJoCo object physics
- One **Yugo** sidebar tab for the original controls and Node Editor controls,
  with a graph launcher in the 3D View
- Bundled Windows x64 physics dependencies and their original license notices

### Improved

- Node Composer, MToon Optimize and Outline share the original Yugo avatar
  list, Scene settings and `preset.blend`; the legacy Comp and preset are retained
- Existing DEV visual ownership tags remain readable without duplicating
  shaders, Shadow Mix, compositor generations or Viewer nodes
- Registration failure rolls back installed resources; repeated enable,
  disable and reload preserve ownership and clean up handlers and timers
- Existing Node graph, socket, node and operator identifiers are preserved;
  DEV's retired nodes remain inactive compatibility declarations

### Installation

- Windows x64; validated with Blender 5.1.2 and 5.2.0 LTS
- Install `yugo-0.2.0.zip` through **Preferences > Get Extensions > Install from Disk**
- Disable separately installed Yugo Node or Yugu Node Studio before enabling
  this package, because they share Node identifiers
- The published ZIP has a SHA-256 file and a source/build provenance report

## [0.1.26] - 2026-09-05

### Added

- Integrated Cam Manager with follow presets, weighted targets, damped motion,
  camera keybinds, and aspect-aware framing
- Scene-level MainFX compositor controls with shared Cryptomatte targeting
- Mesh optimization, MToon outline baking, and avatar collection arrangement
- Shared 60 Hz motion and 2 Hz system/UI schedulers

### Improved

- Safe runtime suspension and cleanup while loading a different Blender file
- Hidden VMC armatures remain valid targets without changing their visibility
- Spring Bone interpolation and low-rate solving avoid redundant scene updates
- MToon, compositor, and outline tools now share one bundled `preset.blend`

## [0.1.14] - 2026-08-29

### Added

- Layered motion mixing for multiple VRM avatars
- VMC/OSC and iFacialMocap live motion sources
- Blender Action Slot sampling and keyboard expression controls
- VRM 0.x and VRM 1.0 Spring Bone motion layers
- Humanoid and blend-shape masks, Solo, Replace, and Layer Weight controls
- MToon Eevee preview optimization and a clean presentation viewport

### Improved

- Stable runtime layer IDs across reordering, removal, Undo, and Redo
- Final post-mix Spring Bone evaluation with cached low-rate solvers and
  interpolated 60 Hz display output
- Network access now follows Blender's Allow Online Access preference

[0.2.1]: https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.1
[0.2.0]: https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.0
[0.1.26]: ../../releases/tag/v0.1.26
[0.1.14]: ../../releases/tag/v0.1.14
