bl_info = {
    "name": "Yugo Node",
    "author": "Yugo Contributors",
    "version": (0, 10, 0),
    "blender": (5, 1, 0),
    "location": "Node Editor / View3D > Sidebar > Yugo",
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
    if (globals().get("_registered", False) or globals().get("_registration_steps")) and callable(shutdown):
        shutdown()
    for name in cached:
        sys.modules.pop(name, None)
    for name in {name[len(prefix):].split(".")[0] for name in cached}:
        globals().pop(name, None)
    importlib.invalidate_caches()


_refresh_cached_package()

from . import network, nodes, operators, runtime, ui, vrm_cache
from .. import composing, mtoon_optimize, outline_bake
from . import studio, scheduler, keyboard, flow, targets
from . import input_nodes, input_runtime, workflow_nodes, workflow, workflow_ui
from . import value_nodes, chat_nodes, chat_runtime, chat_example
from . import chat_ui_nodes, chat_ui_runtime
from . import image_nodes, image_runtime
from . import math_nodes, keyword_nodes
from . import physics, object_transforms, mixer, motion_delay, spring_trace
from . import availability

# Shared text/object/collection sockets remain useful to the active Data nodes.
# Retired node declarations load older files, without their operators or panels.
_EXTRA_CLASSES = (
    input_nodes.CLASSES + workflow_nodes.CLASSES + (workflow_ui.YUGUS_NODE_test_event,)
    + value_nodes.CLASSES + math_nodes.CLASSES
    + tuple(cls for cls in chat_nodes.CLASSES if issubclass(cls, (bpy.types.Node, bpy.types.NodeSocket)))
    + keyword_nodes.CLASSES + image_nodes.CLASSES
    + chat_ui_nodes.SOCKET_CLASSES + chat_ui_nodes.NODE_CLASSES
)
availability.prepare_legacy_classes(nodes.CLASSES + _EXTRA_CLASSES)

_registered = False
_save_state = None
_registration_steps = []


@persistent
def _load_pre(_unused):
    _finish_save()
    physics.clear()
    object_transforms.clear()
    image_runtime.clear()
    chat_ui_runtime.clear()
    chat_runtime.clear()
    flow.clear()
    scheduler.suspend()
    input_runtime.clear()
    keyboard.reset()
    network.stop_all()
    runtime.stop_all_springs(reset_pose=False)
    vrm_cache.clear_all()


@persistent
def _load_post(_unused):
    try:
        _recover_saved_leases()
    finally:
        scheduler.resume()
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


def _deferred_socket_migration():
    return None if nodes.ensure_current_sockets() else 0.1


def _record_class(cls):
    def cleanup():
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    _registration_steps.append((cls.__name__, cleanup))


def _register_classes(classes):
    for cls in classes:
        bpy.utils.register_class(cls)
        _record_class(cls)


def _register_class_module(module, classes=None):
    # These modules register only classes. Record each actual success even if
    # their register() raises halfway through its class list.
    classes = module.CLASSES if classes is None else classes
    previous = {cls for cls in classes if getattr(cls, "is_registered", False)}
    try:
        module.register()
    finally:
        for cls in classes:
            if cls not in previous and getattr(cls, "is_registered", False):
                _record_class(cls)


def _register_step(label, setup, cleanup):
    # Module cleanup functions tolerate an incomplete setup (for example only
    # the first scheduler timer or menu hook having been installed).
    _registration_steps.append((label, cleanup))
    setup()


def _append_handler(handlers, callback):
    if callback not in handlers:
        handlers.append(callback)
        def cleanup():
            if callback in handlers:
                handlers.remove(callback)
        _registration_steps.append((callback.__name__, cleanup))


def _remove_deferred_socket_migration():
    if bpy.app.timers.is_registered(_deferred_socket_migration):
        bpy.app.timers.unregister(_deferred_socket_migration)


def _rollback_registration():
    # Retire event execution and physics before removing their RNA classes.
    # Remaining failed cleanups stay visible and can be retried by unregister().
    failures = []
    for label, cleanup in (
            ("save state", _finish_save), ("scheduler pause", scheduler.suspend),
            ("physics stop", physics.unregister_runtime),
            ("object transforms", object_transforms.clear),
            ("input events", input_runtime.unregister),
            ("chat UI events", chat_ui_runtime.unregister),
            ("chat events", chat_runtime.unregister), ("flow events", flow.clear),
            ("keyboard", keyboard.reset), ("network", network.stop_all)):
        try:
            cleanup()
        except Exception as error:
            print(f"Yugo Node rollback could not stop {label}: {error}")
            failures.append((label, cleanup))
    while _registration_steps:
        label, cleanup = _registration_steps.pop()
        try:
            cleanup()
        except Exception as error:
            print(f"Yugo Node rollback could not remove {label}: {error}")
            failures.append((label, cleanup))
    _registration_steps.extend(reversed(failures))
    vrm_cache.clear_all()
    if failures:
        raise RuntimeError("Yugo Node registration cleanup is incomplete: "
                           + ", ".join(label for label, _cleanup in failures))


def register():
    global _registered
    if _registered:
        return
    if _registration_steps:
        _rollback_registration()
    try:
        _register_classes(nodes.CLASSES)
        _register_class_module(targets)
        from . import flow_nodes
        _register_class_module(flow, flow.CLASSES + flow_nodes.CLASSES)
        _register_classes(_EXTRA_CLASSES)
        _register_classes(object_transforms.CLASSES + physics.CLASSES + mixer.CLASSES
                          + motion_delay.CLASSES + spring_trace.CLASSES)
        _register_classes(operators.CLASSES)
        _register_step("scene graph property", operators.register_properties,
                       operators.unregister_properties)
        _register_classes(ui.CLASSES)
        _register_step("editor hooks", ui.register_hooks, ui.unregister_hooks)
        _register_step("scheduler timers", scheduler.register, scheduler.unregister)
        chat_runtime.unregister()
        _register_step("image runtime", image_runtime.register, image_runtime.unregister)
        chat_ui_runtime.unregister()
        _register_step("motion runtime", runtime.register, runtime.unregister)
        _register_step("physics runtime", physics.register_runtime, physics.unregister_runtime)
        _register_class_module(studio)
        keyboard.reset()
        input_runtime.unregister()
        for handlers, callback in (
                (bpy.app.handlers.undo_pre, _undo_pre),
                (bpy.app.handlers.undo_post, _undo_post),
                (bpy.app.handlers.redo_pre, _undo_pre),
                (bpy.app.handlers.redo_post, _undo_post),
                (bpy.app.handlers.load_pre, _load_pre),
                (bpy.app.handlers.load_post, _load_post),
                (bpy.app.handlers.save_pre, _save_pre),
                (bpy.app.handlers.save_post, _save_post),
                (bpy.app.handlers.save_post_fail, _save_post)):
            _append_handler(handlers, callback)
        if not bpy.app.timers.is_registered(_deferred_socket_migration):
            _register_step("socket migration timer",
                           lambda: bpy.app.timers.register(_deferred_socket_migration,
                                                          first_interval=0.0),
                           _remove_deferred_socket_migration)
    except Exception:
        _rollback_registration()
        raise
    _registered = True


def unregister():
    _finish_save()
    global _registered
    if not _registered:
        if _registration_steps:
            _rollback_registration()
        return
    physics.unregister_runtime()
    object_transforms.clear()
    input_runtime.unregister()
    chat_ui_runtime.unregister()
    image_runtime.unregister()
    chat_runtime.unregister()
    flow.clear()
    for cls in reversed(object_transforms.CLASSES + physics.CLASSES + mixer.CLASSES + motion_delay.CLASSES + spring_trace.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(_EXTRA_CLASSES):
        bpy.utils.unregister_class(cls)
    flow.unregister()
    targets.unregister()
    keyboard.reset()
    scheduler.unregister()
    studio.unregister()
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
    if _save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_pre)
    for handlers in (bpy.app.handlers.save_post, bpy.app.handlers.save_post_fail):
        if _save_post in handlers:
            handlers.remove(_save_post)
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
    _registration_steps.clear()


@persistent
def _undo_pre(_unused):
    _finish_save()
    physics.clear()
    object_transforms.clear()
    image_runtime.clear()
    chat_ui_runtime.clear()
    chat_runtime.clear()
    flow.clear()
    scheduler.suspend()
    input_runtime.clear()
    keyboard.reset()


@persistent
def _undo_post(_unused):
    try:
        _recover_saved_leases()
    finally:
        scheduler.resume()
    nodes.ensure_current_sockets()


def _recover_saved_leases():
    for label, recover in (("object transforms", object_transforms.recover_saved_leases),
                           ("physics", physics.recover_saved_leases)):
        try:
            recover()
        except Exception as error:
            print(f"Yugo could not finish {label} recovery; metadata retained: {error}")


def _finish_save():
    global _save_state
    state, _save_state = _save_state, None
    if state is None:
        return
    try:
        flow.resume_apply_after_save(state["flow"])
    finally:
        if not state["suspended"]:
            scheduler.resume()


@persistent
def _save_pre(_unused):
    global _save_state
    _finish_save()
    _save_state = {"suspended": scheduler.is_suspended(), "flow": None}
    scheduler.suspend()
    # Temporary Bullet-disable flags and simulated poses must not become authoring data.
    for label, cleanup in (
            ("recordings", lambda: spring_trace.stop_all('File saved; recording kept for export')),
            ("physics", physics.clear), ("object transforms", object_transforms.clear)):
        try:
            cleanup()
        except Exception as error:
            print(f"Yugo could not finish {label} cleanup before saving: {error}")
    try:
        _save_state["flow"] = flow.suspend_apply_for_save()
    except Exception:
        _finish_save()
        raise


@persistent
def _save_post(_unused):
    _finish_save()
