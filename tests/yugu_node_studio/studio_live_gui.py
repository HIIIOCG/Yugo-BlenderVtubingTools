"""Actual ZIP + real common timers in a disposable Blender GUI process."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile
import bpy
from _bpy_restrict_state import RestrictBlend

sys.dont_write_bytecode = True
folder = Path(os.environ['NODE_GUI_OUTPUT'])
report = folder / 'gui-result.json'
addon = None
result = {'status': 'failed', 'blender': bpy.app.version_string}


def finish(error=None):
    if error:
        result['error'] = error
    try:
        if addon is not None:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.type = 'VIEW_3D'
            with RestrictBlend():
                addon.unregister()
            assert not addon.extensions.claimed_transforms()
            assert not addon.scheduler._motion_callbacks
            assert not addon.scheduler._system_callbacks
            assert not addon.extensions.lifecycle_errors()
    except Exception:
        result['status'] = 'failed'
        result['cleanup_error'] = traceback.format_exc()
    report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    bpy.ops.wm.quit_blender()


def setup_layout(tree):
    window = bpy.context.window_manager.windows[0]
    area = max(window.screen.areas, key=lambda item: item.width * item.height)
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=.58)
    areas = sorted(window.screen.areas, key=lambda item: item.width * item.height, reverse=True)
    editor, viewport = areas[:2]
    editor.type = 'NODE_EDITOR'
    editor.ui_type = 'YUGUS_NT_humanoid_motion'
    editor.spaces.active.pin = True
    editor.spaces.active.show_region_ui = False
    editor.spaces.active.node_tree = tree
    region = next(item for item in editor.regions if item.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=editor, region=region):
        bpy.ops.node.view_all()
    viewport.type = 'VIEW_3D'
    region = next(item for item in viewport.regions if item.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=viewport, region=region):
        bpy.ops.view3d.view_all(center=True)


try:
    extracted = folder / 'installed-source'
    extracted.mkdir()
    with zipfile.ZipFile(os.environ['NODE_PACKAGE_PATH']) as archive:
        archive.extractall(extracted)
    spec = importlib.util.spec_from_file_location('gui_studio', extracted / '__init__.py',
                                                submodule_search_locations=[str(extracted)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    assert Path(addon.__file__).resolve() == extracted.resolve() / '__init__.py'
    with RestrictBlend():
        addon.register()
    floating = sys.modules['gui_studio.modules.floating_ui']
    physics = sys.modules['gui_studio.modules.physics']
    tree, message, display = floating.create_example(bpy.context)
    scene = bpy.context.scene
    floating.send_message(message, scene)
    floating.send_message(message, scene)
    service = floating.get_service(display, scene)
    assert service is not None and len(service.instances) == 2
    old_row = next(iter(service.instances.values())).root
    initial_row_y = old_row.location.y
    example = physics.create_example(scene)
    scene.frame_set(42, subframe=.25)
    initial_z = example['prop'].matrix_world.translation.z
    ticks = []
    original_update = physics.service.update
    def counted_update(delta):
        ticks.append(delta)
        return original_update(delta)
    physics.service.update = counted_update
    addon.flow.emit_event(example['tree'], example['controls'], 'Start')
    assert physics.service.active()
    setup_layout(tree)
    started = time.monotonic()
    phase = 'motion'
    reframed = False
    attraction = cancellation = None
    ragdoll_example = ragdoll = ragdoll_run = recovery_run = None
    hit_during_recovery = False

    def observe():
        global phase, attraction, cancellation, reframed
        global ragdoll_example, ragdoll, ragdoll_run, recovery_run, hit_during_recovery
        try:
            elapsed = time.monotonic() - started
            if elapsed >= .5 and not reframed:
                window = bpy.context.window_manager.windows[0]
                editor = next(area for area in window.screen.areas if area.type == 'NODE_EDITOR')
                region = next(item for item in editor.regions if item.type == 'WINDOW')
                with bpy.context.temp_override(window=window, area=editor, region=region):
                    bpy.ops.node.view_all()
                reframed = True
            assert elapsed < 30, 'Real-timer test timed out'
            assert scene.frame_current == 42 and abs(scene.frame_subframe - .25) < 1e-6
            if phase == 'motion' and elapsed >= 1.2 and len(ticks) >= 10:
                assert len(service.instances) == 2
                assert abs(old_row.location.y - initial_row_y) > .05
                assert example['prop'].matrix_world.translation.z < initial_z - .2
                result.update({'timer_updates': len(ticks), 'initial_z': initial_z,
                               'after_fall_z': example['prop'].matrix_world.translation.z,
                               'row_displacement': old_row.location.y - initial_row_y,
                               'frame': scene.frame_current, 'subframe': scene.frame_subframe})
                attraction = addon.flow.emit_event(example['tree'], example['controls'], 'Attract')
                assert addon.workflow.status(attraction)['state'] == 'running'
                phase = 'attraction'
            elif phase == 'attraction':
                status = addon.workflow.status(attraction)['state']
                if status == 'end':
                    assert physics.service.body_state(example['prop'])['recall_state'] == 'settled'
                    cancellation = addon.flow.emit_event(example['tree'], example['controls'], 'Attract')
                    assert addon.workflow.status(cancellation)['state'] == 'running'
                    addon.flow.emit_event(example['tree'], example['controls'], 'Stop Attraction')
                    phase = 'cancel'
                else:
                    assert status == 'running', addon.workflow.status(attraction)
            elif phase == 'cancel':
                status = addon.workflow.status(cancellation)['state']
                if status == 'cancelled':
                    addon.flow.emit_event(example['tree'], example['controls'], 'Stop')
                    assert not physics.service.active()
                    bpy.ops.screen.screenshot(filepath=str(folder / 'studio-features.png'))
                    result.update({'attraction': 'end after settled', 'second_attraction': 'cancelled'})
                    ragdoll = sys.modules['gui_studio.modules.ragdoll']
                    ragdoll_example = ragdoll.create_example(scene)
                    ragdoll_example['rig'].location.x = 2
                    bpy.context.view_layer.update()
                    ragdoll_run = addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Enter')
                    assert addon.workflow.status(ragdoll_run)['state'] == 'running'
                    result['ragdoll_initial_hips_z'] = ragdoll_example['rig'].pose.bones['hips'].head.z
                    phase = 'ragdoll'
                    window = bpy.context.window_manager.windows[0]
                    editor = next(area for area in window.screen.areas if area.type == 'NODE_EDITOR')
                    editor.spaces.active.node_tree = ragdoll_example['tree']
                    reframed = False
                    region = next(item for item in editor.regions if item.type == 'WINDOW')
                    with bpy.context.temp_override(window=window, area=editor, region=region):
                        bpy.ops.node.view_all()
                    return .05
                assert status == 'running', addon.workflow.status(cancellation)
            elif phase == 'ragdoll':
                def snapshot():
                    return addon.runtime.EvaluationContext(ragdoll_example['tree'], scene, 0).evaluate(
                        ragdoll_example['apply'])['Status']
                state = snapshot()
                assert snapshot() == state, 'Ragdoll reads advanced the solver'
                if state['status'] == 'RECOVERING' and not hit_during_recovery:
                    assert ragdoll_example['rig'].pose.bones['hips'].head.z < result['ragdoll_initial_hips_z'] - .2
                    recovery_run = addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Recover')
                    before = snapshot()
                    addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Hit')
                    after = snapshot()
                    assert before['session_id'] == after['session_id']
                    assert before['pose'] == after['pose']
                    assert after['status'] == 'PHYSICS'
                    hit_during_recovery = True
                    result['ragdoll_hit_kept_pose_and_session'] = True
                elif state['status'] == 'COMPLETED':
                    assert hit_during_recovery
                    assert addon.workflow.status(ragdoll_run)['state'] == 'end'
                    assert addon.workflow.status(recovery_run)['state'] == 'cancelled'
                    result['ragdoll_completion'] = 'end after stable recovery'
                    result['ragdoll_simulated_seconds'] = state['elapsed']
                    addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Stop')
                    ragdoll_run = addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Enter')
                    assert addon.workflow.status(ragdoll_run)['state'] == 'running'
                    addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Cancel')
                    phase = 'ragdoll_cancel'
                else:
                    assert state['status'] in {'PHYSICS', 'RECOVERING'}, state
            elif phase == 'ragdoll_cancel':
                state = addon.workflow.status(ragdoll_run)['state']
                if state == 'cancelled':
                    assert addon.extensions.transform_owner(ragdoll_example['rig']) is None
                    bpy.ops.screen.screenshot(filepath=str(folder / 'studio-live.png'))
                    assert (folder / 'studio-live.png').is_file()
                    tree_name = ragdoll_example['tree'].name
                    apply_name = ragdoll_example['apply'].name
                    window = bpy.context.window_manager.windows[0]
                    editor = next(area for area in window.screen.areas if area.type == 'NODE_EDITOR')
                    region = next(item for item in editor.regions if item.type == 'WINDOW')
                    with bpy.context.temp_override(window=window, area=editor, region=region):
                        assert bpy.ops.ed.undo_push(message='Studio ragdoll before edit') == {'FINISHED'}
                        ragdoll_example['tree']['qualification_edit'] = True
                        assert bpy.ops.ed.undo_push(message='Studio ragdoll after edit') == {'FINISHED'}
                        undo_run = addon.flow.emit_event(ragdoll_example['tree'], ragdoll_example['controls'], 'Enter')
                        assert addon.workflow.status(undo_run)['state'] == 'running'
                        assert bpy.ops.ed.undo() == {'FINISHED'}
                        restored = bpy.data.node_groups[tree_name]
                        assert not restored.get('qualification_edit')
                        assert ragdoll.get_status(restored.nodes[apply_name])['status'] == 'STOPPED'
                        assert not addon.extensions.claimed_transforms()
                        assert bpy.ops.ed.redo() == {'FINISHED'}
                        restored = bpy.data.node_groups[tree_name]
                        assert restored.get('qualification_edit')
                        assert ragdoll.get_status(restored.nodes[apply_name])['status'] == 'STOPPED'
                        assert not addon.extensions.claimed_transforms()
                    assert bpy.context.scene.frame_current == 42
                    assert abs(bpy.context.scene.frame_subframe - .25) < 1e-6
                    result.update({'status': 'passed', 'source': addon.__file__,
                                   'ragdoll_cancel': 'cancelled and released', 'actual_undo_redo': 'inactive, no leases',
                                   'elapsed': elapsed})
                    finish()
                    return None
                assert state == 'running', addon.workflow.status(ragdoll_run)
            return .05
        except Exception:
            finish(traceback.format_exc())
            return None

    bpy.app.timers.register(observe, first_interval=.2)
except Exception:
    finish(traceback.format_exc())
