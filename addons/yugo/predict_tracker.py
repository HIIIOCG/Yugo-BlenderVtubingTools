"""Critically damped second-order tracking for the Predict Data stage."""

import math
from collections import deque

from mathutils import Quaternion, Vector

from .motion_processing import (
    quaternion_to_rotation_vector,
    rotation_vector_to_quaternion,
)


REPEATED_FRAME_LIMIT = 7
TRACKING_SETTLING_TIME = 0.16
INTERRUPTED_SETTLING_TIME = 0.70
TRANSITION_EASE_IN_TIME = 0.60
VALUE_EPSILON = 1e-6
ROTATION_EPSILON = 1e-7
VELOCITY_HISTORY_SIZE = 10


class ScalarSecondOrderTracker:
    def __init__(self):
        self.initialized = False
        self.value = 0.0
        self.velocity = 0.0

    def initialize(self, value):
        self.initialized = True
        self.value = float(value)
        self.velocity = 0.0

    def step(
        self, target, settling_time, delta_time, response_scale=1.0
    ):
        target = float(target)
        if not self.initialized:
            self.initialize(target)
            return self.value

        delta_time = min(max(float(delta_time), 0.0), 0.1)
        if delta_time <= 0.0:
            return self.value
        omega = (
            4.0 / max(float(settling_time), 1e-4)
        ) * max(min(float(response_scale), 1.0), 0.0)
        offset = self.value - target
        coefficient = self.velocity + (omega * offset)
        decay = math.exp(-omega * delta_time)
        self.value = target + ((offset + coefficient * delta_time) * decay)
        self.velocity = (
            self.velocity - (omega * coefficient * delta_time)
        ) * decay
        return self.value


class QuaternionSecondOrderTracker:
    def __init__(self):
        self.initialized = False
        self.value = Quaternion()
        self.angular_velocity = Vector((0.0, 0.0, 0.0))

    def initialize(self, value):
        self.initialized = True
        self.value = value.copy()
        self.value.normalize()
        self.angular_velocity = Vector((0.0, 0.0, 0.0))

    def step(
        self, target, settling_time, delta_time, response_scale=1.0
    ):
        target = target.copy()
        target.normalize()
        if not self.initialized:
            self.initialize(target)
            return self.value.copy()

        delta_time = min(max(float(delta_time), 0.0), 0.1)
        if delta_time <= 0.0:
            return self.value.copy()
        omega = (
            4.0 / max(float(settling_time), 1e-4)
        ) * max(min(float(response_scale), 1.0), 0.0)
        error = quaternion_to_rotation_vector(self.value @ target.inverted())
        coefficient = self.angular_velocity + (error * omega)
        decay = math.exp(-omega * delta_time)
        new_error = (error + coefficient * delta_time) * decay
        self.angular_velocity = (
            self.angular_velocity - coefficient * (omega * delta_time)
        ) * decay
        self.value = rotation_vector_to_quaternion(new_error) @ target
        self.value.normalize()
        return self.value.copy()


