"""Pure math tests runnable without importing bpy or the add-on package."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest


from _bootstrap import load_math_ops

math_ops = load_math_ops()


class FiniteInputTests(unittest.TestCase):
    def test_scalar_accepts_signed_and_unrestricted_values(self):
        self.assertEqual(math_ops.finite_number(-275), -275.0)
        self.assertEqual(math_ops.finite_number(4.25), 4.25)

    def test_rejects_coercion_and_nonfinite_numbers(self):
        for value in (True, False, "2.5", None, (1,), 1 + 2j):
            with self.subTest(value=value), self.assertRaises(TypeError):
                math_ops.finite_number(value)
        for value in (math.inf, -math.inf, math.nan, 10 ** 10000):
            with self.subTest(type=type(value)), self.assertRaises(ValueError):
                math_ops.finite_number(value)

    def test_vector_accepts_three_components_and_rejects_wrong_types(self):
        self.assertEqual(math_ops.finite_vector([-4, 2.5, 8]), (-4.0, 2.5, 8.0))
        for value in ("123", {1, 2, 3}, {"x": 1}, None, 3):
            with self.subTest(value=value), self.assertRaises(TypeError):
                math_ops.finite_vector(value)
        for value in ((), (1, 2), (1, 2, 3, 4)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                math_ops.finite_vector(value)
        for value, error in (((True, 2, 3), TypeError),
                             ((1, "2", 3), TypeError),
                             ((1, math.nan, 3), ValueError)):
            with self.subTest(value=value), self.assertRaises(error):
                math_ops.finite_vector(value)


class ScalarTests(unittest.TestCase):
    def test_all_scalar_operations_keep_signed_unrestricted_values(self):
        examples = (
            ("ADD", -4, 9, 0, 5),
            ("SUBTRACT", -4, 9, 0, -13),
            ("MULTIPLY", -4, 9, 0, -36),
            ("DIVIDE", -18, 3, 0, -6),
            ("MULTIPLY_ADD", -4, 9, 5, -31),
            ("MINIMUM", -4, 9, 0, -4),
            ("MAXIMUM", -4, 9, 0, 9),
            ("ABSOLUTE", -4, 0, 0, 4),
        )
        for operation, a, b, c, expected in examples:
            with self.subTest(operation=operation):
                self.assertEqual(math_ops.scalar(operation, a, b, c), expected)

    def test_divide_zero_returns_zero(self):
        self.assertEqual(math_ops.scalar("DIVIDE", -12, 0), 0.0)
        self.assertEqual(math_ops.scalar("DIVIDE", 0, -0.0), 0.0)

    def test_overflow_and_invalid_operation_raise_clear_errors(self):
        for operation, a, b in (("ADD", 1e308, 1e308),
                                ("MULTIPLY", 1e308, 2),
                                ("DIVIDE", 1e308, 1e-308)):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "overflow"):
                    math_ops.scalar(operation, a, b)
        with self.assertRaisesRegex(ValueError, "Unsupported scalar"):
            math_ops.scalar("POWER", 2, 3)

    def test_clamp_including_reverse_and_equal_bounds(self):
        self.assertEqual(math_ops.clamp(-4, -2, 3), -2.0)
        self.assertEqual(math_ops.clamp(4, -2, 3), 3.0)
        self.assertEqual(math_ops.clamp(2, 3, -2), 2.0)
        self.assertEqual(math_ops.clamp(4, 3, 3), 3.0)

    def test_map_range_remaps_and_clamps_both_directions(self):
        self.assertEqual(math_ops.map_range(5, 0, 10, -20, 20), 0.0)
        self.assertEqual(math_ops.map_range(-5, 0, 10, -20, 20), -20.0)
        self.assertEqual(math_ops.map_range(15, 0, 10, -20, 20), 20.0)
        self.assertEqual(math_ops.map_range(2.5, 10, 0, -20, 20), 10.0)
        self.assertEqual(math_ops.map_range(15, 0, 10, 20, -20), -20.0)
        self.assertEqual(math_ops.map_range(15, 0, 10, -20, 20, False), 40.0)

    def test_map_range_zero_source_interval_and_overflow(self):
        self.assertEqual(math_ops.map_range(5, 3, 3, -4, 7), -4.0)
        self.assertEqual(math_ops.map_range(5, 3, 3, -4, 7, False), -4.0)
        with self.assertRaisesRegex(ValueError, "overflow"):
            math_ops.map_range(0, -1e308, 1e308, 0, 1)

    def test_mix_clamping_extrapolation_and_large_opposite_endpoints(self):
        self.assertEqual(math_ops.mix(-4, 12, 0.25), 0.0)
        self.assertEqual(math_ops.mix(-4, 12, -1), -4.0)
        self.assertEqual(math_ops.mix(-4, 12, 2), 12.0)
        self.assertEqual(math_ops.mix(-4, 12, 2, False), 28.0)
        self.assertEqual(math_ops.mix(-1e308, 1e308, 0.5), 0.0)
        with self.assertRaisesRegex(ValueError, "overflow"):
            math_ops.mix(-1e308, 1e308, 2, False)

    def test_used_inputs_and_options_are_validated(self):
        with self.assertRaises(TypeError):
            math_ops.scalar("MULTIPLY_ADD", 1, 2, "3")
        with self.assertRaises(ValueError):
            math_ops.clamp(1, 0, math.inf)
        with self.assertRaises(TypeError):
            math_ops.mix(1, 2, 0.5, 1)
        with self.assertRaises(ValueError):
            math_ops.map_range(1, 0, math.nan, 0, 1)


class VectorTests(unittest.TestCase):
    def assertVectorAlmostEqual(self, actual, expected):
        self.assertIsInstance(actual, tuple)
        self.assertEqual(len(actual), 3)
        for actual_component, expected_component in zip(actual, expected):
            self.assertAlmostEqual(actual_component, expected_component)

    def test_component_arithmetic_and_scale(self):
        a, b = (-3, 2, 5), (1, -4, 2)
        self.assertEqual(math_ops.vector("ADD", a, b), (-2.0, -2.0, 7.0))
        self.assertEqual(math_ops.vector("SUBTRACT", a, b), (-4.0, 6.0, 3.0))
        self.assertEqual(math_ops.vector("SCALE", a, scale=-2), (6.0, -4.0, -10.0))

    def test_metric_outputs_are_scalars(self):
        for operation, a, b, expected in (
            ("DOT_PRODUCT", (-3, 2, 5), (1, -4, 2), -1),
            ("LENGTH", (3, 4, 12), (0, 0, 0), 13),
            ("DISTANCE", (-1, -2, -3), (2, 2, 9), 13),
        ):
            with self.subTest(operation=operation):
                result = math_ops.vector(operation, a, b)
                self.assertIsInstance(result, float)
                self.assertEqual(result, expected)

    def test_cross_product_orientation_and_parallel_zero(self):
        self.assertEqual(math_ops.vector("CROSS_PRODUCT", (1, 0, 0), (0, 1, 0)),
                         (0.0, 0.0, 1.0))
        self.assertEqual(math_ops.vector("CROSS_PRODUCT", (0, 1, 0), (1, 0, 0)),
                         (0.0, 0.0, -1.0))
        self.assertEqual(math_ops.vector("CROSS_PRODUCT", (1, 2, 3), (2, 4, 6)),
                         (0.0, 0.0, 0.0))

    def test_normalization_handles_zero_small_and_large_vectors(self):
        self.assertEqual(math_ops.vector("NORMALIZE", (0, 0, 0)), (0.0, 0.0, 0.0))
        self.assertVectorAlmostEqual(math_ops.vector("NORMALIZE", (-3, 4, 0)),
                                     (-0.6, 0.8, 0.0))
        self.assertEqual(math_ops.vector("NORMALIZE", (5e-324, 0, 0)),
                         (1.0, 0.0, 0.0))
        self.assertVectorAlmostEqual(math_ops.vector("NORMALIZE", (1e308, 1e308, 0)),
                                     (math.sqrt(0.5), math.sqrt(0.5), 0.0))

    def test_vector_mix_factors_are_shared_across_components(self):
        a, b = (-4, 2, 6), (12, -2, 10)
        self.assertEqual(math_ops.vector_mix(a, b, 0.25), (0.0, 1.0, 7.0))
        self.assertEqual(math_ops.vector_mix(a, b, 2), tuple(float(x) for x in b))
        self.assertEqual(math_ops.vector_mix(a, b, 2, False), (28.0, -6.0, 14.0))

    def test_overflow_and_nonfinite_vector_operations_reject(self):
        cases = (
            ("ADD", (1e308, 0, 0), (1e308, 0, 0), 1),
            ("SCALE", (1e308, 0, 0), (0, 0, 0), 2),
            ("DOT_PRODUCT", (1e308, 0, 0), (2, 0, 0), 1),
            ("CROSS_PRODUCT", (1e308, 0, 0), (0, 2, 0), 1),
            ("DISTANCE", (-1e308, 0, 0), (1e308, 0, 0), 1),
            ("LENGTH", (1.7e308, 1.7e308, 0), (0, 0, 0), 1),
        )
        for operation, a, b, scale in cases:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "overflow"):
                    math_ops.vector(operation, a, b, scale)
        with self.assertRaises(ValueError):
            math_ops.vector("NORMALIZE", (math.inf, 1, 0))
        with self.assertRaises(TypeError):
            math_ops.vector("SCALE", (1, 2, 3), scale=True)
        with self.assertRaisesRegex(ValueError, "Unsupported vector"):
            math_ops.vector("PROJECT", (1, 2, 3))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
