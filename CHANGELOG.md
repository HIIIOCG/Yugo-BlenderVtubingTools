# Changelog

All notable changes to Yugo are documented here. Version numbers follow
[Semantic Versioning](https://semver.org/).

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

[0.1.26]: ../../releases/tag/v0.1.26
[0.1.14]: ../../releases/tag/v0.1.14