class PredictDataTracker:
    def __init__(self):
        self.scalar_trackers = {}
        self.head_tracker = QuaternionSecondOrderTracker()
        self.latest_values = {}
        self.latest_head = None
        self.rest_head = Quaternion()
        self.previous_values = None
        self.previous_head = None
        self.repeated_frames = 0
        self.interrupted = False
        self.value_history = {}
        self.head_history = deque(maxlen=VELOCITY_HISTORY_SIZE)
        self.sample_time = 0.0
        self.transition_elapsed = TRANSITION_EASE_IN_TIME

    def reset(self):
        self.__init__()

    def set_rest_head(self, head):
        if head is None:
            return
        self.rest_head = head.copy()
        self.rest_head.normalize()

    def _same_frame(self, values, head):
        if self.previous_values is None:
            return False
        keys = set(values) | set(self.previous_values)
        if any(
            abs(values.get(key, 0.0) - self.previous_values.get(key, 0.0))
            > VALUE_EPSILON
            for key in keys
        ):
            return False
        if (head is None) != (self.previous_head is None):
            return False
        if head is not None:
            delta = head @ self.previous_head.inverted()
            if quaternion_to_rotation_vector(delta).length > ROTATION_EPSILON:
                return False
        return True

    @staticmethod
    def _regression_slope(samples):
        if len(samples) < 2:
            return 0.0
        mean_time = sum(time for time, _value in samples) / len(samples)
        mean_value = sum(value for _time, value in samples) / len(samples)
        denominator = sum(
            (time - mean_time) ** 2 for time, _value in samples
        )
        if denominator < 1e-10:
            return 0.0
        return sum(
            (time - mean_time) * (value - mean_value)
            for time, value in samples
        ) / denominator

    def _record_history(self, values, head, sample_time):
        keys = set(self.value_history) | set(values)
        for key in keys:
            history = self.value_history.setdefault(
                key, deque(maxlen=VELOCITY_HISTORY_SIZE)
            )
            history.append((sample_time, values.get(key, 0.0)))
        if head is not None:
            self.head_history.append((sample_time, head.copy()))

    def _estimated_value_velocity(self, key):
        return self._regression_slope(self.value_history.get(key, ()))

    def _estimated_angular_velocity(self):
        if len(self.head_history) < 2:
            return Vector((0.0, 0.0, 0.0))
        reference = self.head_history[0][1]
        vector_samples = [
            (
                sample_time,
                quaternion_to_rotation_vector(rotation @ reference.inverted()),
            )
            for sample_time, rotation in self.head_history
        ]
        return Vector(
            tuple(
                self._regression_slope(
                    [
                        (sample_time, vector[axis])
                        for sample_time, vector in vector_samples
                    ]
                )
                for axis in range(3)
            )
        )

    def _apply_estimated_velocity(self):
        for key, tracker in self.scalar_trackers.items():
            tracker.velocity = self._estimated_value_velocity(key)
        self.head_tracker.angular_velocity = self._estimated_angular_velocity()

    def observe(
        self, values, head, repeated_count=1, sample_time=None
    ):
        values = {name: float(value) for name, value in values.items()}
        if head is not None:
            head = head.copy()
            head.normalize()

        same_frame = self._same_frame(values, head)
        was_interrupted = self.interrupted
        if same_frame:
            self.repeated_frames += max(int(repeated_count), 1)
        else:
            self.repeated_frames = 1
            self.interrupted = False

        self.interrupted = self.repeated_frames >= REPEATED_FRAME_LIMIT
        if self.interrupted != was_interrupted:
            self.transition_elapsed = 0.0
        if sample_time is None:
            self.sample_time += 1.0 / 60.0
            sample_time = self.sample_time
        else:
            self.sample_time = float(sample_time)
            sample_time = self.sample_time
        self._record_history(values, head, sample_time)
        keys = set(self.latest_values) | set(values)
        self.latest_values = {key: values.get(key, 0.0) for key in keys}
        self.latest_head = head
        self.previous_values = dict(values)
        self.previous_head = head.copy() if head is not None else None

        for key, value in self.latest_values.items():
            tracker = self.scalar_trackers.setdefault(
                key, ScalarSecondOrderTracker()
            )
            if not tracker.initialized:
                tracker.initialize(value)
        if head is not None and not self.head_tracker.initialized:
            self.head_tracker.initialize(head)
        if self.interrupted and not was_interrupted:
            self._apply_estimated_velocity()

    def evaluate(self, delta_time):
        settling_time = (
            INTERRUPTED_SETTLING_TIME
            if self.interrupted
            else TRACKING_SETTLING_TIME
        )
        transition_progress = min(
            self.transition_elapsed / TRANSITION_EASE_IN_TIME, 1.0
        )
        response_scale = transition_progress * transition_progress * transition_progress
        self.transition_elapsed = min(
            self.transition_elapsed + max(float(delta_time), 0.0),
            TRANSITION_EASE_IN_TIME,
        )
        predicted_values = {
            key: tracker.step(
                0.0 if self.interrupted else self.latest_values.get(key, 0.0),
                settling_time,
                delta_time,
                response_scale,
            )
            for key, tracker in self.scalar_trackers.items()
        }
        predicted_head = None
        if self.head_tracker.initialized:
            target_head = (
                self.rest_head
                if self.interrupted
                else (
                    self.latest_head
                    if self.latest_head is not None
                    else self.rest_head
                )
            )
            predicted_head = self.head_tracker.step(
                target_head, settling_time, delta_time, response_scale
            )
        return predicted_values, predicted_head
