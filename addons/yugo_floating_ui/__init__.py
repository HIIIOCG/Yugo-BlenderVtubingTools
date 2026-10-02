"""Floating UI feature prototype; manually registered development harness.

This package does not register with Yugu Node Studio or the legacy Yugo add-on.
The production module/scheduler integration remains dependent on tasks #2/#3.
"""


def register():
    """Register the optional native Blender preview editor."""
    from . import preview

    preview.register()


def unregister():
    """Stop preview sessions and remove only this module's generated output."""
    from . import preview

    preview.unregister()
