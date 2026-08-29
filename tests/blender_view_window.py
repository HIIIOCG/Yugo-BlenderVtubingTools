"""Open a secondary ViewWindow and verify live camera/Cover behavior."""

import math
import sys
import traceback

import bpy


WORKSPACE = r"D:\Projects\BCON26\motion_codedev"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import yugo
from yugo.view_window import camera_cover_zoom


NEW_WINDOW = None
CAMERA_A = None
CAMERA_B = None
INITIAL_ZOOM = None


def _view_state():
    view_area = max(
        NEW_WINDOW.screen.areas,
        key=lambda item: item.width * item.height,
    )
    assert view_area.type == "VIEW_3D"
    space = view_area.spaces.active
    region = next(region for region in view_area.regions if region.type == "WINDOW")
    return view_area, space, region


def run_test():
    global NEW_WINDOW, CAMERA_A, CAMERA_B
    try:
        yugo.register()
        scene = bpy.context.scene
        scene.render.resolution_x = 1920
        scene.render.resolution_y = 1080
        scene.render.pixel_aspect_x = 1.0
        scene.render.pixel_aspect_y = 1.0

        camera_a_data = bpy.data.cameras.new("YugoViewWindowCameraAData")
        camera_a = bpy.data.objects.new("YugoViewWindowCameraA", camera_a_data)
        scene.collection.objects.link(camera_a)
        camera_b_data = bpy.data.cameras.new("YugoViewWindowCameraBData")
        camera_b = bpy.data.objects.new("YugoViewWindowCameraB", camera_b_data)
        scene.collection.objects.link(camera_b)
        scene.camera = camera_a
        scene.yugo_view_shading = "MATERIAL"

        window = bpy.context.window_manager.windows[0]
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        existing = {
            candidate.as_pointer()
            for candidate in bpy.context.window_manager.windows
        }
        with bpy.context.temp_override(window=window, area=area):
            assert bpy.ops.yugo.view_window() == {"FINISHED"}

        NEW_WINDOW = next(
            candidate
            for candidate in bpy.context.window_manager.windows
            if candidate.as_pointer() not in existing
        )
        CAMERA_A = camera_a
        CAMERA_B = camera_b
        bpy.app.timers.register(verify_initial_state, first_interval=0.3)
    except Exception:
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
    return None


def verify_initial_state():
    global INITIAL_ZOOM
    try:
        scene = NEW_WINDOW.scene
        _area, space, region = _view_state()
        assert not NEW_WINDOW.screen.show_statusbar
        assert not space.show_region_header
        assert not space.show_region_toolbar
        assert not space.show_region_ui
        assert not space.show_region_tool_header
        assert not space.show_gizmo
        assert not space.overlay.show_overlays
        assert space.shading.type == "MATERIAL"
        assert space.use_local_camera
        assert space.camera == CAMERA_A
        assert space.region_3d.view_perspective == "CAMERA"
        expected = camera_cover_zoom(region.width, region.height, scene)
        print(
            "YUGO_VIEW_WINDOW_INITIAL",
            region.width,
            region.height,
            space.region_3d.view_camera_zoom,
            expected,
        )
        # RegionView3D stores camera zoom in quantized UI steps.
        assert math.isclose(space.region_3d.view_camera_zoom, expected, abs_tol=0.25)
        INITIAL_ZOOM = space.region_3d.view_camera_zoom

        # Simulate a main-camera switch and a render-aspect change. The running
        # ViewWindow must adopt both without reopening or user camera selection.
        scene.camera = CAMERA_B
        scene.render.resolution_x = 1080
        scene.render.resolution_y = 1920
        bpy.app.timers.register(verify_follow_state, first_interval=0.3)
    except Exception:
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
    return None


def verify_follow_state():
    exit_code = 0
    try:
        scene = NEW_WINDOW.scene
        _area, space, region = _view_state()
        assert space.camera == CAMERA_B
        assert space.region_3d.view_perspective == "CAMERA"
        expected = camera_cover_zoom(region.width, region.height, scene)
        print(
            "YUGO_VIEW_WINDOW_FOLLOW",
            region.width,
            region.height,
            space.region_3d.view_camera_zoom,
            expected,
        )
        assert math.isclose(space.region_3d.view_camera_zoom, expected, abs_tol=0.25)
        assert not math.isclose(INITIAL_ZOOM, expected, abs_tol=0.01)

        # The pure calculation also proves window-aspect changes produce a new
        # Cover zoom; the live manager keys its updates from these dimensions.
        scene.render.resolution_x = 1920
        scene.render.resolution_y = 1080
        wide_zoom = camera_cover_zoom(1920, 1080, scene)
        portrait_zoom = camera_cover_zoom(1080, 1920, scene)
        assert portrait_zoom > wide_zoom

        with bpy.context.temp_override(window=NEW_WINDOW):
            bpy.ops.screen.screenshot(
                filepath=r"D:\Projects\BCON26\motion_codedev\tools\yugo_view_window_test.png",
            )
        print("YUGO_VIEW_WINDOW_OK")

        with bpy.context.temp_override(window=NEW_WINDOW):
            bpy.ops.wm.window_close()
        yugo.unregister()
    except Exception:
        exit_code = 1
        traceback.print_exc()
    finally:
        bpy.context.window_manager["yugo_view_window_test_exit"] = exit_code
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run_test, first_interval=0.5)
