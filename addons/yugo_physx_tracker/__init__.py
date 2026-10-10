bl_info = {'name':'Yugo PhysX Tracker','author':'Yugo Contributors','version':(0,4,0),
           'blender':(5,1,0),'category':'Animation',
           'description':'ProtoMotions learned SMPL tracker with standalone PhysX physics'}

import bpy
from bpy.app.handlers import persistent
from . import nodes,bridge,runtime,physics_inputs,reference_display

_registered = False

@persistent
def _invalidate(_unused):
    runtime.clear()
    physics_inputs.uninstall_tracking()
    reference_display.invalidate()

@persistent
def _migrate(_unused):
    if nodes.migrate_trees():
        _cancel_migration()
    elif not bpy.app.timers.is_registered(_deferred_migrate):
        bpy.app.timers.register(_deferred_migrate,first_interval=0.)

def _cancel_migration():
    if bpy.app.timers.is_registered(_deferred_migrate):
        bpy.app.timers.unregister(_deferred_migrate)

def _deferred_migrate():
    if not _registered:
        return None
    return None if nodes.migrate_trees() else .1

def register():
    global _registered
    if _registered: return
    bridge.base()
    done = []
    try:
        for cls in nodes.CLASSES:
            bpy.utils.register_class(cls)
            done.append(cls)
        bridge.install()
        nodes.install_numeric_ui(bridge.base())
        reference_display.register()
        _migrate(None)
        bpy.types.NODE_MT_add.append(nodes.menu)
        for handlers in (bpy.app.handlers.load_pre,bpy.app.handlers.undo_pre,bpy.app.handlers.redo_pre):
            handlers.append(_invalidate)
        bpy.app.handlers.load_post.append(_migrate)
        bpy.app.timers.register(runtime.tick,first_interval=1/60,persistent=True)
        _registered = True
    except Exception:
        _cancel_migration()
        physics_inputs.uninstall_tracking()
        reference_display.unregister()
        nodes.uninstall_numeric_ui()
        bridge.uninstall()
        for handlers in (bpy.app.handlers.load_pre,bpy.app.handlers.undo_pre,bpy.app.handlers.redo_pre):
            if _invalidate in handlers: handlers.remove(_invalidate)
        if _migrate in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(_migrate)
        try: bpy.types.NODE_MT_add.remove(nodes.menu)
        except Exception: pass
        if bpy.app.timers.is_registered(runtime.tick): bpy.app.timers.unregister(runtime.tick)
        for cls in reversed(done): bpy.utils.unregister_class(cls)
        raise

def unregister():
    global _registered
    if not _registered: return
    _cancel_migration()
    if bpy.app.timers.is_registered(runtime.tick): bpy.app.timers.unregister(runtime.tick)
    runtime.clear()
    physics_inputs.uninstall_tracking()
    reference_display.unregister()
    for handlers in (bpy.app.handlers.load_pre,bpy.app.handlers.undo_pre,bpy.app.handlers.redo_pre):
        if _invalidate in handlers: handlers.remove(_invalidate)
    if _migrate in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(_migrate)
    bpy.types.NODE_MT_add.remove(nodes.menu)
    nodes.uninstall_numeric_ui()
    bridge.uninstall()
    for cls in reversed(nodes.CLASSES): bpy.utils.unregister_class(cls)
    _registered = False
