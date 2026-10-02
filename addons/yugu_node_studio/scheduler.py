"""Two-rate main-thread scheduler for Yugo runtime work.

Motion callbacks run in a deterministic 60 Hz order. Slow system and UI
maintenance runs at 2 Hz. Callback code is always executed on Blender's main
thread through ``bpy.app.timers``.
"""

from __future__ import annotations

import time

import bpy


MOTION_INTERVAL = 1.0 / 60.0
SYSTEM_INTERVAL = 1.0 / 2.0
# Kept only so 0.1.23's driver-namespace marker can be removed on upgrade.
GENERATION_KEY = "yugu_node_studio.scheduler.generation"

_motion_callbacks: dict[str, tuple[int, object]] = {}
_system_callbacks: dict[str, tuple[int, object]] = {}
_motion_timer = None
_system_timer = None
_callback_error_times: dict[str, float] = {}
_suspended = False


def _add_callback(registry, name, callback, priority) -> None:
    if not name:
        raise ValueError("A scheduler callback name is required")
    if not callable(callback):
        raise TypeError("A scheduler callback must be callable")
    registry[str(name)] = (int(priority), callback)


def add_motion(name, callback, *, priority=0) -> None:
    _add_callback(_motion_callbacks, name, callback, priority)


def remove_motion(name) -> None:
    _motion_callbacks.pop(str(name), None)


def add_system(name, callback, *, priority=0) -> None:
    _add_callback(_system_callbacks, name, callback, priority)


def remove_system(name) -> None:
    _system_callbacks.pop(str(name), None)


def suspend() -> None:
    """Pause both periodic loops while Blender replaces all file data."""

    global _suspended
    _suspended = True


def resume() -> None:
    global _suspended
    _suspended = False


def is_suspended() -> bool:
    return _suspended


def _run_callbacks(registry, delta_time) -> None:
    ordered = sorted(
        tuple(registry.items()),
        key=lambda item: (item[1][0], item[0]),
    )
    for name, (_priority, callback) in ordered:
        if not callable(callback):
            registry.pop(name, None)
            continue
        try:
            callback(bpy.context, delta_time)
        except Exception as error:
            # Blender can briefly invalidate RNA while loading or rebuilding
            # Undo data. Keep the callback so a transient error cannot
            # permanently stop every connected source. Limit console output
            # while retrying on the following cycle.
            now = time.monotonic()
            error_key = f"{id(registry)}:{name}"
            if now - _callback_error_times.get(error_key, 0.0) >= 2.0:
                _callback_error_times[error_key] = now
                print(f'Yugo scheduler callback "{name}" will retry: {error}')


def run_motion_once(delta_time=MOTION_INTERVAL) -> None:
    """Run one ordered motion cycle. Public for deterministic Blender tests."""

    _run_callbacks(_motion_callbacks, min(max(float(delta_time), 0.0), 0.1))


def run_system_once(delta_time=SYSTEM_INTERVAL) -> None:
    """Run one ordered maintenance cycle. Public for deterministic tests."""

    _run_callbacks(_system_callbacks, min(max(float(delta_time), 0.0), 1.0))


def _make_timer(registry, interval, timer_kind):
    last_time = time.monotonic() - interval
    next_deadline = last_time

    def timer():
        nonlocal last_time, next_deadline
        # Module globals survive a .blend load, unlike driver_namespace. On a
        # script reload, the newly-created timer replaces this identity and
        # safely retires the stale closure.
        active_timer = _motion_timer if timer_kind == "motion" else _system_timer
        if active_timer is not timer:
            return None
        now = time.monotonic()
        delta_time = min(max(now - last_time, 0.0), 0.1)
        if delta_time <= 0.0:
            delta_time = interval
        last_time = now
        if not _suspended:
            _run_callbacks(registry, delta_time)

        # Compensate for callback execution time instead of adding it to every
        # interval. If Blender is already late, skip missed deadlines rather
        # than running several catch-up cycles in one UI event.
        next_deadline += interval
        after = time.monotonic()
        if next_deadline <= after:
            next_deadline = after + interval
        return max(0.001, next_deadline - after)

    return timer


def register() -> None:
    global _motion_timer, _system_timer, _suspended
    for timer in (_motion_timer, _system_timer):
        if timer is None:
            continue
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except ValueError:
            pass
    _motion_callbacks.clear()
    _system_callbacks.clear()
    _callback_error_times.clear()
    _suspended = False
    bpy.app.driver_namespace.pop(GENERATION_KEY, None)
    _motion_timer = _make_timer(
        _motion_callbacks,
        MOTION_INTERVAL,
        "motion",
    )
    _system_timer = _make_timer(
        _system_callbacks,
        SYSTEM_INTERVAL,
        "system",
    )
    bpy.app.timers.register(_motion_timer, first_interval=0.0, persistent=True)
    bpy.app.timers.register(_system_timer, first_interval=0.0, persistent=True)


def unregister() -> None:
    global _motion_timer, _system_timer, _suspended
    _suspended = True
    for timer in (_motion_timer, _system_timer):
        if timer is None:
            continue
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except ValueError:
            pass
    _motion_timer = None
    _system_timer = None
    _motion_callbacks.clear()
    _system_callbacks.clear()
    _callback_error_times.clear()
    bpy.app.driver_namespace.pop(GENERATION_KEY, None)
