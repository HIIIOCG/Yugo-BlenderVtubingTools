"""Only the worker advances physics; evaluation reads the latest received state."""
from pathlib import Path
import json
import math
import sys
import tempfile
import threading
import time
import uuid
import bpy
from . import bridge,data,skeleton,display,motion,protocol,physics_inputs,scene_display,reference_display
from .client import Client

_worlds = {}

def play_requested(node):
    world = _worlds.get(node.runtime_id)
    return bool(world and world['autoplay'])

def preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None

def release(key):
    world = _worlds.pop(key,None)
    if world:
        world['client'].close()
        display.remove(world.get('extra_collection'),key)
        scratch = world.get('scratch')
        if scratch:
            def cleanup():
                try: world['client'].process.wait(timeout=8)
                except Exception: pass
                scratch.cleanup()
            threading.Thread(target=cleanup,name='PhysX input cleanup',daemon=True).start()
        tree = bpy.data.node_groups.get(world['actor'].tree_name)
        node = next((node for node in tree.nodes if node.bl_idname == data.PHYSICS
            and node.runtime_id == key),None) if tree else None
        if node is not None and hasattr(node,'playing'):
            node.playing = False

def clear():
    for key in tuple(_worlds): release(key)
    physics_inputs.clear_revision_cache()
    for tree in bpy.data.node_groups:
        if tree.bl_idname == data.TREE:
            for node in tree.nodes:
                if node.bl_idname == data.PHYSICS:
                    node.status = 'Disconnected'

def invalidate_actor(actor_id):
    for key,world in tuple(_worlds.items()):
        if world['actor'].actor_id == actor_id:
            release(key)
            tree = bpy.data.node_groups.get(world['actor'].tree_name)
            if tree:
                node = next((node for node in tree.nodes if node.bl_idname == data.PHYSICS
                    and node.runtime_id == key),None)
                if node:
                    node.status = 'Disconnected · Skeleton changed; Play again'

def free_node(node):
    if node.bl_idname == data.SKELETON:
        invalidate_actor(node.runtime_id)
        display.remove(node.collection,node.runtime_id)
    elif node.bl_idname == data.PHYSICS:
        release(node.runtime_id)
        reference_display.remove(node.reference_collection,node.runtime_id)

def setup(ctx,node):
    if node.source_mode == 'BUILTIN':
        source = None
        binding,matrices = skeleton.build_builtin(ctx.scene.unit_settings.scale_length,node.offset)
    else:
        mapped = ctx.input(node,'Bone Map',None)
        if not isinstance(mapped,bridge.base().data.BoneMapData):
            raise ValueError('Connect the original Yugo Bone Map, or choose Built-in SMPL')
        source = ctx.scene.objects.get(mapped.armature_name)
        if source is None or source.type != 'ARMATURE' or source == node.rig:
            raise ValueError('Bone Map must use the source Armature')
        binding,matrices = skeleton.build(source,dict(mapped.by_humanoid),ctx.scene.unit_settings.scale_length,node.offset)
    collection,rig = display.create(ctx.scene,node.runtime_id,matrices,binding['unit_scale'])
    invalidate_actor(node.runtime_id)
    old = node.collection
    node.collection,node.rig,node.source = collection,rig,source
    node.binding_json = json.dumps(binding)
    node.revision = uuid.uuid4().hex
    display.remove(old,node.runtime_id)
    node.status = 'SMPL ready · connect Tracker and Play'

