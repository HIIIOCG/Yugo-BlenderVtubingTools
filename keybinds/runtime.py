"""One modal keyboard listener driving multiple per-avatar Keybind layers."""

from __future__ import annotations

import time

from .. import scheduler
from ..state import (
    active_armature,
    find_avatar_for_layer,
    layer_locator,
    resolve_layer_locator,
)
from .logic import eased_value, expression_mask_type, key_matches, normalize_expression_name
from .vrm import expression_lookup


_operator = None
_window_manager = None
_targets = {}
_last_tick = 0.0
_last_redraw_time = 0.0
_undo_in_progress = False


def _item_key(item):
    try:
        return item.as_pointer()
    except (AttributeError, ReferenceError):
        return id(item)


class _Target:
    def __init__(self, scene, layer, armature):
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.layer_pointer = layer.as_pointer()
        self.locator = layer_locator(scene, layer)
        self.held_items = set()
        self.triggered_items = set()
        self.values = {}
        self.base_values = {}
        self.last_written = {}

    def detach_for_undo(self):
        if self.scene is not None and self.layer is not None:
            self.locator = layer_locator(self.scene, self.layer) or self.locator
        self.scene = None
        self.layer = None
        self.armature = None
        self.held_items.clear()
        self.triggered_items.clear()
        self.values.clear()
        self.base_values.clear()
        self.last_written.clear()

    def rebind_after_undo(self):
        resolved = resolve_layer_locator(self.locator)
        if resolved is None:
            return False
        scene, _avatar, layer, armature = resolved
        if str(layer.source_type) != "KEYBINDS":
            return False
        if armature is None:
            return False
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.layer_pointer = layer.as_pointer()
        self.layer.connected = True
        return True

    def active(self, item):
        key = _item_key(item)
        return key in self.held_items or key in self.triggered_items

    def process_event(self, event):
        try:
            bindings = tuple(self.layer.keybinds)
        except ReferenceError:
            return False
        event_type = getattr(event, "type", "")
        if event.value == "RELEASE":
            matched = False
            for item in bindings:
                if item.mode == "HOLD" and item.key == event_type:
                    key = _item_key(item)
                    matched = matched or key in self.held_items
                    self.held_items.discard(key)
            return matched
        if bool(getattr(event, "is_repeat", False)):
            return any(key_matches(item, event) for item in bindings)
        matched = False
        for item in bindings:
            if not key_matches(item, event):
                continue
            matched = True
            key = _item_key(item)
            if item.mode == "HOLD":
                self.held_items.add(key)
            elif item.mode == "SOLO":
                solo_keys = {_item_key(other) for other in bindings if other.mode == "SOLO"}
                self.held_items.difference_update(solo_keys)
                if key in self.triggered_items:
                    self.triggered_items.remove(key)
                else:
                    self.triggered_items.difference_update(solo_keys - {key})
                    self.triggered_items.add(key)
            elif key in self.triggered_items:
                self.triggered_items.remove(key)
            else:
                self.triggered_items.add(key)
        return matched

    def layer_influence(self):
        try:
            if not self.layer.enabled:
                return 0.0
            avatar = find_avatar_for_layer(self.scene, self.layer)
            if avatar is not None and any(item.solo for item in avatar.motion_layers):
                if not self.layer.solo:
                    return 0.0
            return min(max(float(self.layer.weight), 0.0), 1.0)
        except ReferenceError:
            return 0.0

    def blendshape_mask_lookup(self):
        try:
            return {
                item.normalized_name: item
                for item in self.layer.blendshape_masks
                if item.normalized_name
            }
        except ReferenceError:
            return {}

    def capture_base(self, identifier, expression):
        current = min(max(float(getattr(expression, "preview", 0.0)), 0.0), 1.0)
        previous_write = self.last_written.get(identifier)
        if previous_write is None or abs(current - previous_write) > 0.0001:
            self.base_values[identifier] = current
        return self.base_values.get(identifier, current)

    def tick(self, delta_time):
        try:
            bindings = tuple(self.layer.keybinds)
        except ReferenceError:
            return False
        records = expression_lookup(self.armature)
        if not records:
            self.reset_expressions()
            return True

        valid_item_keys = {_item_key(item) for item in bindings}
        self.held_items.intersection_update(valid_item_keys)
        self.triggered_items.intersection_update(valid_item_keys)
        layer_influence = self.layer_influence()
        for item in bindings:
            item_key = _item_key(item)
            target = 1.0 if self.active(item) else 0.0
            self.values[item_key] = (
                target
                if item.binary
                else eased_value(self.values.get(item_key, 0.0), target, item.smooth, delta_time)
            )

        active_bindings = [
            item
            for item in bindings
            if layer_influence > 0.0 and self.values.get(_item_key(item), 0.0) > 0.0
        ]
        active_solos = [item for item in active_bindings if item.mode == "SOLO"]
        if active_solos:
            active_bindings = active_solos
        else:
            exclusive = next((item for item in active_bindings if item.mask_exclusive), None)
            if exclusive is not None:
                active_bindings = [exclusive]

        lock_eyes = any(item.mask_eyes for item in active_bindings)
        lock_mouth = any(item.mask_mouth for item in active_bindings)
        try:
            for mask_item in self.layer.blendshape_masks:
                mask_item.raw_value = 0.0
                mask_item.output_value = 0.0
        except ReferenceError:
            pass

        desired = {}
        for identifier, record in records.items():
            base = self.capture_base(identifier, record.expression)
            mask_type = expression_mask_type(record.name)
            desired[identifier] = (
                0.0
                if (lock_eyes and mask_type == "EYES") or (lock_mouth and mask_type == "MOUTH")
                else base
            )

        mask_lookup = self.blendshape_mask_lookup()
        for item in active_bindings:
            record = records.get(item.expression_id)
            if record is None:
                continue
            raw = self.values.get(_item_key(item), 0.0) * float(item.value)
            mask_item = mask_lookup.get(normalize_expression_name(record.name))
            mask_weight = float(mask_item.weight) if mask_item is not None else 1.0
            contribution = raw * layer_influence * mask_weight
            desired[record.identifier] = max(desired[record.identifier], contribution)
            if mask_item is not None:
                mask_item.raw_value = raw
                mask_item.output_value = min(max(contribution, 0.0), 1.0)

        controlled = {
            item.expression_id
            for item in active_bindings
            if item.expression_id in records and self.values.get(_item_key(item), 0.0) > 0.0
        }
        if lock_eyes:
            controlled.update(
                identifier
                for identifier, record in records.items()
                if expression_mask_type(record.name) == "EYES"
            )
        if lock_mouth:
            controlled.update(
                identifier
                for identifier, record in records.items()
                if expression_mask_type(record.name) == "MOUTH"
            )

        still_driven = set()
        for identifier in controlled:
            record = records[identifier]
            value = min(max(float(desired[identifier]), 0.0), 1.0)
            try:
                record.expression.preview = value
                self.last_written[identifier] = float(record.expression.preview)
                still_driven.add(identifier)
            except (AttributeError, ReferenceError, TypeError):
                continue
        for identifier in tuple(self.last_written):
            if identifier in still_driven:
                continue
            record = records.get(identifier)
            if record is not None:
                try:
                    record.expression.preview = self.base_values.get(identifier, 0.0)
                except (AttributeError, ReferenceError, TypeError):
                    pass
            self.last_written.pop(identifier, None)
            self.base_values.pop(identifier, None)
        return True

    def reset_expressions(self):
        records = expression_lookup(self.armature)
        for identifier in tuple(self.last_written):
            record = records.get(identifier)
            if record is not None:
                try:
                    record.expression.preview = self.base_values.get(identifier, 0.0)
                except (AttributeError, ReferenceError, TypeError):
                    pass
        self.last_written.clear()
        self.base_values.clear()
        try:
            for item in self.layer.blendshape_masks:
                item.raw_value = 0.0
                item.output_value = 0.0
        except ReferenceError:
            pass

    def close(self, *, reset=True):
        if reset:
            self.reset_expressions()
            try:
                self.layer.connected = False
            except ReferenceError:
                pass
        self.held_items.clear()
        self.triggered_items.clear()
        self.values.clear()


