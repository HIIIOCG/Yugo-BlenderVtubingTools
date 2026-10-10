"""Reference-only viewport ghost and editable, reference-relative point goals.

The ghost has no object or mesh datablocks. Generated goals are ordinary
Blender empties; only their owned parent anchors follow the accepted reference.
"""
from contextlib import contextmanager
from functools import lru_cache
import json
import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector
from . import data, display, model, protocol, skeleton
from .point_contract import POINTS, POINT_IDS

ROLE = 'physx_reference_role'
GOAL_ID = 'physx_goal_id'
PREVIEW = 'physx_reference_preview'
POSE = 'physx_reference_pose'
BINDING = 'physx_reference_binding'
SHOW = 'physx_reference_show'
OPACITY = 'physx_reference_opacity'

_handler = None
_shaders = {}
_batches = {}
_velocity = {}
last_error = ''


@lru_cache(maxsize=1)
def _smpl():
    return model.Skeleton()


def _owned(collection, owner):
    if (not isinstance(owner,str) or not owner or collection is None
            or collection.get(data.OWNER) != owner or not collection.get(PREVIEW)):
        raise ValueError('Reference collection belongs to another node')


def _linked(scene, collection):
    return collection in tuple(scene.collection.children_recursive)


def ensure(scene, owner, binding, collection=None):
    """Create only an owned collection, preserving saved poses and goal offsets."""
    if collection is None:
        collection = next((item for item in scene.collection.children_recursive
            if item.get(data.OWNER) == owner and item.get(PREVIEW)), None)
    if collection is not None:
        _owned(collection, owner)
        if not _linked(scene, collection):
            scene.collection.children.link(collection)
        return collection
    collection = bpy.data.collections.new('PhysX · Reference and targets')
    collection[data.OWNER] = owner
    collection[PREVIEW] = True
    scene.collection.children.link(collection)
    try:
        positions, rotations = _smpl().fk()
        sync(collection, binding, {'positions':positions.tolist(),'rotations':rotations.tolist()})
        return collection
    except Exception:
        display.remove(collection, owner)
        raise


def reference_pose(collection):
    """Return the exact native pose last supplied by the worker, as a copy."""
    return json.loads(collection[POSE])


def points(collection, owner):
    if collection is None:
        return {}
    _owned(collection, owner)
    result = {}
    for obj in collection.objects:
        key = obj.get(GOAL_ID)
        if obj.get(data.OWNER) == owner and obj.get(ROLE) == 'goal' and key in POINTS:
            if key in result:
                raise ValueError('Duplicate PhysX goal: '+str(key))
            result[key] = obj
    return result


def _anchors(collection, owner):
    return {obj.get(GOAL_ID):obj for obj in collection.objects
            if obj.get(data.OWNER) == owner and obj.get(ROLE) == 'anchor' and obj.get(GOAL_ID) in POINTS}


def _anchor_matrix(matrices, key, units):
    definition = POINTS[key]
    return matrices[definition['body']] @ Matrix.Translation(Vector(definition['local_position'])/units)


def configure(collection, show=True, opacity=.2):
    if (isinstance(opacity, bool) or not isinstance(opacity, (int,float))
            or not math.isfinite(opacity) or not 0 <= opacity <= 1):
        raise ValueError('Reference opacity must be between 0 and 1')
    collection[SHOW] = bool(show)
    collection[OPACITY] = float(opacity)
    tag_redraw()


def sync(collection, binding, reference, show=True, opacity=.2):
    """Follow the worker reference rather than the simulated body's pose."""
    _owned(collection, collection.get(data.OWNER))
    protocol.pose(reference)
    payload = {name:reference[name] for name in ('positions','rotations','velocities','angular_velocities')
               if name in reference}
    matrices = skeleton.world_matrices(payload['positions'],payload['rotations'],binding)
    collection[POSE] = json.dumps(payload, allow_nan=False, separators=(',',':'))
    collection[BINDING] = json.dumps(binding, allow_nan=False, separators=(',',':'))
    for key, anchor in _anchors(collection, collection[data.OWNER]).items():
        anchor.matrix_world = _anchor_matrix(matrices,key,binding['unit_scale'])
    configure(collection,show,opacity)


