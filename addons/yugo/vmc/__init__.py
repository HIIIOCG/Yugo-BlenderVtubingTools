"""VMC/OSC receiver integrated with Yugo motion layers."""

from . import runtime


def register():
    runtime.register()


def unregister():
    runtime.unregister()
