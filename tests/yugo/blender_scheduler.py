"""Validate Yugo's single 60 Hz motion and 2 Hz system schedulers."""

from pathlib import Path
import sys

import bpy


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_yugo

load_yugo()

import yugo
from yugo import motion_mixer, mtoon_optimize, scheduler, view_window
from yugo.vmc import runtime as vmc_runtime


def main():
    yugo.register()
    receiver = None
    try:
        assert abs(scheduler.MOTION_INTERVAL - (1.0 / 60.0)) < 1.0e-9
        assert abs(scheduler.SYSTEM_INTERVAL - 0.5) < 1.0e-9
        assert bpy.app.timers.is_registered(scheduler._motion_timer)
        assert bpy.app.timers.is_registered(scheduler._system_timer)

        # Former independent periodic mechanisms must stay disconnected.
        assert not bpy.app.timers.is_registered(motion_mixer._apply_timer)
        assert not bpy.app.timers.is_registered(view_window._update_view_windows)
        assert (
            mtoon_optimize._sync_installed_materials
            not in bpy.app.handlers.depsgraph_update_post
        )
        receiver = vmc_runtime._Receiver("127.0.0.1", 0)
        assert not bpy.app.timers.is_registered(receiver.timer)

        assert set(scheduler._motion_callbacks) >= {
            "vmc",
            "ifacialmocap",
            "keybinds",
            "motion_mixer",
            "camera",
        }
        assert set(scheduler._system_callbacks) >= {
            "mtoon",
            "view_window",
            "camera",
        }

        # Blender clears driver_namespace while loading some .blend files.
        # Persistent timers must continue because their lifetime is now based
        # on module-local identity instead of that volatile namespace.
        bpy.app.driver_namespace.pop(scheduler.GENERATION_KEY, None)
        assert scheduler._motion_timer() is not None
        assert scheduler._system_timer() is not None

        # Spring's post-mix registration must also survive the namespace entry
        # disappearing during a file lifecycle transition.
        post_mix_ticks = []
        callbacks = bpy.app.driver_namespace[motion_mixer.POST_APPLY_CALLBACKS_KEY]
        callbacks["test_post_mix"] = lambda _context, _delta: post_mix_ticks.append(1)
        bpy.app.driver_namespace.pop(motion_mixer.POST_APPLY_CALLBACKS_KEY, None)
        motion_mixer._apply_timer()
        assert post_mix_ticks == [1]
        motion_mixer._post_apply_registry.pop("test_post_mix", None)

        order = []
        scheduler.add_motion(
            "test_late",
            lambda _context, _delta: order.append("late"),
            priority=902,
        )
        scheduler.add_motion(
            "test_early",
            lambda _context, _delta: order.append("early"),
            priority=901,
        )
        scheduler.run_motion_once()
        assert order == ["early", "late"]
        scheduler.remove_motion("test_early")
        scheduler.remove_motion("test_late")

        system_ticks = []
        scheduler.add_system(
            "test_system",
            lambda _context, delta: system_ticks.append(delta),
            priority=999,
        )
        scheduler.run_system_once()
        assert system_ticks == [scheduler.SYSTEM_INTERVAL]
        scheduler.remove_system("test_system")

        retry_count = []

        def transient_callback(_context, _delta):
            retry_count.append(1)
            if len(retry_count) == 1:
                raise ReferenceError("temporary RNA rebuild")

        scheduler.add_motion("test_retry", transient_callback, priority=900)
        scheduler.run_motion_once()
        assert "test_retry" in scheduler._motion_callbacks
        scheduler.run_motion_once()
        assert len(retry_count) == 2
        scheduler.remove_motion("test_retry")
        print("Yugo dual scheduler test passed")
    finally:
        if receiver is not None:
            receiver.close()
        yugo.unregister()


main()
