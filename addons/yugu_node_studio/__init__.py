bl_info = {
    "name": "Yugu Node Studio",
    "author": "Yugo Contributors",
    "version": (0, 7, 0),
    "blender": (5, 1, 0),
    "location": "Node Editor / View3D > Sidebar > Yugu Studio",
    "description": "Node-based VRM Humanoid motion driving native Blender data",
    "category": "Animation",
}

import bpy
from bpy.app.handlers import persistent


def _refresh_cached_package():
    """Reload only this extension's children after retiring their RNA and timers."""
    import importlib
    import sys

    prefix = __name__ + "."
    cached = [name for name in tuple(sys.modules) if name.startswith(prefix)]
    if not cached:
        return
    shutdown = globals().get("unregister")
    if globals().get("_registered", False) and callable(shutdown):
        shutdown()
    for name in cached:
        sys.modules.pop(name, None)
    for name in {name[len(prefix):].split(".")[0] for name in cached}:
        globals().pop(name, None)
    importlib.invalidate_caches()


_refresh_cached_package()

from . import network, nodes, operators, runtime, ui, vrm_cache
from . import composing, studio, scheduler, mtoon_optimize, keyboard, flow, targets
from . import input_nodes, input_runtime, workflow_nodes, workflow, workflow_ui
from . import extensions

_registered = False


@persistent
def _load_pre(_unused):
    scheduler.suspend()
    flow.clear()
    extensions.cleanup("load")
    input_runtime.clear()
    keyboard.reset()
    network.stop_all()
    runtime.stop_all_springs(reset_pose=False)
    vrm_cache.clear_all()


@persistent
def _load_post(_unused):
    extensions.recover("load")
    nodes.ensure_current_sockets()
    vrm_cache.invalidate_saved_nodes()
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "YUGUS_NT_humanoid_motion":
            continue
        tree.runtime_enabled = False
        tree.last_status = "Idle · Reload VRM Data"
        for node in tree.nodes:
            if hasattr(node, "connected"):
                node.connected = False
            if node.bl_idname == "YUGUS_NODE_blender_output":
                node.is_active_output = False
                node.active_validated = False
                node.last_summary = "Inactive"
            elif node.bl_idname == "YUGUS_NODE_spring_simulation":
                node.spring_active = False
                node.spring_status = "Stopped"
                node.spring_last_error = ""
    scheduler.resume()


def _deferred_socket_migration():
    return None if nodes.ensure_current_sockets() else 0.1


def register():
    global _registered
    if _registered:
        return
    for cls in nodes.CLASSES:
        bpy.utils.register_class(cls)
    targets.register()
    flow.register()
    for cls in input_nodes.CLASSES + workflow_nodes.CLASSES + workflow_ui.CLASSES:
        bpy.utils.register_class(cls)
    for cls in operators.CLASSES:
        bpy.utils.register_class(cls)
    operators.register_properties()
    for cls in ui.CLASSES:
        bpy.utils.register_class(cls)
    ui.register_hooks()
    scheduler.register()
    runtime.register()
    composing.register()
    studio.register()
    mtoon_optimize.register()
    keyboard.register()
    input_runtime.register()
    _registered = True
    try:
        extensions.register()
    except Exception:
        unregister()
        raise
    bpy.app.handlers.undo_pre.append(_undo_pre)
    bpy.app.handlers.undo_post.append(_undo_post)
    bpy.app.handlers.redo_pre.append(_undo_pre)
    bpy.app.handlers.redo_post.append(_undo_post)
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if not bpy.app.timers.is_registered(_deferred_socket_migration):
        bpy.app.timers.register(_deferred_socket_migration, first_interval=0.0)


def unregister():
    global _registered
    if not _registered:
        return
    input_runtime.unregister()
    flow.clear()
    extensions.unregister()
    for cls in reversed(input_nodes.CLASSES + workflow_nodes.CLASSES + workflow_ui.CLASSES):
        bpy.utils.unregister_class(cls)
    flow.unregister()
    targets.unregister()
    keyboard.unregister()
    mtoon_optimize.unregister()
    scheduler.unregister()
    studio.unregister()
    composing.unregister()
    for handlers, callback in ((bpy.app.handlers.undo_pre, _undo_pre),
                               (bpy.app.handlers.undo_post, _undo_post),
                               (bpy.app.handlers.redo_pre, _undo_pre),
                               (bpy.app.handlers.redo_post, _undo_post)):
        if callback in handlers:
            handlers.remove(callback)
    network.stop_all()
    runtime.unregister()
    if bpy.app.timers.is_registered(_deferred_socket_migration):
        bpy.app.timers.unregister(_deferred_socket_migration)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    ui.unregister_hooks()
    for cls in reversed(ui.CLASSES):
        bpy.utils.unregister_class(cls)
    operators.unregister_properties()
    for cls in reversed(operators.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(nodes.CLASSES):
        bpy.utils.unregister_class(cls)
    vrm_cache.clear_all()
    _registered = False


@persistent
def _undo_pre(_unused):
    scheduler.suspend()
    flow.clear()
    extensions.cleanup("undo/redo")
    input_runtime.clear()
    keyboard.reset()


@persistent
def _undo_post(_unused):
    extensions.recover("undo/redo")
    scheduler.resume()
