"""Create and maintain a clean presentation-only camera window."""

import math

import bpy

from . import scheduler


_VIEW_WINDOWS = {}
_UPDATE_INTERVAL = scheduler.SYSTEM_INTERVAL


def _view_area(window):
    area = max(window.screen.areas, key=lambda item: item.width * item.height)
    if area.type != "VIEW_3D":
        area.type = "VIEW_3D"
    return area


def _window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _render_aspect(scene):
    render = scene.render
    width = max(float(render.resolution_x) * float(render.pixel_aspect_x), 1.0)
    height = max(float(render.resolution_y) * float(render.pixel_aspect_y), 1.0)
    return width / height


def camera_cover_zoom(region_width, region_height, scene):
    """Return the camera-view zoom that covers the complete viewport."""

    viewport_aspect = max(float(region_width), 1.0) / max(
        float(region_height), 1.0
    )
    camera_aspect = _render_aspect(scene)
    cover_scale = max(
        viewport_aspect / camera_aspect,
        camera_aspect / viewport_aspect,
    )
    # Blender's camera-view scale is nonlinear. A scale of 1.0 fits a
    # same-aspect camera frame exactly; Cover enlarges whichever axis needs it.
    zoom = (math.sqrt(4.0 * cover_scale) - math.sqrt(2.0)) * 50.0
    return max(-30.0, min(600.0, zoom))


def configure_view_space(
    space,
    scene,
    shading_type,
    compositor_mode,
    region,
) -> None:
    space.show_region_header = False
    space.show_region_toolbar = False
    space.show_region_ui = False
    space.show_region_tool_header = False
    space.show_region_hud = False
    space.show_gizmo = False
    space.overlay.show_overlays = False
    space.shading.type = shading_type
    space.shading.use_compositor = compositor_mode

    camera = scene.camera
    if camera is not None and getattr(camera, "type", "") == "CAMERA":
        space.use_local_camera = True
        space.camera = camera
        space.region_3d.view_perspective = "CAMERA"
        space.region_3d.view_camera_offset = (0.0, 0.0)
        if region is not None:
            space.region_3d.view_camera_zoom = camera_cover_zoom(
                region.width,
                region.height,
                scene,
            )
    else:
        space.use_local_camera = False
        space.camera = None
        if space.region_3d.view_perspective == "CAMERA":
            space.region_3d.view_perspective = "PERSP"


def _window_signature(window, scene, shading_type, compositor_mode):
    area = _view_area(window)
    region = _window_region(area)
    camera = scene.camera
    camera_pointer = camera.as_pointer() if camera is not None else 0
    render = scene.render
    return (
        camera_pointer,
        region.width if region is not None else 0,
        region.height if region is not None else 0,
        render.resolution_x,
        render.resolution_y,
        render.pixel_aspect_x,
        render.pixel_aspect_y,
        shading_type,
        compositor_mode,
    )


def _restore_locked_workspace(window, state) -> bool:
    if state is None:
        return False
    workspace = state.get("workspace")
    if workspace is None or window.workspace == workspace:
        return False
    window.workspace = workspace
    state["signature"] = None
    return True


def configure_view_window(
    window,
    scene,
    shading_type,
    compositor_mode="ALWAYS",
    *,
    force=False,
) -> None:
    pointer = window.as_pointer()
    state = _VIEW_WINDOWS.get(pointer)
    _restore_locked_workspace(window, state)
    signature = _window_signature(
        window,
        scene,
        shading_type,
        compositor_mode,
    )
    if not force and state is not None and state.get("signature") == signature:
        return

    window.screen.show_statusbar = False
    area = _view_area(window)
    space = area.spaces.active
    if space is None:
        raise RuntimeError("The No UI Window 3D Viewport is not ready")
    configure_view_space(
        space,
        scene,
        shading_type,
        compositor_mode,
        _window_region(area),
    )
    if state is not None:
        state["signature"] = signature
    area.tag_redraw()


def _update_view_windows():
    try:
        window_manager = bpy.context.window_manager
        if window_manager is None:
            return _UPDATE_INTERVAL if _VIEW_WINDOWS else None
        open_windows = {window.as_pointer(): window for window in window_manager.windows}
        for pointer in tuple(_VIEW_WINDOWS):
            window = open_windows.get(pointer)
            if window is None:
                _VIEW_WINDOWS.pop(pointer, None)
                continue
            scene = window.scene
            shading_type = getattr(scene, "yugo_view_shading", "RENDERED")
            compositor_mode = getattr(
                scene,
                "yugo_view_compositor",
                "ALWAYS",
            )
            configure_view_window(
                window,
                scene,
                shading_type,
                compositor_mode,
            )
    except (AttributeError, ReferenceError, RuntimeError):
        # UI data can be briefly unavailable while Blender closes a window.
        pass
    return _UPDATE_INTERVAL if _VIEW_WINDOWS else None


def _ensure_update_timer():
    # The shared 2 Hz system scheduler is always active while Yugo is enabled.
    return None


def _system_update(_context, _delta_time):
    _update_view_windows()


def open_view_window(
    context,
    shading_type="RENDERED",
    compositor_mode="ALWAYS",
):
    if context.window is None or context.area is None:
        raise RuntimeError("No UI Window must be opened from a Blender window")
    if context.area.type != "VIEW_3D":
        raise RuntimeError("No UI Window must be opened from the 3D Viewport")

    window_manager = context.window_manager
    existing = {window.as_pointer() for window in window_manager.windows}
    result = bpy.ops.wm.window_new()
    if result != {"FINISHED"}:
        raise RuntimeError("Blender could not create the No UI Window")

    new_window = next(
        (
            window
            for window in window_manager.windows
            if window.as_pointer() not in existing
        ),
        None,
    )
    if new_window is None:
        raise RuntimeError("The new No UI Window could not be found")

    new_window.scene = context.scene
    area = next(
        (area for area in new_window.screen.areas if area.type == "VIEW_3D"),
        None,
    )
    if area is None:
        area = _view_area(new_window)
    window_region = _window_region(area)
    if not new_window.screen.show_fullscreen and window_region is not None:
        with context.temp_override(
            window=new_window,
            screen=new_window.screen,
            area=area,
            region=window_region,
        ):
            bpy.ops.screen.screen_full_area(use_hide_panels=True)

    pointer = new_window.as_pointer()
    _VIEW_WINDOWS[pointer] = {
        "signature": None,
        "workspace": new_window.workspace,
    }
    configure_view_window(
        new_window,
        new_window.scene,
        shading_type,
        compositor_mode,
        force=True,
    )
    _ensure_update_timer()

    # Blender can reopen the HUD and finish sizing the OS window after the
    # creating operator returns. Reapply UI state and Cover zoom on the next tick.
    def finalize():
        try:
            current = next(
                (
                    window
                    for window in window_manager.windows
                    if window.as_pointer() == pointer
                ),
                None,
            )
            if current is not None:
                state = _VIEW_WINDOWS.get(pointer)
                if state is not None:
                    state["signature"] = None
                configure_view_window(
                    current,
                    current.scene,
                    getattr(current.scene, "yugo_view_shading", shading_type),
                    getattr(
                        current.scene,
                        "yugo_view_compositor",
                        compositor_mode,
                    ),
                    force=True,
                )
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        return None

    bpy.app.timers.register(finalize, first_interval=0.0)
    return new_window


def register():
    scheduler.add_system("view_window", _system_update, priority=200)


def unregister():
    scheduler.remove_system("view_window")
    _VIEW_WINDOWS.clear()
