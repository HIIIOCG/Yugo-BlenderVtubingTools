"""CPU MuJoCo world for rigid bodies and articulated Spring chains together.

This module never writes Blender channels or changes native rigid-body flags.
Boundary sampling, native integration, result publication and Apply are separate
operations. A result read cannot advance simulation time.
"""
from __future__ import annotations

import math
import uuid
from xml.etree import ElementTree as ET

import numpy as np
from mathutils import Matrix, Quaternion, Vector

from .data import HumanoidTransform
from .mujoco_dependency import load_mujoco
from .physics_data import ObjectTransform, ObjectTransforms, PhysicsDescription
from .physics_inputs import _spring_tail_inertia, matrix_pose, unit_scale


class BoundaryModelChanged(ValueError):
    """A source, boundary scale or scene unit edit requires a model audit."""


def _numbers(values):
    return " ".join(format(float(value), ".12g") for value in values)


def _rate(fraction):
    return -math.log(max(1e-6, 1.0 - fraction))


def _solref(restitution, timestep):
    if restitution <= 0:
        damping_ratio = 1.0
    else:
        logarithm = math.log(max(1e-6, min(restitution, 1.0 - 1e-6)))
        damping_ratio = max(0.05, -logarithm / math.sqrt(math.pi * math.pi + logarithm * logarithm))
    return _numbers((max(0.01, timestep * 2), damping_ratio))


