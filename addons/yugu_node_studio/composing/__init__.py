"""Yugo compositor controls and bundled MainFX preset."""


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()