def actor_value(ctx,node):
    if not node.binding_json:
        return None
    if node.rig is None or node.collection is None:
        raise ValueError('Skeleton objects removed; Setup again')
    binding = json.loads(node.binding_json)
    if binding.get('source_mode','BONE_MAP') != node.source_mode:
        raise ValueError('Skeleton source changed; Setup again')
    if node.source_mode == 'BUILTIN':
        if not skeleton.builtin_binding_current(binding,ctx.scene.unit_settings.scale_length,node.offset):
            raise ValueError('SMPL facing, placement or scene units changed; Setup again')
        return data.Actor(node.runtime_id,node.revision,node.binding_json,node.name,node.id_data.name)
    if node.source is None:
        raise ValueError('Source Armature removed; Setup again')
    mapped = ctx.input(node,'Bone Map',None)
    if not isinstance(mapped,bridge.base().data.BoneMapData):
        raise ValueError('Bone Map disconnected; Setup again')
    if (mapped.armature_name != node.source.name
            or ctx.scene.objects.get(node.source.name) != node.source
            or skeleton.signature(node.source,dict(mapped.by_humanoid),ctx.scene.unit_settings.scale_length,'TEMPLATE',node.offset) != binding['source_signature']):
        raise ValueError('Bone Map or rest pose changed; Setup again')
    return data.Actor(node.runtime_id,node.revision,node.binding_json,node.name,node.id_data.name)

def controller_value(ctx,node):
    actor = ctx.input(node,'Actor',None)
    if actor is None:
        return None
    if not isinstance(actor,data.Actor):
        raise ValueError('Connect an PhysX Skeleton')
    reference = ctx.input(node,'Reference Motion',None) if node.reference_mode == 'LIVE' else None
    reference = motion.validate(reference) if reference is not None else None
    return data.Controller(actor,reference,node.runtime_id,bpy.path.abspath(node.model_path) if node.model_path else '',
                           bpy.path.abspath(node.clip_path) if node.clip_path else '',node.reference_mode)

def settings(controller,node=None):
    return (controller.actor.actor_id,controller.actor.revision,controller.model_path,controller.clip_path,
            controller.reference_mode,getattr(node,'physics_hz',120),getattr(node,'solver','TGS'),
            getattr(node,'iterations',8))

def root_support_value(ctx,node):
    value = ctx.input(node,'Root Support',0.)
    if (isinstance(value,bool) or not isinstance(value,(int,float))
            or not math.isfinite(value) or not 0 <= value <= 2):
        raise ValueError('Root Support must be between 0 and 2')
    return float(value)

def send_root_support(world,strength):
    if world['last_root_support'] != strength:
        world['client'].send({'type':'root_support','strength':strength})
        world['last_root_support'] = strength

def point_settings(ctx,node):
    values = {'strength':ctx.input(node,'Position Strength',1.),
              'frequency_hz':node.spring_frequency,'damping_ratio':node.spring_damping,
              'max_force_n':node.max_point_force}
    from .point_contract import normalize_point_targets
    return normalize_point_targets({**values,'targets':[]})

def ensure_reference(scene,node,binding):
    collection = reference_display.ensure(scene,node.runtime_id,binding,node.reference_collection)
    node.reference_collection = collection
    reference_display.configure(collection,node.reference_visible,node.reference_opacity)
    return collection

def sync_reference(scene,node,binding,reference):
    collection = ensure_reference(scene,node,binding)
    reference_display.sync(collection,binding,reference,node.reference_visible,node.reference_opacity)

def send_point_targets(ctx,node,world,force=False):
    payload = point_settings(ctx,node)
    collection = ensure_reference(ctx.scene,node,world['binding'])
    payload['targets'] = reference_display.collect_points(collection,node.runtime_id,world['binding'])
    # Generated targets are relative to the worker's reference pose. It updates
    # that pose each physics substep; only edited offsets/settings need a packet.
    signature = {**payload,'targets':[{key:value for key,value in record.items()
        if key not in {'position','velocity'}} for record in payload['targets']]}
    for record in signature['targets']:
        record['reference_offset'] = [round(value,7) for value in record['reference_offset']]
    signature = json.dumps(signature,allow_nan=False,sort_keys=True,separators=(',',':'))
    if force or signature != world['last_point_targets']:
        world['client'].send({'type':'point_targets',**payload})
        world['last_point_targets'] = signature

