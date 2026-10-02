"""Smoke the actual ZIP in an isolated import path, with no source fallback."""

from pathlib import Path
import sys
import zipfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
archive = ROOT / "artifacts/yugo_physics_lab-0.1.0.zip"
destination = ROOT / "artifacts/package-check/yugo_physics_lab"
destination.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    for member in bundle.infolist():
        path = (destination / member.filename).resolve()
        assert path.is_relative_to(destination.resolve()), "Unsafe archive path"
        assert "__pycache__" not in member.filename and not member.filename.endswith(".pyc")
    bundle.extractall(destination)
sys.path.insert(0, str(destination.parent))
import yugo_physics_lab as addon
from yugo_physics_lab import demo, preview, service
assert Path(addon.__file__).resolve().is_relative_to(destination)
addon.register()
demo.create_demo(bpy.context.scene)
scene = bpy.context.scene
scene.frame_set(42)
target = scene.yugo_physics_lab.target
initial = target.matrix_world.translation.z
preview.start_preview(scene, scene.yugo_physics_lab)
for _ in range(60):
    service.update(1 / 60)
assert service.active(), service.status()
assert target.matrix_world.translation.z < initial - .2
assert scene.frame_current == 42
preview.stop_preview(clear=True)
addon.unregister()
assert not bpy.app.timers.is_registered(preview._timer)
print("YUGO_PHYSICS_LAB_PACKAGE_OK", addon.__file__)
