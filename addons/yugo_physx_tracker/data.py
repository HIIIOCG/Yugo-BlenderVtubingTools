"""Independent PhysX nodes; no imports from the MuJoCo extension."""
from dataclasses import dataclass

TREE = 'YUGUS_NT_humanoid_motion'
SKELETON = 'YUGOPX_NODE_skeleton'
PROTO = 'YUGOPX_NODE_tracker'
PHYSICS = 'YUGOPX_NODE_physics'
RIGID_BODIES = 'YUGOPX_NODE_rigid_bodies'
SPRINGS = 'YUGOPX_NODE_springs'
NODE_IDS = {SKELETON, PROTO, PHYSICS, RIGID_BODIES, SPRINGS}
INPUT_NODES = {RIGID_BODIES, SPRINGS}
ACTOR_SOCKET = 'YUGOPX_NS_actor'
CONTROL_SOCKET = 'YUGOPX_NS_controller'
STATE_SOCKET = 'YUGOPX_NS_state'
RIGID_SOCKET = 'YUGOPX_NS_rigid_bodies'
SPRING_SOCKET = 'YUGOPX_NS_spring_bones'
BONE_TRANSFORM = 'YUGUS_NS_bone_rotation'
EVENT = 'YUGUS_NS_update_event'
MOTION = 'YUGUS_NS_humanoid_motion'
OWNER = 'yugo_physx_tracker_owner'
ACTIONS = {SKELETON: {'Setup', 'Clear'}, PROTO: {'Reset'},
           PHYSICS: {'Play', 'Reset', 'Generate Targets', 'Clear Targets'}}

@dataclass(frozen=True)
class RigidBodiesInput:
    objects: tuple = ()
    display_meta: tuple = ()
    warnings: tuple = ()

@dataclass(frozen=True)
class SpringBonesInput:
    springs: tuple = ()
    colliders: tuple = ()
    display_meta: tuple = ()
    armature_name: str = ''
    warnings: tuple = ()
    spring_data: object = None

@dataclass(frozen=True)
class Actor:
    actor_id: str
    revision: str
    binding_json: str
    skeleton_node: str
    tree_name: str

@dataclass(frozen=True)
class Controller:
    actor: Actor
    reference: object
    node_id: str
    model_path: str
    clip_path: str
    reference_mode: str