def scene_file(scratch,spec,revision):
    payload = json.dumps(spec,allow_nan=False,separators=(',',':'))
    if len(payload.encode('utf-8')) > 16*1024*1024:
        raise ValueError('Scene collision inputs exceed 16 MiB; use simpler colliders')
    path = Path(scratch.name)/(revision+'.json')
    path.write_text(payload,encoding='utf-8')
    return path

def sync_scene(ctx,node,world,force=False):
    """Collect geometry only after connected source settings actually change."""
    now = time.monotonic()
    if world['pending_scene'] is not None:
        return
    if not force and now-world['last_scene_check'] < .25:
        return
    world['last_scene_check'] = now
    try:
        revision = physics_inputs.revision(ctx,node,world['binding'])
        if revision == world['input_revision']:
            return
        world['input_revision'] = revision
        spec = physics_inputs.collect(ctx,node,world['binding'])
        protocol.scene_budget(spec)
        packet = ctx.input(node,'Spring Bones',None)
        spring_data = getattr(packet,'spring_data',None)
        if spec['topology_hash'] == world['scene_spec']['topology_hash']:
            world['scene_error'] = ''
            world['spring_data'] = spring_data
            # Names and input metadata can change without native geometry.
            world['extra_collection'],world['extra_proxies'] = scene_display.reconcile(
                ctx.scene,node.runtime_id,spec,world['binding'],
                world['extra_collection'],world['extra_proxies'])
            world['scene_spec'] = spec
            return
        request = uuid.uuid4().hex
        path = scene_file(world['scratch'],spec,request)
        world['pending_scene'] = {'revision':request,'spec':spec,'spring_data':spring_data,'path':path}
        world['client'].send({'type':'scene_reconcile','path':str(path),'revision':request})
    except (ValueError,TypeError,ReferenceError) as error:
        world['scene_error'] = str(error)
        node.status = 'Scene edit pending · '+str(error)

def scene_result(scene,node,world,event):
    pending = world['pending_scene']
    if pending is None or event.get('revision') != pending['revision']:
        return
    if event['type'] == 'scene_applied':
        world['extra_collection'],world['extra_proxies'] = scene_display.reconcile(
            scene,node.runtime_id,pending['spec'],world['binding'],
            world['extra_collection'],world['extra_proxies'])
        world['scene_spec'] = pending['spec']
        world['spring_data'] = pending['spring_data']
        world['scene_error'] = ''
        world['scene_revision'] = pending['revision']
        world['state']['scene_revision'] = pending['revision']
    else:
        world['scene_error'] = event.get('message','Scene update was rejected')
        node.status = 'Scene edit pending · '+world['scene_error']
    pending['path'].unlink(missing_ok=True)
    world['pending_scene'] = None

def worker_executable(prefs=None):
    mode = getattr(prefs,'worker_mode','AUTO') if prefs else 'AUTO'
    override = getattr(prefs,'executable_path','') if prefs else ''
    prefix = Path(sys.prefix)
    bundled = next((path for path in (prefix/'bin/python.exe',prefix/'python.exe',
                    *sorted((prefix/'bin').glob('python3.*'))) if path.is_file()
                    and path.suffix.lower() not in {'.dll','.pyd'}),None)
    if override:
        path = Path(bpy.path.abspath(override))
        if not path.is_file():
            raise ValueError('Worker executable is missing; check addon preferences')
        if mode == 'AUTO':
            raise ValueError('Choose Standalone Python or Background Blender for a custom executable')
        return str(path),mode
    if mode != 'BLENDER' and bundled:
        return str(bundled),'PYTHON'
    if mode == 'PYTHON':
        raise ValueError('Blender bundled Python was not found; set a compatible executable')
    blender = Path(bpy.app.binary_path)
    if not blender.is_file():
        raise ValueError('Blender worker executable was not found')
    return str(blender),'BLENDER'

