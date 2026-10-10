# Changelog

All notable changes to Yugo are documented here. Version numbers follow
[Semantic Versioning](https://semver.org/).

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

[0.2.0]: https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.0
[0.1.26]: ../../releases/tag/v0.1.26
[0.1.14]: ../../releases/tag/v0.1.14
