"""Self-closing GUI timer test; use only a dedicated hidden Blender process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_demo import load_source_package, make_character, make_graph


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts" / "live-timer-report.json")
    args = parser.parse_args(argv)
    assert not bpy.app.background, "Run this test in a separate GUI process"
    addon = load_source_package()
    from yugo_ragdoll import preview
    armature = make_character("Live Timer Character")
    tree, source, ragdoll, apply = make_graph(armature)
    apply.preview_seconds = 0.7
    ragdoll.auto_recover = False
    bpy.context.scene.frame_set(42, subframe=0.25)
    saved = {bone.name: bone.matrix_basis.copy() for bone in armature.pose.bones}
    ticks = []
    movements = []
    original_step = preview.step
    def recorded_step(tree_arg, apply_arg, dt):
        result = original_step(tree_arg, apply_arg, dt)
        ticks.append(dt)
        movements.append(max(abs(saved[bone.name][r][c] - bone.matrix_basis[r][c])
                             for bone in armature.pose.bones for r in range(4) for c in range(4)))
        return result
    preview.step = recorded_step
    preview.play(tree, apply)
    preview.hit(tree, ragdoll)
    started = time.monotonic()
    def finish():
        if time.monotonic() - started < 1.2:
            return 0.05
        report = {"status": "passed", "blender": bpy.app.version_string, "ticks": len(ticks),
                  "elapsed": time.monotonic() - started, "frame": bpy.context.scene.frame_current,
                  "subframe": bpy.context.scene.frame_subframe}
        try:
            assert len(ticks) >= 3, "Real app timer did not advance enough simulation steps"
            assert all(dt >= 0 for dt in ticks)
            assert any(delta > 1e-4 for delta in movements), "App timer never moved the rig"
            assert not bpy.app.timers.is_registered(preview._timer), "Finite preview timer remained active"
            assert bpy.context.scene.frame_current == 42
            assert abs(bpy.context.scene.frame_subframe - 0.25) < 1e-6
            preview.stop(tree, apply)
            assert max(abs(saved[bone.name][r][c] - bone.matrix_basis[r][c])
                       for bone in armature.pose.bones for r in range(4) for c in range(4)) < 1e-6
            report["restored"] = True
        except Exception:
            report["status"] = "failed"
            report["error"] = traceback.format_exc()
        finally:
            preview.step = original_step
            addon.unregister()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("RAGDOLL_LIVE_TIMER", json.dumps(report))
            bpy.ops.wm.quit_blender()
        return None
    bpy.app.timers.register(finish, first_interval=0.1, persistent=False)


if __name__ == "__main__":
    main()
