"""Dependency-free key matching, easing, and expression mask helpers."""

from __future__ import annotations

import math
import re


def normalize_expression_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def expression_mask_type(name: str) -> str:
    """Classify common VRM and ARKit expression names for mask locking."""

    normalized = normalize_expression_name(name)
    if (
        normalized.startswith("eye")
        or normalized.startswith("blink")
        or normalized.startswith("look")
    ):
        return "EYES"
    if (
        normalized in {"a", "i", "u", "e", "o", "aa", "ih", "ou", "ee", "oh"}
        or normalized.startswith("mouth")
        or normalized.startswith("jaw")
    ):
        return "MOUTH"
    return "OTHER"


def key_matches(binding, event) -> bool:
    """Return whether a Blender-style event matches a binding exactly."""

    return (
        getattr(binding, "key", "") == getattr(event, "type", "")
        and bool(getattr(binding, "use_shift", False))
        == bool(getattr(event, "shift", False))
        and bool(getattr(binding, "use_ctrl", False))
        == bool(getattr(event, "ctrl", False))
        and not bool(getattr(event, "alt", False))
        and not bool(getattr(event, "oskey", False))
    )


def eased_value(current: float, target: float, smooth: float, delta_time: float) -> float:
    """Exponential ease-in/ease-out where Smooth maps to up to one second."""

    current = min(max(float(current), 0.0), 1.0)
    target = min(max(float(target), 0.0), 1.0)
    smooth = min(max(float(smooth), 0.0), 1.0)
    if smooth <= 0.0 or delta_time <= 0.0:
        return target

    duration = 0.02 + smooth * 0.98
    factor = 1.0 - math.exp(-max(float(delta_time), 0.0) * 5.0 / duration)
    value = current + (target - current) * factor
    if abs(target - value) < 0.0001:
        return target
    return value
