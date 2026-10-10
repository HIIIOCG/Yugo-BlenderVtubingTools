"""Author and control additional native PhysX bodies and VRM spring segments.

Inputs use meters and XYZW quaternions in the SMPL simulation frame. Blender is
only a source of geometry/parameters; this module never imports or writes bpy.
USD is authored directly so the packaged worker needs no USD Python runtime.
"""
import copy
import hashlib
import json
import math
import warnings


SCHEMA = 'yugo-physx-scene-inputs-v1'


def _vector(value, count, label):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(label + ' must contain ' + str(count) + ' numbers')
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result):
        raise ValueError(label + ' contains non-finite numbers')
    return result


def _number(value, label, low=0, high=1e6):
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(label + ' outside supported range')
    return result


def _pose(value, label='pose'):
    result = _vector(value, 7, label)
    norm = math.sqrt(sum(v*v for v in result[3:]))
    if norm < 1e-8:
        raise ValueError(label + ' has a zero quaternion')
    result[3:] = [v/norm for v in result[3:]]
    return result


def _qmul(a, b):
    x,y,z,w = a
    X,Y,Z,W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def _qinv(q):
    return [-q[0],-q[1],-q[2],q[3]]


def _rotate(q, v):
    return _qmul(_qmul(q, [*v,0]), _qinv(q))[:3]


def _compose(parent, local):
    delta = _rotate(parent[3:],local[:3])
    return [parent[i]+delta[i] for i in range(3)] + _qmul(parent[3:],local[3:])


def _relative(parent, child):
    return _rotate(_qinv(parent[3:]), [child[i]-parent[i] for i in range(3)]) + _qmul(_qinv(parent[3:]),child[3:])


