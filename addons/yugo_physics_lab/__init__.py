"""Standalone stage-two preview; Studio registration belongs to tasks #2/#3."""

bl_info = {
    "name": "Yugo Physics Lab",
    "author": "Yugo Contributors",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Physics Lab",
    "description": "Live prop physics prototype with the timeline stopped",
    "category": "Physics",
}

from . import preview


def register():
    preview.register()


def unregister():
    preview.unregister()
