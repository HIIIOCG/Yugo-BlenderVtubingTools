"""Explicitly started preview adapter, isolated from Studio's runtime."""

import time

import bpy
from bpy.app.handlers import persistent

from . import service

_registered = False
_last_time = None
_error = ""


def _timer():
    global _last_time, _error
    if not service.active():
        _last_time = None
        return None
    if bpy.context.screen and bpy.context.screen.is_animation_playing:
        _error = "Preview stopped: pause the animation timeline before running physics"
        service.stop()
        _last_time = None
        return None
    now = time.perf_counter()
    elapsed = max(0.0, now - (_last_time if _last_time is not None else now))
    _last_time = now
    try:
        service.update(elapsed)
    except Exception as error:
        _error = str(error)
        service.stop()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    if not service.active():
        _last_time = None
        return None
    return 1.0 / 60.0


def start_preview(scene, settings):
    global _last_time, _error
    if bpy.context.screen and bpy.context.screen.is_animation_playing:
        raise ValueError("Pause the animation timeline before starting Physics Lab")
    service.start(scene, settings)
    _error = ""
    _last_time = time.perf_counter()
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=1.0 / 60.0)


def stop_preview(*, clear=False):
    global _last_time
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    service.clear() if clear else service.stop()
    _last_time = None


@persistent
def _before_replace(_unused):
    global _error
    stop_preview(clear=True)
    _error = ""


class YUGOPL_Settings(bpy.types.PropertyGroup):
    bodies: bpy.props.PointerProperty(name="Dynamic Props", type=bpy.types.Collection)
    colliders: bpy.props.PointerProperty(name="Static Boxes", type=bpy.types.Collection)
    left_anchor: bpy.props.PointerProperty(name="Left Anchor", type=bpy.types.Object)
    right_anchor: bpy.props.PointerProperty(name="Right Anchor", type=bpy.types.Object)
    target: bpy.props.PointerProperty(name="Prop", type=bpy.types.Object)
    gravity: bpy.props.FloatVectorProperty(name="Gravity", size=3, default=(0, 0, -9.81))
    use_floor: bpy.props.BoolProperty(name="Ground Plane", default=True)
    floor_height: bpy.props.FloatProperty(name="Ground Z", default=0)
    radius: bpy.props.FloatProperty(name="Sphere Radius", default=.28, min=.02, max=10)
    mass: bpy.props.FloatProperty(name="Mass", default=1, min=.01, max=100)
    damping: bpy.props.FloatProperty(name="Linear Damping", default=.3, min=0, max=20)
    angular_damping: bpy.props.FloatProperty(name="Angular Damping", default=.3, min=0, max=20)
    friction: bpy.props.FloatProperty(name="Friction", default=.6, min=0, max=1)
    restitution: bpy.props.FloatProperty(name="Bounce", default=.25, min=0, max=1)
    max_speed: bpy.props.FloatProperty(name="Speed Limit", default=30, min=1, max=60)
    grab_radius: bpy.props.FloatProperty(name="Grab Range", default=1.5, min=.01, max=20)
    grab_strength: bpy.props.FloatProperty(name="Grab Strength", default=120, min=1, max=1000)
    grab_damping: bpy.props.FloatProperty(name="Grab Damping", default=22, min=0, max=100)
    grab_max_force: bpy.props.FloatProperty(name="Grab Force Limit", default=250, min=1, max=5000)
    recall_strength: bpy.props.FloatProperty(name="Attraction", default=80, min=1, max=1000)
    recall_damping: bpy.props.FloatProperty(name="Recall Damping", default=18, min=0, max=100)
    recall_max_force: bpy.props.FloatProperty(name="Recall Force Limit", default=200, min=1, max=5000)
    recall_timeout: bpy.props.FloatProperty(name="Timeout", default=8, min=.1, max=60)
    recall_hold: bpy.props.BoolProperty(name="Maintain Attraction", default=True)
    recall_orientation: bpy.props.BoolProperty(name="Restore Orientation", default=True)
    impulse: bpy.props.FloatVectorProperty(name="Impulse", size=3, default=(3, 0, 5))
    angular_impulse: bpy.props.FloatVectorProperty(name="Angular Impulse", size=3, default=(0, 0, .2))
    force: bpy.props.FloatVectorProperty(name="Continuous Force", size=3, default=(0, 0, 0))


