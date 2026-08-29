"""Keep live network receivers connected while Blender rebuilds undo data."""

import bpy
from bpy.app.handlers import persistent

from . import motion_mixer
from .ifacialmocap import runtime as ifacialmocap_runtime
from .keybinds import runtime as keybinds_runtime
from .springbone import runtime as springbone_runtime
from .vmc import runtime as vmc_runtime


_in_transition = False


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


def register():
    for handlers, callback in (
        (bpy.app.handlers.undo_pre, _undo_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_pre, _undo_pre),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    global _in_transition
    for handlers, callback in (
        (bpy.app.handlers.undo_pre, _undo_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_pre, _undo_pre),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if callback in handlers:
            handlers.remove(callback)
    if _in_transition:
        motion_mixer.finish_undo()
    _in_transition = False
