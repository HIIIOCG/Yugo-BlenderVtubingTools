"""Real restricted enable, linked actions, saved graph and unload cleanup."""
import bpy
from _bpy_restrict_state import RestrictBlend

from _bootstrap import OUTPUT, assert_clean, finish, load

yugo, host, addon = load()
yugo.register()
originals = (host.runtime._evaluate_node, host.flow._action_input,
             host.flow._Pulse._receive, host.flow.YUGUS_NS_update_event.draw)
try:
    with RestrictBlend():
        addon.register()
        assert addon._registered
        assert addon.nodes.migrate_trees() is False
        assert bpy.app.timers.is_registered(addon._deferred_migrate)
    assert addon._deferred_migrate() is None
    # Calling a timer manually does not unregister it in Blender's dispatcher.
    addon._migrate(None)
    assert not bpy.app.timers.is_registered(addon._deferred_migrate)
    tree = bpy.data.node_groups.new("PhysX lifecycle", addon.data.TREE)
    tree.use_fake_user = True
    actor = tree.nodes.new(addon.data.SKELETON)
    actor.source_mode = 'BUILTIN'
    physics = tree.nodes.new(addon.data.PHYSICS)
    trigger = tree.nodes.new('YUGUS_NODE_button_trigger')
    tree.links.new(trigger.outputs['Trigger'], actor.inputs['Setup'])
    host.flow.emit_event(tree, trigger, 'Trigger')
    assert actor.binding_json and actor.rig is not None, actor.status
    assert not addon.runtime._worlds, "Setup must not launch the native worker"
    assert len(addon.bridge._bindings) == 4
    assert bpy.app.timers.is_registered(addon.runtime.tick)
    assert host.nodes.NUMERIC_PARAMETERS[addon.data.PHYSICS] == ('Root Support', 'root_support')
    # Simulate persisted obsolete ports without losing the authoring graph.
    physics.name = 'Preserved physics'
    physics.inputs.new(addon.data.EVENT, 'Build')
    physics.inputs.new(addon.data.EVENT, 'Step')
    physics.inputs.remove(physics.inputs['Root Support'])
    tree.links.new(trigger.outputs['Trigger'], physics.inputs['Play'])
    addon.unregister()
    assert_clean(addon, host)
    assert originals == (host.runtime._evaluate_node, host.flow._action_input,
                         host.flow._Pulse._receive, host.flow.YUGUS_NS_update_event.draw)
    with RestrictBlend():
        addon.register()
        assert bpy.app.timers.is_registered(addon._deferred_migrate)
    assert physics.inputs.get('Build') and physics.inputs.get('Step')
    assert addon._deferred_migrate() is None
    addon._migrate(None)
    assert not physics.inputs.get('Build') and not physics.inputs.get('Step')
    assert physics.inputs['Play'].links[0].from_node == trigger
    assert physics.inputs['Root Support'].default_value == 0
    assert actor.source_mode == 'BUILTIN'
    saved = OUTPUT / 'saved-graph.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    tree = bpy.data.node_groups['PhysX lifecycle']
    actor = next(node for node in tree.nodes if node.bl_idname == addon.data.SKELETON)
    physics = tree.nodes['Preserved physics']
    assert actor.source_mode == 'BUILTIN'
    assert physics.inputs['Play'].is_linked
    assert not addon.runtime._worlds, "Loading a saved graph must not auto-start physics"
    assert not addon.runtime.play_requested(physics)
    addon._invalidate(None)
    assert not addon.runtime._worlds
    addon.unregister()
    assert_clean(addon, host)
    assert originals == (host.runtime._evaluate_node, host.flow._action_input,
                         host.flow._Pulse._receive, host.flow.YUGUS_NS_update_event.draw)
    yugo.unregister()
    assert not yugo._registered_modules
    finish('lifecycle', restricted_enable=True, deferred_migration=True,
           linked_setup_action=True, reenable_keeps_play_link=True,
           saved_graph_load_manual_start=True, adapters_restored=True,
           timer_handlers_and_caches_cleared=True)
finally:
    addon.unregister()
    yugo.unregister()