def is_running():
    return _operator is not None and bool(_targets)


def is_layer_running(layer):
    if layer is None:
        return False
    try:
        return layer.as_pointer() in _targets
    except ReferenceError:
        return False


def start(operator, context, layer):
    global _operator, _window_manager, _last_tick
    if is_layer_running(layer):
        raise RuntimeError("This Keybinds layer is already listening")
    armature = active_armature(context.scene)
    if armature is None:
        raise RuntimeError("Select a VRM Armature first")
    if not layer.keybinds:
        raise RuntimeError("Add at least one keybind first")
    target = _Target(context.scene, layer, armature)
    _targets[target.layer_pointer] = target
    layer.connected = True
    if _operator is None:
        _operator = operator
        _window_manager = context.window_manager
        _last_tick = time.monotonic()
        context.window_manager.modal_handler_add(operator)
        return True
    return False


def owns(operator):
    return _operator is operator


def stop(layer=None, *, reset=True):
    global _operator, _window_manager, _last_tick, _last_redraw_time
    if layer is not None:
        try:
            pointer = layer.as_pointer()
        except (AttributeError, ReferenceError):
            return
        target = _targets.pop(pointer, None)
        if target is not None:
            target.close(reset=reset)
        if _targets:
            _redraw_view3d(force=True)
            return
    else:
        for target in tuple(_targets.values()):
            target.close(reset=reset)
        _targets.clear()
    _operator = None
    _window_manager = None
    _last_tick = 0.0
    _last_redraw_time = 0.0


