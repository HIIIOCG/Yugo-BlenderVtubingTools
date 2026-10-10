"""Shared position-attraction attachments, in SMPL body-local physical metres.

Centres are copied from assets/smpl_humanoid.xml: box `pos`, or the midpoint
of the Spine capsule's `fromto`. Hips, elbows and knees use joint origins.
The asset and trained policy are unchanged.
"""
import math

SOURCE_XML_SHA256 = '58dc997c830b3ded2d2b4528797ed2768f9528b2f25aa008e541fe3a9083e7cc'
POINTS = {
    'hips': {'label':'Hips','body':'Pelvis','local_position':(0.,0.,0.)},
    'chest': {'label':'Chest','body':'Spine','local_position':(.0127,.00075,.02645)},
    'head': {'label':'Head','body':'Head','local_position':(-.0116,-.0042,.0876)},
    'leftHand': {'label':'Left Palm','body':'L_Hand','local_position':(-.0058,.0493,.001)},
    'rightHand': {'label':'Right Palm','body':'R_Hand','local_position':(-.0079,-.0462,-.0009)},
    'leftLowerArm': {'label':'Left Elbow','body':'L_Elbow','local_position':(0.,0.,0.)},
    'rightLowerArm': {'label':'Right Elbow','body':'R_Elbow','local_position':(0.,0.,0.)},
    'leftFoot': {'label':'Left Foot','body':'L_Ankle','local_position':(.0242,.0233,-.0239)},
    'rightFoot': {'label':'Right Foot','body':'R_Ankle','local_position':(.0256,-.0212,-.0174)},
    'leftLowerLeg': {'label':'Left Knee','body':'L_Knee','local_position':(0.,0.,0.)},
    'rightLowerLeg': {'label':'Right Knee','body':'R_Knee','local_position':(0.,0.,0.)},
}
POINT_IDS = tuple(POINTS)
MAX_POINT_TARGETS = len(POINTS)
POINT_DEFAULTS = {'strength':1.,'frequency_hz':3.,'damping_ratio':1.,'max_force_n':250.}
POINT_LIMITS = {'strength':(0.,2.),'frequency_hz':(.1,12.),'damping_ratio':(0.,4.),'max_force_n':(.001,5000.)}


def _number(value,name,lower,upper):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError(name+' must be a finite number')
    value=float(value)
    if not math.isfinite(value) or not lower<=value<=upper:
        raise ValueError(name+' must be in '+str(lower)+'..'+str(upper))
    return value


def _vector(value,name):
    if not isinstance(value,(list,tuple)) or len(value)!=3:
        raise ValueError(name+' must contain three finite numbers')
    return tuple(_number(item,name,-1e8,1e8) for item in value)


def normalize_point_targets(message,settings=None):
    """Pure protocol/backend validation; return settings and copied goal data."""
    if not isinstance(message,dict):
        raise ValueError('Point targets require an object')
    defaults=POINT_DEFAULTS if settings is None else settings
    result={name:_number(message.get(name,defaults[name]),'Point '+name,*limits)
            for name,limits in POINT_LIMITS.items()}
    records=message.get('targets',[])
    if not isinstance(records,list) or len(records)>MAX_POINT_TARGETS:
        raise ValueError('Point targets require at most eleven goals')
    targets=[];seen=set()
    for raw in records:
        if not isinstance(raw,dict):
            raise ValueError('Point goal must be an object')
        identifier=raw.get('id')
        if not isinstance(identifier,str) or identifier not in POINTS or identifier in seen:
            raise ValueError('Point goal has an unknown or duplicate humanoid id')
        seen.add(identifier);point=POINTS[identifier]
        if raw.get('body',point['body'])!=point['body']:
            raise ValueError('Point body does not match its humanoid attachment')
        local=_vector(raw.get('local_position',point['local_position']),'Point local position')
        if any(abs(a-b)>1e-7 for a,b in zip(local,point['local_position'])):
            raise ValueError('Point local position does not match its humanoid attachment')
        if any(name in raw for name in ('rotation','orientation','torque','angular_velocity')):
            raise ValueError('Point goals accept position and velocity only')
        target={'id':identifier,'body':point['body'],'local_position':local,
                'position':_vector(raw.get('position'),'Point target position'),
                'velocity':_vector(raw.get('velocity',(0.,0.,0.)),'Point target velocity'),
                'strength':_number(raw.get('strength',1.),'Point goal strength',0,2)}
        if 'reference_offset' in raw:
            target['reference_offset']=_vector(raw['reference_offset'],'Point reference offset')
        targets.append(target)
    result['targets']=targets
    return result
