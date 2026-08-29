bl_info = {
    "name": "Yugo",
    "author": "ShenDeng",
    "version": (0, 1, 14),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Yugo",
    "description": "Layered motion mixing UI for VRM avatars",
    "category": "Animation",
}

from . import (
    ifacialmocap,
    keybinds,
    mtoon_optimize,
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
    properties,
    mtoon_optimize,
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
)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