def generate(scene, owner, binding, reference=None, collection=None):
    """Generate 11 selectable goal spheres once; keep existing local offsets."""
    collection = ensure(scene,owner,binding,collection)
    if reference is not None:
        sync(collection,binding,reference,collection.get(SHOW,True),collection.get(OPACITY,.2))
    pose = reference_pose(collection)
    matrices = skeleton.world_matrices(pose['positions'],pose['rotations'],binding)
    anchors = _anchors(collection,owner)
    goals = points(collection,owner)
    for key in POINT_IDS:
        definition = POINTS[key]
        anchor = anchors.get(key)
        if anchor is None:
            anchor = bpy.data.objects.new('PhysX reference · '+definition['label'],None)
            anchor[data.OWNER],anchor[ROLE],anchor[GOAL_ID] = owner,'anchor',key
            anchor.hide_select = True
            anchor.hide_render = True
            anchor.empty_display_size = .001/binding['unit_scale']
            collection.objects.link(anchor)
            anchor.hide_set(True)
            anchors[key] = anchor
        anchor.matrix_world = _anchor_matrix(matrices,key,binding['unit_scale'])
        goal = goals.get(key)
        if goal is None:
            goal = bpy.data.objects.new('PhysX target · '+definition['label'],None)
            goal[data.OWNER],goal[ROLE],goal[GOAL_ID] = owner,'goal',key
            goal.empty_display_type = 'SPHERE'
            goal.empty_display_size = .035/binding['unit_scale']
            goal.show_in_front = True
            goal.hide_render = True
            goal['physx_body'] = definition['body']
            goal['physx_local_point'] = definition['local_position']
            collection.objects.link(goal)
            goal.parent = anchor
            goal.matrix_parent_inverse = Matrix.Identity(4)
            goal.matrix_basis = Matrix.Identity(4)
            goals[key] = goal
        elif goal.parent != anchor:
            world = goal.matrix_world.copy()
            goal.parent = anchor
            goal.matrix_parent_inverse = Matrix.Identity(4)
            goal.matrix_basis = anchor.matrix_world.inverted() @ world
    reset_velocity(collection,owner)
    tag_redraw()
    return collection,goals


def reset_velocity(collection=None, owner=None):
    if collection is None:
        _velocity.clear()
        return
    _velocity.pop((collection.as_pointer(),owner or collection.get(data.OWNER)),None)


def clear_targets(collection, owner):
    if collection is None:
        return
    _owned(collection,owner)
    goals = points(collection,owner)
    for obj in [*goals.values(),*_anchors(collection,owner).values()]:
        bpy.data.objects.remove(obj,do_unlink=True)
    reset_velocity(collection,owner)
    tag_redraw()


def remove(collection, owner):
    if collection is None:
        return
    _owned(collection,owner)
    reset_velocity(collection,owner)
    collection[SHOW] = False
    display.remove(collection,owner)
    tag_redraw()


def _world_point(goal, anchor):
    if goal.parent == anchor and not goal.constraints:
        # Parent matrices are assigned during the same timer tick. Compose
        # them directly instead of forcing a scene-wide dependency update.
        return (anchor.matrix_world @ goal.matrix_parent_inverse @ goal.matrix_basis).translation
    graph = bpy.context.evaluated_depsgraph_get()
    return goal.evaluated_get(graph).matrix_world.translation.copy()


def collect_points(collection, owner, binding, now=None):
    """Read generated positions without writing any goal or user source data."""
    goals = points(collection,owner)
    if not goals:
        return []
    now = time.monotonic() if now is None else float(now)
    if not math.isfinite(now):
        raise ValueError('Point target time must be finite')
    inverse = skeleton.unflat(binding['placement']).inverted()
    units = binding['unit_scale']
    key = (collection.as_pointer(),owner)
    previous = _velocity.get(key)
    elapsed = now-previous['now'] if previous else 0.
    positions = {}
    result = []
    anchors = _anchors(collection,owner)
    for ident in POINT_IDS:
        goal = goals.get(ident)
        anchor = anchors.get(ident)
        if goal is None or anchor is None:
            continue
        world = _world_point(goal,anchor)
        position = inverse @ (world*units)
        offset = ((goal.matrix_parent_inverse @ goal.matrix_basis).translation*units
                  if goal.parent == anchor and not goal.constraints else
                  (anchor.matrix_world.inverted() @ world)*units)
        old = previous['positions'].get(ident) if previous else None
        velocity = (position-old)/elapsed if old is not None and 1e-7 < elapsed <= 1 else Vector()
        if not all(math.isfinite(value) for vector in (position,offset,velocity) for value in vector):
            raise ValueError('Point target contains non-finite position: '+ident)
        definition = POINTS[ident]
        result.append({'id':ident,'body':definition['body'],'local_position':list(definition['local_position']),
            'reference_offset':list(offset),'position':list(position),'velocity':list(velocity)})
        positions[ident] = position.copy()
    _velocity[key] = {'now':now,'positions':positions}
    return result