def controller_for_play(ctx,node):
    failure = None
    try:
        controller = ctx.input(node,'Controller',None)
        if isinstance(controller,data.Controller):
            return controller
    except ValueError as error:
        failure = error
    # One-click startup also prepares a directly connected Skeleton when its
    # saved binding is absent or was invalidated by a source/basis change.
    controller_socket = node.inputs.get('Controller')
    tracker = controller_socket.links[0].from_node if controller_socket and controller_socket.is_linked else None
    actor_socket = tracker.inputs.get('Actor') if tracker and tracker.bl_idname == data.PROTO else None
    source = actor_socket.links[0].from_node if actor_socket and actor_socket.is_linked else None
    if source is None or source.bl_idname != data.SKELETON:
        if failure: raise failure
        raise ValueError('Connect PhysX Skeleton and its Tracker')
    setup(ctx,source)
    ctx.cache.clear()
    return ctx.input(node,'Controller',None)

def build(ctx,node,play=False):
    controller = controller_for_play(ctx,node) if play else ctx.input(node,'Controller',None)
    if not isinstance(controller,data.Controller):
        raise ValueError('Setup PhysX Skeleton and connect its Tracker')
    if any(key != node.runtime_id and world['actor'].actor_id == controller.actor.actor_id
           for key,world in _worlds.items()):
        raise ValueError('This Actor already belongs to another PhysX Physics node')
    prefs = preferences()
    executable,launch_mode = worker_executable(prefs)
    if node.physics_hz % 30 or not 120 <= node.physics_hz <= 1200:
        raise ValueError('Physics Hz must be a multiple of 30 in 120..1200')
    args = ['--mode',controller.reference_mode,'--physics-hz',str(node.physics_hz),
            '--solver',node.solver,'--iterations',str(node.iterations)]
    if controller.model_path: args += ['--model',controller.model_path]
    if controller.clip_path: args += ['--clip',controller.clip_path]
    log_dir = Path(bpy.utils.user_resource('CONFIG',path='yugo_physx_tracker',create=True))
    binding = json.loads(controller.actor.binding_json)
    scene_spec = physics_inputs.collect(ctx,node,binding)
    protocol.scene_budget(scene_spec)
    input_revision = physics_inputs.revision(ctx,node,binding)
    root_support_value(ctx,node)
    point_settings(ctx,node)
    ensure_reference(ctx.scene,node,binding)
    spring_input = ctx.input(node,'Spring Bones',None)
    spring_data = getattr(spring_input,'spring_data',None)
    scratch = tempfile.TemporaryDirectory(prefix='yugo_physx_inputs_')
    try:
        args += ['--scene-directory',scratch.name]
        if scene_spec['objects'] or scene_spec['springs']:
            args += ['--scene-inputs',str(scene_file(scratch,scene_spec,'initial'))]
    except Exception:
        scratch.cleanup()
        raise
    release(node.runtime_id)
    try:
        client = Client(executable,args,log_dir/(node.runtime_id+'.log'),launch_mode=launch_mode)
    except Exception:
        if scratch: scratch.cleanup()
        raise
    _worlds[node.runtime_id] = {'client':client,'actor':controller.actor,'settings':settings(controller,node),
        'binding':binding,'scene_spec':scene_spec,'scratch':scratch,'extra_collection':None,'extra_proxies':{},
        'spring_motion':None,'spring_data':spring_data,'last_root_support':None,'last_point_targets':None,'last_scene_frame':0.,'state':{'connected':False,'running':False,
            'ready':False,'learned_policy_loaded':False},'motion':None,'ready':False,
        'autoplay':play,'last_frame':0.,'last_reference':None,'scene':ctx.scene.name,'last_event':time.monotonic(),
        'input_revision':input_revision,'last_scene_check':0.,'pending_scene':None,'scene_error':'','scene_revision':'initial'}
    node.status = 'Starting standalone PhysX and loading SMPL actor…'
    if hasattr(node,'playing'): node.playing = bool(play)

def actor_node(actor):
    tree = bpy.data.node_groups.get(actor.tree_name)
    return next((n for n in tree.nodes if n.bl_idname == data.SKELETON and n.runtime_id == actor.actor_id),None) if tree else None