def process_event(event):
    if _undo_in_progress:
        return False
    if getattr(event, "value", "") not in {"PRESS", "RELEASE"}:
        return False
    matched = False
    for target in tuple(_targets.values()):
        matched = target.process_event(event) or matched
    if matched:
        _redraw_view3d(force=True)
    return matched


def binding_is_on(item):
    if _undo_in_progress:
        return False
    key = _item_key(item)
    return any(key in target.held_items or key in target.triggered_items for target in _targets.values())


def _redraw_view3d(force=False):
    global _last_redraw_time
    now = time.monotonic()
    if not force and now - _last_redraw_time < scheduler.SYSTEM_INTERVAL:
        return
    _last_redraw_time = now
    if _window_manager is None:
        return
    for window in _window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def tick():
    global _last_tick
    if _undo_in_progress or not _targets:
        return
    now = time.monotonic()
    delta_time = min(max(now - _last_tick, 0.0), 0.1)
    _last_tick = now
    for pointer, target in tuple(_targets.items()):
        try:
            if not target.tick(delta_time):
                _targets.pop(pointer, None)
        except (AttributeError, ReferenceError, RuntimeError):
            target.close()
            _targets.pop(pointer, None)
    _redraw_view3d()


def reset_expressions():
    for target in tuple(_targets.values()):
        target.reset_expressions()


def prepare_undo():
    global _undo_in_progress
    _undo_in_progress = True
    for target in tuple(_targets.values()):
        try:
            target.detach_for_undo()
        except (AttributeError, ReferenceError, RuntimeError):
            target.scene = None
            target.layer = None
            target.armature = None


def finish_undo():
    global _undo_in_progress, _last_tick
    try:
        rebound_targets = {}
        for old_pointer, target in tuple(_targets.items()):
            try:
                target.rebind_after_undo()
            except (AttributeError, ReferenceError, RuntimeError):
                target.scene = None
                target.layer = None
                target.armature = None
            key = target.layer_pointer if target.layer is not None else old_pointer
            rebound_targets[key] = target
        _targets.clear()
        _targets.update(rebound_targets)
        _last_tick = time.monotonic()
    finally:
        _undo_in_progress = False


def _motion_update(_context, _delta_time):
    tick()


def register():
    scheduler.add_motion("keybinds", _motion_update, priority=300)


def unregister():
    scheduler.remove_motion("keybinds")
    stop()