def _require_convex_volume(points):
    """Reject planar/linear input instead of silently cooking a different hull."""
    sub = lambda a,b:[a[i]-b[i] for i in range(3)]
    dot = lambda a,b:sum(a[i]*b[i] for i in range(3))
    cross = lambda a,b:[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    origin = points[0]
    first = max((sub(p,origin) for p in points),key=lambda d:dot(d,d))
    scale = math.sqrt(dot(first,first))
    normal = max((cross(first,sub(p,origin)) for p in points),key=lambda d:dot(d,d))
    normal_length = math.sqrt(dot(normal,normal))
    distance = max(abs(dot(normal,sub(p,origin))) for p in points)
    if scale <= 0 or normal_length <= 0 or distance <= normal_length*scale*1e-7:
        raise ValueError('Convex collision geometry is flat or degenerate; choose Mesh for a passive surface')


def _fmt(value):
    return format(float(value), '.12g')


def _tuple(value):
    return '(' + ', '.join(_fmt(v) for v in value) + ')'


def _quat(value):
    return _tuple([value[3], *value[:3]])


def _identifier(kind, identifier):
    return kind + '_' + hashlib.sha256(str(identifier).encode('utf-8')).hexdigest()[:20]


def _transform(pose):
    return ['double3 xformOp:translate = '+_tuple(pose[:3]),
            'quatf xformOp:orient = '+_quat(pose[3:]),
            'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient"]']


def _prim(kind, name, attributes, schemas=(), children=()):
    header = 'def '+kind+' '+json.dumps(name)
    if schemas:
        header += ' (\n    apiSchemas = '+json.dumps(list(schemas))+'\n)'
    body = '\n'.join(attributes)
    if children:
        body += '\n'+'\n'.join(children)
    return header+'\n{\n'+'\n'.join('    '+line for line in body.splitlines())+'\n}'


def _mesh(shape, item):
    """Native convex cooking for cylinders/cones; triangle geometry for meshes."""
    if shape in ('CYLINDER','CONE'):
        radius, height = item['radius'], item['height']
        axis = 'XYZ'.index(item['axis'])
        axes = [i for i in range(3) if i != axis]
        points = []
        count = 32
        for end in (-1,1):
            for i in range(count):
                p = [0.,0.,0.]
                p[axis] = end*height/2
                r = 0. if shape == 'CONE' and end == 1 else radius
                p[axes[0]] = r*math.cos(2*math.pi*i/count)
                p[axes[1]] = r*math.sin(2*math.pi*i/count)
                points.append(p)
        triangles = []
        for i in range(1,count-1):
            triangles.extend([[0,i+1,i],[count,count+i,count+i+1]])
        for i in range(count):
            j = (i+1)%count
            triangles.extend([[i,j,count+j],[i,count+j,count+i]])
        return points,triangles
    return item['points'],item['triangles']


def _collider(item, center=(0,0,0)):
    shape = item['shape']
    schemas = ['PhysicsCollisionAPI','PhysxCollisionAPI','MaterialBindingAPI']
    attributes = ['rel material:binding:physics = <'+item['material_path']+'>',
                  'float physxCollision:contactOffset = '+_fmt(item['contact_offset']),
                  'float physxCollision:restOffset = 0',
                  'double3 xformOp:translate = '+_tuple(center)]
    order = ['xformOp:translate']
    if shape == 'BOX':
        kind = 'Cube'
        attributes += ['double size = 1','float3 xformOp:scale = '+_tuple(item['size'])]
        order.append('xformOp:scale')
    elif shape == 'SPHERE':
        kind = 'Sphere'
        attributes += ['double radius = '+_fmt(item['radius'])]
    elif shape == 'CAPSULE':
        kind = 'Capsule'
        attributes += ['double radius = '+_fmt(item['radius']),
                       'double height = '+_fmt(item['height']),
                       'uniform token axis = '+json.dumps(item['axis'])]
    else:
        kind = 'Mesh'
        schemas.append('PhysicsMeshCollisionAPI')
        points,triangles = _mesh(shape,item)
        attributes += ['point3f[] points = ['+', '.join(_tuple(p) for p in points)+']',
                       'int[] faceVertexCounts = ['+', '.join('3' for _ in triangles)+']',
                       'int[] faceVertexIndices = ['+', '.join(str(i) for t in triangles for i in t)+']',
                       'uniform token subdivisionScheme = "none"',
                       'uniform token physics:approximation = '+json.dumps('none' if shape=='MESH' else 'convexHull')]
    attributes.append('uniform token[] xformOpOrder = '+json.dumps(order))
    return _prim(kind,'Collider',attributes,schemas)


def _material(item):
    return _prim('Material',item['material_path'].rsplit('/',1)[1],
                 ['float physics:dynamicFriction = '+_fmt(item['friction']),
                  'float physics:staticFriction = '+_fmt(item['friction']),
                  'float physics:restitution = '+_fmt(item['restitution']),
                  'uniform token physxMaterial:frictionCombineMode = "average"',
                  'uniform token physxMaterial:restitutionCombineMode = "average"'],
                 ['PhysicsMaterialAPI','PhysxMaterialAPI'])


def _body(item, anchor=False):
    schemas = []
    attributes = _transform(item['pose'])
    if item['mode'] != 'STATIC':
        schemas = ['PhysicsRigidBodyAPI','PhysicsMassAPI','PhysxRigidBodyAPI']
        attributes += ['bool physics:rigidBodyEnabled = 1',
                       'bool physics:kinematicEnabled = '+str(int(item['mode']=='KINEMATIC')),
                       'float physics:mass = '+_fmt(item['mass']),
                       'float physxRigidBody:linearDamping = '+_fmt(item['linear_damping']),
                       'float physxRigidBody:angularDamping = '+_fmt(item['angular_damping']),
                       'float physxRigidBody:sleepThreshold = 0',
                       'bool physxRigidBody:disableGravity = '+str(int(anchor or item.get('spring_link',False))),
                       'int physxRigidBody:solverPositionIterationCount = 8',
                       'int physxRigidBody:solverVelocityIterationCount = 4',
                       'int physxRigidBody:lockedPosAxis = '+str(item['lock_location_mask']),
                       'int physxRigidBody:lockedRotAxis = '+str(item['lock_rotation_mask'])]
        if anchor:
            # Kinematic roots have no collider, so mass/inertia cannot be inferred.
            attributes += ['float3 physics:diagonalInertia = (1, 1, 1)']
    if item.get('filtered_paths'):
        schemas.append('PhysicsFilteredPairsAPI')
        attributes.append('rel physics:filteredPairs = ['+', '.join('<'+p+'>' for p in item['filtered_paths'])+']')
    children = [] if anchor else [_collider(item,item.get('collider_center',(0,0,0)))]
    return _prim('Xform',item['path'].rsplit('/',1)[1],attributes,schemas,children)


def _joint(spring, link, parent, index):
    local = _relative(parent['pose'],link['pose'])
    schemas = ['PhysicsLimitAPI:trans'+a for a in 'XYZ'] + ['PhysicsDriveAPI:rot'+a for a in 'XYZ']
    attributes = ['rel physics:body0 = <'+parent['path']+'>',
                  'rel physics:body1 = <'+link['path']+'>',
                  'bool physics:collisionEnabled = 0',
                  'point3f physics:localPos0 = '+_tuple(local[:3]),
                  'point3f physics:localPos1 = (0, 0, 0)',
                  'quatf physics:localRot0 = '+_quat(local[3:]),
                  'quatf physics:localRot1 = (1, 0, 0, 0)']
    for axis in 'XYZ':
        attributes += ['float limit:trans'+axis+':physics:low = 1',
                       'float limit:trans'+axis+':physics:high = -1']
        # USD torque/degree drive gains convert to native torque/radian.
        attributes += ['uniform token drive:rot'+axis+':physics:type = "force"',
                       'float drive:rot'+axis+':physics:stiffness = '+_fmt(link['stiffness']*math.pi/180),
                       'float drive:rot'+axis+':physics:damping = '+_fmt(link['drive_damping']*math.pi/180),
                       'float drive:rot'+axis+':physics:targetPosition = 0',
                       'float drive:rot'+axis+':physics:targetVelocity = 0',
                       'float drive:rot'+axis+':physics:maxForce = 1000000']
    return _prim('PhysicsJoint',_identifier('Joint',spring['id']+':'+str(index)),attributes,schemas)


def _normalize_object(raw, index, kind='Object'):
    item = copy.deepcopy(raw)
    item['id'] = str(item.get('id',kind+str(index)))
    item['name'] = str(item.get('name',item['id']))
    item['mode'] = str(item.get('mode','DYNAMIC')).upper()
    item['shape'] = str(item.get('shape','BOX')).upper()
    if item['mode'] not in ('STATIC','DYNAMIC','KINEMATIC'):
        raise ValueError('Unsupported body mode '+item['mode'])
    if item['shape'] not in ('BOX','SPHERE','CAPSULE','CONVEX','MESH','CYLINDER','CONE'):
        raise ValueError('Unsupported collision shape '+item['shape'])
    if item['shape']=='MESH' and item['mode']=='DYNAMIC':
        raise ValueError('Dynamic concave MESH is unsupported by standalone PhysX; choose CONVEX')
    item['pose'] = _pose(item.get('pose',[0,0,0,0,0,0,1]),item['name']+' pose')
    anchor = item.get('anchor',{})
    if anchor.get('smpl_body') or item.get('anchor_body'):
        if item['mode'] != 'KINEMATIC':
            raise ValueError('A SMPL-bound collider must be kinematic')
        item['anchor_body'] = str(anchor.get('smpl_body',item.get('anchor_body')))
        item['anchor_local_pose'] = _pose(item.get('anchor_local_pose',anchor.get('local_pose',[0,0,0,0,0,0,1])),'collider anchor local pose')
    item['path'] = '/World/SceneInputs/'+_identifier(kind,item['id'])
    item['material_path'] = '/World/SceneInputs/'+_identifier('Material',kind+item['id'])
    item['mass'] = _number(item.get('mass',1),item['name']+' mass',1e-6,1e6)
    item['friction'] = _number(item.get('friction',.5),'friction',0,10)
    item['restitution'] = _number(item.get('restitution',0),'restitution',0,1)
    item['linear_damping'] = _number(item.get('linear_damping',.04),'linear damping',0,10000)
    item['angular_damping'] = _number(item.get('angular_damping',.1),'angular damping',0,10000)
    item['contact_offset'] = max(1e-5,_number(item.get('margin',.002) if item.get('use_margin',False) else .002,'margin',0,1))
    item['axis'] = str(item.get('axis','Z')).upper()
    if item['axis'] not in 'XYZ' or len(item['axis'])!=1:
        raise ValueError('Unsupported collision axis')
    item['size'] = _vector(item.get('size',[.1,.1,.1]),3,'size')
    if item['shape']=='BOX' and min(item['size']) <= 0:
        raise ValueError('Collision size must be positive')
    item['radius'] = _number(item.get('radius',.05),'radius',1e-5,10000)
    item['height'] = _number(item.get('height',.1),'height',0,10000)
    if item['shape'] in ('CYLINDER','CONE') and item['height']<=0:
        raise ValueError('Cylinder/cone collision height must be positive')
    item['collider_center'] = _vector(item.get('collider_center',[0,0,0]),3,'collider center')
    for field in ('lock_location','lock_rotation'):
        locks = item.get(field,[False]*3)
        if len(locks)!=3:
            raise ValueError(field+' needs three axis flags')
        item[field+'_mask'] = sum((1<<i) for i,v in enumerate(locks) if v)
    if item['shape'] in ('CONVEX','MESH'):
        item['points'] = [_vector(v,3,'mesh point') for v in item.get('points',item.get('vertices',[]))]
        minimum = 3 if item['shape']=='MESH' else 4
        if not minimum <= len(item['points']) <= 1000000:
            raise ValueError('Mesh needs '+str(minimum)+'..1000000 points')
        faces = item.get('triangles',item.get('faces',[]))
        item['triangles'] = []
        for face in faces:
            if len(face)!=3 or any(int(v)!=v or not 0 <= v < len(item['points']) for v in face):
                raise ValueError('Invalid mesh triangle')
            item['triangles'].append([int(v) for v in face])
        if not item['triangles']:
            raise ValueError('Collision mesh needs triangles')
        if item['shape']=='CONVEX':
            _require_convex_volume(item['points'])
    return item


def append_scene_inputs(source, config=None):
    """Append connected collector inputs to the verified SMPL USDA.

    Returns (source, normalized_spec). The latter is passed to NativeScene after
    the stage attaches and warms up. Absent collectors create no extra bodies.
    """
    config = config or {}
    if config and config.get('schema',SCHEMA) != SCHEMA:
        raise ValueError('Unsupported scene input schema')
    spec = {'schema':SCHEMA,'objects':[],'colliders':[],'springs':[],
            'warnings':list(config.get('warnings',[]))}
    seen = set()
    for field,kind in (('objects','Object'),('colliders','Collider')):
        for i,raw in enumerate(config.get(field,[])):
            item = _normalize_object(raw,i,kind)
            if item['id'] in seen:
                raise ValueError('Duplicate scene body ID '+item['id'])
            seen.add(item['id'])
            spec[field].append(item)
    for i,raw in enumerate(config.get('springs',[])):
        spring = copy.deepcopy(raw)
        spring['id'] = str(spring.get('id','Spring'+str(i)))
        spring['name'] = str(spring.get('name',spring['id']))
        spring['anchor_pose'] = _pose(spring.get('anchor_pose',[0,0,0,0,0,0,1]),'spring anchor')
        anchor_binding = spring.get('anchor',{})
        if anchor_binding.get('smpl_body'):
            spring['anchor_body'] = anchor_binding['smpl_body']
        spring['anchor_local_pose'] = _pose(spring.get('anchor_local_pose',anchor_binding.get('local_pose',[0,0,0,0,0,0,1])),'spring anchor local pose')
        anchor = _normalize_object({'id':spring['id'],'pose':spring['anchor_pose'],'mode':'KINEMATIC'},i,'Anchor')
        spring['anchor'] = anchor
        links = []
        for j,raw_link in enumerate(raw.get('links',[])):
            link = _normalize_object(dict(raw_link, mode='DYNAMIC',shape='CAPSULE',axis='Y',
                                          id=str(raw_link.get('id',spring['id']+':'+str(j)))),j,'Link')
            link['bone'] = str(raw_link.get('bone',link['id']))
            link['length'] = _number(raw_link.get('length',.1),'spring length',1e-5,100)
            link['height'] = max(0,link['length']-2*link['radius'])
            link['collider_center'] = [0,link['length']/2,0]
            link['spring_link'] = True
            link['stiffness'] = _number(raw_link.get('stiffness',1),'spring stiffness',0,10000)
            link['drag'] = _number(raw_link.get('drag',.4),'spring drag',0,1)
            # VRM drag is a per-frame velocity retention. Convert it to native
            # damping and critical-drive damping; the native solver owns motion.
            link['linear_damping'] = min(1000,-60*math.log(max(1e-6,1-link['drag'])))
            link['angular_damping'] = link['linear_damping']
            inertia = link['mass']*link['length']**2/3
            link['drive_damping'] = 2*link['drag']*math.sqrt(link['stiffness']*inertia)
            link['gravity'] = _vector(raw_link.get('gravity',[0,0,-9.81]),3,'spring gravity')
            if link['id'] in seen:
                raise ValueError('Duplicate spring link ID '+link['id'])
            seen.add(link['id'])
            links.append(link)
        if not links:
            continue
        for link in links:
            # Adjacent and nonadjacent links of the same authored chain do not
            # collide with themselves; external bodies and SMPL remain enabled.
            link['filtered_paths'] = [v['path'] for v in links if v is not link]
        spring['links'] = links
        spec['springs'].append(spring)
    if not any(spec[field] for field in ('objects','colliders','springs')):
        return source,spec
    children = []
    for item in spec['objects']+spec['colliders']:
        children += [_material(item),_body(item)]
    for spring in spec['springs']:
        children.append(_body(spring['anchor'],anchor=True))
        parent = spring['anchor']
        link_by_id = {link['id']:link for link in spring['links']}
        for i,link in enumerate(spring['links']):
            if 'parent_id' in link:
                parent_id = link['parent_id']
                if parent_id is None:
                    parent = spring['anchor']
                elif str(parent_id) in link_by_id and str(parent_id)!=link['id']:
                    parent = link_by_id[str(parent_id)]
                else:
                    raise ValueError('Invalid spring parent link '+str(parent_id))
            children += [_material(link),_body(link),_joint(spring,link,parent,i)]
            parent = link
    end = source.rfind('}')
    if end < 0 or source[end+1:].strip():
        raise ValueError('SMPL USD scene has an unexpected root structure')
    addition = _prim('Xform','SceneInputs',[],children=children)
    return source[:end]+'\n'+'\n'.join('    '+line for line in addition.splitlines())+'\n'+source[end:],spec


build_scene_source = append_scene_inputs


class _TransformWriter:
    def __init__(self, stage, path):
        import ovstage
        self.stage,self.ovstage = stage,ovstage
        self.paths = ovstage.PathDictionary(stage)
        self.path_list = self.paths.create_path_list_from_strings([path])
        self.query = stage.query_from_path_list(self.path_list)

    def write(self, pose, ordinal):
        import numpy as np
        x,y,z,w = pose[3:]
        # ovstage matrices follow USD's row-vector convention.
        matrix = np.eye(4,dtype=np.float64)
        matrix[:3,:3] = [[1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w)],
                         [2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w)],
                         [2*(x*z+y*w),2*(y*z-x*w),1-2*(x*x+y*y)]]
        matrix[3,:3] = pose[:3]
        tensor = self.ovstage.make_dltensor(matrix,
            dtype=self.ovstage.DLDataType(self.ovstage.DLDataTypeCode.kDLFloat,64,16),shape=[1],ndim=1)
        for attribute in ('omni:xform','omni:fabric:worldMatrix'):
            self.stage.write_attribute(self.query,attribute,ordinal,tensor,is_array=False).wait()

    def close(self):
        self.stage.release_query(self.query).wait()
        self.paths.destroy_path_list(self.path_list)
        self.paths.destroy()