def send_reference(world,snode,controller,force=False):
    # A missing source retains its last target. Explicit empty motion means rest.
    payload = world['last_reference']
    if controller.reference is None and payload is not None and not force:
        return False
    if controller.reference is not None or payload is None:
        reference = controller.reference if controller.reference is not None else motion.validate(None)
        payload = motion.reference_state(snode.source,world['binding'],reference)
    world['client'].send({'type':'reference',**payload})
    world['last_reference'] = payload
    return True

def action(ctx,node,name):
    if node.bl_idname == data.SKELETON:
        if name == 'Setup': setup(ctx,node)
        elif name == 'Clear':
            invalidate_actor(node.runtime_id)
            display.remove(node.collection,node.runtime_id)
            node.collection = node.rig = node.source = None
            node.binding_json = node.revision = ''
            node.status = 'Built-in SMPL · Setup without Bone Map' if node.source_mode == 'BUILTIN' else 'Connect Bone Map, then Setup'
        return
    if node.bl_idname == data.PROTO:
        controller = controller_value(ctx,node)
        for world in _worlds.values():
            if controller and world['actor'].actor_id == controller.actor.actor_id:
                if world['ready'] and controller.reference_mode == 'LIVE':
                    snode = actor_node(world['actor'])
                    if snode is not None:
                        send_reference(world,snode,controller,force=True)
                world['client'].send({'type':'reset'})
                world['autoplay'] = False
                world['state']['running'] = False
                tree = bpy.data.node_groups.get(world['actor'].tree_name)
                physics_node = next((candidate for candidate in tree.nodes
                    if candidate.bl_idname == data.PHYSICS and _worlds.get(candidate.runtime_id) is world),None) if tree else None
                if physics_node is not None and hasattr(physics_node,'playing'):
                    physics_node.playing = False
        return
    if name == 'Generate Targets':
        controller = controller_for_play(ctx,node)
        if not isinstance(controller,data.Controller):
            raise ValueError('Connect PhysX Skeleton and its Tracker')
        binding = json.loads(controller.actor.binding_json)
        world = _worlds.get(node.runtime_id)
        reference = world['state'].get('reference_pose') if world else None
        if reference is None and controller.reference_mode == 'LIVE':
            snode = actor_node(controller.actor)
            reference = motion.reference_state(snode.source,binding,
                controller.reference if controller.reference is not None else motion.validate(None))
        elif reference is None and controller.reference_mode == 'CLIP':
            from .model import ASSETS,Skeleton
            from .clip import MotionClip
            clip = MotionClip(Path(controller.clip_path) if controller.clip_path else ASSETS/'smpl_amass/walk.npz',Skeleton().names)
            positions,rotations,velocities,angular = clip.sample(0.)
            reference = {'positions':positions.tolist(),'rotations':rotations.tolist(),
                         'velocities':velocities.tolist(),'angular_velocities':angular.tolist()}
        collection = ensure_reference(ctx.scene,node,binding)
        node.reference_collection,_ = reference_display.generate(ctx.scene,node.runtime_id,binding,reference,collection)
        if world and world['ready']:
            send_point_targets(ctx,node,world,force=True)
        if not world:
            node.status = '11 position targets ready · follow reference with local offsets'
        return
    if name == 'Clear Targets':
        reference_display.clear_targets(node.reference_collection,node.runtime_id)
        world = _worlds.get(node.runtime_id)
        if world and world['ready']:
            send_point_targets(ctx,node,world,force=True)
        if not world:
            node.status = 'Position targets cleared'
        return
    if name == 'Play':
        world = _worlds.get(node.runtime_id)
        if world is None:
            build(ctx,node,play=True)
            return
        desired = not world['autoplay']
        world['autoplay'] = desired
        if hasattr(node,'playing'): node.playing = desired
        if not world['ready']:
            node.status = 'Loading PhysX · '+('will play when ready' if desired else 'paused')
            return
        if desired:
            controller = ctx.input(node,'Controller',None)
            if not isinstance(controller,data.Controller) or settings(controller,node) != world['settings']:
                build(ctx,node,play=True)
                return
            sync_scene(ctx,node,world,force=True)
        world['client'].send({'type':'play' if desired else 'pause'})
        if not desired:
            world['state']['running'] = False
        return
    if name == 'Disconnect':
        release(node.runtime_id)
        node.status = 'Disconnected'
    elif name == 'Build':
        build(ctx,node,play=name=='Play')
    else:
        world = _worlds.get(node.runtime_id)
        if not world:
            raise ValueError('Play PhysX first')
        if not world['ready']:
            if name in {'Play','Pause'}:
                world['autoplay'] = name == 'Play'
                return
            raise ValueError('PhysX is still starting')
        controller = ctx.input(node,'Controller',None)
        if not isinstance(controller,data.Controller) or settings(controller,node) != world['settings']:
            raise ValueError('Tracker, Skeleton or physics settings changed; Play again')
        if name == 'Reset' and controller.reference_mode == 'LIVE':
            snode = actor_node(world['actor'])
            if snode is None:
                raise ValueError('Skeleton removed; Setup again')
            send_reference(world,snode,controller,force=True)
        world['client'].send({'type':name.lower()})
        if name in {'Pause','Reset','Step'}:
            world['autoplay'] = False
            world['state']['running'] = False
            if hasattr(node,'playing'): node.playing = False

