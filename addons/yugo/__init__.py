bl_info = {
    "name": "Yugo",
    "author": "Yugo Contributors",
    "version": (0, 1, 26),
    "blender": (5, 1, 0),
    "location": "View3D / Node Editor > Sidebar > Yugo",
    "description": "Avatar motion layers, nodes, physics, camera and compositing",
    "category": "Animation",
}

import bpy


def _refresh_cached_package():
    """Retire this package before reloading its children, without touching others."""
    import importlib
    import sys

    prefix = __name__ + "."
    cached = [name for name in tuple(sys.modules) if name.startswith(prefix)]
    if not cached:
        return
    shutdown = globals().get("unregister")
    if globals().get("_registered_modules") and callable(shutdown):
        shutdown()
    for name in cached:
        sys.modules.pop(name, None)
    for name in {name[len(prefix):].split(".")[0] for name in cached}:
        globals().pop(name, None)
    importlib.invalidate_caches()


_refresh_cached_package()

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
from . import node


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
    node,
)

_registered_modules = []
_registration_complete = False


def _check_node_owner():
    owner = bpy.types.NodeTree.bl_rna_get_subclass_py(node.constants.TREE_ID, None)
    if owner is not None and owner is not node.nodes.YUGUS_NT_humanoid_motion:
        raise RuntimeError(
            "Another Yugo Node / Yugu Node Studio add-on is enabled. "
            "Disable that add-on before enabling the integrated Yugo."
        )


def register():
    global _registration_complete
    if _registered_modules:
        if _registration_complete:
            return
        unregister()
    # Both packages retain their saved graph IDs. Check before any RNA or timer
    # is installed so a rejected enable leaves the existing package operational.
    _check_node_owner()
    try:
        for module in MODULES:
            try:
                module.register()
            except Exception:
                # A module may have started installing resources before it
                # failed. Let its transaction unwind before the parent modules.
                try:
                    module.unregister()
                except Exception as error:
                    print(f"Yugo could not finish {module.__name__} cleanup: {error}")
                    _registered_modules.append(module)
                raise
            _registered_modules.append(module)
    except Exception:
        try:
            unregister()
        except Exception as error:
            print(f"Yugo could not finish registration rollback: {error}")
        raise
    _registration_complete = True


def unregister():
    global _registration_complete
    _registration_complete = False
    errors = []
    failed_modules = []
    while _registered_modules:
        module = _registered_modules.pop()
        try:
            module.unregister()
        except Exception as error:
            errors.append(error)
            failed_modules.append(module)
            print(f"Yugo could not finish {module.__name__} cleanup: {error}")
    _registered_modules.extend(reversed(failed_modules))
    if errors:
        raise RuntimeError("Yugo shutdown could not release every module") from errors[0]
