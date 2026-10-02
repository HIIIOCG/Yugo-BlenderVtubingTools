"""Pure tests for actual message snapshots, bursts and interrupted movement."""

from dataclasses import FrozenInstanceError
import importlib.util
from pathlib import Path
import sys
import unittest
import uuid


SOURCE = Path(__file__).resolve().parents[2] / "addons/yugo_floating_ui/model.py"
SPEC = importlib.util.spec_from_file_location("floating_ui_test_model", SOURCE)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)
Message, StackModel = MODEL.Message, MODEL.StackModel


def message(identifier):
    return Message.from_payload({"id": identifier, "text": f"Message {identifier}"})


class MessageTests(unittest.TestCase):
    def test_frozen_snapshot_does_not_follow_original_payload(self):
        payload = {"id": "first", "text": "你好", "author": "Viewer",
                   "avatar_path": "avatar.png", "metadata": {"badges": ["subscriber"]}}
        snapshot = Message.from_payload(payload)
        payload["text"] = "changed"
        payload["author"] = "Other"
        payload["metadata"]["badges"].clear()
        self.assertEqual((snapshot.id, snapshot.text, snapshot.author, snapshot.avatar_path),
                         ("first", "你好", "Viewer", "avatar.png"))
        with self.assertRaises(FrozenInstanceError):
            snapshot.text = "mutable"

    def test_missing_id_is_unique_uuid_and_optional_fields_default_empty(self):
        first = Message.from_payload({"text": ""})
        second = Message.from_payload({"text": ""})
        self.assertEqual(uuid.UUID(first.id).hex, first.id)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual((first.author, first.avatar_path), ("", ""))

    def test_missing_text_and_invalid_field_types_rejected(self):
        with self.assertRaises(ValueError):
            Message.from_payload({"id": "a"})
        with self.assertRaises(TypeError):
            Message.from_payload([{"text": "a"}])
        for field in ("id", "text", "author", "avatar_path"):
            for invalid in (None, 1, True, [], {}):
                with self.subTest(field=field, value=invalid), self.assertRaises(TypeError):
                    Message.from_payload({"text": "valid", field: invalid})
        for invalid in ("", "   "):
            with self.subTest(id=invalid), self.assertRaises(ValueError):
                Message.from_payload({"id": invalid, "text": "valid"})

    def test_extra_metadata_must_be_strict_json(self):
        for invalid in (object(), float("nan"), float("inf"), (1, 2), {1: "bad key"}):
            with self.subTest(metadata=invalid), self.assertRaises((TypeError, ValueError)):
                Message.from_payload({"text": "valid", "metadata": invalid})

    def test_cyclic_metadata_is_rejected_as_invalid_snapshot(self):
        metadata = []
        metadata.append(metadata)
        with self.assertRaises(ValueError):
            Message.from_payload({"text": "valid", "metadata": metadata})

    def test_direct_construction_has_same_field_validation(self):
        with self.assertRaises(TypeError):
            Message("a", None)
        with self.assertRaises(ValueError):
            Message("", "valid")


