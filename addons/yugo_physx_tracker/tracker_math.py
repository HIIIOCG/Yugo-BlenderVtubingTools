"""SMPL AMASS inference contract, NumPy implementation (WXYZ quaternions).

Observation order follows NVlabs/ProtoMotions 7a8417a9, envs/obs/{humanoid,
target_poses}.py. Parity is tested against those upstream Torch functions.
"""
import numpy as np


def conjugate(q):
    return np.asarray(q) * np.array([1., -1., -1., -1.])


def multiply(a, b):
    a, b = np.broadcast_arrays(a, b)
    return np.concatenate((a[..., :1]*b[..., :1] - np.sum(a[..., 1:]*b[..., 1:], axis=-1, keepdims=True),
                           a[..., :1]*b[..., 1:] + b[..., :1]*a[..., 1:] + np.cross(a[..., 1:], b[..., 1:])), axis=-1)


def rotate(q, v):
    q, v = np.asarray(q), np.asarray(v)
    t = 2*np.cross(q[..., 1:], v)
    return v + q[..., :1]*t + np.cross(q[..., 1:], t)


def from_exp(v):
    v = np.asarray(v)
    angle = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.concatenate((np.cos(angle*.5), v * (.5*np.sinc(angle/(2*np.pi)))), axis=-1)


def to_exp(q):
    q = np.asarray(q) / np.linalg.norm(q, axis=-1, keepdims=True)
    q = np.where(q[..., :1] < 0, -q, q)
    length = np.linalg.norm(q[..., 1:], axis=-1, keepdims=True)
    return q[..., 1:] * (2*np.arctan2(length, q[..., :1])/np.maximum(length, 1e-12))


def slerp(a, b, t):
    return multiply(a, from_exp(to_exp(multiply(conjugate(a), b))*t))


def tan_norm(q):
    return np.concatenate((rotate(q, np.array([1., 0., 0.])), rotate(q, np.array([0., 0., 1.]))), axis=-1)


def heading_inverse(q):
    direction = rotate(q, np.array([1., 0., 0.]))
    angle = -np.arctan2(direction[..., 1], direction[..., 0])
    return from_exp(np.stack((angle*0, angle*0, angle), axis=-1))


def observations(current, target, previous, ground_height=0):
    """358 self + 576 one-frame future target + 69 previous raw actions."""
    p, q, v, w = current
    tp, tq, tv, tw = target
    h = heading_inverse(q[0])
    flat = lambda a: np.asarray(a).reshape(-1)
    own = np.concatenate(([p[0, 2]-ground_height], flat(rotate(h, p[1:]-p[0])),
                          flat(tan_norm(multiply(h, q))), flat(rotate(h, v)), flat(rotate(h, w))))
    future = np.concatenate((flat(rotate(h, tp-p[0])), flat(rotate(h, tp-p)),
                             flat(tan_norm(multiply(h, tq))), flat(tan_norm(multiply(conjugate(q), tq))),
                             flat(rotate(h, tv-v)), flat(rotate(h, tw-w))))
    result = np.concatenate((own, future, previous)).astype(np.float32)[None, :]
    if result.shape != (1, 1003) or not np.isfinite(result).all():
        raise ValueError('SMPL tracker requires finite 24-body state / 69 previous actions')
    return result

