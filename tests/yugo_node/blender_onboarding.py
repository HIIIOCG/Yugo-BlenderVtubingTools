"""Qualify the real first-use operators without network or private avatars.

The core test uses a generated native rig. An explicitly provisioned official
VRM extension adds real VRM 0.x/1.0 PropertyGroup reload coverage; its absence is
recorded as unrun supplemental coverage, never as a real VRM pass.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch
import webbrowser

import bpy
from _bpy_restrict_state import RestrictBlend
from mathutils import Quaternion

from _bootstrap import load_node, output_directory

addon = load_node()
root = addon.root
TREE = "YUGUS_NT_humanoid_motion"
SOURCE_TYPES = {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}


def trees():
    return tuple(tree for tree in bpy.data.node_groups if tree.bl_idname == TREE)


def structure(tree):
    return (tuple(sorted((node.name, node.bl_idname, node.as_pointer()) for node in tree.nodes)),
            tuple(sorted((link.from_node.name, link.from_socket.identifier,
                          link.to_node.name, link.to_socket.identifier) for link in tree.links)))


def state():
    selected = bpy.context.scene.yugu_node_studio_tree
    return (tuple((tree.as_pointer(), structure(tree)) for tree in trees()),
            selected.as_pointer() if selected else None,
            tuple(sorted(obj.as_pointer() for obj in bpy.data.objects)),
            len(bpy.context.scene.yugo_avatars))


def one(tree, kind):
    matches = [node for node in tree.nodes if node.bl_idname == kind]
    assert len(matches) == 1, (kind, [node.bl_idname for node in tree.nodes])
    return matches[0]


def linked(tree, source, output, target, input_name):
    assert any(link.from_node == source and link.from_socket == source.outputs[output]
               and link.to_node == target and link.to_socket == target.inputs[input_name]
               and link.is_valid and not link.is_muted for link in tree.links), (source.name, output, target.name, input_name)


def make_character(name):
    bpy.ops.object.select_all(action="DESELECT")
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("hips")
    hips.head, hips.tail = (0, 0, 0), (0, 0, 1)
    head = data.edit_bones.new("head")
    head.head, head.tail, head.parent = (0, 0, 1), (0, 0, 2), hips
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh = bpy.data.meshes.new(name + "Face")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    face = bpy.data.objects.new(name + "Face", mesh)
    bpy.context.scene.collection.objects.link(face)
    face.parent = rig
    face.shape_key_add(name="Basis")
    face.shape_key_add(name="Smile").value = 0.25
    return rig, face


class LayoutRecorder:
    """Capture actual draw calls; native Blender operators are tested separately."""
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls

    def row(self, **_kwargs):
        return LayoutRecorder(self.calls)

    column = box = row

    def split(self, **_kwargs):
        return self.row()

    def operator(self, identifier, **kwargs):
        self.calls.append(("operator", identifier, kwargs))
        return SimpleNamespace()

    def label(self, **kwargs):
        self.calls.append(("label", kwargs.get("text", "")))

    def separator(self, **_kwargs):
        pass

    def template_list(self, *_args, **_kwargs):
        pass

    template_ID = template_list

    def prop(self, *_args, **_kwargs):
        pass


def test_empty_avatar_entry():
    assert not bpy.context.scene.yugo_avatars
    layout = LayoutRecorder()
    context = SimpleNamespace(scene=bpy.context.scene, region=SimpleNamespace(width=380))
    root.panels.YUGO_PT_avatar.draw(SimpleNamespace(layout=layout), context)
    entries = [call for call in layout.calls if call[0] == "operator"]
    assert any(call[1] == "yugu_node_studio.open_editor" and call[2].get("text") == "Open Yugo Node Editor" for call in entries)
    assert any(call[1] == "yugo.fast_started" for call in entries)
    assert any(call[1] == "yugo.help_placeholder" for call in entries)
    assert bpy.ops.yugu_node_studio.open_editor.get_rna_type().name == "Open Yugo Node Editor"


def test_editor_area():
    window = bpy.context.window
    assert window is not None and window.screen.areas, "Factory-startup screen is required for actual area switching"
    area = next((item for item in window.screen.areas if item.type == "VIEW_3D"), window.screen.areas[0])
    original_type = area.type
    before = len(trees())
    with bpy.context.temp_override(window=window, area=area):
        assert bpy.ops.yugu_node_studio.open_editor() == {"FINISHED"}
        assert area.type == "NODE_EDITOR" and area.spaces.active.tree_type == TREE
        created = bpy.context.scene.yugu_node_studio_tree
        assert created is not None and len(trees()) == before + 1
        assert area.spaces.active.edit_tree is created
        assert one(created, "YUGUS_NODE_hz_trigger").mute, "A first-use graph must begin paused"
        before_layout = structure(created)
        bpy.context.scene.yugu_node_studio_tree = None
        assert bpy.ops.yugu_node_studio.open_editor() == {"FINISHED"}
        assert bpy.context.scene.yugu_node_studio_tree is created, "Fallback reuse must synchronize the scene pointer"
        assert structure(created) == before_layout and len(trees()) == before + 1
        custom = bpy.data.node_groups.new("OnboardingExistingGraph", TREE)
        first = custom.nodes.new("YUGUS_NODE_float_value")
        second = custom.nodes.new("YUGUS_NODE_math")
        first.value = 7.25
        custom.links.new(first.outputs["Value"], second.inputs["A"])
        expected = structure(custom)
        bpy.context.scene.yugu_node_studio_tree = custom
        count = len(trees())
        assert bpy.ops.yugu_node_studio.open_editor() == {"FINISHED"}
        assert area.spaces.active.edit_tree is custom
        assert bpy.context.scene.yugu_node_studio_tree is custom
        assert structure(custom) == expected and len(trees()) == count, "Opening the editor rewrote an existing graph"
    area.type = original_type


def assert_graph(tree, rig, face_capture, body_motion):
    assert bpy.context.scene.yugu_node_studio_tree is tree
    data = one(tree, "YUGUS_NODE_vrm_data")
    assert data.armature is rig and data.cache_status == "READY"
    snapshot = root.node.vrm_cache.get_snapshot(data)
    assert snapshot and snapshot.armature_name == rig.name
    assert snapshot.bone_map.by_humanoid["hips"] == "hips"
    clock = one(tree, "YUGUS_NODE_hz_trigger")
    assert clock.mute
    assert root.quick_start.starter_clock(tree) == clock
    unrelated = tree.nodes.new("YUGUS_NODE_hz_trigger")
    unrelated.mute = True
    assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
    assert not clock.mute and unrelated.mute
    assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
    assert clock.mute and unrelated.mute, "Output control changed an unrelated clock"
    tree.nodes.remove(unrelated)
    for panel in (root.panels.YUGO_PT_avatar, root.panels.YUGO_PT_node_start):
        layout = LayoutRecorder()
        context = SimpleNamespace(scene=bpy.context.scene, region=SimpleNamespace(width=380),
                                  space_data=SimpleNamespace(tree_type=TREE,
                                                            type="NODE_EDITOR" if panel is root.panels.YUGO_PT_node_start else "VIEW_3D",
                                                            edit_tree=tree))
        panel.draw(SimpleNamespace(layout=layout), context)
        assert any(call[0] == "operator" and call[1] == "yugo.starter_output_toggle"
                   and call[2].get("text") == "Start Outputs" for call in layout.calls)
        has_view_window = any(call[0] == "operator" and call[1] == "yugo.view_window" for call in layout.calls)
        assert has_view_window == (panel is root.panels.YUGO_PT_avatar), "A Node editor advertises a viewport-only action"
    sources = [node for node in tree.nodes if node.bl_idname in SOURCE_TYPES]
    assert sum(node.bl_idname == "YUGUS_NODE_vmc_source" for node in sources) == int(face_capture == "VMC" or body_motion == "VMC")
    assert sum(node.bl_idname == "YUGUS_NODE_ifacial_source" for node in sources) == int(face_capture == "IFACIALMOCAP")
    assert all(not node.connected and not root.node.network.is_running(str(node.runtime_id)) for node in sources)
    for source in sources:
        assert source.address == "127.0.0.1"
        if source.bl_idname == "YUGUS_NODE_vmc_source":
            assert source.port == 39539 and source.inputs["Address"].default_value == "127.0.0.1"
            assert source.inputs["Port"].default_value == 39539
        else:
            assert source.device_port == 49983 and source.listen_address == "0.0.0.0" and source.listen_port == 49986
            assert source.inputs["iPhone Address"].default_value == "127.0.0.1" and source.inputs["Device Port"].default_value == 49983
    assert not root.node.network._vmc_receivers and not root.node.network._ifacial_receivers
    buttons = {node.label: node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_button_trigger"}
    assert set(buttons) == {"Connect receivers", "Disconnect receivers", "Reload character"}
    for source in sources:
        linked(tree, buttons["Connect receivers"], "Trigger", source, "Connect")
        linked(tree, buttons["Disconnect receivers"], "Trigger", source, "Disconnect")
    linked(tree, buttons["Reload character"], "Trigger", data, "Reload")
    assert any(node.bl_idname == "NodeFrame" and node.label for node in tree.nodes), "Starter instructions are missing"
    motion = body_motion == "VMC" or face_capture == "IFACIALMOCAP"
    if motion:
        convert = one(tree, "YUGUS_NODE_motion_to_rotation")
        apply = one(tree, "YUGUS_NODE_apply_rotation")
        assert apply.enabled
        linked(tree, data, "Bone Map", convert, "Bone Map")
        linked(tree, data, "Armatures", apply, "Armatures")
        linked(tree, convert, "Bone Transform", apply, "Bone Transform")
        linked(tree, clock, "Trigger", apply, "Enter")
        if body_motion == "VMC" and face_capture == "IFACIALMOCAP":
            mixer = one(tree, "YUGUS_NODE_mixer")
            assert mixer.data_type == "HUMANOID_MOTION"
            factor = next(socket for socket in mixer.inputs if getattr(socket, "mixer_slot", "") == "Humanoid Motion Factor")
            assert factor.default_value == 1.0
            linked(tree, one(tree, "YUGUS_NODE_vmc_source"), "Humanoid Motion", mixer, "Humanoid Motion A")
            linked(tree, one(tree, "YUGUS_NODE_ifacial_source"), "Humanoid Motion", mixer, "Humanoid Motion B")
            linked(tree, mixer, "Humanoid Motion Result", convert, "Motion")
        else:
            source = one(tree, "YUGUS_NODE_vmc_source" if body_motion == "VMC" else "YUGUS_NODE_ifacial_source")
            linked(tree, source, "Humanoid Motion", convert, "Motion")
    else:
        assert not any(node.bl_idname == "YUGUS_NODE_apply_rotation" for node in tree.nodes)
    if face_capture != "NONE":
        convert = one(tree, "YUGUS_NODE_expression_to_shapes")
        apply = one(tree, "YUGUS_NODE_apply_shapes")
        assert apply.enabled
        linked(tree, data, "Expression Map", convert, "Expression Map")
        linked(tree, data, "Objects", apply, "Objects")
        linked(tree, convert, "Shape Keys Values", apply, "Shape Keys Values")
        linked(tree, clock, "Trigger", apply, "Enter")
        linked(tree, one(tree, "YUGUS_NODE_vmc_source" if face_capture == "VMC" else "YUGUS_NODE_ifacial_source"), "Expressions", convert, "Expressions")
    else:
        assert not any(node.bl_idname == "YUGUS_NODE_apply_shapes" for node in tree.nodes)
    revision = data.cache_revision
    assert bpy.ops.yugu_node_studio.button_trigger(tree_name=tree.name, node_name=buttons["Reload character"].name) == {"FINISHED"}
    root.node.flow.tick(now=10.0)
    assert data.cache_status == "READY" and data.cache_revision > revision, "The starter's reload event did not refresh its configured character"
    assert not root.node.network._vmc_receivers and not root.node.network._ifacial_receivers
    return data


def test_starters(rig, face):
    before = len(trees())
    assert bpy.ops.yugo.fast_started(character=rig.name) == {"FINISHED"}
    tree = bpy.context.scene.yugu_node_studio_tree
    assert len(trees()) == before + 1
    assert_graph(tree, rig, "NONE", "VMC")
    for face_capture, body_motion in (("VMC", "VMC"), ("IFACIALMOCAP", "VMC"), ("IFACIALMOCAP", "NONE")):
        before_layout = structure(tree)
        created = root.quick_start.create_starter(bpy.context, rig.name, face_capture, body_motion)
        assert created is not tree and structure(tree) == before_layout
        assert_graph(created, rig, face_capture, body_motion)
        assert created.get("yugo_onboarding_warning"), "Unmapped native facial targets must be explained"
    pose = tuple(tuple(bone.matrix_basis) for bone in rig.pose.bones)
    shape = face.data.shape_keys.key_blocks["Smile"].value
    for now in (11.0, 60.0, 120.0):
        root.node.flow.tick(now=now)
    assert tuple(tuple(bone.matrix_basis) for bone in rig.pose.bones) == pose
    assert face.data.shape_keys.key_blocks["Smile"].value == shape
    test_output_writes(tree, rig)


def matrix_values(bone):
    return tuple(value for row in bone.matrix_basis for value in row)


def test_output_writes(tree, rig):
    """Follow the created VMC→converter→Apply route through actual clock ticks."""
    from yugo.node.data import ExpressionFrame, HumanoidMotion, HumanoidTransform
    bpy.context.scene.yugu_node_studio_tree = tree
    clock = root.quick_start.starter_clock(tree)
    apply = one(tree, "YUGUS_NODE_apply_rotation")
    previous_graphs = tuple((item.as_pointer(), structure(item)) for item in trees())
    baseline = matrix_values(rig.pose.bones["head"])
    first = HumanoidMotion({"head": HumanoidTransform(rotation=Quaternion((0, 0, 1), 0.45))})
    second = HumanoidMotion({"head": HumanoidTransform(rotation=Quaternion((0, 0, 1), -0.8))})
    with patch.object(root.node.network, "source_snapshot", return_value=(first, ExpressionFrame())) as packets:
        root.node.flow.tick(now=200.0)
        assert matrix_values(rig.pose.bones["head"]) == baseline
        assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
        root.node.flow.tick(now=201.0)
        root.node.flow.tick(now=201.1)
        written = matrix_values(rig.pose.bones["head"])
        assert any(abs(a - b) > 1e-4 for a, b in zip(written, baseline)), "Start Outputs did not write the starter's mapped pose"
        assert root.node.flow._owners.get(str(apply.runtime_id)), "Started output did not own its target"
        assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
        packets.return_value = (second, ExpressionFrame())
        root.node.flow.tick(now=202.0)
        paused = matrix_values(rig.pose.bones["head"])
        assert all(abs(a - b) < 1e-6 for a, b in zip(paused, baseline)), "Pause did not restore the owned pose"
        assert str(apply.runtime_id) not in root.node.flow._owners, "Pause retained target ownership"
        root.node.flow.tick(now=203.0)
        root.node.flow.tick(now=240.0)
        assert matrix_values(rig.pose.bones["head"]) == paused, "Paused output consumed later pose data"
    assert clock.mute and tuple((item.as_pointer(), structure(item)) for item in trees()) == previous_graphs
    assert not root.node.network._vmc_receivers and not root.node.network._ifacial_receivers


def test_editor_targeting(rig):
    """A pinned editor must control its visible graph when Scene picks another."""
    first = root.quick_start.create_starter(bpy.context, rig.name, "NONE", "VMC")
    second = root.quick_start.create_starter(bpy.context, rig.name, "NONE", "VMC")
    first_clock, second_clock = root.quick_start.starter_clock(first), root.quick_start.starter_clock(second)
    original = (structure(first), structure(second))
    window = bpy.context.window
    area = next((item for item in window.screen.areas if item.type == "VIEW_3D"), window.screen.areas[0])
    original_type = area.type
    with bpy.context.temp_override(window=window, area=area):
        area.type = "NODE_EDITOR"
        area.spaces.active.tree_type = TREE
        area.spaces.active.pin = True
        area.spaces.active.path.start(second)
        bpy.context.scene.yugu_node_studio_tree = first
        assert area.spaces.active.edit_tree is second
        layout = LayoutRecorder()
        root.panels.draw_starter_controls(layout, bpy.context)
        assert ("label", second.name) in layout.calls and ("label", first.name) not in layout.calls
        assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
        assert first_clock.mute and not second_clock.mute, "Control toggled the Scene graph instead of the visible pinned graph"
        assert bpy.ops.yugo.starter_output_toggle() == {"FINISHED"}
        assert first_clock.mute and second_clock.mute
    area.type = original_type
    assert (structure(first), structure(second)) == original


def test_rejections(rig, face):
    elsewhere = bpy.data.objects.new("OnboardingOtherSceneRig", bpy.data.armatures.new("OnboardingOtherSceneRig"))
    other_scene = bpy.data.scenes.new("OnboardingOtherScene")
    other_scene.collection.objects.link(elsewhere)
    empty = bpy.data.objects.new("OnboardingUnmappedRig", bpy.data.armatures.new("OnboardingUnmappedRig"))
    bpy.context.scene.collection.objects.link(empty)
    for character, face_capture, body_motion in (("MissingRig", "NONE", "VMC"), (face.name, "NONE", "VMC"),
                                                (elsewhere.name, "NONE", "VMC"), (rig.name, "NONE", "NONE"),
                                                (rig.name, "STEAMVR", "VMC"), (rig.name, "NONE", "ROKOKO"),
                                                (empty.name, "NONE", "VMC"), (rig.name, "VMC", "NONE")):
        before = state()
        try:
            root.quick_start.create_starter(bpy.context, character, face_capture, body_motion)
        except ValueError:
            pass
        else:
            raise AssertionError(("Unsupported setup was accepted", character, face_capture, body_motion))
        assert state() == before, "Rejected setup changed graphs, targets or the scene selection"
    for settings in ({"body_motion": "ROKOKO"}, {"face_capture": "NONE", "body_motion": "NONE"},
                     {"camera": "FOLLOW_CHARACTER"}, {"fx": "AUTO"}, {"keybinds": "AUTO"}):
        before = state()
        try:
            result = bpy.ops.yugo.fast_started(character=rig.name, **settings)
        except (RuntimeError, TypeError) as error:
            assert settings.get("body_motion") == "ROKOKO" or "Error:" in str(error), str(error)
        else:
            assert result == {"CANCELLED"}, settings
        assert state() == before, ("Operator rejection mutated existing data", settings)
    before = state()
    caches = set(root.node.vrm_cache._snapshots)
    with patch.object(root.node.vrm_cache, "reload_node", side_effect=ValueError("Injected character cache failure")):
        try:
            root.quick_start.create_starter(bpy.context, rig.name, "NONE", "VMC")
        except ValueError as error:
            assert "Injected character cache failure" in str(error), str(error)
        else:
            raise AssertionError("A failed character snapshot was published")
    assert state() == before and set(root.node.vrm_cache._snapshots) == caches, "Rollback touched existing graphs or caches"


def test_help():
    with patch.object(webbrowser, "open", return_value=True) as browser:
        assert bpy.ops.yugo.help_placeholder() == {"FINISHED"}
        assert browser.call_count == 1
        assert browser.call_args.args[0] == "https://hiiiodigital.com/Yugo-Docs/"


def test_official_vrm():
    fixture = os.environ.get("FUSION_TEST_VRM_SOURCE")
    if not fixture:
        print("ONBOARDING_VRM_SCHEMA_SKIPPED: --vrm-source prerequisite not supplied", flush=True)
        return {"status": "not_run", "reason": "Official VRM extension not provisioned; core native rig coverage passed"}
    folder = Path(fixture).resolve(strict=True)
    record = json.loads(Path(os.environ["FUSION_TEST_VRM_RECORD"]).read_text())
    assert str(folder) == record["copy"]
    for name, expected in record["source_files_sha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected, name
    name = "onboarding_official_vrm"
    spec = importlib.util.spec_from_file_location(name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == folder / "__init__.py"
    assert ".".join(map(str, module.bl_info["version"])) == record["version"]
    # Normal enablement creates the AddonPreferences entry before register.
    # All preferences and extension bytes live in this process's private profile.
    preferences = bpy.context.preferences.addons.new()
    preferences.module = name
    module.register()
    try:
        rig, face = make_character("OnboardingOfficialVRM")
        extension = rig.data.vrm_addon_extension
        extension.spec_version = "1.0"
        extension.vrm1.humanoid.human_bones.hips.node.bone_name = "hips"
        extension.vrm1.humanoid.human_bones.head.node.bone_name = "head"
        extension.vrm1.meta.name = "Synthetic VRM1 Starter"
        partial = root.quick_start.create_starter(bpy.context, rig.name, "VMC", "VMC")
        assert root.quick_start.starter_warnings(partial), "Initially missing facial binds were not explained"
        expression = extension.vrm1.expressions.custom.add()
        expression.custom_name = "StarterSmile"
        bind = expression.morph_target_binds.add()
        bind.node.mesh_object_name, bind.index, bind.weight = face.name, "Smile", 0.75
        partial_data = one(partial, "YUGUS_NODE_vrm_data")
        reload_button = next(node for node in partial.nodes if node.label == "Reload character")
        assert bpy.ops.yugu_node_studio.button_trigger(tree_name=partial.name, node_name=reload_button.name) == {"FINISHED"}
        root.node.flow.tick(now=260.0)
        assert partial_data.cache_status == "READY" and not root.quick_start.starter_warnings(partial), "Reload did not clear outdated facial-map instructions"
        tree = root.quick_start.create_starter(bpy.context, rig.name, "VMC", "VMC")
        data = assert_graph(tree, rig, "VMC", "VMC")
        first = root.node.vrm_cache.get_snapshot(data)
        assert first.vrm_version == "1.0" and first.meta.values["name"] == "Synthetic VRM1 Starter"
        assert first.expression_map.definitions["startersmile"].morph_targets[0].shape_key_name == "Smile"
        assert not tree.get("yugo_onboarding_warning")
        assert not root.quick_start.starter_warnings(tree)
        face_only = root.quick_start.create_starter(bpy.context, rig.name, "VMC", "NONE")
        assert_graph(face_only, rig, "VMC", "NONE")
        bpy.context.scene.yugu_node_studio_tree = tree
        extension.spec_version = "0.0"
        extension.vrm0.meta.title = "Synthetic VRM0 Reload"
        for canonical in ("hips", "head"):
            bone = extension.vrm0.humanoid.human_bones.add()
            bone.bone = canonical
            bone.node.bone_name = canonical
        group = extension.vrm0.blend_shape_master.blend_shape_groups.add()
        group.name = "StarterLegacySmile"
        group.preset_name = "unknown"
        bind = group.binds.add()
        bind.mesh.mesh_object_name, bind.index, bind.weight = face.name, "Smile", 0.5
        before = structure(tree)
        revision = data.cache_revision
        assert bpy.ops.yugu_node_studio.reload_vrm(tree_name=tree.name, node_name=data.name) == {"FINISHED"}
        snapshot = root.node.vrm_cache.get_snapshot(data)
        assert snapshot.vrm_version == "0.0" and snapshot.meta.values["name"] == "Synthetic VRM0 Reload"
        assert snapshot.expression_map.definitions["starterlegacysmile"].morph_targets[0].shape_key_name == "Smile"
        assert data.cache_status == "READY" and data.cache_revision == revision + 1
        assert structure(tree) == before and data.armature is rig
        assert not root.node.network._vmc_receivers and not root.node.network._ifacial_receivers
        print(f"ONBOARDING_OFFICIAL_VRM_OK: {record['version']}: {record['manifest_sha256']}: {folder}", flush=True)
        return {"status": "passed", "version": record["version"], "source": record["source"],
                "copy": str(folder), "manifest_sha256": record["manifest_sha256"],
                "tree_sha256": record["tree_sha256"], "schemas": ["1.0", "0.0"]}
    finally:
        module.unregister()
        bpy.context.preferences.addons.remove(preferences)


with RestrictBlend():
    addon.register()
try:
    test_empty_avatar_entry()
    test_editor_area()
    rig, face = make_character("OnboardingNativeRig")
    with patch.object(root.node.network, "start_vmc", side_effect=AssertionError("Onboarding started a VMC receiver")), \
         patch.object(root.node.network, "start_ifacial", side_effect=AssertionError("Onboarding started an iFacial receiver")):
        test_starters(rig, face)
        test_rejections(rig, face)
        test_editor_targeting(rig)
        test_help()
        official = test_official_vrm()
    (output_directory() / "onboarding.json").write_text(json.dumps({"core": "passed", "official_vrm": official}, indent=2) + "\n")
    print("YUGO_ONBOARDING_OK", flush=True)
finally:
    addon.unregister()
