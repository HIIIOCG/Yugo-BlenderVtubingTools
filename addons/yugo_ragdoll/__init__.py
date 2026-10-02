"""Ragdoll feature and explicit Motion Node development preview.

Studio integration awaits its public extension contract (repository #2/#3).
"""

bl_info = {
    "name": "Yugo Ragdoll Motion Node Preview",
    "author": "Yugo contributors",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "Node Editor > Ragdoll Motion Preview",
    "description": "Jointed ragdoll motion, directed hits and pose recovery",
    "category": "Animation",
}


def register():
    from . import preview
    preview.register()


def unregister():
    from . import preview
    preview.unregister()
