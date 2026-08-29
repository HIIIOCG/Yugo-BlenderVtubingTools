# Contributing

Contributions and focused bug reports are welcome.

## Development setup

1. Use Blender 5.1 or newer.
2. Clone the repository outside Blender's managed extension directories.
3. Link or copy the repository into a local Blender extension repository for
   development.
4. Run `python scripts/check_version.py` before committing.
5. Run `scripts/build.ps1` on Windows, or use Blender's `extension validate`
   and `extension build` commands directly.

Keep pull requests limited to one feature or fix. Include the Blender version,
operating system, reproduction steps, and relevant console output for bugs.
Never commit generated ZIP archives, Blender recovery files, or Python caches.

By contributing, you agree that your contribution is licensed under
GPL-3.0-or-later.