def evaluate(ctx,node):
    if node.bl_idname == data.SKELETON:
        return bridge.outputs(node,Actor=actor_value(ctx,node),Rig=node.rig)
    if node.bl_idname == data.PROTO:
        return bridge.outputs(node,Controller=controller_value(ctx,node))
    world = _worlds.get(node.runtime_id)
    return bridge.outputs(node,**{'Humanoid Motion':world['motion'] if world else None,
                                'Spring Motion':world['spring_motion'] if world else None,
                                'Physics State':dict(world['state']) if world else {'connected':False,
                                    'running':False,'ready':False,'learned_policy_loaded':False}})

def tick():
    for tree in tuple(bpy.data.node_groups):
        if tree.bl_idname != data.TREE:
            continue
        for node in tuple(tree.nodes):
            world = _worlds.get(getattr(node,'runtime_id','')) if node.bl_idname == data.PHYSICS else None
            if not world:
                continue
            try:
                scene = bpy.data.scenes.get(world['scene'])
                if scene is None: raise ValueError('Scene removed; reconnect PhysX')
                ctx = bridge.context(tree,scene)
                controller = ctx.input(node,'Controller',None)
                if not isinstance(controller,data.Controller) or settings(controller,node) != world['settings']:
                    raise ValueError('Tracker, Skeleton or physics settings changed; Play again')
                snode = actor_node(world['actor'])
                if snode is None: raise ValueError('Skeleton removed')
                support = root_support_value(ctx,node)
                accepted_state = False
                for event in world['client'].drain():
                    world['last_event'] = time.monotonic()
                    if event['type'] in {'scene_applied','scene_rejected'}:
                        scene_result(scene,node,world,event)
                    if event['type'] == 'error':
                        if event.get('fatal',True):
                            raise ValueError(event['message'])
                        node.status = event['message']
                        world['autoplay'] = False
                        world['state']['running'] = False
                        if hasattr(node,'playing'): node.playing = False
                        world['state']['error'] = event['message']
                    if event['type'] == 'status': node.status = event['message']
                    if event['type'] == 'ready':
                        world['ready'] = True
                        world['state'].update(connected=True,ready=True,backend=event['backend'],
                            physics_hz=event['physics_hz'],policy_hz=event['policy_hz'])
                        node.status = 'Standalone PhysX ready · paused'
                        send_root_support(world,support)
                        if world['scene_spec']['objects'] or world['scene_spec']['springs']:
                            world['extra_collection'],world['extra_proxies'] = scene_display.create(
                                scene,node.runtime_id,world['scene_spec'],world['binding'])
                        if controller.reference_mode == 'LIVE':
                            send_reference(world,snode,controller,force=True)
                            world['client'].send({'type':'reset'})
                        if world['autoplay']:
                            world['client'].send({'type':'play'})
                    if event['type'] == 'state':
                        protocol.pose(event)
                        if type(event.get('running')) is not bool or any(
                            isinstance(event.get(key),bool) or not isinstance(event.get(key),(int,float))
                            or not math.isfinite(event[key]) for key in ('seconds','tracking_error_m')):
                            raise ValueError('PhysX returned invalid state metadata')
                        reported_support = event.get('root_support_strength',0.)
                        if (event.get('learned_policy_loaded') is not True
                                or type(event.get('no_root_support')) is not bool
                                or isinstance(reported_support,bool) or not isinstance(reported_support,(int,float))
                                or not math.isfinite(reported_support) or not 0 <= reported_support <= 2
                                or event['no_root_support'] != (reported_support == 0)):
                            raise ValueError('PhysX worker did not confirm the learned tracker contract')
                        matrices = skeleton.world_matrices(event['positions'],event['rotations'],world['binding'])
                        reference = event.get('reference_pose')
                        if reference is not None:
                            protocol.pose(reference)
                            sync_reference(scene,node,world['binding'],reference)
                        display.sync(snode,matrices)
                        world['motion'] = motion.output(matrices,world['binding'],snode.source)
                        if 'extra_scene' in event:
                            scene_display.sync(world['extra_proxies'],event['extra_scene'],world['binding'])
                            world['spring_motion'] = scene_display.spring_motion(world['scene_spec'],event['extra_scene'],world['binding'],world['spring_data'])
                        world['state'] = {k:v for k,v in event.items() if k not in {'positions','rotations'}}
                        world['state']['connected'] = True
                        world['state']['ready'] = True
                        world['state']['scene_revision'] = world['scene_revision']
                        node.status = ('Playing' if event['running'] else 'Paused')+f" · PhysX CPU · {event['seconds']:.1f} s\nLearned SMPL tracker · error {event['tracking_error_m']*100:.1f} cm"
                        if reported_support:
                            node.status += f' · Root Support {reported_support:g}'
                        points = event.get('point_targets',{})
                        if points.get('count'):
                            node.status += f"\nPosition targets: {points['count']} · "+('active' if points.get('enabled') else 'off')
                        if world['scene_error']:
                            node.status += '\nScene edit pending · '+world['scene_error']
                        accepted_state = True
                now = time.monotonic()
                if world['ready']:
                    send_root_support(world,support)
                    send_point_targets(ctx,node,world)
                    sync_scene(ctx,node,world)
                if world['ready'] and now-world['last_scene_frame'] >= 1/60:
                    update = physics_inputs.updates(ctx,node,world['binding'],world['scene_spec'])
                    if update['objects'] or update['anchors']:
                        world['client'].send({'type':'scene_update',**update})
                    world['last_scene_frame'] = max(world['last_scene_frame']+1/60,now-1/60)
                if world['ready'] and controller.reference_mode == 'LIVE' and now-world['last_frame'] >= 1/60:
                    send_reference(world,snode,controller)
                    # Preserve the cadence across timer jitter; bound catch-up to
                    # one frame so an idle Blender never floods the worker.
                    world['last_frame'] = max(world['last_frame']+1/60,now-1/60)
                if world['state'].get('running') and now-world['last_event'] > 10:
                    raise RuntimeError('PhysX stopped responding; reconnect')
                if not world['ready'] and now-world['client'].started > 120:
                    raise RuntimeError('PhysX startup timed out; check its log')
                if accepted_state:
                    # Publish after all current data is stored so downstream Bind
                    # and Apply nodes pull the same frame with a fresh cache.
                    for event_name in ('Updated','Done'):
                        output = node.outputs.get(event_name)
                        if output is not None and output.is_linked:
                            bridge.base().flow.emit_event(tree,node,event_name,now=now)
            except Exception as error:
                node.status = str(error)
                release(node.runtime_id)
    return 1/60
