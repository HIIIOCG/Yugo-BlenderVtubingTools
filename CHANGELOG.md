# Changelog

All notable changes to Yugo are documented here. Version numbers follow
[Semantic Versioning](https://semver.org/).

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

[0.1.14]: ../../releases/tag/v0.1.14
