"""Exercise the entire fusion lifecycle, saved graph and shared legacy tools."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import FunctionType

import bpy
from _bpy_restrict_state import RestrictBlend

from _bootstrap import ROOT, LEGACY_PRESET_SHA256, load_node, output_directory, package_directory

addon = load_node()
root = addon.root
TREE = "YUGUS_NT_humanoid_motion"


def package_handlers(prefix="yugo."):
    return {name: tuple(fn for fn in getattr(bpy.app.handlers, name)
                        if getattr(fn, "__module__", "").startswith(prefix))
            for name in dir(bpy.app.handlers)
            if not name.startswith("_") and isinstance(getattr(bpy.app.handlers, name), list)}


def timer_functions(prefix="yugo."):
    found = {}
    for name, module in tuple(sys.modules.items()):
        if name.startswith(prefix):
            for value in tuple(vars(module).values()):
                if isinstance(value, FunctionType) and value.__module__.startswith(prefix):
                    found[id(value)] = value
    return tuple(found.values())


def signature(handlers):
    return {name: sorted((fn.__module__, fn.__qualname__) for fn in callbacks)
            for name, callbacks in handlers.items()}


def assert_runtime():
    node = root.node
    assert root._registered_modules == list(root.MODULES)
    assert node._registered
    assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE, None) is node.nodes.YUGUS_NT_humanoid_motion
    for scheduler in (root.scheduler, node.scheduler):
        assert bpy.app.timers.is_registered(scheduler._motion_timer)
        assert bpy.app.timers.is_registered(scheduler._system_timer)
    handlers = package_handlers()
    for name, callbacks in handlers.items():
        assert len(callbacks) == len(set(callbacks)), (name, callbacks)
        names = [(fn.__module__, fn.__qualname__) for fn in callbacks]
        assert len(names) == len(set(names)), (name, names)
    for owner in (bpy.types.NODE_MT_add, bpy.types.NODE_HT_header):
        callbacks = [fn for fn in getattr(owner.draw, "_draw_funcs", ())
                     if getattr(fn, "__module__", "").startswith("yugo.node.")]
        assert len(callbacks) == 1, callbacks
        assert callbacks[0].__module__ == node.__name__ + ".ui"
    return signature(handlers)


def assert_unregistered(old_functions):
    assert not root._registered_modules and not root.node._registered
    assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE, None) is None
    assert not hasattr(bpy.types.Scene, "yugu_node_studio_tree")
    assert not hasattr(bpy.types.Scene, "yugo_avatars")
    assert all(not callbacks for callbacks in package_handlers().values())
    assert all(not bpy.app.timers.is_registered(fn) for fn in old_functions)
    assert not root.node.scheduler._motion_callbacks and not root.node.scheduler._system_callbacks
    assert not root.node.physics._worlds and not root.node.flow._owners
    for name, module in tuple(sys.modules.items()):
        if name.startswith("yugo."):
            for value in tuple(vars(module).values()):
                if (isinstance(value, type) and getattr(value, "__module__", "").startswith("yugo.")
                        and hasattr(value, "is_registered")):
                    assert not value.is_registered, (name, value)
    for owner in (bpy.types.NODE_MT_add, bpy.types.NODE_HT_header):
        assert not [fn for fn in getattr(owner.draw, "_draw_funcs", ())
                    if getattr(fn, "__module__", "").startswith("yugo.node.")]


def inventory():
    path = Path(__file__).with_name("node_class_inventory.json")
    expected = json.loads(path.read_text(encoding="utf-8"))["classes"]
    available = set()
    tree = bpy.data.node_groups.new("FusionInventoryGraph", TREE)
    try:
        for entry in expected:
            module = sys.modules.get("yugo.node." + entry["module"])
            assert module is not None, entry
            cls = getattr(module, entry["class"])
            assert getattr(cls, "is_registered", False), entry
            base = next(base for base in (bpy.types.Node, bpy.types.NodeSocket, bpy.types.NodeTree)
                        if issubclass(cls, base))
            assert base.bl_rna_get_subclass_py(cls.bl_idname, None) is cls, entry
            if issubclass(cls, bpy.types.Node) and root.node.availability.is_available(cls.bl_idname):
                node = tree.nodes.new(cls.bl_idname)
                assert node.bl_idname == cls.bl_idname
                available.add(cls.bl_idname)
        advertised = {identifier for _title, entries in root.node.nodes.NODE_MENU for identifier, _label in entries}
        assert available >= advertised, sorted(advertised - available)
    finally:
        bpy.data.node_groups.remove(tree)
    panels = set()
    for name, module in tuple(sys.modules.items()):
        if name.startswith("yugo."):
            for value in tuple(vars(module).values()):
                if isinstance(value, type) and issubclass(value, bpy.types.Panel):
                    if getattr(value, "is_registered", False) and value.bl_region_type == "UI":
                        panels.add(value)
    assert panels
    assert {panel.bl_category for panel in panels} == {"Yugo"}
    assert {panel.bl_space_type for panel in panels} >= {"VIEW_3D", "NODE_EDITOR"}
    assert root.composing.addon.YUGO_COMPOSING_PT_main in panels
    assert root.node.studio.YUGUS_PT_node_composer in panels
    return len(expected), len(panels), len(available)


def shared_tools():
    node = root.node
    assert node.composing is root.composing
    assert node.mtoon_optimize is root.mtoon_optimize
    assert node.outline_bake is root.outline_bake
    assert node.studio.composing is root.composing.runtime
    assert node.studio.composing_ui is root.composing.addon
    for path in (root.composing.runtime._preset_path(), root.mtoon_optimize._preset_path(),
                 root.outline_bake._preset_path()):
        assert Path(path).resolve() == package_directory() / "preset.blend", path
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == LEGACY_PRESET_SHA256
    assert not hasattr(bpy.types.Scene, "yugus_composing")
    assert not hasattr(bpy.types.Scene, "yugus_avatars")
    rig = bpy.data.objects.new("FusionSharedRig", bpy.data.armatures.new("FusionSharedRig"))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    before = len(bpy.context.scene.yugo_avatars)
    assert bpy.ops.yugu_node_studio.composer_character(action="ADD") == {"FINISHED"}
    assert len(bpy.context.scene.yugo_avatars) == before + 1
    avatar = bpy.context.scene.yugo_avatars[bpy.context.scene.yugo_avatar_index]
    assert avatar.armature is rig
    assert bpy.ops.yugu_node_studio.composer_reload() == {"FINISHED"}
    assert bpy.context.scene.yugo_composing.initialized
    material = node.outline_bake._append_outline_material()
    assert material.name.startswith(node.outline_bake.OUTLINE_MATERIAL_NAME)
    bpy.data.materials.remove(material)
    return rig


def graph_snapshot(tree):
    return {"nodes": sorted((node.name, node.bl_idname, str(getattr(node, "runtime_id", "")),
                             tuple((socket.name, socket.identifier) for socket in node.inputs),
                             tuple((socket.name, socket.identifier) for socket in node.outputs))
                            for node in tree.nodes),
            "links": sorted((link.from_node.name, link.from_socket.identifier,
                             link.to_node.name, link.to_socket.identifier) for link in tree.links)}


def saved_graph():
    assert bpy.ops.yugu_node_studio.new_graph() == {"FINISHED"}
    tree = bpy.context.scene.yugu_node_studio_tree
    tree.name = "FusionSavedGraph"
    tree.use_fake_user = True
    first = tree.nodes.new("YUGUS_NODE_float_value")
    first.value = 7.25
    second = tree.nodes.new("YUGUS_NODE_math")
    second.operation = "MULTIPLY"
    second.inputs["B"].default_value = 3.5
    tree.links.new(first.outputs["Value"], second.inputs["A"])
    source = tree.nodes.new("YUGUS_NODE_vmc_source")
    tree.links.new(first.outputs["Value"], source.inputs["Connect"])
    # Background scripts do not advance the UI timer that normalizes freshly
    # constructed legacy-compatible nodes. Snapshot the canonical layout.
    root.node.nodes.ensure_current_sockets()
    before = graph_snapshot(tree)
    path = output_directory() / "fusion-saved.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    tree = bpy.data.node_groups["FusionSavedGraph"]
    assert graph_snapshot(tree) == before, {"before": before, "after": graph_snapshot(tree)}
    assert bpy.context.scene.yugu_node_studio_tree is tree
    assert bpy.context.scene.yugo_avatars[0].armature.name == "FusionSharedRig"
    assert not root.node.scheduler.is_suspended() and not root.scheduler.is_suspended()
    return before


def load_independent_studio():
    source = ROOT / "addons/yugu_node_studio"
    name = "fusion_independent_studio"
    spec = importlib.util.spec_from_file_location(name, source / "__init__.py",
                                                 submodule_search_locations=[str(source)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def expect_conflict(register):
    try:
        with RestrictBlend():
            register()
    except RuntimeError as error:
        assert "enabled" in str(error) or "Disable" in str(error), str(error)
    else:
        raise AssertionError("A second package replaced the live Node owner")


original_studio_register = root.node.studio.register


def fail_after_studio_registration():
    original_studio_register()
    raise RuntimeError("Injected fusion Node studio registration failure")


# Exercise failure after Node classes, properties and schedulers already exist,
# including a child registration that completed before raising to its caller.
old_functions = timer_functions()
root.node.studio.register = fail_after_studio_registration
try:
    try:
        with RestrictBlend():
            root.register()
    except RuntimeError as error:
        assert "Injected fusion Node studio registration failure" in str(error), str(error)
    else:
        raise AssertionError("Injected registration failure was ignored")
finally:
    root.node.studio.register = original_studio_register
assert_unregistered(old_functions)


with RestrictBlend():
    root.register()
try:
    before_handlers = assert_runtime()
    counts = inventory()
    with RestrictBlend():
        root.register()
    assert assert_runtime() == before_handlers
    shared_tools()
    before_graph = saved_graph()
    studio = load_independent_studio()
    before_handlers = assert_runtime()
    expect_conflict(studio.register)
    assert not studio._registered
    assert assert_runtime() == before_handlers
    assert graph_snapshot(bpy.data.node_groups["FusionSavedGraph"]) == before_graph

    # Re-execute the same entry loader, exercising the package's complete cached
    # child retirement even when an extracted ZIP has no importable folder name.
    for _ in range(2):
        old_functions = timer_functions()
        root.__spec__.loader.exec_module(root)
        assert_unregistered(old_functions)
        with RestrictBlend():
            root.register()
        assert assert_runtime() == before_handlers
        inventory()
        tree = bpy.data.node_groups["FusionSavedGraph"]
        assert graph_snapshot(tree) == before_graph
        assert bpy.context.scene.yugu_node_studio_tree is tree
    for tree in tuple(bpy.data.node_groups):
        if tree.bl_idname == TREE:
            bpy.data.node_groups.remove(tree)
    old_functions = timer_functions()
    root.unregister()
    assert_unregistered(old_functions)

    # Reverse order: the independent historical Studio is now the live owner.
    with RestrictBlend():
        studio.register()
    try:
        studio_owner = bpy.types.NodeTree.bl_rna_get_subclass_py(TREE, None)
        studio_handlers = signature(package_handlers(studio.__name__ + "."))
        expect_conflict(root.register)
        assert not root._registered_modules
        assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE, None) is studio_owner
        assert studio._registered
        assert signature(package_handlers(studio.__name__ + ".")) == studio_handlers
        assert bpy.app.timers.is_registered(studio.scheduler._motion_timer)
        assert bpy.ops.yugu_node_studio.new_graph() == {"FINISHED"}
        tree = bpy.context.scene.yugu_node_studio_tree
        tree.nodes.new("YUGUS_NODE_vrm_data")
        for tree in tuple(bpy.data.node_groups):
            if tree.bl_idname == TREE:
                bpy.data.node_groups.remove(tree)
    finally:
        studio.unregister()
    with RestrictBlend():
        root.register()
    assert_runtime()
    print("FUSION_LIFECYCLE_OK " + json.dumps({"blender": bpy.app.version_string,
          "node_socket_tree_classes": counts[0], "single_category_panels": counts[1],
          "available_nodes_created": counts[2],
          "shared_legacy_tools": True, "save_load": True, "full_reloads": 2,
          "conflict_guard_both_orders": True, "registration_failure_retry": True}, sort_keys=True))
finally:
    old_functions = timer_functions()
    root.unregister()
    assert_unregistered(old_functions)
