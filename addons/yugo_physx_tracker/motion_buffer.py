"""Delayed reference poses and world-space derivatives for live SMPL tracking."""
from collections import deque
import math
import numpy as np
from . import tracker_math as tm


class MotionBuffer:
    def __init__(self, delay=.1, max_age=.5, max_samples=120):
        if not math.isfinite(delay) or not math.isfinite(max_age) or delay < 0 or max_age <= delay:
            raise ValueError('Motion buffer needs nonnegative delay and a longer history')
        if type(max_samples) is not int or max_samples < 2:
            raise ValueError('Motion buffer needs at least two samples')
        self.delay = float(delay)
        self.max_age = float(max_age)
        self.frames = deque(maxlen=max_samples)

    def clear(self):
        self.frames.clear()

    def push(self, seconds, positions, rotations, velocities=None, angular_velocities=None):
        seconds = float(seconds)
        if not math.isfinite(seconds):
            raise ValueError('Motion timestamp must be finite')
        if self.frames and seconds < self.frames[-1][0]:
            raise ValueError('Motion timestamps must increase')
        p = self._array(positions, (24, 3), 'positions')
        q = self._array(rotations, (24, 4), 'rotations')
        norm = np.linalg.norm(q, axis=-1, keepdims=True)
        if np.any(np.abs(norm-1) > .001):
            raise ValueError('Motion rotations must be unit WXYZ quaternions')
        q /= norm
        v = None if velocities is None else self._array(velocities, (24, 3), 'velocities')
        w = None if angular_velocities is None else self._array(angular_velocities, (24, 3), 'angular velocities')
        frame = (seconds, p, q, v, w)
        if self.frames and seconds == self.frames[-1][0]:
            self.frames[-1] = frame
        else:
            self.frames.append(frame)
        cutoff = seconds-self.max_age
        while len(self.frames) > 2 and self.frames[1][0] < cutoff:
            self.frames.popleft()

    @staticmethod
    def _array(value, shape, name):
        result = np.array(value, dtype=np.float64, copy=True)
        if result.shape != shape or not np.isfinite(result).all():
            raise ValueError('Invalid motion '+name)
        return result

    @staticmethod
    def _hold(frame):
        _, p, q, _, _ = frame
        return p.copy(), q.copy(), np.zeros_like(p), np.zeros_like(p)

    def sample(self, seconds):
        if not self.frames:
            raise ValueError('Live reference has not received a pose')
        seconds = float(seconds)
        if not math.isfinite(seconds):
            raise ValueError('Motion sample time must be finite')
        stamp = seconds-self.delay
        if len(self.frames) == 1 or stamp >= self.frames[-1][0]:
            return self._hold(self.frames[-1])
        if stamp < self.frames[0][0]:
            return self._hold(self.frames[0])
        for a, b in zip(self.frames, list(self.frames)[1:]):
            if stamp <= b[0]:
                dt = b[0]-a[0]
                alpha = (stamp-a[0])/dt
                p = a[1]*(1-alpha)+b[1]*alpha
                q = tm.slerp(a[2], b[2], alpha)
                v = (b[1]-a[1])/dt if a[3] is None or b[3] is None else a[3]*(1-alpha)+b[3]*alpha
                # Left-multiplication extracts angular velocity in world axes.
                w = tm.to_exp(tm.multiply(b[2], tm.conjugate(a[2])))/dt if a[4] is None or b[4] is None else a[4]*(1-alpha)+b[4]*alpha
                return p, q, v, w
        return self._hold(self.frames[-1])
