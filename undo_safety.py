"""Keep live network receivers connected while Blender rebuilds undo data."""

import bpy
from bpy.app.handlers import persistent

from . import camera, motion_mixer, scheduler
from .ifacialmocap import runtime as ifacialmocap_runtime
from .keybinds import runtime as keybinds_runtime
from .springbone import runtime as springbone_runtime
from .vmc import runtime as vmc_runtime


_in_transition = False
_load_transition = False


@persistent
def _undo_pre(*_args):
    global _in_transition
    if _in_transition:
        return
    _in_transition = True
    motion_mixer.prepare_undo()
    for runtime in (
        ifacialmocap_runtime,
        vmc_runtime,
        keybinds_runtime,
        springbone_runtime,
    ):
        try:
            runtime.prepare_undo()
        except Exception as error:
            print(f"Yugo undo preparation failed in {runtime.__name__}: {error}")


@persistent
def _undo_post(*_args):
    global _in_transition
    if not _in_transition:
        return
    try:
        for runtime in (
            ifacialmocap_runtime,
            vmc_runtime,
            keybinds_runtime,
            springbone_runtime,
        ):
            try:
                runtime.finish_undo()
            except Exception as error:
                print(f"Yugo undo rebind failed in {runtime.__name__}: {error}")
    finally:
        motion_mixer.finish_undo()
        _in_transition = False


@persistent
def _load_pre(*_args):
    """Release every live runtime before Blender frees the current file RNA."""

    global _in_transition, _load_transition
    if _load_transition:
        return
    _load_transition = True
    _in_transition = False
    scheduler.suspend()

    shutdowns = (
        (
            "iFacialMocap",
            lambda: ifacialmocap_runtime.stop(
                send_stop_command=True,
                reset_expressions=False,
            ),
        ),
        ("VMC", lambda: vmc_runtime.stop(clear=False)),
        ("Keybinds", lambda: keybinds_runtime.stop(reset=False)),
        ("Spring Bone", lambda: springbone_runtime.stop_all(reset_pose=False)),
        (
            "Camera",
            lambda: camera.stop_listener(
                restart_idle=False,
                publish_ui=False,
            ),
        ),
    )
    for name, shutdown in shutdowns:
        try:
            shutdown()
        except Exception as error:
            print(f"Yugo file-load shutdown failed in {name}: {error}")
    motion_mixer.discard_all()


@persistent
def _load_post(*_args):
    global _load_transition
    motion_mixer.discard_all()
    _load_transition = False
    scheduler.resume()


def register():
    for handlers, callback in (
        (bpy.app.handlers.undo_pre, _undo_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_pre, _undo_pre),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if callback not in handlers:
            handlers.append(callback)
    # Run before subsystem-specific load handlers so no periodic callback or
    # network thread can retain old Scene/Layer RNA during file replacement.
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.insert(0, _load_pre)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    global _in_transition, _load_transition
    for handlers, callback in (
        (bpy.app.handlers.undo_pre, _undo_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_pre, _undo_pre),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if callback in handlers:
            handlers.remove(callback)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _in_transition:
        motion_mixer.finish_undo()
    _in_transition = False
    _load_transition = False
    scheduler.resume()
