"""Pure solver checks; do not import Blender or the add-on registration layer."""

import importlib.util
import math
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "addons/yugu_node_studio/modules/ragdoll/solver.py"
SPEC = importlib.util.spec_from_file_location("ragdoll_solver_test_module", SOURCE)
solver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = solver
SPEC.loader.exec_module(solver)
BoneSpec, JointLimit, Settings, Ragdoll = solver.BoneSpec, solver.JointLimit, solver.Settings, solver.Ragdoll


def distance(a, b):
    return math.dist(a, b)


def angle(first, second):
    a = tuple(first[1][i] - first[0][i] for i in range(3))
    b = tuple(second[1][i] - second[0][i] for i in range(3))
    cosine = sum(a[i] * b[i] for i in range(3)) / (math.hypot(*a) * math.hypot(*b))
    return math.acos(max(-1.0, min(1.0, cosine)))


def humanoid(height=0.0):
    def p(x, z):
        return (x, 0.0, z + height)
    return [
        BoneSpec("hips", None, p(0, 1.0), p(0, 1.15), mass=6),
        BoneSpec("spine", "hips", p(0, 1.15), p(0, 1.4), mass=4),
        BoneSpec("chest", "spine", p(0, 1.4), p(0, 1.6), mass=4),
        BoneSpec("head", "chest", p(0, 1.6), p(0, 1.9), mass=3, radius=.09),
        BoneSpec("leftUpperArm", "chest", p(.12, 1.57), p(.42, 1.57)),
        BoneSpec("leftLowerArm", "leftUpperArm", p(.42, 1.57), p(.7, 1.57)),
        BoneSpec("rightUpperArm", "chest", p(-.12, 1.57), p(-.42, 1.57)),
        BoneSpec("rightLowerArm", "rightUpperArm", p(-.42, 1.57), p(-.7, 1.57)),
        BoneSpec("leftUpperLeg", "hips", p(.1, 1.0), p(.1, .55), mass=3),
        BoneSpec("leftLowerLeg", "leftUpperLeg", p(.1, .55), p(.1, .1), mass=2),
        BoneSpec("rightUpperLeg", "hips", p(-.1, 1.0), p(-.1, .55), mass=3),
        BoneSpec("rightLowerLeg", "rightUpperLeg", p(-.1, .55), p(-.1, .1), mass=2),
    ]


def advance(body, seconds, hz=60):
    for _ in range(round(seconds * hz)):
        body.step(1.0 / hz)


