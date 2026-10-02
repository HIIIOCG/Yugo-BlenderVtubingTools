"""Small Blender-independent timing helpers."""

from __future__ import annotations


def fixed_step_count(
    accumulator: float,
    elapsed: float,
    step: float,
    max_steps: int,
) -> tuple[int, float]:
    """Return a bounded fixed-step count and the remaining accumulated time."""

    if step <= 0.0 or max_steps <= 0:
        return 0, 0.0
    accumulator = max(0.0, float(accumulator)) + max(0.0, float(elapsed))
    available = int(accumulator / step)
    count = min(available, max_steps)
    accumulator -= count * step
    if available > max_steps:
        # Dropping an oversized backlog keeps the viewport responsive after a stall.
        accumulator = min(accumulator, step)
    return count, accumulator
