"""Bounded JSON pipes for the private PhysX worker; poses use WXYZ."""
import json
import math

VERSION = 1
PREFIX = 'YUGO_PHYSX_V1 '
LIMIT = 256 * 1024
COMMANDS = {'reference', 'scene_update', 'scene_reconcile', 'root_support', 'point_targets', 'play', 'pause', 'reset', 'step', 'shutdown'}


def encode(message):
    if not isinstance(message, dict):
        raise ValueError('PhysX messages must be objects')
    if 'version' in message and (type(message['version']) is not int or message['version'] != VERSION):
        raise ValueError('PhysX protocol version mismatch')
    raw = json.dumps({**message, 'version': VERSION}, allow_nan=False, separators=(',', ':'))
    if len(raw.encode('utf-8'))+1 > LIMIT:
        raise ValueError('PhysX message too large')
    return raw + '\n'


def decode(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > LIMIT:
        raise ValueError('PhysX message too large')
    def invalid(_value):
        raise ValueError('Non-finite JSON')
    message = json.loads(raw, parse_constant=invalid)
    if not isinstance(message, dict) or type(message.get('version')) is not int or message['version'] != VERSION:
        raise ValueError('PhysX protocol version mismatch')
    return message


def array(value, rows, columns):
    if not isinstance(value, list) or len(value) != rows:
        raise ValueError('Invalid PhysX pose dimensions')
    for row in value:
        if not isinstance(row, list) or len(row) != columns:
            raise ValueError('Invalid PhysX pose dimensions')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in row):
            raise ValueError('Invalid PhysX pose values')
    return value


def pose(message):
    array(message.get('positions'), 24, 3)
    rotations = array(message.get('rotations'), 24, 4)
    if any(not .999 < sum(v*v for v in q) < 1.001 for q in rotations):
        raise ValueError('Invalid PhysX quaternion')
    for name in ('velocities', 'angular_velocities'):
        if name in message:
            array(message[name], 24, 3)
    return message


def command(message):
    if not isinstance(message, dict) or message.get('type') not in COMMANDS:
        raise ValueError('Unknown PhysX command')
    if message['type'] == 'reference':
        pose(message)
    elif message['type'] == 'point_targets':
        from .point_contract import normalize_point_targets
        normalize_point_targets(message)
    elif message['type'] == 'scene_reconcile':
        path,revision = message.get('path'),message.get('revision')
        if (not isinstance(path,str) or not 0 < len(path) <= 2048
                or any(char in path for char in ('\0','\n','\r'))):
            raise ValueError('Invalid PhysX scene configuration path')
        if (not isinstance(revision,str) or not 0 < len(revision) <= 128
                or not all(char.isalnum() or char in '-_' for char in revision)):
            raise ValueError('Invalid PhysX scene revision')
    elif message['type'] == 'root_support':
        strength = message.get('strength')
        if (isinstance(strength,bool) or not isinstance(strength,(int,float))
                or not math.isfinite(strength) or not 0 <= strength <= 2):
            raise ValueError('Root Support must be between 0 and 2')
    elif message['type'] == 'scene_update':
        for key in ('objects','anchors'):
            records = message.get(key,[])
            if not isinstance(records,list) or len(records) > 512:
                raise ValueError('Invalid PhysX scene update count')
            for record in records:
                if not isinstance(record,dict) or not isinstance(record.get('id'),str) or not 0 < len(record['id']) <= 256:
                    raise ValueError('Invalid PhysX scene object identity')
                values = array([record.get('pose')],1,7)[0]
                if not .999 < sum(value*value for value in values[3:]) < 1.001:
                    raise ValueError('Invalid PhysX scene quaternion')
    return message


def scene_budget(spec):
    """Reject configurations that cannot travel through the bounded live pipe."""
    objects = list(spec.get('objects',[])) + list(spec.get('colliders',[]))
    springs = spec.get('springs',[])
    count = len(objects) + len(springs) + sum(len(s.get('links',[])) for s in springs)
    if count > 512:
        raise ValueError('PhysX scene inputs support up to 512 additional bodies, including spring anchors')
    # Allow wide finite number representations, escaped Unicode names, native
    # counters/settings, both SMPL poses and eleven point diagnostics. Check
    # before creating the worker or committing an incremental scene edit.
    number = -1.2345678901234567e-100
    extra = {'objects':[{'id':o['id'],'name':o.get('name',o['id']),
                        'mode':o.get('mode','DYNAMIC'),'pose':[number]*7,
                        'spring_collider':bool(o.get('spring_collider',False))} for o in objects],
             'springs':[{'id':s['id'],'name':s.get('name',s['id']),'anchor_pose':[number]*7,
                         'links':[{'id':link['id'],'bone':link.get('bone',link['id']),
                                   'pose':[number]*7,'head':[number]*3,'tail':[number]*3}
                                  for link in s.get('links',[])]} for s in springs],
             'warnings':spec.get('warnings',[])}
    try:
        encode({'extra_scene':extra,'reserved':' '*40000})
    except ValueError as error:
        raise ValueError('PhysX scene output exceeds the live data limit; use fewer collision objects or spring bones') from error