def _geometry(kind, size):
    if kind == 'box':
        x,y,z = size
        vertices = [(a*x,b*y,c*z) for a,b,c in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                    (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    else:
        radius = size[0]
        half = size[1] if kind == 'capsule' else 0
        rings = [(radius*math.cos(t),-half+radius*math.sin(t))
                 for t in [-math.pi/2+i*math.pi/12 for i in range(1,7)]]
        if half:
            rings.append((radius,half))
        rings += [(radius*math.cos(t),half+radius*math.sin(t)) for t in [i*math.pi/12 for i in range(1,6)]]
        sides = 12
        vertices = [(0,0,-half-radius)]
        for r,z in rings:
            vertices += [(r*math.cos(2*math.pi*i/sides),r*math.sin(2*math.pi*i/sides),z) for i in range(sides)]
        top = len(vertices)
        vertices.append((0,0,half+radius))
        faces = [(0,1+(i+1)%sides,1+i) for i in range(sides)]
        for row in range(len(rings)-1):
            a,b = 1+row*sides,1+(row+1)*sides
            faces += [(a+i,a+(i+1)%sides,b+(i+1)%sides,b+i) for i in range(sides)]
        a = 1+(len(rings)-1)*sides
        faces += [(a+i,a+(i+1)%sides,top) for i in range(sides)]
    triangles = [(face[0],face[index],face[index+1]) for face in faces for index in range(1,len(face)-1)]
    edges = sorted({tuple(sorted((face[index],face[(index+1)%len(face)])))
                    for face in faces for index in range(len(face))})
    return vertices,triangles,edges


def draw_records(collection):
    """CPU-only world geometry records, also used by the focused visual test."""
    binding = json.loads(collection[BINDING])
    pose = reference_pose(collection)
    matrices = skeleton.world_matrices(pose['positions'],pose['rotations'],binding)
    units = binding['unit_scale']
    result = []
    for body,geom in _smpl().geoms:
        kind = geom.get('type','sphere')
        size = list(map(float,geom.get('size','.05').split()))
        local = Quaternion(tuple(map(float,geom.get('quat','1 0 0 0').split()))).to_matrix().to_4x4()
        local.translation = Vector(tuple(map(float,geom.get('pos','0 0 0').split())))/units
        if 'fromto' in geom:
            ends = list(map(float,geom['fromto'].split()))
            a,b = Vector(ends[:3]),Vector(ends[3:])
            local = Vector((0,0,1)).rotation_difference(b-a).to_matrix().to_4x4()
            local.translation = (a+b)*.5/units
            size = [size[0],(b-a).length*.5]
        result.append({'matrix':matrices[_smpl().names[body]] @ local,
                       'geometry':(kind,tuple(value/units for value in size))})
    return result


def tag_redraw():
    manager = getattr(bpy.context,'window_manager',None)
    if manager is not None:
        for window in manager.windows:
            if window.screen:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()


def _drawable(context):
    space,region = getattr(context,'space_data',None),getattr(context,'region',None)
    return bool(space and space.type == 'VIEW_3D' and region and region.type == 'WINDOW'
                and getattr(context,'region_data',None) and space.overlay.show_overlays)


def _visible_collections(context):
    def visit(layer, hidden=False):
        hidden = hidden or layer.exclude or layer.hide_viewport or layer.collection.hide_viewport
        if not hidden and layer.collection.get(PREVIEW) and layer.collection.get(SHOW,True):
            yield layer.collection
        for child in layer.children:
            yield from visit(child,hidden)
    yield from visit(context.view_layer.layer_collection)


def _shader(name):
    import gpu
    if name not in _shaders:
        _shaders[name] = gpu.shader.from_builtin(name)
    return _shaders[name]


def _batch(key):
    from gpu_extras.batch import batch_for_shader
    if key not in _batches:
        vertices,triangles,edges = _geometry(*key)
        _batches[key] = (batch_for_shader(_shader('UNIFORM_COLOR'),'TRIS',{'pos':vertices},indices=triangles),
            batch_for_shader(_shader('POLYLINE_UNIFORM_COLOR'),'LINES',{'pos':vertices},indices=edges))
    return _batches[key]


@contextmanager
def _drawing_state():
    import gpu
    blend,depth,mask = gpu.state.blend_get(),gpu.state.depth_test_get(),gpu.state.depth_mask_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        yield
    finally:
        gpu.state.depth_mask_set(mask)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def _draw(context):
    import gpu
    if not _drawable(context):
        return
    groups = [(collection,draw_records(collection)) for collection in _visible_collections(context)
              if collection.get(OPACITY,.2) > 0]
    if not groups:
        return
    viewport = tuple(gpu.state.viewport_get()[2:])
    with _drawing_state():
        for wire in (False,True):
            shader = _shader('POLYLINE_UNIFORM_COLOR' if wire else 'UNIFORM_COLOR')
            for collection,records in groups:
                opacity = float(collection.get(OPACITY,.2))
                color = (.18,.35,.5,opacity) if wire else (.52,.78,.96,opacity)
                for record in records:
                    with gpu.matrix.push_pop():
                        gpu.matrix.multiply_matrix(record['matrix'])
                        shader.bind()
                        shader.uniform_float('color',color)
                        if wire:
                            shader.uniform_float('viewportSize',viewport)
                            shader.uniform_float('lineWidth',1.1)
                        _batch(record['geometry'])[int(wire)].draw(shader)


def _draw_callback():
    global last_error
    try:
        _draw(bpy.context)
        last_error = ''
    except Exception as error:
        last_error = type(error).__name__+': '+str(error)


def invalidate():
    global last_error
    _shaders.clear()
    _batches.clear()
    reset_velocity()
    last_error = ''
    tag_redraw()


def register():
    global _handler
    if _handler is None and not bpy.app.background:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw_callback,(),'WINDOW','POST_VIEW')
    tag_redraw()


def unregister():
    global _handler
    if _handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handler,'WINDOW')
        except (ReferenceError,ValueError):
            pass
        _handler = None
    invalidate()
