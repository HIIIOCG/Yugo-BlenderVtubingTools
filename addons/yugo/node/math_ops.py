"""Pure, finite numerical operations for Yugo's Float and Vector nodes.

The functions never mutate Blender data or retain evaluation state. Invalid
input types raise TypeError; nonfinite inputs and overflowing results raise
ValueError. Divide by zero returns zero, and normalizing zero returns zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Set
from numbers import Real


SCALAR_OPERATIONS = (
    "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "MULTIPLY_ADD",
    "MINIMUM", "MAXIMUM", "ABSOLUTE",
)
VECTOR_OPERATIONS = (
    "ADD", "SUBTRACT", "SCALE", "DOT_PRODUCT", "CROSS_PRODUCT",
    "LENGTH", "DISTANCE", "NORMALIZE",
)


def finite_number(value):
    """Return a finite float without coercing strings or boolean values."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("Expected a real number, excluding bool")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("Number must be finite") from exc
    if not math.isfinite(result):
        raise ValueError("Number must be finite")
    return result


def finite_vector(value):
    """Return exactly three finite components, including vector-like inputs."""
    if isinstance(value, (str, bytes, bytearray, Mapping, Set)):
        raise TypeError("Expected a three-component vector")
    try:
        length = len(value)
        components = tuple(value)
    except TypeError as exc:
        raise TypeError("Expected a three-component vector") from exc
    if length != 3 or len(components) != 3:
        raise ValueError("Vector must have exactly three components")
    try:
        return tuple(finite_number(component) for component in components)
    except (TypeError, ValueError) as exc:
        raise type(exc)("Vector components must be finite real numbers") from exc


def _result(value):
    if not math.isfinite(value):
        raise ValueError("Math result must be finite (arithmetic overflow)")
    return float(value)


def _vector_result(value):
    return tuple(_result(component) for component in value)


def _flag(value, name):
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")
    return value


def scalar(operation, a, b=0, c=0):
    """Evaluate a popular Math operation on unrestricted finite scalars."""
    if operation not in SCALAR_OPERATIONS:
        raise ValueError(f"Unsupported scalar operation: {operation!r}")
    a = finite_number(a)
    if operation == "ABSOLUTE":
        return abs(a)
    b = finite_number(b)
    if operation == "ADD":
        return _result(a + b)
    if operation == "SUBTRACT":
        return _result(a - b)
    if operation == "MULTIPLY":
        return _result(a * b)
    if operation == "DIVIDE":
        return _result(a / b) if b != 0.0 else 0.0
    if operation == "MULTIPLY_ADD":
        return _result(a * b + finite_number(c))
    if operation == "MINIMUM":
        return min(a, b)
    return max(a, b)


def clamp(value, min, max):
    """Limit a finite scalar to the interval, accepting reversed bounds."""
    value = finite_number(value)
    lower = finite_number(min)
    upper = finite_number(max)
    if lower > upper:
        lower, upper = upper, lower
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def map_range(value, from_min, from_max, to_min, to_max, clamp_result=True):
    """Linearly remap; a zero-width source interval yields to_min.

    When clamped, values stay inside the destination interval, including when
    either interval runs in descending order. Disabling clamp extrapolates.
    """
    value = finite_number(value)
    from_min = finite_number(from_min)
    from_max = finite_number(from_max)
    to_min = finite_number(to_min)
    to_max = finite_number(to_max)
    _flag(clamp_result, "Clamp Result")
    if from_min == from_max:
        return to_min
    factor = _result(
        _result(value - from_min) / _result(from_max - from_min)
    )
    return mix(to_min, to_max, factor, clamp_factor=clamp_result)


def mix(a, b, factor, clamp_factor=True):
    """Interpolate finite scalars; unclamped factors allow extrapolation."""
    a = finite_number(a)
    b = finite_number(b)
    factor = finite_number(factor)
    _flag(clamp_factor, "Clamp Factor")
    if clamp_factor:
        factor = clamp(factor, 0.0, 1.0)
    if factor == 0.0:
        return a
    if factor == 1.0:
        return b
    # Weighted endpoints avoid overflowing b - a within the normal interval.
    return _result((1.0 - factor) * a + factor * b)


def vector_mix(a, b, factor, clamp_factor=True):
    """Interpolate corresponding components of two three-vectors."""
    a = finite_vector(a)
    b = finite_vector(b)
    factor = finite_number(factor)
    _flag(clamp_factor, "Clamp Factor")
    return tuple(mix(x, y, factor, clamp_factor) for x, y in zip(a, b))


def vector(operation, a, b=(0, 0, 0), scale=1):
    """Evaluate a Vector Math operation, with scalar metric outputs.

    Dot Product, Length and Distance return a float. All other operations
    return a tuple of three floats. Normalize preserves small/large vectors
    through scaling before computing the magnitude.
    """
    if operation not in VECTOR_OPERATIONS:
        raise ValueError(f"Unsupported vector operation: {operation!r}")
    a = finite_vector(a)
    if operation == "LENGTH":
        return _result(math.hypot(*a))
    if operation == "NORMALIZE":
        magnitude_scale = max(abs(component) for component in a)
        if magnitude_scale == 0.0:
            return (0.0, 0.0, 0.0)
        scaled = tuple(component / magnitude_scale for component in a)
        magnitude = math.hypot(*scaled)
        return _vector_result(component / magnitude for component in scaled)
    if operation == "SCALE":
        scale = finite_number(scale)
        return _vector_result(component * scale for component in a)
    b = finite_vector(b)
    if operation == "ADD":
        return _vector_result(x + y for x, y in zip(a, b))
    if operation == "SUBTRACT":
        return _vector_result(x - y for x, y in zip(a, b))
    if operation == "DOT_PRODUCT":
        return _result(sum(x * y for x, y in zip(a, b)))
    if operation == "CROSS_PRODUCT":
        return _vector_result((
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ))
    difference = _vector_result(x - y for x, y in zip(a, b))
    return _result(math.hypot(*difference))