class SolverTests(unittest.TestCase):
    def test_targeted_and_additive_impulses_do_not_reset_pose(self):
        body = Ragdoll(humanoid(), Settings(auto_recover=False))
        original = body.pose()
        body.hit("head", (3.0, 0.0, 0.0))
        self.assertEqual(body.pose(), original)
        self.assertEqual(body.velocities["head"], ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
        self.assertEqual(body.velocities["hips"], ((0.0, 0.0, 0.0),) * 2)
        body.hit("head", (6.0, 0.0, 0.0))
        self.assertEqual(body.velocities["head"], ((3.0, 0.0, 0.0),) * 2)
        body.step(1 / 60)
        before = body.pose()
        speed = body.velocities["head"]
        body.hit("head", (3.0, 0.0, 0.0))
        self.assertEqual(body.pose(), before)
        self.assertAlmostEqual(body.velocities["head"][0][0], speed[0][0] + 1.0)
        self.assertEqual(body.since_hit, 0.0)

    def test_connected_lengths_ground_and_bend_limits_during_fall(self):
        bones = humanoid()
        body = Ragdoll(bones, Settings(auto_recover=False, timeout=12))
        body.hit("chest", (16.0, 7.0, 3.0))
        peak_gap, peak_length_error, peak_angle_excess = 0.0, 0.0, 0.0
        for _ in range(360):
            body.step(1 / 60)
            pose = body.pose()
            for bone in bones:
                peak_length_error = max(peak_length_error, abs(distance(*pose[bone.name]) - distance(bone.head, bone.tail)))
                for endpoint in pose[bone.name]:
                    self.assertGreaterEqual(endpoint[2] + 1e-8, body.settings.ground_z + bone.radius)
                if bone.parent and distance(bone.head, next(b.tail for b in bones if b.name == bone.parent)) < 1e-8:
                    peak_gap = max(peak_gap, distance(pose[bone.name][0], pose[bone.parent][1]))
                elif bone.parent:
                    rest_offset = distance(bone.head, next(b.tail for b in bones if b.name == bone.parent))
                    self.assertAlmostEqual(distance(pose[bone.name][0], pose[bone.parent][1]), rest_offset, places=7)
                if bone.parent and "Lower" in bone.name:
                    peak_angle_excess = max(peak_angle_excess, angle(pose[bone.parent], pose[bone.name]) - math.radians(145))
        self.assertLess(peak_length_error, 1e-5)
        self.assertLess(peak_gap, 1e-5)
        self.assertLess(peak_angle_excess, 1e-5)
        self.assertTrue(body.contact)
        self.assertTrue(all(math.isfinite(x) for pair in body.pose().values() for p in pair for x in p))

    def test_updates_at_8_12_24_30_60_hz_have_identical_simulation_time_and_pose(self):
        results = []
        for hz in (8, 12, 24, 30, 60):
            body = Ragdoll(humanoid(), Settings(auto_recover=False))
            body.hit("leftLowerArm", (2.0, 1.0, 0.0))
            advance(body, .5, hz)
            self.assertAlmostEqual(body.elapsed, .5)
            self.assertAlmostEqual(body.dropped_time, 0.0)
            results.append(body.pose())
        for result in results[1:]:
            for name, endpoints in result.items():
                for point, reference in zip(endpoints, results[0][name]):
                    self.assertLess(distance(point, reference), 1e-10)

    def test_repeated_reads_are_pure_and_fractional_time_is_accumulated(self):
        body = Ragdoll(humanoid())
        original = body.pose()
        for _ in range(100):
            self.assertEqual(body.pose(), original)
            self.assertEqual(body.elapsed, 0.0)
            self.assertEqual(body.velocities["head"], ((0.0, 0.0, 0.0),) * 2)
        body.step(1 / 240)
        self.assertEqual(body.pose(), original)
        body.step(1 / 240)
        self.assertAlmostEqual(body.elapsed, 1 / 120)
        self.assertNotEqual(body.pose(), original)

    def test_stall_cap_discards_excess_instead_of_catching_up_next_tick(self):
        body = Ragdoll(humanoid(), Settings(auto_recover=False))
        body.step(10.0)
        self.assertAlmostEqual(body.elapsed, .25)
        self.assertAlmostEqual(body.dropped_time, 9.75)
        body.step(1 / 60)
        self.assertAlmostEqual(body.elapsed, .25 + 1 / 60)

    def test_recovery_requires_hold_ground_and_continuous_settling(self):
        bone = BoneSpec("body", None, (0, 0, .05), (.4, 0, .05), radius=.05)
        body = Ragdoll([bone], Settings(min_hold=.5, settle_time=.2, recovery_duration=.25))
        advance(body, .4)
        self.assertEqual(body.state, solver.PHYSICS)
        self.assertTrue(body.contact)
        self.assertLessEqual(body.speed, body.settings.settle_speed)
        advance(body, .2)
        self.assertEqual(body.state, solver.RECOVERING)
        advance(body, .2)
        self.assertEqual(body.state, solver.COMPLETED)
        self.assertEqual(body.pose(), {bone.name: (bone.head, bone.tail)})
        airborne = Ragdoll([BoneSpec("body", None, (0, 0, 4), (.4, 0, 4))],
                           Settings(gravity=(0, 0, 0), min_hold=0, settle_time=0, timeout=1))
        advance(airborne, .5)
        self.assertEqual(airborne.state, solver.PHYSICS)
        self.assertFalse(airborne.contact)

    def test_motion_resets_settled_dwell(self):
        bone = BoneSpec("body", None, (0, 0, .05), (.4, 0, .05), radius=.05)
        body = Ragdoll([bone], Settings(min_hold=0, settle_time=.3, recovery_duration=.5))
        advance(body, .2)
        self.assertGreater(body.settled_elapsed, .1)
        body.hit("body", (1, 0, 0))
        self.assertEqual(body.settled_elapsed, 0.0)
        advance(body, .2)
        self.assertEqual(body.state, solver.PHYSICS)

    def test_full_target_recovery_preserves_lengths_and_hit_cancels_recovery(self):
        bones = [BoneSpec("root", None, (0, 0, .05), (.4, 0, .05), radius=.05),
                 BoneSpec("child", "root", (.4, 0, .05), (.8, 0, .05), radius=.05)]
        body = Ragdoll(bones, Settings(min_hold=.1, settle_time=.05, recovery_duration=.5))
        target = {"root": ((0, 0, 1), (0, 0, 1.4)), "child": ((0, 0, 1.4), (0, 0, 1.8))}
        body.set_target(target)
        advance(body, .2)
        self.assertEqual(body.state, solver.RECOVERING)
        for pair in body.pose().values():
            self.assertAlmostEqual(distance(*pair), .4, places=7)
        self.assertLess(distance(body.pose()["root"][1], body.pose()["child"][0]), 1e-7)
        before, velocities = body.pose(), body.velocities
        body.hit("child", (2, 0, 0))
        self.assertEqual(body.state, solver.PHYSICS)
        self.assertEqual(body.pose(), before)
        self.assertEqual(body.velocities["root"], velocities["root"])
        self.assertAlmostEqual(body.velocities["child"][0][0], velocities["child"][0][0] + 2)
        advance(body, 4)
        self.assertEqual(body.state, solver.COMPLETED)
        self.assertEqual(body.pose(), target)

    def test_explicit_recovery_and_timeout_and_cancel(self):
        bone = BoneSpec("body", None, (0, 0, .04), (.4, 0, .04))
        body = Ragdoll([bone], Settings(auto_recover=False, min_hold=.1, settle_time=.05))
        advance(body, .3)
        self.assertEqual(body.state, solver.PHYSICS)
        body.recover()
        advance(body, 1)
        self.assertEqual(body.state, solver.COMPLETED)
        flying = Ragdoll([bone], Settings(gravity=(0, 0, 0), ground_z=-10, timeout=.2))
        advance(flying, .3)
        self.assertEqual(flying.state, solver.FAILED)
        self.assertIn("timeout", flying.failure_reason)
        cancelled = Ragdoll([bone])
        cancelled.step(.1)
        cancelled.cancel()
        before, elapsed = cancelled.pose(), cancelled.elapsed
        cancelled.step(1)
        self.assertEqual(cancelled.state, solver.CANCELLED)
        self.assertEqual(cancelled.pose(), before)
        self.assertEqual(cancelled.elapsed, elapsed)

    def test_humanoid_offset_joints_settle_after_a_strong_hit_and_restore_pose(self):
        body = Ragdoll(humanoid())
        original = body.pose()
        body.hit("chest", (16.0, 7.0, 3.0))
        advance(body, 8)
        self.assertEqual(body.state, solver.COMPLETED, body.error)
        self.assertEqual(body.pose(), original)
        self.assertGreater(body.elapsed, body.settings.min_hold + body.settings.settle_time)

    def test_manual_physics_has_no_recovery_timeout_until_recovery_requested(self):
        bone = BoneSpec("body", None, (0, 0, 4), (.4, 0, 4))
        body = Ragdoll([bone], Settings(auto_recover=False, gravity=(0, 0, 0), timeout=.2))
        advance(body, 1)
        self.assertEqual(body.state, solver.PHYSICS)
        body.recover()
        advance(body, .1)
        self.assertEqual(body.state, solver.PHYSICS)
        advance(body, .2)
        self.assertEqual(body.state, solver.FAILED)
        self.assertEqual(body.error, body.failure_reason)

    def test_recovery_rejects_broken_parent_attachments_atomically(self):
        bones = [BoneSpec("root", None, (0, 0, .05), (.4, 0, .05), radius=.05),
                 BoneSpec("connected", "root", (.4, 0, .05), (.8, 0, .05), radius=.05),
                 BoneSpec("offset", "root", (.4, .2, .05), (.8, .2, .05), radius=.05)]
        body = Ragdoll(bones, Settings(auto_recover=False, min_hold=.1, settle_time=.05, recovery_duration=.1))
        # Rotate the complete target by 90 degrees around Z and translate up.
        valid = {bone.name: tuple((-point[1] + 1, point[0] + 2, point[2] + 1)
                                 for point in (bone.head, bone.tail)) for bone in bones}
        body.set_target(valid)
        original_pose, original_state = body.pose(), body.state
        for name in ("connected", "offset"):
            broken = dict(valid)
            broken[name] = tuple((point[0] + .25, point[1], point[2]) for point in valid[name])
            with self.assertRaisesRegex(ValueError, "parent attachments"):
                body.set_target(broken)
            with self.assertRaisesRegex(ValueError, "parent attachments"):
                body.recover(broken)
            self.assertEqual(body.pose(), original_pose)
            self.assertEqual(body.state, original_state)
        advance(body, .3)
        self.assertEqual(body.state, solver.PHYSICS)
        body.recover()
        advance(body, 1)
        self.assertEqual(body.state, solver.COMPLETED, body.error)
        self.assertEqual(body.pose(), valid)

    def test_explicit_hinge_rejects_out_of_plane_swing(self):
        bones = [BoneSpec("parent", None, (0, 0, 2), (0, 0, 3), mass=1e12),
                 BoneSpec("child", "parent", (0, 0, 3), (0, 0, 4),
                          limit=JointLimit(0, math.pi / 2, (1, 0, 0)))]
        body = Ragdoll(bones, Settings(gravity=(0, 0, 0), auto_recover=False, iterations=32))
        body.hit("child", (3, -3, 0))
        advance(body, .5)
        pose = body.pose()
        self.assertLessEqual(angle(pose["parent"], pose["child"]), math.pi / 2 + 1e-5)
        self.assertLess(distance(pose["parent"][1], pose["child"][0]), 1e-5)
        self.assertLess(abs(pose["child"][1][0] - pose["child"][0][0]), 1e-8)

    def test_capsule_midpoint_collides_with_sphere_and_box(self):
        bone = BoneSpec("bar", None, (-1, 0, 2), (1, 0, 2), radius=.05)
        for collider in (solver.SphereCollider((0, 0, 2), .3),
                         solver.BoxCollider((-.2, -.2, 1.8), (.2, .2, 2.2))):
            body = Ragdoll([bone], Settings(gravity=(0, 0, 0), auto_recover=False,
                                          colliders=(collider,), iterations=32))
            body.step(1 / 120)
            self.assertNotEqual(body.pose()["bar"], (bone.head, bone.tail))
            self.assertAlmostEqual(distance(*body.pose()["bar"]), 2.0, places=7)
            if isinstance(collider, solver.BoxCollider):
                a, b = body.pose()["bar"]
                for sample in range(101):
                    point = tuple(a[axis] + (b[axis] - a[axis]) * sample / 100 for axis in range(3))
                    self.assertFalse(all(collider.minimum[axis] - bone.radius + 1e-7 < point[axis]
                                         < collider.maximum[axis] + bone.radius - 1e-7 for axis in range(3)))
        sphere = Ragdoll([bone], Settings(gravity=(0, 0, 0), auto_recover=False,
                                        colliders=(solver.SphereCollider((0, 0, 2), .3),)))
        sphere.step(1 / 120)
        a, b = sphere.pose()["bar"]
        self.assertGreaterEqual(distance(tuple((a[i] + b[i]) * .5 for i in range(3)), (0, 0, 2)), .35 - 1e-7)

    def test_invalid_inputs_reject_without_mutating_pose_or_nan(self):
        body = Ragdoll(humanoid())
        original = body.pose()
        for dt in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                body.step(dt)
        for impulse in ((math.nan, 0, 0), (math.inf, 0, 0), (0, 1)):
            with self.assertRaises(ValueError):
                body.hit("head", impulse)
        with self.assertRaises(ValueError):
            body.hit("missing", (0, 0, 1))
        with self.assertRaises(ValueError):
            body.recover({"head": ((0, 0, 0), (0, 0, 1))})
        self.assertEqual(body.pose(), original)
        self.assertEqual(body.elapsed, 0)
        for settings in ({"iterations": 0}, {"fixed_dt": 0}, {"damping": math.nan}, {"timeout": -1}):
            with self.assertRaises(ValueError):
                Settings(**settings)
        with self.assertRaises(ValueError):
            Ragdoll([BoneSpec("zero", None, (0, 0, 0), (0, 0, 0))])
        with self.assertRaises(ValueError):
            Ragdoll([BoneSpec("a", "b", (0, 0, 0), (0, 0, 1)),
                     BoneSpec("b", "a", (0, 0, 1), (0, 0, 2))])


    def test_numerical_overflow_fails_with_last_finite_pose(self):
        bone = BoneSpec("body", None, (0, 0, 1), (0, 0, 2), mass=1e-12)
        body = Ragdoll([bone])
        original = body.pose()
        # A finite impulse can still produce an overflowing solver intermediate.
        body.hit("body", (1e295, 1e295, 1e295))
        body.step(.25)
        self.assertEqual(body.state, solver.FAILED)
        self.assertEqual(body.pose(), original)
        self.assertTrue(all(math.isfinite(value) for pair in body.pose().values() for point in pair for value in point))


if __name__ == "__main__":
    unittest.main()
