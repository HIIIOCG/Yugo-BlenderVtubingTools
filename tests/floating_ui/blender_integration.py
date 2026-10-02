"""Real Blender regression checks for the standalone Floating UI service.

Example (use task-specific BLENDER_USER_* directories):
  blender --background --factory-startup --python tests/floating_ui/blender_integration.py

All materials are generated locally in artifacts/floating_ui. No live preset,
installed add-on, platform account, port, or user's Blender scene is accessed.
"""
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture import ROLE, artifacts_dir, counts, image_file, make_template
from yugo_floating_ui.service import OWNER, FloatingUIService, load_template


def instance_objects(service, message_id):
    """Inspect actual scene data rather than private runtime container fields."""
    instance = service.instances[message_id]
    collection = getattr(instance, "collection", None)
    if collection is None and isinstance(instance, dict):
        collection = instance.get("collection")
    if collection is not None:
        return list(collection.all_objects)
    objects = getattr(instance, "objects", None)
    if objects is None and isinstance(instance, dict):
        objects = instance.get("objects")
    if objects is None:
        raise AssertionError("instances must expose their owned collection or objects")
    return list(objects.values()) if isinstance(objects, dict) else list(objects)


def role_object(service, message_id, role):
    matches = [obj for obj in instance_objects(service, message_id) if obj.get(ROLE) == role]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {role!r} object, got {len(matches)}")
    return matches[0]


def avatar_image(service, message_id):
    icon = role_object(service, message_id, "icon")
    for mat in icon.data.materials:
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    return node.image
    return None


def assert_matrix_close(test, left, right, places=5):
    for row in range(4):
        for col in range(4):
            test.assertAlmostEqual(left[row][col], right[row][col], places=places)


class FloatingUIBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = artifacts_dir()
        cls.avatar_a = image_file(cls.artifacts / "integration_avatar_a.png", (0.1, 0.7, 0.8))
        cls.avatar_b = image_file(cls.artifacts / "integration_avatar_b.png", (0.8, 0.2, 0.5))

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.original_image = bpy.data.images.load(self.avatar_a)
        self.template, self.parts = make_template(image=self.original_image, transformed=True)
        self.anchor = bpy.data.objects.new("Chat Anchor", None)
        self.scene.collection.objects.link(self.anchor)
        self.anchor.matrix_world = Matrix.Translation((2.0, -1.0, 3.0)) @ Matrix.Rotation(0.3, 4, "Y")
        self.service = None

    def tearDown(self):
        if self.service:
            self.service.cleanup()

    def create_service(self, **kwargs):
        options = {"anchor": self.anchor, "max_messages": 3, "spacing": 1.4, "move_duration": 1.0}
        options.update(kwargs)
        self.service = FloatingUIService(self.scene, self.template, **options)
        return self.service

    def preview_graph(self, preview):
        tree = bpy.data.node_groups.new("Integration Preview", preview.TREE_ID)
        tree.use_fake_user = True
        source = tree.nodes.new(preview.SOURCE_ID)
        source.text = "節點送出的一則留言"
        source.author = "測試觀眾"
        source.avatar_path = self.avatar_b
        display = tree.nodes.new(preview.DISPLAY_ID)
        display.template = self.template
        display.anchor = self.anchor
        tree.links.new(source.outputs[0], display.inputs[0])
        return tree, source, display

    def assert_no_generated_data(self):
        for database in (bpy.data.objects, bpy.data.collections, bpy.data.curves,
                         bpy.data.meshes, bpy.data.materials, bpy.data.images):
            tagged = [block.name for block in database if block.get(OWNER)]
            self.assertFalse(tagged, f"leftover generated data: {tagged}")

    def test_clone_native_text_and_independent_avatar_materials(self):
        baseline = counts()
        source_text = self.parts["message"].data
        source_body, source_size = source_text.body, source_text.size
        source_mat = self.parts["icon"].data.materials[0]
        source_image = source_mat.node_tree.nodes["Avatar"].image
        service = self.create_service()
        first = service.push({"id": "first", "text": "你好，Blender！\n原生文字測試", "author": "小雨",
                              "avatar_path": self.avatar_a}, now=0.0)
        second = service.push({"id": "second", "text": "另一則留言", "author": "Yugo",
                               "avatar_path": self.avatar_b}, now=0.0)
        first_text = role_object(service, first, "message")
        second_text = role_object(service, second, "message")
        self.assertEqual(first_text.type, "FONT")
        self.assertEqual(first_text.data.body, "你好，Blender！\n原生文字測試")
        self.assertEqual(role_object(service, first, "author").data.body, "小雨")
        self.assertAlmostEqual(first_text.data.size, source_size)
        self.assertIsNot(first_text.data, source_text)
        self.assertIsNot(first_text.data, second_text.data)
        self.assertEqual(source_text.body, source_body)
        first_mat = role_object(service, first, "icon").data.materials[0]
        second_mat = role_object(service, second, "icon").data.materials[0]
        self.assertIsNot(first_mat, source_mat)
        self.assertIsNot(first_mat, second_mat)
        self.assertEqual(Path(avatar_image(service, first).filepath).resolve(), Path(self.avatar_a))
        self.assertEqual(Path(avatar_image(service, second).filepath).resolve(), Path(self.avatar_b))
        self.assertIs(source_mat.node_tree.nodes["Avatar"].image, source_image)
        service.cleanup()
        self.assertEqual(counts(), baseline, "cleanup must restore datablock counts")

    def test_nested_collection_and_parent_transforms_are_preserved(self):
        service = self.create_service()
        message_id = service.push({"text": "Layout"}, now=0.0)
        copied_text = role_object(service, message_id, "message")
        copied_panel = role_object(service, message_id, "panel")
        self.assertIsNotNone(copied_text.parent)
        self.assertIs(copied_text.parent, copied_panel.parent)
        self.assertIsNot(copied_text.parent, self.parts["root"])
        # Relative transforms remain unchanged even after arbitrary anchor placement.
        assert_matrix_close(self, copied_text.matrix_basis, self.parts["message"].matrix_basis)
        assert_matrix_close(self, copied_panel.matrix_basis, self.parts["panel"].matrix_basis)
        bpy.context.view_layer.update()
        # Unlinked template objects have no evaluated dependency graph. The two
        # source objects share one parent, so use their local matrices directly.
        relative_original = self.parts["panel"].matrix_basis.inverted() @ self.parts["message"].matrix_basis
        relative_copy = copied_panel.matrix_world.inverted() @ copied_text.matrix_world
        assert_matrix_close(self, relative_copy, relative_original)
        self.assertEqual(len(service.instances[message_id].collection.children), len(self.template.children))

    def test_capacity_smooth_rise_remove_and_bounded_resources(self):
        service = self.create_service()
        first = service.push({"id": "m0", "text": "First"}, now=0.0)
        service.update(now=0.0)
        bpy.context.view_layer.update()
        first_object = role_object(service, first, "message")
        initial = first_object.matrix_world.translation.copy()
        service.push({"id": "m1", "text": "Second"}, now=0.0)
        service.update(now=0.5)
        bpy.context.view_layer.update()
        middle = first_object.matrix_world.translation.copy()
        service.update(now=1.0)
        bpy.context.view_layer.update()
        final = first_object.matrix_world.translation.copy()
        self.assertGreater((middle - initial).length, 0.01)
        self.assertLess((middle - initial).length, (final - initial).length)
        self.assertAlmostEqual((final - initial).length, 1.4, places=4)
        # A moving anchor should move all cards without editing the source template.
        before = first_object.matrix_world.translation.copy()
        self.anchor.location += Vector((1, 2, 3))
        service.update(now=1.0)
        bpy.context.view_layer.update()
        assert_matrix_close(self, Matrix.Translation(first_object.matrix_world.translation - before),
                            Matrix.Translation((1, 2, 3)))
        service.push({"id": "m2", "text": "Third"}, now=1.0)
        service.push({"id": "m3", "text": "Fourth"}, now=1.0)
        self.assertEqual(len(service.instances), 3)
        self.assertNotIn("m0", service.instances)
        stable_counts = counts()
        for i in range(4, 54):
            service.push({"id": f"m{i}", "text": f"Comment {i}", "avatar_path": self.avatar_b}, now=float(i))
            service.update(now=float(i) + 1.0)
        self.assertEqual(len(service.instances), 3)
        steady_counts = counts()
        for i in range(54, 104):
            service.push({"id": f"m{i}", "text": f"Comment {i}", "avatar_path": self.avatar_b}, now=float(i))
            service.update(now=float(i) + 1.0)
        self.assertEqual(counts(), steady_counts, "chatting must not leak orphan data")
        for key in ("objects", "collections", "curves", "meshes", "materials"):
            self.assertEqual(steady_counts[key], stable_counts[key], f"bounded {key}")
        service.remove("m102")
        self.assertNotIn("m102", service.instances)
        self.assertEqual(len(service.instances), 2)
        service.clear()
        self.assertEqual(len(service.instances), 0)
        # Reusable after clear; cleanup is idempotent.
        service.push({"text": "After clear"}, now=200)
        service.cleanup()
        service.cleanup()

    def test_external_preset_library_roundtrip_and_validation(self):
        preset = self.artifacts / "generated_test_preset.blend"
        template_name = self.template.name
        bpy.data.libraries.write(str(preset), {self.template}, fake_user=True)
        imported = load_template(str(preset), template_name)
        self.assertIsNot(imported, self.template)
        self.assertEqual(len(imported.all_objects), len(self.template.all_objects))
        self.assertFalse(any(imported == col for col in self.scene.collection.children))
        service = FloatingUIService(self.scene, imported, anchor=self.anchor)
        try:
            message_id = service.push({"text": "從 preset.blend 載入"}, now=0)
            self.assertEqual(role_object(service, message_id, "message").data.body, "從 preset.blend 載入")
            bpy.context.view_layer.update()
            copied = role_object(service, message_id, "message")
            expected_local = self.parts["root"].matrix_basis @ self.parts["message"].matrix_basis
            assert_matrix_close(self, copied.matrix_world, self.anchor.matrix_world @ expected_local)
        finally:
            service.cleanup()
        with self.assertRaises((ValueError, RuntimeError, FileNotFoundError)) as missing:
            load_template(str(preset), "Missing Collection")
        self.assertIn("Missing Collection", str(missing.exception))
        del self.parts["icon"][ROLE]
        with self.assertRaises((ValueError, RuntimeError)) as malformed:
            FloatingUIService(self.scene, self.template)
        self.assertIn("icon", str(malformed.exception).lower())

    def test_shared_template_material_avatar_change_is_icon_only(self):
        original = self.parts["icon"].data.materials[0]
        self.parts["panel"].data.materials[0] = original
        service = self.create_service()
        message_id = service.push({"text": "Shared material", "avatar_path": self.avatar_b}, now=0)
        icon = role_object(service, message_id, "icon")
        panel = role_object(service, message_id, "panel")
        self.assertIsNot(icon.data, self.parts["icon"].data)
        self.assertIsNot(icon.data.materials[0], panel.data.materials[0])
        self.assertIs(panel.data.materials[0].node_tree.nodes["Avatar"].image, self.original_image)
        self.assertIs(original.node_tree.nodes["Avatar"].image, self.original_image)
        self.assertEqual(Path(avatar_image(service, message_id).filepath).resolve(), Path(self.avatar_b))

    def test_two_display_owners_share_avatar_without_leaking_or_deleting_it(self):
        baseline = counts()
        first_service = self.create_service()
        other = FloatingUIService(self.scene, self.template, anchor=self.anchor)
        try:
            first_service.push({"text": "First display", "avatar_path": self.avatar_b}, now=0)
            message_id = other.push({"text": "Second display", "avatar_path": self.avatar_b}, now=0)
            shared_image = avatar_image(other, message_id)
            first_service.cleanup()
            self.assertEqual(Path(shared_image.filepath).resolve(), Path(self.avatar_b))
            self.assertIs(avatar_image(other, message_id), shared_image)
            other.cleanup()
            self.assertEqual(counts(), baseline)
        finally:
            other.cleanup()

    def test_invalid_payload_does_not_destroy_existing_message(self):
        service = self.create_service()
        first = service.push({"id": "good", "text": "Keep this"}, now=0)
        with self.assertRaises((ValueError, TypeError)):
            service.push({"author": "Missing message"}, now=1)
        self.assertIn(first, service.instances)
        self.assertEqual(role_object(service, first, "message").data.body, "Keep this")

    def test_invalid_avatar_rolls_back_all_new_data_and_keeps_existing_rows(self):
        service = self.create_service()
        first = service.push({"id": "good", "text": "Keep this", "avatar_path": self.avatar_a}, now=0)
        baseline = counts()
        missing = self.artifacts / "missing_avatar_for_transaction_test.png"
        self.assertFalse(missing.exists())
        with self.assertRaises((ValueError, OSError)):
            service.push({"id": "bad", "text": "Cannot load avatar", "avatar_path": str(missing)}, now=1)
        self.assertEqual(counts(), baseline, "failed clone must release every allocated datablock")
        self.assertEqual(list(service.instances), [first])
        self.assertEqual(role_object(service, first, "message").data.body, "Keep this")

    def test_message_author_binding_overlap_is_actionable_and_allocates_nothing(self):
        baseline = counts()
        with self.assertRaises((ValueError, RuntimeError)) as error:
            FloatingUIService(self.scene, self.template, text_object=self.parts["author"].name)
        self.assertTrue(any(term in str(error.exception).lower() for term in ("author", "same", "distinct")))
        self.assertEqual(counts(), baseline)

    def test_preview_native_operators_snapshot_fanout_and_unregister(self):
        from yugo_floating_ui import preview
        preview.register()
        preview.register()  # Repeated registration must not duplicate handlers.
        try:
            tree, source, display = self.preview_graph(preview)
            result = bpy.ops.yugofui.send(tree_name=tree.name, node_name=source.name)
            self.assertEqual(result, {"FINISHED"})
            service = preview.get_service(display, self.scene)
            self.assertEqual(len(service.instances), 1)
            first = next(iter(service.instances))
            self.assertEqual(role_object(service, first, "message").data.body, source.text)
            source.text = "下一則，不會覆寫前一則"
            source.author = "另一位觀眾"
            self.assertEqual(role_object(service, first, "message").data.body, "節點送出的一則留言")
            other_display = tree.nodes.new(preview.DISPLAY_ID)
            other_display.template = self.template
            tree.links.new(source.outputs[0], other_display.inputs[0])
            message_id, receivers = preview.send_message(source, self.scene)
            self.assertEqual(receivers, 2)
            self.assertIn(message_id, preview.get_service(display, self.scene).instances)
            self.assertIn(message_id, preview.get_service(other_display, self.scene).instances)
            self.assertEqual(bpy.ops.yugofui.clear(tree_name=tree.name, node_name=display.name), {"FINISHED"})
            self.assertEqual(len(preview.get_service(display, self.scene).instances), 0)
            before_change = preview.get_service(other_display, self.scene)
            other_display.max_messages = 2
            self.assertTrue(before_change.closed, "property changes must discard the previous runtime")
            self.assertEqual(len(preview.get_service(other_display, self.scene).instances), 0)
            # Tree removal calls native free() and releases scene-specific owners.
            bpy.data.node_groups.remove(tree)
            bpy.context.view_layer.update()
            self.assert_no_generated_data()
        finally:
            preview.unregister()
            preview.unregister()
        self.assert_no_generated_data()
        for handlers, callback in preview._HANDLERS:
            self.assertNotIn(callback, handlers)

    def test_preview_load_recovers_tagged_saved_outputs_and_keeps_template(self):
        from yugo_floating_ui import preview
        preview.register()
        try:
            tree, source, display = self.preview_graph(preview)
            preview.send_message(source, self.scene)
            preset_name = self.template.name
            scene_file = self.artifacts / "preview_load_recovery.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(scene_file))
            self.assertTrue(any(obj.get(OWNER) for obj in bpy.data.objects))
            bpy.ops.wm.open_mainfile(filepath=str(scene_file))
            self.assert_no_generated_data()
            self.assertIsNotNone(bpy.data.collections.get(preset_name))
            restored_tree = bpy.data.node_groups["Integration Preview"]
            restored_source = next(node for node in restored_tree.nodes if node.bl_idname == preview.SOURCE_ID)
            restored_display = next(node for node in restored_tree.nodes if node.bl_idname == preview.DISPLAY_ID)
            self.assertEqual(restored_source.text, "節點送出的一則留言")
            self.assertIsNotNone(restored_display.template)
            self.assertEqual(preview.send_message(restored_source, bpy.context.scene)[1], 1)
            self.assertEqual(len(preview.get_service(restored_display, bpy.context.scene).instances), 1)
        finally:
            preview.unregister()
        self.assert_no_generated_data()

    def test_preview_manual_generated_data_deletion_recovers_on_next_send(self):
        from yugo_floating_ui import preview
        preview.register()
        try:
            _tree, source, display = self.preview_graph(preview)
            preview.send_message(source, self.scene)
            for deletion in ("message", "row_root", "panel_root", "output"):
                old_service = preview.get_service(display, self.scene)
                instance = next(iter(old_service.instances.values()))
                if deletion == "message":
                    bpy.data.objects.remove(instance.message_object, do_unlink=True)
                elif deletion == "row_root":
                    bpy.data.objects.remove(instance.root, do_unlink=True)
                elif deletion == "panel_root":
                    bpy.data.objects.remove(old_service.panel_root, do_unlink=True)
                else:
                    bpy.data.collections.remove(old_service.output)
                event_id, receivers = preview.send_message(source, self.scene)
                recovered = preview.get_service(display, self.scene)
                self.assertEqual(receivers, 1)
                self.assertTrue(old_service.closed)
                self.assertIsNot(recovered, old_service)
                self.assertEqual(list(recovered.instances), [event_id])
                self.assertEqual(role_object(recovered, event_id, "message").data.body, source.text)
        finally:
            preview.unregister()
        self.assert_no_generated_data()

    def test_preview_actual_undo_redo_removes_runtime_and_retains_graph(self):
        from yugo_floating_ui import preview
        preview.register()
        try:
            tree, source, _display = self.preview_graph(preview)
            bpy.context.preferences.edit.use_global_undo = True
            bpy.ops.ed.undo_push(message="Floating UI graph setup")
            preview.send_message(source, self.scene)
            bpy.ops.ed.undo_push(message="Floating UI generated message")
            self.assertTrue(bpy.ops.ed.undo.poll(), "This Blender process must support Undo")
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.assert_no_generated_data()
            self.assertIsNotNone(bpy.data.node_groups.get("Integration Preview"))
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            self.assert_no_generated_data()
            restored_tree = bpy.data.node_groups["Integration Preview"]
            self.assertEqual(len(restored_tree.nodes), 2)
        finally:
            preview.unregister()
        self.assert_no_generated_data()


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FloatingUIBlenderTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"FLOATING_UI_BLENDER_TESTS_PASSED {result.testsRun}")


if __name__ == "__main__":
    main()
