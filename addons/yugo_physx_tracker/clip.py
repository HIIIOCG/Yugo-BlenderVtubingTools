import numpy as np
from . import tracker_math as tm

class MotionClip:
    def __init__(self, path, names):
        with np.load(path, allow_pickle=False) as data:
            if list(data['body_names']) != list(names):
                raise ValueError('Motion clip body order does not match the SMPL tracker')
            self.fps = float(data['fps'])
            self.pos, self.rot, self.vel, self.ang = [np.array(data[k], dtype=np.float64) for k in ('positions', 'rotations', 'velocities', 'angular_velocities')]
        n = len(self.pos)
        expected = [(n, 24, 3), (n, 24, 4), (n, 24, 3), (n, 24, 3)]
        if n < 2 or not np.isfinite(self.fps) or self.fps <= 0:
            raise ValueError('Motion clip requires at least two frames and a positive FPS')
        for value, shape in zip((self.pos, self.rot, self.vel, self.ang), expected):
            if value.shape != shape or not np.isfinite(value).all():
                raise ValueError('Invalid SMPL motion clip arrays')
        norm = np.linalg.norm(self.rot, axis=-1, keepdims=True)
        if np.min(norm) < 1e-6:
            raise ValueError('Motion clip contains a zero quaternion')
        self.rot /= norm
        self.duration = (n-1)/self.fps

    def sample(self, seconds):
        f = np.clip(seconds*self.fps, 0, len(self.pos)-1)
        a = min(int(f), len(self.pos)-2)
        t = f-a
        lerp = lambda array: array[a]*(1-t)+array[a+1]*t
        p, q = lerp(self.pos), tm.slerp(self.rot[a], self.rot[a+1], t)
        if seconds >= self.duration:
            return p, q, np.zeros_like(p), np.zeros_like(p)
        return p, q, lerp(self.vel), lerp(self.ang)

