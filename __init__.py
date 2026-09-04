bl_info = {
    "name": "Yugo",
    "author": "Yugo Contributors",
    "version": (0, 1, 26),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Yugo",
    "description": "Layered motion, camera control, and compositing for VRM avatars",
    "category": "Animation",
}

from . import (
    scheduler,
    camera,
    composing,
    ifacialmocap,
    keybinds,
    mtoon_optimize,
    outline_bake,
    motion_mixer,
    operators,
    panels,
    properties,
    springbone,
    ui_lists,
    undo_safety,
    view_window,
    vmc,
)


MODULES = (
    scheduler,
    properties,
    composing,
    mtoon_optimize,
    outline_bake,
    motion_mixer,
    ifacialmocap,
    vmc,
    keybinds,
    springbone,
    undo_safety,
    view_window,
    operators,
    ui_lists,
    panels,
    camera,
)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