class NativeScene:
    """Native body bindings, custom gravity and kinematic anchor publication."""
    def __init__(self, physx, stage, spec):
        import numpy as np
        from ovphysx.types import TensorType
        self.np,self.physx,self.stage,self.spec = np,physx,stage,spec
        self.bodies,self.writers,self.targets = {},{},{}
        self.ordinal = 1
        self.binding_count = 0
        self.closed = False
        try:
            items = list(spec['objects'])+list(spec['colliders'])
            for spring in spec['springs']:
                items.extend([spring['anchor'],*spring['links']])
            for item in items:
                if item['mode']=='STATIC':
                    continue
                binding_types = {'pose':TensorType.RIGID_BODY_POSE,'velocity':TensorType.RIGID_BODY_VELOCITY}
                if item.get('spring_link'):
                    binding_types['force'] = TensorType.RIGID_BODY_WRENCH
                record = {'item':item,'bindings':{},'buffers':{}}
                self.bodies[item['path']] = record
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',DeprecationWarning)
                    for name,tensor in binding_types.items():
                        binding = physx.create_tensor_binding(pattern=item['path'],tensor_type=tensor,raise_if_empty=True)
                        if binding.count!=1:
                            binding.destroy()
                            raise ValueError('Expected one native scene body at '+item['path'])
                        record['bindings'][name] = binding
                        record['buffers'][name] = np.zeros(binding.shape,dtype=np.float32)
                        self.binding_count += 1
                if item['mode']=='KINEMATIC':
                    self.writers[item['path']] = _TransformWriter(stage,item['path'])
                    self.targets[item['path']] = item['pose'][:]
        except Exception:
            self.close()
            raise

    def _read(self, item, name):
        record = self.bodies[item['path']]
        array = record['buffers'][name]
        record['bindings'][name].read(array)
        if not self.np.isfinite(array).all():
            raise ValueError('Non-finite native scene '+name)
        return array[0].tolist()

    def _write(self, item, name, value):
        record = self.bodies[item['path']]
        array = record['buffers'][name]
        array[:] = value
        if not self.np.isfinite(array).all():
            raise ValueError('Invalid native scene '+name)
        record['bindings'][name].write(array)

    def reset(self):
        for record in self.bodies.values():
            item = record['item']
            self._write(item,'pose',item['pose'])
            if item['mode']!='KINEMATIC':
                self._write(item,'velocity',[0]*6)
            if 'force' in record['bindings']:
                self._write(item,'force',[0]*9)
            if item['mode']=='KINEMATIC':
                self.targets[item['path']] = item['pose'][:]

    def update(self, scene_update):
        """Only source-following kinematic objects/anchors accept live updates."""
        by_id = {v['id']:v for v in self.spec['objects']+self.spec['colliders']}
        anchors = {v['id']:v for v in self.spec['springs']}
        for raw in scene_update.get('objects',[])+scene_update.get('colliders',[]):
            item = by_id.get(str(raw.get('id','')))
            if item and item['mode']=='KINEMATIC' and not item.get('anchor_body'):
                self.targets[item['path']] = _pose(raw['pose'])
        for raw in scene_update.get('anchors',[])+scene_update.get('springs',[]):
            spring = anchors.get(str(raw.get('id','')))
            if spring and not spring.get('anchor_body'):
                self.targets[spring['anchor']['path']] = _pose(raw.get('pose',raw.get('anchor_pose')))

    def before_step(self, dt, smplstate=None):
        if smplstate is not None:
            names = smplstate.get('names',smplstate.get('body_names',[]))
            positions = smplstate.get('positions',smplstate.get('body_positions',[]))
            rotations = smplstate.get('rotations',smplstate.get('body_rotations',[]))
            for spring in self.spec['springs']:
                name = spring.get('anchor_body')
                if name and name in names:
                    i = names.index(name)
                    q = rotations[i]
                    parent = list(positions[i])+[q[1],q[2],q[3],q[0]]
                    self.targets[spring['anchor']['path']] = _compose(parent,spring['anchor_local_pose'])
            for item in self.spec['objects']+self.spec['colliders']:
                name = item.get('anchor_body')
                if name and name in names:
                    i = names.index(name)
                    q = rotations[i]
                    parent = list(positions[i])+[q[1],q[2],q[3],q[0]]
                    self.targets[item['path']] = _compose(parent,item['anchor_local_pose'])
        if self.writers:
            self.ordinal += 1
            for path,writer in self.writers.items():
                writer.write(self.targets[path],self.ordinal)
            self.stage.advance_write_floor(ordinal=self.ordinal).wait()
            self.physx.update_from_ovstage(self.ordinal,self.ordinal)
        for spring in self.spec['springs']:
            for link in spring['links']:
                # Per-link external acceleration becomes a native force. PhysX
                # clears these forces every step; no Python state integration.
                pose = self._read(link,'pose')
                center = _compose(pose,[*link['collider_center'],0,0,0,1])[:3]
                self._write(link,'force',[link['mass']*g for g in link['gravity']]+[0]*3+center)

    def snapshot(self):
        objects = []
        for item in self.spec['objects']+self.spec['colliders']:
            pose = item['pose'][:] if item['mode']=='STATIC' else self._read(item,'pose')
            objects.append({'id':item['id'],'name':item['name'],'mode':item['mode'],
                            'pose':pose,'spring_collider':bool(item.get('spring_collider',False))})
        springs = []
        for spring in self.spec['springs']:
            links = []
            for link in spring['links']:
                pose = self._read(link,'pose')
                delta = _rotate(pose[3:],[0,link['length'],0])
                links.append({'id':link['id'],'bone':link['bone'],'pose':pose,
                              'head':pose[:3],'tail':[pose[i]+delta[i] for i in range(3)]})
            springs.append({'id':spring['id'],'name':spring['name'],
                            'anchor_pose':self._read(spring['anchor'],'pose'),'links':links})
        return {'objects':objects,'springs':springs,
                'native_body_count':len(self.bodies),
                'rigid_body_count':sum(not v.get('spring_collider',False) for v in self.spec['objects']),
                'spring_collider_count':sum(bool(v.get('spring_collider',False)) for v in self.spec['objects']+self.spec['colliders']),
                'spring_link_count':sum(len(v['links']) for v in self.spec['springs']),
                'spring_solver':'Native PhysX D6 drive',
                'warnings':self.spec.get('warnings',[])}

    def close(self):
        if self.closed:
            return
        self.closed = True
        for writer in self.writers.values():
            writer.close()
        for record in self.bodies.values():
            for binding in record['bindings'].values():
                binding.destroy()
        self.writers.clear()
        self.bodies.clear()