class YUGOPL_OT_control(bpy.types.Operator):
    bl_idname = "yugo_physics_lab.control"
    bl_label = "Physics Lab Control"
    bl_description = "Operate the explicitly started physics preview"
    action: bpy.props.EnumProperty(items=[(name, name.replace("_", " ").title(), "") for name in (
        "START", "STOP", "RESET", "IMPULSE", "FORCE", "CLEAR_FORCE", "RECALL", "CANCEL_RECALL",
        "GRAB_L", "RELEASE_L", "GRAB_R", "RELEASE_R",
    )])

    def execute(self, context):
        global _error
        settings = context.scene.yugo_physics_lab
        try:
            if self.action == "START":
                start_preview(context.scene, settings)
            elif self.action == "STOP":
                stop_preview()
            elif self.action == "RESET":
                service.reset()
            elif self.action == "IMPULSE":
                service.impulse(settings.target, tuple(settings.impulse), tuple(settings.angular_impulse))
            elif self.action in {"FORCE", "CLEAR_FORCE"}:
                service.force(settings.target, tuple(settings.force) if self.action == "FORCE" else (0, 0, 0))
            elif self.action == "RECALL":
                service.recall(settings.target, {
                    "strength": settings.recall_strength,
                    "damping": settings.recall_damping,
                    "max_force": settings.recall_max_force,
                    "timeout": settings.recall_timeout,
                    "hold": settings.recall_hold,
                    "orient": settings.recall_orientation,
                })
            elif self.action == "CANCEL_RECALL":
                service.cancel_recall(settings.target)
            else:
                anchor = settings.left_anchor if self.action.endswith("_L") else settings.right_anchor
                if self.action.startswith("GRAB"):
                    name = service.grab(anchor, settings.grab_radius)
                    self.report({"INFO"}, f"Grabbed {name}")
                else:
                    service.release(anchor)
            _error = ""
        except Exception as error:
            _error = str(error)
            self.report({"WARNING"}, _error)
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGOPL_OT_demo(bpy.types.Operator):
    bl_idname = "yugo_physics_lab.create_demo"
    bl_label = "Create Physics Lab Demo"
    bl_description = "Create two props, two movable grab anchors and a blocking wall"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .demo import create_demo
        stop_preview(clear=True)
        create_demo(context.scene)
        self.report({"INFO"}, "Demo ready. Press Start, then Impulse or Grab; move the anchor to throw.")
        return {"FINISHED"}


def _button(layout, text, action, icon="NONE"):
    op = layout.operator("yugo_physics_lab.control", text=text, icon=icon)
    op.action = action


class YUGOPL_PT_preview(bpy.types.Panel):
    bl_label = "Yugo Physics Lab"
    bl_idname = "YUGOPL_PT_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Physics Lab"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.yugo_physics_lab
        layout.label(text="Stage 2 · Timeline stays stopped")
        layout.operator("yugo_physics_lab.create_demo", icon="ADD")
        row = layout.row(align=True)
        _button(row, "Start", "START", "PLAY")
        _button(row, "Stop", "STOP", "PAUSE")
        _button(row, "Reset Home", "RESET", "LOOP_BACK")
        layout.label(text=service.status())
        if _error:
            layout.label(text=_error, icon="ERROR")
        config = layout.column()
        config.enabled = not service.active()
        config.prop(settings, "bodies")
        config.prop(settings, "colliders")
        config.prop(settings, "gravity")
        config.prop(settings, "use_floor")
        if settings.use_floor:
            config.prop(settings, "floor_height")
        row = config.row(align=True)
        row.prop(settings, "radius")
        row.prop(settings, "mass")
        row = config.row(align=True)
        row.prop(settings, "friction")
        row.prop(settings, "restitution")
        row = config.row(align=True)
        row.prop(settings, "damping")
        row.prop(settings, "angular_damping")
        config.prop(settings, "max_speed")
        config.prop(settings, "left_anchor")
        config.prop(settings, "right_anchor")
        box = layout.box()
        box.label(text="Hands · Move the anchor while holding")
        box.prop(settings, "grab_radius")
        row = box.row(align=True)
        _button(row, "Grab Left", "GRAB_L")
        _button(row, "Release Left", "RELEASE_L")
        row = box.row(align=True)
        _button(row, "Grab Right", "GRAB_R")
        _button(row, "Release Right", "RELEASE_R")
        config = box.column()
        config.enabled = not service.active()
        config.prop(settings, "grab_strength")
        config.prop(settings, "grab_damping")
        config.prop(settings, "grab_max_force")
        box = layout.box()
        box.prop(settings, "target")
        box.prop(settings, "impulse")
        box.prop(settings, "angular_impulse")
        _button(box, "Apply Impulse", "IMPULSE")
        box.prop(settings, "force")
        row = box.row(align=True)
        _button(row, "Apply Force", "FORCE")
        _button(row, "Clear Force", "CLEAR_FORCE")
        box.prop(settings, "recall_strength")
        box.prop(settings, "recall_damping")
        box.prop(settings, "recall_max_force")
        box.prop(settings, "recall_timeout")
        box.prop(settings, "recall_hold")
        box.prop(settings, "recall_orientation")
        row = box.row(align=True)
        _button(row, "Attract Home", "RECALL")
        _button(row, "Stop Attraction", "CANCEL_RECALL")
        if settings.target:
            state = service.body_state(settings.target)
            if state:
                box.label(text=f"Recall: {state['recall_state']}")
                box.label(text=f"Speed: {sum(v*v for v in state['velocity'])**.5:.2f} m/s")
        layout.label(text="Sphere props · static box colliders")


CLASSES = (YUGOPL_Settings, YUGOPL_OT_control, YUGOPL_OT_demo, YUGOPL_PT_preview)


def register():
    global _registered
    if _registered:
        return
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.yugo_physics_lab = bpy.props.PointerProperty(type=YUGOPL_Settings)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _before_replace not in handlers:
            handlers.append(_before_replace)
    _registered = True


def unregister():
    global _registered
    stop_preview(clear=True)
    if not _registered:
        return
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _before_replace in handlers:
            handlers.remove(_before_replace)
    del bpy.types.Scene.yugo_physics_lab
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _registered = False