class StackModelTests(unittest.TestCase):
    def test_newest_is_at_zero_and_oldest_moves_up(self):
        stack = StackModel(spacing=2.0, move_duration=1.0)
        stack.add(message("a"), 10.0)
        stack.add(message("b"), 10.0)
        self.assertEqual([item.id for item in stack.messages], ["a", "b"])
        self.assertEqual(stack.positions(10.0), {"a": 0.0, "b": 0.0})
        self.assertEqual(stack.positions(10.25), {"a": 0.3125, "b": 0.0})
        self.assertEqual(stack.positions(10.5), {"a": 1.0, "b": 0.0})
        self.assertEqual(stack.positions(10.75), {"a": 1.6875, "b": 0.0})
        self.assertEqual(stack.positions(11.0), {"a": 2.0, "b": 0.0})
        self.assertEqual(stack.positions(12.0), stack.positions(11.0))

    def test_arrival_during_movement_rebases_without_a_jump(self):
        stack = StackModel(spacing=2.0, move_duration=1.0)
        stack.add(message("a"), 0.0)
        stack.add(message("b"), 0.0)
        before = stack.positions(0.5)
        stack.add(message("c"), 0.5)
        self.assertEqual(stack.positions(0.5), {**before, "c": 0.0})
        self.assertEqual(stack.positions(1.0), {"a": 2.5, "b": 1.0, "c": 0.0})
        self.assertEqual(stack.positions(1.5), {"a": 4.0, "b": 2.0, "c": 0.0})

    def test_burst_retains_only_capacity_and_returns_evicted_ids(self):
        stack = StackModel(max_messages=3, move_duration=0.25)
        evicted = []
        for index in range(10):
            evicted.extend(stack.add(message(str(index)), 5.0))
        self.assertEqual(evicted, [str(index) for index in range(7)])
        self.assertEqual(tuple(item.id for item in stack.messages), ("7", "8", "9"))
        self.assertEqual(stack.positions(5.25), {"7": 2.0, "8": 1.0, "9": 0.0})

    def test_duplicate_does_not_replace_payload_or_restart_movement(self):
        stack = StackModel(move_duration=1.0)
        stack.add(message("a"), 0.0)
        stack.add(message("b"), 0.0)
        original = stack.messages
        self.assertEqual(stack.add(Message("a", "replacement"), 0.5), [])
        self.assertEqual(stack.messages, original)
        self.assertEqual(stack.positions(1.0), {"a": 1.0, "b": 0.0})

    def test_capacity_eviction_preserves_survivors_current_positions(self):
        stack = StackModel(max_messages=3, move_duration=1.0)
        for identifier in ("a", "b", "c"):
            stack.add(message(identifier), 0.0)
        before = stack.positions(0.5)
        self.assertEqual(stack.add(message("d"), 0.5), ["a"])
        self.assertEqual(stack.positions(0.5), {"b": before["b"], "c": before["c"], "d": 0.0})
        self.assertEqual(stack.positions(1.5), {"b": 2.0, "c": 1.0, "d": 0.0})

    def test_remove_rebases_surviving_messages_and_unknown_is_noop(self):
        stack = StackModel(move_duration=1.0)
        for identifier in ("a", "b", "c"):
            stack.add(message(identifier), 0.0)
        before = stack.positions(0.5)
        self.assertTrue(stack.remove("b", 0.5))
        self.assertEqual(stack.positions(0.5), {"a": before["a"], "c": before["c"]})
        self.assertFalse(stack.remove("missing", 0.75))
        self.assertEqual(stack.positions(1.5), {"a": 1.0, "c": 0.0})

    def test_removing_newest_closes_gap_smoothly(self):
        stack = StackModel(move_duration=1.0)
        for identifier in ("a", "b", "c"):
            stack.add(message(identifier), 0.0)
        stack.remove("c", 1.0)
        self.assertEqual(stack.positions(1.0), {"a": 2.0, "b": 1.0})
        self.assertEqual(stack.positions(1.5), {"a": 1.5, "b": 0.5})
        self.assertEqual(stack.positions(2.0), {"a": 1.0, "b": 0.0})

    def test_zero_duration_applies_immediately_including_removal(self):
        stack = StackModel(spacing=3.0, move_duration=0.0)
        for identifier in ("a", "b", "c"):
            stack.add(message(identifier), 0.0)
        self.assertEqual(stack.positions(0.0), {"a": 6.0, "b": 3.0, "c": 0.0})
        stack.remove("c", 0.0)
        self.assertEqual(stack.positions(0.0), {"a": 3.0, "b": 0.0})

    def test_clear_and_position_results_are_isolated(self):
        stack = StackModel()
        stack.add(message("a"), 0.0)
        stack.add(message("b"), 0.0)
        result = stack.positions(1.0)
        result["a"] = 999.0
        self.assertEqual(stack.positions(1.0)["a"], 1.0)
        saved_messages = stack.messages
        self.assertEqual(stack.clear(), ["a", "b"])
        self.assertEqual(stack.clear(), [])
        self.assertEqual(stack.messages, ())
        self.assertEqual(stack.positions(2.0), {})
        self.assertEqual(len(saved_messages), 2)
        stack.add(message("a"), 3.0)
        self.assertEqual(stack.positions(3.0), {"a": 0.0})

    def test_capacity_one_evicts_without_animating(self):
        stack = StackModel(max_messages=1)
        stack.add(message("a"), 0.0)
        self.assertEqual(stack.add(message("b"), 0.01), ["a"])
        self.assertEqual(stack.positions(0.01), {"b": 0.0})

    def test_config_validation_and_readonly_properties(self):
        for capacity in (True, 1.0, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(TypeError):
                StackModel(max_messages=capacity)
        for capacity in (0, -1, 201):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                StackModel(max_messages=capacity)
        for option in ("spacing", "move_duration"):
            for invalid in (True, "1", None):
                with self.subTest(option=option, value=invalid), self.assertRaises(TypeError):
                    StackModel(**{option: invalid})
            for invalid in (float("nan"), float("inf"), -float("inf")):
                with self.subTest(option=option, value=invalid), self.assertRaises(ValueError):
                    StackModel(**{option: invalid})
        for spacing in (0.0, -1.0, 1e308):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                StackModel(spacing=spacing)
        with self.assertRaises(ValueError):
            StackModel(move_duration=-1.0)
        with self.assertRaises(AttributeError):
            StackModel().max_messages = 500

    def test_finite_time_validation_even_for_noops(self):
        stack = StackModel()
        for invalid in (float("nan"), float("inf"), -float("inf")):
            for operation in (lambda: stack.add(message("a"), invalid),
                              lambda: stack.remove("missing", invalid),
                              lambda: stack.positions(invalid)):
                with self.subTest(now=invalid), self.assertRaises(ValueError):
                    operation()
        for invalid in (True, "1", None):
            with self.subTest(now=invalid), self.assertRaises(TypeError):
                stack.positions(invalid)
        with self.assertRaises(TypeError):
            stack.add({"text": "not a snapshot"}, 0.0)
        with self.assertRaises(TypeError):
            stack.remove(None, 0.0)


if __name__ == "__main__":
    unittest.main()