class World:
    def __init__(self, description, timestep=1.0 / 240.0, gravity=(0.0, 0.0, -9.81)):
        if not isinstance(description, PhysicsDescription):
            raise TypeError("Yugo Physics Solver expects Physics Description")
        timestep = float(timestep)
        if not math.isfinite(timestep) or not 0 < timestep <= 1.0 / 30.0:
            raise ValueError("Physics timestep must be positive and at most 1/30 second")
        gravity = tuple(float(value) for value in gravity)
        if len(gravity) != 3 or not all(math.isfinite(value) for value in gravity):
            raise ValueError("Physics gravity needs three finite SI acceleration values")
        if not description.bodies and not description.springs:
            raise ValueError("Physics Description contains no bodies or Spring chains")
        for chain in description.springs:
            if (timestep > 1.0 / 240.0 + 1e-12 and len(chain.joints) >= 12
                    and all(j.reference_hz and j.stiffness == 0.0 for j in chain.joints)
                    and min(Vector(j.tail).length for j in chain.joints) / description.unit_scale < 0.01
                    and any(any(value != 0.0 for value in j.gravity) for j in chain.joints)):
                # Known native integration limit, detected before compiling or
                # stepping rather than publishing a reset/unstable pose.
                raise ValueError("Long Spring chain with short segments and zero Stiffness needs Physics Hz of at least 240")
        self.description = description
        self.timestep = timestep
        self.gravity = gravity
        self.world_id = uuid.uuid4().hex
        self.tick = 0
        self.closed = False
        self.engine = load_mujoco()
        self._rigid_names = {}
        self._anchor_names = {}
        self._spring_names = {}
        self._collider_names = {}
        self._scene = None
        self._armature_world = {}
        self._parent_poses = {}
        self._collider_matrices = {}
        self._build()

    def _build(self):
        xml = ET.Element("mujoco", model="Yugo Physics")
        ET.SubElement(xml, "compiler", angle="radian", inertiafromgeom="auto", alignfree="false")
        ET.SubElement(xml, "option", timestep=format(self.timestep, ".12g"), gravity=_numbers(self.gravity),
                      integrator="implicitfast", cone="elliptic", iterations="50")
        if any(joint.reference_hz and not joint.stiffness and not joint.world_damping
               for chain in self.description.springs for joint in chain.joints):
            # Unforced, undamped articulated motion needs the Coriolis
            # derivative retained by the full native implicit integrator.
            xml.find("option").set("integrator", "implicit")
        world_drag = any(joint.world_damping > 0.0
                         for chain in self.description.springs for joint in chain.joints)
        viscosity = 1.0
        if world_drag:
            viscosity = max((joint.world_damping / (6.0 * math.pi * min(1e-4, Vector(joint.tail).length * 1e-3))
                             for chain in self.description.springs for joint in chain.joints), default=1.0)
            viscosity = max(1.0, viscosity)
            xml.find("option").set("viscosity", format(viscosity, ".12g"))
            xml.find("option").set("density", "0")
        fluid_geoms = set()
        defaults = ET.SubElement(xml, "default")
        ET.SubElement(defaults, "geom", condim="3", friction="0.5 0.005 0.0001",
                      solref=_solref(0.0, self.timestep))
        worldbody = ET.SubElement(xml, "worldbody")
        contact = ET.SubElement(xml, "contact")
        equality = ET.SubElement(xml, "equality")
        rigid_geoms = []
        for index, body in enumerate(self.description.bodies):
            name = f"rigid_{index}"
            self._rigid_names[body.body_id] = name
            attrs = dict(name=name, pos=_numbers(body.position), quat=_numbers(body.quaternion))
            if body.motion == "KINEMATIC":
                attrs["mocap"] = "true"
            element = ET.SubElement(worldbody, "body", **attrs)
            if body.motion == "DYNAMIC":
                ET.SubElement(element, "freejoint", name=f"free_{index}", align="false")
            geom_name = f"rigid_geom_{index}"
            rigid_geoms.append(geom_name)
            ET.SubElement(element, "geom", name=geom_name, type=body.shape.lower(),
                          size=_numbers(body.size), pos=_numbers(body.geom_position),
                          mass=format(body.mass, ".12g"), friction=_numbers((body.friction, 0.005, 0.0001)),
                          margin=format(body.margin, ".12g"),
                          contype=str(body.collision_mask), conaffinity=str(body.collision_mask),
                          solref=_solref(body.restitution, self.timestep))
        collider_geoms = {}
        physical_bones = {(chain.armature_name, joint.bone_name)
                          for chain in self.description.springs for joint in chain.joints}
        attached_colliders = {}
        for index, collider in enumerate(self.description.colliders):
            name = f"collider_{index}"
            geom_name = f"collider_geom_{index}"
            collider_geoms[collider.collider_id] = geom_name
            attrs = dict(name=geom_name, type=collider.shape.lower(),
                         size=_numbers((collider.radius,)), contype="0", conaffinity="0")
            if collider.shape == "CAPSULE":
                if (Vector(collider.tail) - Vector(collider.offset)).length < 1e-7:
                    raise ValueError("Spring Capsule collider must have distinct endpoints")
                attrs["fromto"] = _numbers(collider.offset + collider.tail)
            else:
                attrs["pos"] = _numbers(collider.offset)
            identity = (collider.armature_name, collider.bone_name)
            if not collider.object_name and identity in physical_bones:
                # This is real geometry on the physical source body, so native
                # contact applies equal and opposite impulses to both bodies.
                attached_colliders.setdefault(identity, []).append(attrs)
            else:
                self._collider_names[collider.collider_id] = name
                element = ET.SubElement(worldbody, "body", name=name, mocap="true",
                                        pos=_numbers(collider.position), quat=_numbers(collider.quaternion))
                ET.SubElement(element, "geom", **attrs)
        for index, chain in enumerate(self.description.springs):
            mocap_name = f"spring_boundary_{index}"
            anchor_name = f"spring_anchor_{index}"
            self._anchor_names[chain.chain_id] = (mocap_name, anchor_name)
            ET.SubElement(worldbody, "body", name=mocap_name, mocap="true",
                          pos=_numbers(chain.position), quat=_numbers(chain.quaternion))
            anchor = ET.SubElement(worldbody, "body", name=anchor_name,
                                   pos=_numbers(chain.position), quat=_numbers(chain.quaternion), gravcomp="1")
            ET.SubElement(anchor, "freejoint", name=f"anchor_free_{index}", align="false")
            anchor_mass = max(1e-5, sum(joint.mass for joint in chain.joints))
            ET.SubElement(anchor, "inertial", pos="0 0 0", mass=format(anchor_mass, ".12g"),
                          diaginertia=_numbers((anchor_mass * 0.01,) * 3))
            # A dynamic welded anchor carries acceleration into the chain;
            # putting joints directly under a teleported mocap body loses this.
            ET.SubElement(equality, "weld", body1=anchor_name, body2=mocap_name,
                          solref=_numbers((self.timestep * 2, 1)), torquescale="1")
            elements = {}
            for joint_index, joint in enumerate(chain.joints):
                name = f"spring_{index}_{joint_index}"
                self._spring_names[(chain.armature_name, joint.bone_name)] = name
                parent = elements[joint.parent_bone] if joint.parent_bone else anchor
                element = ET.SubElement(parent, "body", name=name, pos=_numbers(joint.position),
                                        quat=_numbers(joint.quaternion), gravcomp="1")
                elements[joint.bone_name] = element
                for attributes in attached_colliders.get((chain.armature_name, joint.bone_name), ()):
                    ET.SubElement(element, "geom", **attributes)
                # Finite reflected inertia prevents the tail point's almost
                # massless twist axis from exploding. dt*k damping accounts
                # for next-position spring feedback in the implicit velocity
                # solve, without raising the user's Physics Hz.
                length = Vector(joint.tail).length
                armature = joint.mass * length * length
                ET.SubElement(element, "joint", name=f"ball_{index}_{joint_index}", type="ball",
                              stiffness=format(joint.stiffness, ".12g"),
                              damping=format(joint.damping + self.timestep * joint.stiffness, ".12g"),
                              armature=format(armature, ".12g"))
                # A point mass at the tail produces gravity torque around the
                # skeletal pivot. A small finite inertia keeps the model valid.
                inertia = (_spring_tail_inertia(joint.mass, length) if joint.reference_hz else
                           joint.mass * max(joint.radius ** 2 * 0.4, length ** 2 * 1e-4, 1e-9))
                ET.SubElement(element, "inertial", pos=_numbers(joint.tail), mass=format(joint.mass, ".12g"),
                              diaginertia=_numbers((inertia,) * 3))
                if joint.radius > 0:
                    geom_name = f"spring_geom_{index}_{joint_index}"
                    ET.SubElement(element, "geom", name=geom_name, type="sphere", pos=_numbers(joint.tail),
                                  size=format(joint.radius, ".12g"), contype="0", conaffinity="0", mass="0")
                    # VRM collider groups select contact pairs. All explicitly
                    # included rigid bodies can also contact the physical tails.
                    for other in rigid_geoms + [collider_geoms[value] for value in chain.collider_ids]:
                        ET.SubElement(contact, "pair", geom1=geom_name, geom2=other,
                                      condim="3", solref=_solref(0.0, self.timestep))
                if world_drag and (joint.reference_hz or joint.world_damping):
                    # Native Stokes drag acts on WORLD tail velocity. Ordinary
                    # relative-joint damping would drag the tail with its parent.
                    # Keep angular fluid drag negligible, even at Drag=1.
                    # Encode the coefficient in radius, with interaction=1:
                    # MuJoCo 3.3.7's implicit derivative treats that flag as a
                    # boolean and omits arbitrary interaction multipliers.
                    fluid_name = f"tail_drag_{index}_{joint_index}"
                    fluid_radius = max(1e-30, joint.world_damping / (6.0 * math.pi * viscosity))
                    ET.SubElement(element, "geom", name=fluid_name, type="sphere", pos=_numbers(joint.tail),
                                  size=format(fluid_radius, ".12g"), mass="0", contype="0", conaffinity="0",
                                  fluidshape="ellipsoid", fluidcoef="0 0 0 0 0")
                    fluid_geoms.add(fluid_name)
        if world_drag:
            # A positive ellipsoid interaction disables MuJoCo's automatic
            # inertia-box fluid fallback. Give every other moving body an
            # effectively inert selector, so shared rigid bodies and anchors
            # retain their existing damping and trajectory.
            for element in worldbody.iter("body"):
                if element.find("joint") is None and element.find("freejoint") is None:
                    continue
                if any(geom.get("name", "") in fluid_geoms for geom in element.findall("geom")):
                    continue
                fluid_name = "fluid_off_" + element.get("name", "")
                ET.SubElement(element, "geom", name=fluid_name, type="sphere", size="1e-30", mass="0",
                              contype="0", conaffinity="0", fluidshape="ellipsoid", fluidcoef="0 0 0 0 0")
                fluid_geoms.add(fluid_name)
        self.xml = ET.tostring(xml, encoding="unicode")
        try:
            self.model = self.engine.MjModel.from_xml_string(self.xml)
            self.data = self.engine.MjData(self.model)
        except Exception as error:
            raise ValueError(f"Cannot compile native MuJoCo description: {error}") from error
        self._rigid_ids = {identity: self._body_id(name) for identity, name in self._rigid_names.items()}
        self._spring_ids = {identity: self._body_id(name) for identity, name in self._spring_names.items()}
        self._spring_qpos = {identity: int(self.model.jnt_qposadr[self.model.body_jntadr[body_id]])
                             for identity, body_id in self._spring_ids.items()}
        self._constant_forces = np.zeros_like(self.data.xfrc_applied)
        for chain in self.description.springs:
            for joint in chain.joints:
                self._constant_forces[self._spring_ids[(chain.armature_name, joint.bone_name)], :3] = np.asarray(joint.gravity) * joint.mass
        self._damped_bodies = tuple(body for body in self.description.bodies if body.motion == "DYNAMIC"
                                    and (body.linear_damping or body.angular_damping))
        self._mocap_ids = {
            name: int(self.model.body_mocapid[self._body_id(name)])
            for name in [pair[0] for pair in self._anchor_names.values()] + list(self._collider_names.values())
            + [self._rigid_names[body.body_id] for body in self.description.bodies if body.motion == "KINEMATIC"]
        }
        self.engine.mj_forward(self.model, self.data)
        self._previous_qpos = self.data.qpos.copy()
        self._presentation_state = None
        self._presentation_velocity = np.zeros(self.model.nv)
        self._swing_history = {}
        self._swing_base_history = {}
        self._swing_base_time = None
        self._swing_time = None

    def _body_id(self, name):
        return int(self.engine.mj_name2id(self.model, self.engine.mjtObj.mjOBJ_BODY, name))

    def _check(self):
        if self.closed:
            raise RuntimeError("This MuJoCo world is closed")

    def _set_mocap(self, name, matrix, *, label, uniform=False, expected_scale=None):
        position, quaternion, scale = matrix_pose(matrix, self.description.unit_scale,
                                                   label=label, uniform=uniform)
        if expected_scale is not None and any(not math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-5)
                                              for a, b in zip(scale, expected_scale)):
            raise BoundaryModelChanged(f"{label}: boundary scale changed; reset the physics model")
        mocap = self._mocap_ids[name]
        self.data.mocap_pos[mocap] = position
        self.data.mocap_quat[mocap] = quaternion

    def update_boundaries(self, scene, *, forward=True):
        self._check()
        if unit_scale(scene) != self.description.unit_scale:
            raise BoundaryModelChanged("Scene length units changed; reset the physics model")
        self._scene = scene
        import bpy
        if bpy.context.scene == scene:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        else:
            depsgraph = scene.view_layers[0].depsgraph
        def evaluated(name):
            obj = scene.objects.get(name)
            if obj is None:
                raise BoundaryModelChanged(f"Physics boundary source is missing: {name}")
            return obj.evaluated_get(depsgraph)
        for body in self.description.bodies:
            if scene.objects.get(body.object_name) is None:
                # Deleted/renamed sources may already be absent from the current
                # selection. Audit it before retiring surviving native states.
                raise BoundaryModelChanged(f"Physics source object is missing: {body.object_name}")
            if body.motion == "KINEMATIC":
                self._set_mocap(self._rigid_names[body.body_id], evaluated(body.object_name).matrix_world,
                               label=body.object_name, expected_scale=body.object_scale)
        simulated = set(self._spring_names)
        for chain in self.description.springs:
            armature = evaluated(chain.armature_name)
            self._armature_world[chain.armature_name] = armature.matrix_world.copy()
            matrix = armature.matrix_world.copy()
            if chain.parent_bone:
                bone = armature.pose.bones.get(chain.parent_bone)
                if bone is None:
                    raise ValueError(f"Spring attachment bone is missing: {chain.parent_bone}")
                self._parent_poses[(chain.armature_name, chain.parent_bone)] = bone.matrix.copy()
                matrix = matrix @ bone.matrix
            self._set_mocap(self._anchor_names[chain.chain_id][0], matrix,
                           label=f"Spring attachment {chain.chain_id}", uniform=True,
                           expected_scale=(chain.armature_scale,) * 3)
        for collider in self.description.colliders:
            if collider.collider_id not in self._collider_names:
                continue
            if collider.object_name:
                matrix = evaluated(collider.object_name).matrix_world.copy()
            else:
                armature = evaluated(collider.armature_name)
                if (collider.armature_name, collider.bone_name) in simulated:
                    # Colliders attached to simulated bones must use this same
                    # world's published pose, not last Apply's evaluated bones.
                    matrix = self._world_matrix(self._spring_ids[(collider.armature_name, collider.bone_name)])
                else:
                    bone = armature.pose.bones.get(collider.bone_name)
                    if bone is None:
                        raise ValueError(f"Spring collider bone is missing: {collider.bone_name}")
                    matrix = armature.matrix_world @ bone.matrix
            self._collider_matrices[collider.collider_id] = matrix.copy()
            self._set_mocap(self._collider_names[collider.collider_id], matrix,
                           label=collider.collider_id, uniform=True, expected_scale=collider.source_scale)
        if self.tick == 0:
            # Reset initializes neutral joints at the current animated boundary,
            # rather than pulling them from an obsolete model-build location.
            for mocap_name, anchor_name in self._anchor_names.values():
                body_id = self._body_id(anchor_name)
                joint_id = int(self.model.body_jntadr[body_id])
                address = int(self.model.jnt_qposadr[joint_id])
                mocap_id = self._mocap_ids[mocap_name]
                self.data.qpos[address:address + 3] = self.data.mocap_pos[mocap_id]
                self.data.qpos[address + 3:address + 7] = self.data.mocap_quat[mocap_id]
        if forward:
            self.engine.mj_forward(self.model, self.data)
        if self.tick == 0:
            self._previous_qpos = self.data.qpos.copy()

    def _world_matrix(self, body_id, pose_data=None):
        state = self.data if pose_data is None else pose_data
        matrix = Quaternion(tuple(state.xquat[body_id])).to_matrix().to_4x4()
        matrix.translation = Vector(tuple(state.xpos[body_id])) / self.description.unit_scale
        return matrix

    def _forces(self):
        self.data.xfrc_applied[:] = self._constant_forces
        for body in self._damped_bodies:
            body_id = self._rigid_ids[body.body_id]
            velocity = np.zeros(6)
            self.engine.mj_objectVelocity(self.model, self.data, self.engine.mjtObj.mjOBJ_BODY,
                                         body_id, velocity, 0)
            self.data.xfrc_applied[body_id, :3] = -body.mass * _rate(body.linear_damping) * velocity[3:]
            inertia_rotation = self.data.ximat[body_id].reshape(3, 3)
            inertia_world = inertia_rotation @ np.diag(self.model.body_inertia[body_id]) @ inertia_rotation.T
            self.data.xfrc_applied[body_id, 3:] = -_rate(body.angular_damping) * (inertia_world @ velocity[:3])

    def step(self, n=1, *, observer=None, forward=True):
        self._check()
        if isinstance(n, bool) or int(n) != n or not 0 <= n <= 10000:
            raise ValueError("Physics step count must be an integer in [0, 10000]")
        n = int(n)
        if observer is not None:
            # Diagnostic capture is opt-in. Normal playback retains native
            # batching; recording observes each integrated state before it is lost.
            import time
            for _ in range(n):
                started = time.perf_counter()
                self.step(1)
                observer(self, (time.perf_counter() - started) * 1000)
            return
        if n and not self._damped_bodies:
            # Constant forces let MuJoCo batch small steps entirely in native
            # code, avoiding per-bone Python work at the simulation frequency.
            self._forces()
            if n > 1:
                self.engine.mj_step(self.model, self.data, nstep=n - 1)
            self._previous_qpos = self.data.qpos.copy()
            self.engine.mj_step(self.model, self.data)
            self.tick += n
        else:
            for _ in range(n):
                self._forces()
                self._previous_qpos = self.data.qpos.copy()
                self.engine.mj_step(self.model, self.data)
                self.tick += 1
                self._validate_state()
        self._validate_state()
        # Playback can refresh derived poses once after its final substep:
        # the next mj_step recomputes full dynamics itself. Custom rigid
        # damping reads those velocities before stepping, so keep its refresh.
        if forward or self._damped_bodies:
            self.forward()

    def forward(self):
        """Refresh the integrated state's derived poses without advancing time."""
        self._check()
        self.engine.mj_forward(self.model, self.data)

    def _validate_state(self):
        if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
            raise RuntimeError("MuJoCo produced a nonfinite physics state")
        # MuJoCo may silently auto-reset bad acceleration to finite positions.
        # Do not publish that reset as a valid pose.
        warning = self.engine.mjtWarning
        if any(self.data.warning[k].number for k in (warning.mjWARN_BADQPOS, warning.mjWARN_BADQVEL, warning.mjWARN_BADQACC)):
            raise RuntimeError("MuJoCo detected an unstable physics state; Reset before continuing")

    def presentation_data(self, alpha):
        """Interpolate one native step for display without changing raw state."""
        self._check()
        alpha = max(0.0, min(1.0, float(alpha)))
        if self._presentation_state is None:
            self._presentation_state = self.engine.MjData(self.model)
        state = self._presentation_state
        state.qpos[:] = self._previous_qpos
        self.engine.mj_differentiatePos(self.model, self._presentation_velocity, 1.0,
                                       self._previous_qpos, self.data.qpos)
        self.engine.mj_integratePos(self.model, state.qpos, self._presentation_velocity, alpha)
        state.mocap_pos[:] = self.data.mocap_pos
        state.mocap_quat[:] = self.data.mocap_quat
        state.time = max(0.0, float(self.data.time) - self.timestep + alpha * self.timestep) if self.tick else float(self.data.time)
        self.engine.mj_kinematics(self.model, state)
        return state

    def object_transforms(self, *, pose_data=None):
        self._check()
        records = []
        for body in self.description.bodies:
            if body.motion != "DYNAMIC":
                continue
            matrix = self._world_matrix(self._rigid_ids[body.body_id], pose_data)
            for axis in range(3):
                for row in range(3):
                    matrix[row][axis] *= body.object_scale[axis]
            records.append(ObjectTransform(body.object_name, tuple(tuple(row) for row in matrix)))
        return ObjectTransforms(tuple(records), self.world_id, self.description.revision, self.tick)

    def _raw_bone_transforms(self, pose_data=None, parent_poses=None, armature_world=None):
        self._check()
        from .flow import BoneTransforms
        if not self.description.springs:
            return BoneTransforms()
        name = self.description.springs[0].armature_name
        if self._scene is None:
            raise RuntimeError("Sample Spring boundaries before reading bone transforms")
        armature = self._scene.objects.get(name)
        if armature is None:
            raise ValueError(f"Spring output armature is missing: {name}")
        state = self.data if pose_data is None else pose_data
        worlds = self._armature_world if armature_world is None else armature_world
        parents = self._parent_poses if parent_poses is None else parent_poses
        armature_inverse = worlds[name].inverted()
        armature_rotation_inverse = worlds[name].to_quaternion().inverted()
        matrices = {}
        scale = self.description.springs[0].armature_scale
        def pose_matrix(bone_name):
            if bone_name in matrices:
                return matrices[bone_name]
            body_id = self._spring_ids[(name, bone_name)]
            world_matrix = self._world_matrix(body_id, state)
            for axis in range(3):
                for row in range(3):
                    world_matrix[row][axis] *= scale
            matrices[bone_name] = armature_inverse @ world_matrix
            return matrices[bone_name]
        bones = {}
        for armature_name, bone_name in self._spring_ids:
            bone = armature.data.bones.get(bone_name)
            if bone is None:
                raise ValueError(f"Spring output bone is missing: {bone_name}")
            if not bone.use_inherit_rotation:
                body_id = self._spring_ids[(name, bone_name)]
                rotation = bone.matrix_local.to_quaternion().inverted() @ armature_rotation_inverse @ Quaternion(tuple(state.xquat[body_id]))
                bones[bone_name] = HumanoidTransform(rotation=rotation.normalized())
                continue
            if bone.parent is not None and (name, bone.parent.name) in self._spring_ids and bone.use_inherit_rotation:
                # Child ball quaternions already are the local pose rotation.
                # Keep full conversion for attachment roots and non-inheriting
                # bones, whose boundary orientation needs explicit accounting.
                address = self._spring_qpos[(name, bone_name)]
                bones[bone_name] = HumanoidTransform(rotation=Quaternion(tuple(state.qpos[address:address + 4])).normalized())
                continue
            matrix = pose_matrix(bone_name)
            kwargs = {}
            if bone.parent is not None:
                parent_matrix = pose_matrix(bone.parent.name) if (name, bone.parent.name) in self._spring_ids else None
                if parent_matrix is None:
                    parent_matrix = parents.get((name, bone.parent.name))
                if parent_matrix is None:
                    raise ValueError(f"Spring parent binding is missing: {bone.parent.name}")
                kwargs = {"parent_matrix": parent_matrix, "parent_matrix_local": bone.parent.matrix_local}
            # Scale lives in the armature object, not native pose channels.
            location, rotation, _ = matrix.decompose()
            matrix = Matrix.LocRotScale(location, rotation, Vector((1.0, 1.0, 1.0)))
            local = bone.convert_local_to_pose(matrix, bone.matrix_local, invert=True, **kwargs)
            bones[bone_name] = HumanoidTransform(rotation=local.to_quaternion().normalized())
        return BoneTransforms(name, bones)

    @staticmethod
    def _tail_swing(axis, target, previous=None, recovering=False, elapsed=0.0):
        """Minimum authored swing with a continuous gauge near the antipode.

        Away from reversal, no independent roll survives. Near 180 degrees a
        shortest-swing frame is singular, so transport the preceding frame.
        Recover only that presentation roll at at most 90 degrees/second on
        leaving the antipodal cone. Ordinary circular swings never enter this
        policy and therefore cannot accumulate transport roll.
        """
        dot = max(-1.0, min(1.0, axis.dot(target)))
        if dot < -1.0 + 1e-10:
            basis = min((Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))),
                        key=lambda value: abs(axis.dot(value)))
            shortest = Quaternion(axis.cross(basis).normalized(), math.pi)
        else:
            shortest = axis.rotation_difference(target).normalized()
        if previous is None or (not recovering and dot >= math.cos(math.radians(170))):
            return shortest
        preceding_direction = previous @ axis
        transported = preceding_direction.rotation_difference(target) @ previous
        transported.normalize()
        if dot <= math.cos(math.radians(175)):
            return transported
        delta = shortest.conjugated() @ transported
        component = axis * Vector((delta.x, delta.y, delta.z)).dot(axis)
        twist = Quaternion((delta.w, component.x, component.y, component.z))
        if twist.magnitude < 1e-10:
            return shortest
        twist.normalize()
        amount = max(0.0, min(1.0, (math.radians(175) - math.acos(dot)) / math.radians(25)))
        amount = amount * amount * (3.0 - 2.0 * amount)
        angle = 2.0 * math.acos(min(1.0, abs(twist.w)))
        recovery = math.radians(90) * max(0.0, elapsed) * amount
        retention = max(0.0, 1.0 - recovery / angle) if angle > 1e-8 else 0.0
        return (shortest @ Quaternion().slerp(twist, retention)).normalized()

    def bone_transforms(self, *, pose_data=None, parent_poses=None, armature_world=None,
                        raw=False, presentation_time=None, commit=False):
        """Publish authored tail directions, retaining full native trace poses.

        Children are converted against the corrected displayed parent, not
        against raw ball quaternions. Off-axis attachments receive local
        translation corrections so removing roll does not move their heads.
        Continuity advances only on an explicit presentation-clock commit.
        """
        self._check()
        if raw or not any(j.reference_hz for c in self.description.springs for j in c.joints):
            return self._raw_bone_transforms(pose_data, parent_poses, armature_world)
        from .flow import BoneTransforms
        if self._scene is None:
            raise RuntimeError("Sample Spring boundaries before reading bone transforms")
        name = self.description.springs[0].armature_name
        armature = self._scene.objects.get(name)
        if armature is None:
            raise ValueError(f"Spring output armature is missing: {name}")
        state = self.data if pose_data is None else pose_data
        worlds = self._armature_world if armature_world is None else armature_world
        parents = self._parent_poses if parent_poses is None else parent_poses
        clock = float(state.time) if presentation_time is None else float(presentation_time)
        # Same-time publication can have a newly sampled animated parent. The
        # world directions must be re-expressed in that current display basis;
        # caching local packets by clock alone would add the parent's motion.
        if self._swing_time is None or clock > self._swing_time + 1e-12:
            previous = self._swing_history
            preceding_time = self._swing_time
        elif abs(clock - self._swing_time) < 1e-12:
            previous = self._swing_base_history
            preceding_time = self._swing_base_time
        else:
            previous = {}
            preceding_time = None
        elapsed = max(0.0, clock - preceding_time) if preceding_time is not None else 0.0
        world_matrix = worlds[name]
        inverse = world_matrix.inverted()
        world_rotation = world_matrix.to_quaternion()
        scale = self.description.springs[0].armature_scale
        matrices, bones, history = {}, {}, {}
        for chain in self.description.springs:
            attachment_shift = None
            for joint in chain.joints:
                bone = armature.data.bones.get(joint.bone_name)
                if bone is None:
                    raise ValueError(f"Spring output bone is missing: {joint.bone_name}")
                kwargs = {}
                if bone.parent is not None:
                    parent = matrices.get(bone.parent.name, parents.get((name, bone.parent.name)))
                    if parent is None:
                        raise ValueError(f"Spring parent binding is missing: {bone.parent.name}")
                    kwargs = {"parent_matrix": parent, "parent_matrix_local": bone.parent.matrix_local}
                body = self._spring_ids[(name, joint.bone_name)]
                native = self._world_matrix(body, state)
                if joint.reference_hz:
                    reference = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local, **kwargs)
                    reference_rotation = world_rotation @ reference.to_quaternion()
                    if attachment_shift is None:
                        # SpringBone attachments belong to the animated rig.
                        # Native weld compliance/display delay must not route a
                        # root location through a connected animated ancestor.
                        attachment_shift = world_matrix @ reference.translation - native.translation
                    axis = Vector(joint.tail).normalized()
                    direction = Quaternion(tuple(state.xquat[body])) @ axis
                    target = reference_rotation.conjugated() @ direction
                    preceding_frame = previous.get(joint.bone_name)
                    preceding = reference_rotation.conjugated() @ preceding_frame[0] if preceding_frame is not None else None
                    recovering = preceding_frame[1] if preceding_frame is not None else False
                    swing = self._tail_swing(axis, target.normalized(), preceding, recovering, elapsed)
                    rotation = reference_rotation @ swing
                    # Record whether the temporary antipodal gauge still has
                    # roll to recover; normal minimum-swing frames stay exact.
                    active = axis.dot(target) < math.cos(math.radians(170))
                    if recovering and not active:
                        shortest = self._tail_swing(axis, target.normalized())
                        residual = shortest.rotation_difference(swing)
                        active = Vector((residual.x, residual.y, residual.z)).length_squared > 1e-12
                    history[joint.bone_name] = (rotation.copy(), active)
                    native = Matrix.LocRotScale(native.translation + attachment_shift, rotation,
                                               Vector((scale, scale, scale)))
                else:
                    for axis in range(3):
                        for row in range(3):
                            native[row][axis] *= scale
                pose = inverse @ native
                matrices[joint.bone_name] = pose
                local = bone.convert_local_to_pose(pose, bone.matrix_local, invert=True, **kwargs)
                location, rotation, _ = local.decompose()
                bones[joint.bone_name] = HumanoidTransform(rotation=rotation.normalized(),
                    location=location if joint.reference_hz and location.length_squared > 1e-14 else None)
        packet = BoneTransforms(name, bones)
        if commit:
            self._swing_base_history = previous
            self._swing_base_time = preceding_time
            self._swing_history, self._swing_time = history, clock
        return packet

    def snapshot(self):
        self._check()
        def body_state(body_id):
            velocity = np.zeros(6)
            self.engine.mj_objectVelocity(self.model, self.data, self.engine.mjtObj.mjOBJ_BODY,
                                         body_id, velocity, 0)
            return {"position": tuple(float(value) for value in self.data.xpos[body_id]),
                    "quaternion": tuple(float(value) for value in self.data.xquat[body_id]),
                    "angular_velocity": tuple(float(value) for value in velocity[:3]),
                    "linear_velocity": tuple(float(value) for value in velocity[3:])}
        return {
            "engine": "MuJoCo", "backend": "MuJoCo CPU", "engine_version": self.engine.__version__,
            "world_id": self.world_id, "revision": self.description.revision,
            "tick": self.tick, "time": float(self.data.time), "timestep": self.timestep,
            "dynamic_objects": sum(body.motion == "DYNAMIC" for body in self.description.bodies),
            "kinematic_objects": sum(body.motion == "KINEMATIC" for body in self.description.bodies),
            "spring_chains": len(self.description.springs), "spring_bones": len(self._spring_ids),
            "bodies": int(self.model.nbody - 1), "joints": int(self.model.njnt),
            "contacts": int(self.data.ncon),
            "object_names": tuple(body.object_name for body in self.description.bodies),
            "armature_names": tuple(sorted({chain.armature_name for chain in self.description.springs})),
            "body_states": tuple(dict(body_id=body.body_id, object_name=body.object_name, motion=body.motion,
                                      **body_state(self._rigid_ids[body.body_id]))
                                 for body in self.description.bodies),
            "spring_states": tuple(dict(armature_name=armature_name, bone_name=bone_name,
                                        **body_state(body_id))
                                   for (armature_name, bone_name), body_id in self._spring_ids.items()),
        }

    def transfer_state_from(self, previous):
        """Continue compatible identities in a newly compiled world.

        Sample this world's current boundaries first. New or structurally
        changed bodies retain their compiled birth pose and zero velocity.
        MuJoCo's generated names and joint addresses can shift after an
        insertion, so every joint slice is remapped through physics identities.
        The previous world remains alive and unchanged until the caller swaps.
        """
        self._check()
        previous._check()
        if previous is self:
            raise ValueError("Cannot transfer a physics world into itself")
        previous._validate_state()
        self._previous_qpos = self.data.qpos.copy()
        transferred = {"bodies": 0, "springs": 0, "anchors": 0}
        compatible_springs = set()

        def copy_joint(new_body, old_body):
            if (int(self.model.body_jntnum[new_body]) != 1
                    or int(previous.model.body_jntnum[old_body]) != 1):
                return False
            new_joint = int(self.model.body_jntadr[new_body])
            old_joint = int(previous.model.body_jntadr[old_body])
            joint_type = int(self.model.jnt_type[new_joint])
            if joint_type != int(previous.model.jnt_type[old_joint]):
                return False
            widths = {
                int(self.engine.mjtJoint.mjJNT_FREE): (7, 6),
                int(self.engine.mjtJoint.mjJNT_BALL): (4, 3),
            }
            if joint_type not in widths:
                return False
            nq, nv = widths[joint_type]
            new_q = int(self.model.jnt_qposadr[new_joint])
            old_q = int(previous.model.jnt_qposadr[old_joint])
            new_v = int(self.model.jnt_dofadr[new_joint])
            old_v = int(previous.model.jnt_dofadr[old_joint])
            self.data.qpos[new_q:new_q+nq] = previous.data.qpos[old_q:old_q+nq]
            self.data.qvel[new_v:new_v+nv] = previous.data.qvel[old_v:old_v+nv]
            self._previous_qpos[new_q:new_q+nq] = previous._previous_qpos[old_q:old_q+nq]
            return True

        same_units = self.description.unit_scale == previous.description.unit_scale
        if same_units:
            old_bodies = {body.body_id: body for body in previous.description.bodies}
            for body in self.description.bodies:
                old = old_bodies.get(body.body_id)
                # Geometry changes can alter the physical frame and contact
                # clearance; retain states only for the same authored shape.
                shape = (body.motion, body.shape, body.size, body.geom_position, body.object_scale)
                if (old is None or body.motion != "DYNAMIC"
                        or shape != (old.motion, old.shape, old.size, old.geom_position, old.object_scale)):
                    continue
                if copy_joint(self._rigid_ids[body.body_id], previous._rigid_ids[old.body_id]):
                    transferred["bodies"] += 1

            old_chains = {chain.chain_id: chain for chain in previous.description.springs}
            for chain in self.description.springs:
                old_chain = old_chains.get(chain.chain_id)
                binding = (chain.armature_name, chain.parent_bone, chain.armature_scale)
                if (old_chain is None or binding != (old_chain.armature_name,
                                                     old_chain.parent_bone, old_chain.armature_scale)):
                    continue
                anchor = self._anchor_names[chain.chain_id][1]
                old_anchor = previous._anchor_names[old_chain.chain_id][1]
                if not copy_joint(self._body_id(anchor), previous._body_id(old_anchor)):
                    continue
                transferred["anchors"] += 1
                old_joints = {joint.bone_name: joint for joint in old_chain.joints}
                for joint in chain.joints:
                    old = old_joints.get(joint.bone_name)
                    identity = (chain.armature_name, joint.bone_name)
                    if (old is None
                            or (joint.parent_bone, joint.position, joint.quaternion, joint.tail)
                            != (old.parent_bone, old.position, old.quaternion, old.tail)
                            or (joint.parent_bone and
                                (chain.armature_name, joint.parent_bone) not in compatible_springs)):
                        continue
                    if copy_joint(self._spring_ids[identity], previous._spring_ids[identity]):
                        compatible_springs.add(identity)
                        transferred["springs"] += 1

        compatible_names = {name for _armature, name in compatible_springs}
        def history_copy(history):
            result = {}
            for name in compatible_names:
                value = history.get(name)
                if value is not None and all(math.isfinite(component) for component in value[0]):
                    result[name] = (value[0].copy(), bool(value[1]))
            return result

        self._swing_history = history_copy(previous._swing_history)
        self._swing_base_history = history_copy(previous._swing_base_history)
        self._swing_time = previous._swing_time if self._swing_history else None
        self._swing_base_time = previous._swing_base_time if self._swing_base_history else None
        self.data.time = float(previous.data.time)
        self.tick = previous.tick
        self._presentation_state = None
        self._presentation_velocity.fill(0)
        self.engine.mj_forward(self.model, self.data)
        self._validate_state()
        if not np.isfinite(self._previous_qpos).all():
            raise RuntimeError("Cannot transfer a nonfinite previous physics pose")
        return transferred

    def reset(self):
        self._check()
        self.engine.mj_resetData(self.model, self.data)
        self.tick = 0
        self._swing_history = {}
        self._swing_base_history = {}
        self._swing_base_time = None
        self._swing_time = None
        if self._scene is not None:
            self.update_boundaries(self._scene)
        else:
            self.engine.mj_forward(self.model, self.data)
        self._previous_qpos = self.data.qpos.copy()

    def close(self):
        if not self.closed:
            self.closed = True
            self._scene = None
            self.data = None
            self.model = None
            self._presentation_state = None
            self._swing_history = {}
            self._swing_base_history = {}
            self._swing_base_time = None
