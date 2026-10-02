"""Blender-independent message snapshots and smoothly rebased stack layout."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from numbers import Real
import uuid


def _finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _validate_json(value) -> None:
    """Reject handles, implicit JSON coercions and non-finite metadata."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Message payload must contain finite JSON numbers")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Message payload JSON keys must be strings")
            _validate_json(item)
        return
    raise TypeError("Message payload must contain only JSON data")


@dataclass(frozen=True, slots=True)
class Message:
    """An immutable display snapshot, without platform or Blender references."""

    id: str
    text: str
    author: str = ""
    avatar_path: str = ""

    def __post_init__(self):
        for name in ("id", "text", "author", "avatar_path"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"Message {name} must be a string")
        if not self.id.strip():
            raise ValueError("Message id must not be blank")

    @classmethod
    def from_payload(cls, payload: Mapping) -> Message:
        """Validate a JSON event and retain only the supported display fields.

        Extra JSON metadata is accepted for platform adapters. It is not kept
        by the display model, so a later mutation cannot change this snapshot.
        Missing optional strings are empty; a supplied null is not a string.
        """
        if not isinstance(payload, Mapping):
            raise TypeError("Message payload must be a mapping")
        value = dict(payload)
        # Reject cycles before recursively checking strict JSON container types.
        json.dumps(value, allow_nan=False)
        _validate_json(value)
        if "text" not in value:
            raise ValueError("Message payload requires text")
        return cls(
            id=value["id"] if "id" in value else uuid.uuid4().hex,
            text=value["text"],
            author=value.get("author", ""),
            avatar_path=value.get("avatar_path", ""),
        )


class StackModel:
    """Bounded oldest-first messages with newest-at-zero vertical offsets.

    All times come from the caller. Reading positions has no side effects and
    adding during a movement starts the next movement at its current position.
    Configuration is immutable; rebuild the model when panel settings change.
    """

    def __init__(self, max_messages=8, spacing=1.0, move_duration=0.25):
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise TypeError("max_messages must be an integer")
        if not 1 <= max_messages <= 200:
            raise ValueError("max_messages must be between 1 and 200")
        spacing = _finite_number(spacing, "spacing")
        move_duration = _finite_number(move_duration, "move_duration")
        if spacing <= 0.0:
            raise ValueError("spacing must be positive")
        if not math.isfinite(spacing * max(1, max_messages - 1)):
            raise ValueError("spacing must produce finite stack positions")
        if move_duration < 0.0:
            raise ValueError("move_duration must not be negative")
        self._max_messages = max_messages
        self._spacing = spacing
        self._move_duration = move_duration
        self._messages: list[Message] = []
        self._from: dict[str, float] = {}
        self._to: dict[str, float] = {}
        self._start_time = 0.0

    @property
    def max_messages(self) -> int:
        return self._max_messages

    @property
    def spacing(self) -> float:
        return self._spacing

    @property
    def move_duration(self) -> float:
        return self._move_duration

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def _rebase(self, previous: dict[str, float], now: float) -> None:
        count = len(self._messages)
        self._from = {message.id: previous.get(message.id, 0.0)
                      for message in self._messages}
        self._to = {message.id: (count - 1 - index) * self._spacing
                    for index, message in enumerate(self._messages)}
        self._start_time = now

    def add(self, message: Message, now) -> list[str]:
        now = _finite_number(now, "now")
        if not isinstance(message, Message):
            raise TypeError("StackModel.add requires a Message")
        if any(item.id == message.id for item in self._messages):
            return []
        previous = self.positions(now)
        self._messages.append(message)
        evicted = []
        while len(self._messages) > self._max_messages:
            evicted.append(self._messages.pop(0).id)
        self._rebase(previous, now)
        return evicted

    def remove(self, message_id: str, now) -> bool:
        now = _finite_number(now, "now")
        if not isinstance(message_id, str):
            raise TypeError("message_id must be a string")
        index = next((index for index, item in enumerate(self._messages)
                      if item.id == message_id), None)
        if index is None:
            return False
        previous = self.positions(now)
        del self._messages[index]
        self._rebase(previous, now)
        return True

    def clear(self) -> list[str]:
        removed = [message.id for message in self._messages]
        self._messages.clear()
        self._from.clear()
        self._to.clear()
        self._start_time = 0.0
        return removed

    def positions(self, now) -> dict[str, float]:
        now = _finite_number(now, "now")
        if self._move_duration == 0.0:
            return dict(self._to)
        amount = min(1.0, max(0.0, (now - self._start_time) / self._move_duration))
        eased = amount * amount * (3.0 - 2.0 * amount)
        return {message_id: self._from[message_id] +
                (target - self._from[message_id]) * eased
                for message_id, target in self._to.items()}
