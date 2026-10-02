"""Run in a dedicated hidden Blender GUI to exercise the real app timer."""

import json
from pathlib import Path
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
import yugo_physics_lab as addon
from yugo_physics_lab import demo, preview, service

folder = ROOT / "artifacts"
folder.mkdir(exist_ok=True)
REPORT = folder / "live-timer-report.json"
addon.register()
demo.create_demo(bpy.context.scene)
scene = bpy.context.scene
scene.frame_set(42, subframe=.25)
target = scene.yugo_physics_lab.target
initial = tuple(target.matrix_world.translation)
ticks = []
original_update = service.update


def counted_update(dt):
    ticks.append(dt)
    return original_update(dt)


service.update = counted_update
preview.start_preview(scene, scene.yugo_physics_lab)


def verify():
    result = {"blender": bpy.app.version_string, "source": addon.__file__, "ticks": len(ticks)}
    try:
        assert len(ticks) >= 10, f"Only {len(ticks)} application timer updates"
        assert service.active(), service.status()
        assert scene.frame_current == 42 and abs(scene.frame_subframe - .25) < 1e-6
        assert target.matrix_world.translation.z < initial[2] - .2
        assert target.matrix_world.translation.z >= .279
        result.update({"frame": scene.frame_current, "subframe": scene.frame_subframe,
                       "initial_position": initial, "final_position": tuple(target.matrix_world.translation)})
        preview.stop_preview(clear=True)
        assert not bpy.app.timers.is_registered(preview._timer)
        assert not service.active()
        addon.unregister()
        result["passed"] = True
    except Exception:
        result.update({"passed": False, "error": traceback.format_exc()})
        preview.stop_preview(clear=True)
    REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify, first_interval=1.5)
