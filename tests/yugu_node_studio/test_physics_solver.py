"""Behavioral checks for the dependency-free preview physics solver."""

import importlib.util
import math
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "addons/yugu_node_studio/modules/physics/solver.py"
SPEC = importlib.util.spec_from_file_location("physics_lab_solver_test", SOURCE)
solver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = solver
SPEC.loader.exec_module(solver)
Body, Box, World = solver.Body, solver.Box, solver.World


def simulate(world, seconds, dt=1 / 60):
    for _ in range(round(seconds / dt)):
        world.advance(dt)


def length(vector):
    return math.sqrt(sum(v * v for v in vector))


class SolverTests(unittest.TestCase):
    def test_floor_rest_is_stable_and_friction_creates_spin(self):
        world = World()
        body = world.add_body(Body("ball", (0, 0, 2), velocity=(2, 0, 0)))
        simulate(world, 5)
        self.assertAlmostEqual(body.position[2], body.radius, delta=1e-4)
        self.assertLess(abs(body.velocity[2]), 1e-4)
        self.assertLess(abs(body.velocity[0]), 2)
        self.assertGreater(abs(body.angular_velocity[1]), 0.05)
        self.assertAlmostEqual(length(body.orientation), 1, places=10)
        before = body.position[2]
        simulate(world, 5)
        self.assertAlmostEqual(body.position[2], before, delta=1e-5)

    def test_impulse_obeys_mass_and_sphere_inertia(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        a = world.add_body(Body("light", (0, 0, 0), mass=1, damping=0, angular_damping=0))
        b = world.add_body(Body("heavy", (0, 5, 0), mass=4, damping=0, angular_damping=0))
        world.apply_impulse(a.name, (2, 0, 0), (0, 0, 0.036))
        world.apply_impulse(b.name, (2, 0, 0), (0, 0, 0.036))
        self.assertEqual(a.velocity, (2, 0, 0))
        self.assertEqual(b.velocity, (0.5, 0, 0))
        self.assertAlmostEqual(a.angular_velocity[2], 1)
        self.assertAlmostEqual(b.angular_velocity[2], 0.25)
        world.advance(1 / 60)
        self.assertAlmostEqual(a.position[0], 4 * b.position[0])
        self.assertGreater(abs(a.orientation[3]), 0)

    def test_floor_uses_body_restitution(self):
        world = World(gravity=(0, 0, 0))
        body = world.add_body(Body("ball", (0, 0, 0.31), velocity=(0, 0, -2),
                                  damping=0, restitution=1))
        world.advance(1 / 60)
        self.assertAlmostEqual(body.velocity[2], 2, places=10)

    def test_sphere_collision_conserves_momentum_and_rebounds(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        a = world.add_body(Body("a", (-0.8, 0, 0), velocity=(2, 0, 0),
                               mass=1, damping=0, friction=0, restitution=1))
        b = world.add_body(Body("b", (0.8, 0, 0), velocity=(-1, 0, 0),
                               mass=2, damping=0, friction=0, restitution=1))
        before = a.mass * a.velocity[0] + b.mass * b.velocity[0]
        simulate(world, 1)
        self.assertLess(a.velocity[0], 0)
        self.assertGreater(b.velocity[0], 0)
        self.assertAlmostEqual(a.mass * a.velocity[0] + b.mass * b.velocity[0], before, places=10)
        self.assertGreaterEqual(length(solver._sub(a.position, b.position)), a.radius + b.radius - 1e-5)

    def test_static_box_contact_and_rotated_normal(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        angle = math.pi / 4
        rotation = (math.cos(angle / 2), 0, 0, math.sin(angle / 2))
        normal = (math.cos(angle), math.sin(angle), 0)
        box = world.add_box(Box("wall", (0, 0, 0), (0.05, 3, 3), rotation,
                                friction=0, restitution=1))
        body = world.add_body(Body("ball", solver._mul(normal, 1),
                                  velocity=solver._mul(normal, -4), damping=0,
                                  friction=0, restitution=1))
        simulate(world, 0.4)
        self.assertGreater(solver._dot(body.velocity, normal), 3.9)
        local = solver._rotate(solver._conjugate(box.orientation), body.position)
        self.assertGreater(local[0], body.radius + box.half_extents[0] - 1e-4)

    def test_thin_box_and_small_sphere_do_not_tunnel_at_capped_speed(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        world.add_box(Box("thin", (0, 0, 0), (0.0001, 1, 1), friction=0, restitution=0))
        body = world.add_body(Body("tiny", (-0.1, 0, 0), radius=0.001,
                                  velocity=(1000, 0, 0), damping=0,
                                  friction=0, restitution=0))
        simulate(world, 0.2)
        self.assertLessEqual(body.position[0], -body.radius - 0.0001 + 1e-5)
        self.assertLessEqual(length(body.velocity), world._speed_limit(body) + 1e-8)

    def test_collision_accelerated_small_sphere_cannot_tunnel_next_microstep(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        world.add_box(Box("thin", (0.08, 0, 0), (0.0001, 1, 1), friction=0, restitution=0))
        world.add_body(Body("large", (-0.43, 0, 0), radius=0.3, mass=10,
                            velocity=(30, 0, 0), damping=0, friction=0, restitution=1))
        small = world.add_body(Body("small", (0, 0, 0), radius=0.05, mass=0.1,
                                   damping=0, friction=0, restitution=1))
        world.advance(world.fixed_dt)
        self.assertLessEqual(small.position[0], 0.08 - 0.0001 - small.radius + 1e-5)

    def test_persistent_force_can_be_cleared(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        body = world.add_body(Body("ball", (0, 0, 0), mass=2, damping=0))
        world.set_force(body.name, (4, 0, 0))
        simulate(world, 1)
        self.assertAlmostEqual(body.velocity[0], 2, places=10)
        world.set_force(body.name, (0, 0, 0))
        simulate(world, 1)
        self.assertAlmostEqual(body.velocity[0], 2, places=10)

    def test_moving_soft_grab_and_release_preserve_velocity(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        body = world.add_body(Body("ball", (0, 0, 0), damping=0, angular_damping=0))
        world.grab(body.name, "hand", (2, 0, 0))
        self.assertEqual(body.position, (0, 0, 0))
        self.assertEqual(body.velocity, (0, 0, 0))
        for index in range(60):
            angle = index / 60
            world.move_anchor("hand", (2 + index / 60, 0, 0),
                              (math.cos(angle / 2), 0, 0, math.sin(angle / 2)))
            world.advance(1 / 60)
        self.assertGreater(body.position[0], 2)
        self.assertGreater(body.velocity[0], 0.5)
        self.assertGreater(body.angular_velocity[2], 0.5)
        pose, velocity, angular_velocity = body.position, body.velocity, body.angular_velocity
        world.release(body.name)
        self.assertEqual(body.position, pose)
        self.assertEqual(body.velocity, velocity)
        self.assertEqual(body.angular_velocity, angular_velocity)
        self.assertIsNone(body.grab_anchor)
        self.assertNotIn("hand", world.anchors)
        world.advance(1 / 60)
        self.assertGreater(body.position[0], pose[0])

    def test_recall_returns_position_orientation_and_holds_against_gravity(self):
        world = World(floor_height=None)
        body = world.add_body(Body("ball", (0, 0, 2)))
        body.position = (2, 1, 3)
        body.orientation = (0, 0, 0, 1)
        body.velocity = (1, 0, 0)
        world.recall(body.name)
        self.assertEqual(body.position, (2, 1, 3))
        self.assertEqual(body.recall_state, "seeking")
        simulate(world, 4)
        self.assertEqual(body.recall_state, "settled")
        self.assertTrue(body.recall_hold)
        self.assertLess(length(solver._sub(body.position, body.home_position)), 0.04)
        self.assertLess(length(solver._rotation_vector(body.home_orientation, body.orientation)), 0.04)
        simulate(world, 2)
        self.assertLess(abs(body.position[2] - 2), 0.01)
        world.cancel_recall(body.name)
        self.assertEqual(body.recall_state, "idle")
        world.advance(1 / 60)
        self.assertLess(body.velocity[2], 0)

    def test_recall_blocked_by_wall_times_out_without_teleport(self):
        world = World(gravity=(0, 0, 0), floor_height=None)
        body = world.add_body(Body("ball", (-1, 0, 0)))
        world.add_box(Box("wall", (0, 0, 0), (0.05, 2, 2)))
        body.position = (1, 0, 0)
        world.recall(body.name, timeout=1)
        simulate(world, 1.2)
        self.assertEqual(body.recall_state, "timeout")
        self.assertFalse(body.recall_hold)
        self.assertGreaterEqual(body.position[0], body.radius + 0.05 - 1e-4)
        self.assertGreater(length(solver._sub(body.position, body.home_position)), 1)

    def test_recall_without_hold_completes_then_resumes_gravity(self):
        world = World(floor_height=None)
        body = world.add_body(Body("ball", (0, 0, 2)))
        world.recall(body.name, hold=False, settle_time=0.02)
        simulate(world, 0.1)
        self.assertEqual(body.recall_state, "settled")
        self.assertFalse(body.recall_hold)
        self.assertIsNone(body._recall)
        self.assertLess(body.position[2], 2)

    def test_fixed_steps_match_equal_elapsed_jitter_and_order(self):
        def make_world(reverse=False):
            world = World()
            bodies = [Body("a", (0, 0, 1), velocity=(0.2, 0, 0)),
                      Body("b", (0.7, 0, 1), velocity=(-0.2, 0, 0))]
            for body in reversed(bodies) if reverse else bodies:
                world.add_body(body)
            return world
        regular, jittered = make_world(), make_world(True)
        simulate(regular, 2)
        for _ in range(60):
            jittered.advance(1 / 120)
            jittered.advance(1 / 40)
        for name in regular.bodies:
            a, b = regular.bodies[name], jittered.bodies[name]
            for left, right in zip(a.position + a.velocity + a.orientation,
                                   b.position + b.velocity + b.orientation):
                self.assertAlmostEqual(left, right, places=10)

    def test_stall_caps_simulated_time_zero_delta_and_reset(self):
        world = World()
        body = world.add_body(Body("ball", (0, 0, 2)))
        self.assertEqual(world.advance(0), 0)
        self.assertEqual(body.position, body.home_position)
        world.advance(30)
        self.assertLessEqual(world.simulation_time, world.max_frame_dt + 1e-10)
        self.assertGreater(body.position[2], 1.8)
        world.set_force(body.name, (1, 2, 3))
        world.grab(body.name, "hand", (1, 0, 3))
        world.reset()
        self.assertEqual(body.position, body.home_position)
        self.assertEqual(body.orientation, body.home_orientation)
        self.assertEqual(body.velocity, (0, 0, 0))
        self.assertEqual(body.force, (0, 0, 0))
        self.assertEqual(body.recall_state, "idle")
        self.assertFalse(world.anchors)
        self.assertEqual(world.simulation_time, 0)

    def test_invalid_numbers_and_degenerate_parameters_are_rejected(self):
        for invalid in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                Body("invalid", (invalid, 0, 0))
            with self.assertRaises(ValueError):
                World().advance(invalid)
        for kwargs in ({"mass": 0}, {"radius": 0}, {"orientation": (0, 0, 0, 0)},
                       {"restitution": 2}, {"damping": -1}):
            with self.assertRaises(ValueError):
                Body("invalid", (0, 0, 0), **kwargs)
        world = World()
        body = world.add_body(Body("ball", (0, 0, 1)))
        with self.assertRaises(ValueError):
            world.advance(-0.01)
        with self.assertRaises(ValueError):
            world.set_force(body.name, (math.nan, 0, 0))
        body.velocity = (0, math.inf, 0)
        with self.assertRaises(ValueError):
            world.advance(1 / 60)


if __name__ == "__main__":
    unittest.main()
