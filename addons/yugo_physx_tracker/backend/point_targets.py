"""Position-only springs applied through native PhysX force-at-point writes."""
from yugo_physx_runtime.point_contract import POINT_DEFAULTS,POINTS,normalize_point_targets


class PointTargets:
    def __init__(self,backend):
        self.backend=backend
        np=backend.np
        self.settings=dict(POINT_DEFAULTS)
        self.targets=[]
        self.last=[]
        self.indices={name:index for index,name in enumerate(backend.smpl.names)}
        self.mass=backend.read('ARTICULATION_BODY_MASS')[0,backend.body_order].astype(np.float64)
        self.local_com=backend.read('ARTICULATION_BODY_COM_POSE')[0,backend.body_order,:3].astype(np.float64)
        # Verified native inertia is expressed in body axes about COM; do not
        # rotate by the COM-local quaternion a second time.
        inertia=backend.read('ARTICULATION_BODY_INERTIA')[0,backend.body_order].reshape(24,3,3).astype(np.float64)
        if not np.isfinite(self.mass).all() or np.min(self.mass)<=0:
            raise ValueError('Position attraction requires positive native link masses')
        if not np.isfinite(inertia).all() or np.min(np.linalg.eigvalsh(inertia))<=0:
            raise ValueError('Position attraction requires positive native link inertia')
        self.point_mass={}
        for identifier,point in POINTS.items():
            i=self.indices[point['body']]
            x,y,z=np.asarray(point['local_position'])-self.local_com[i]
            cross=np.array([[0.,-z,y],[z,0.,-x],[-y,x,0.]])
            inverse_mass=np.eye(3)/self.mass[i]+cross.T@np.linalg.inv(inertia[i])@cross
            self.point_mass[identifier]=np.linalg.inv(inverse_mass)

    def set(self,message):
        """Atomically replace goals/settings; do not advance or reset physics."""
        normalized=normalize_point_targets(message,self.settings)
        self.settings={name:normalized[name] for name in POINT_DEFAULTS}
        self.targets=normalized['targets']
        self.last=[]

    @property
    def enabled(self):
        return bool(self.settings['strength'] and any(point['strength'] for point in self.targets))

    def reset_diagnostics(self):
        self.last=[]

    def _point(self,record,state):
        np,tm=self.backend.np,self.backend.tm
        p,q,v,w=state
        i=self.indices[record['body']]
        com_offset=tm.rotate(q[i],self.local_com[i])
        point_offset=tm.rotate(q[i],np.asarray(record['local_position']))
        point=p[i]+point_offset
        lever=point_offset-com_offset
        point_velocity=v[i]+np.cross(w[i],lever)
        return i,point,lever,point_velocity

    def _goal(self,record,reference=None):
        if 'reference_offset' not in record:
            return self.backend.np.asarray(record['position']),self.backend.np.asarray(record['velocity'])
        np,tm=self.backend.np,self.backend.tm
        tp,tq,tv,tw=self.backend.target(self.backend.seconds) if reference is None else reference
        i=self.indices[record['body']]
        offset=tm.rotate(tq[i],np.asarray(record['local_position'])+record['reference_offset'])
        # Reference derivatives come from body-origin FK positions. Native
        # velocities below are COM velocities; their point conversion differs.
        return tp[i]+offset,tv[i]+np.cross(tw[i],offset)

    def before_step(self,dt,seconds=None,wall=None):
        if not self.enabled:
            self.last=[]
            return
        backend=self.backend;np=backend.np
        state=backend.state()
        reference=backend.target(backend.seconds if seconds is None else seconds,wall) if any(
            'reference_offset' in point for point in self.targets) else None
        omega=2*np.pi*min(self.settings['frequency_hz'],.12/dt)
        last=[]
        for record in self.targets:
            i,point,lever,velocity=self._point(record,state)
            target_position,target_velocity=self._goal(record,reference)
            error=target_position-point
            strength=self.settings['strength']*record['strength']
            rotation=backend.tm.rotate(state[1][i],np.eye(3)).T
            mass=rotation@self.point_mass[record['id']]@rotation.T
            damping=2*self.settings['damping_ratio']*omega
            # The implicit scalar denominator keeps strong damping/strength
            # stable at the native timestep. Articulation coupling and contacts
            # are still solved by PhysX; this is only a requested external force.
            denominator=1+strength*(damping*dt+omega**2*dt**2)
            force=mass@(strength*(omega**2*error+damping*(target_velocity-velocity))/denominator)
            magnitude=float(np.linalg.norm(force));limit=self.settings['max_force_n']
            if magnitude>limit:force*=limit/magnitude
            # PhysX accumulates these writes until it advances. Each point has
            # zero directly commanded torque; force at this world point creates
            # its physical moment about COM. Root support remains additive.
            if strength:
                wrench=np.zeros((1,24,9),dtype=np.float32)
                wrench[0,int(backend.body_order[i]),:3]=force
                wrench[0,int(backend.body_order[i]),6:9]=point
                backend.write('ARTICULATION_LINK_WRENCH',wrench)
            last.append({'id':record['id'],'body':record['body'],'application_point':point.tolist(),
                'point_velocity':velocity.tolist(),'error_m':float(np.linalg.norm(error)),
                'force_n':force.tolist(),'induced_torque_nm':np.cross(lever,force).tolist(),
                'force_target_position':target_position.tolist()})
        self.last=last

    def snapshot(self,reference=None):
        records={value['id']:value for value in self.last}
        state=self.backend.state() if self.targets else None
        points=[]
        for target in self.targets:
            _,point,_,velocity=self._point(target,state)
            desired,desired_velocity=self._goal(target,reference)
            last=records.get(target['id'],{})
            points.append({'id':target['id'],'body':target['body'],'local_position':list(target['local_position']),
                'mode':'REFERENCE_OFFSET' if 'reference_offset' in target else 'WORLD',
                'reference_offset':list(target['reference_offset']) if 'reference_offset' in target else None,
                'target_position':desired.tolist(),'target_velocity':desired_velocity.tolist(),'application_point':point.tolist(),
                'point_velocity':velocity.tolist(),'error_m':float(self.backend.np.linalg.norm(desired-point)),
                'force_n':last.get('force_n',[0.,0.,0.]),'induced_torque_nm':last.get('induced_torque_nm',[0.,0.,0.]),
                'direct_torque_nm':[0.,0.,0.],'strength':target['strength']})
        return {'enabled':self.enabled,'count':len(points),'settings':dict(self.settings),
                'solver':'Native PhysX force at point','mass_model':'Native link point effective mass',
                'integration':'Implicit spring force coefficients','points':points}
