"""Standalone native PhysX + exported SMPL actor; no Isaac, torch or bpy.

The actor sees body-origin poses and world velocities with WXYZ quaternions.
PhysX tensor boundaries are normalized here, never in Blender's drawing code.
"""
from pathlib import Path
import hashlib
import json
import math
import re
import tempfile
import time
import warnings


class Backend:
    def __init__(self, assets, model_path='', clip_path='', mode='CLIP', status=lambda _:None,
                 physics_hz=120, solver='TGS', iterations=8,scene_inputs_path=''):
        import numpy as np
        import onnxruntime as ort
        import ovphysx
        import ovstage
        from ovphysx import PhysX, PhysXConfig
        from ovphysx.types import TensorType
        from yugo_physx_runtime import tracker_math as tm
        from yugo_physx_runtime.clip import MotionClip
        from yugo_physx_runtime.model import Skeleton
        from yugo_physx_runtime.motion_buffer import MotionBuffer

        self.np, self.tm = np, tm
        self.assets = Path(assets).resolve()
        self.smpl = Skeleton()
        self.physics_hz = int(physics_hz)
        if self.physics_hz < 120 or self.physics_hz > 1200 or self.physics_hz % 30:
            raise ValueError('Physics frequency must be a multiple of 30 in 120..1200 Hz')
        if solver not in ('TGS', 'PGS') or not 1 <= int(iterations) <= 64:
            raise ValueError('Unsupported PhysX solver settings')
        self.dt = 1 / 30
        self.substeps = self.physics_hz // 30
        self.backend_name = 'Standalone PhysX 5.11 / CPU (OVPhysX 0.6.3)'
        self.bindings, self.stage, self.physx = {}, None, None
        self.scene_inputs = None
        self.point_targets = None
        self._root_support_strength = 0.
        self.last_root_force = np.zeros(3)
        self.last_root_torque = np.zeros(3)
        self.temp = tempfile.TemporaryDirectory(prefix='yugo_physx_')
        try:
            path = Path(model_path).resolve() if model_path else self.assets/'smpl_amass/tracker.onnx'
            self.contract = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            if (self.contract.get('schema') != 'yugo-smpl-amass-v1'
                or hashlib.sha256(path.read_bytes()).hexdigest() != self.contract.get('onnx_sha256')
                or self.contract.get('body_names') != self.smpl.names
                or self.contract.get('control_dt') is None
                or abs(self.contract['control_dt'] - self.dt) > 1e-7):
                raise ValueError('Model contract does not match the bundled SMPL skeleton')
            self.metadata = json.loads((self.assets/'physics.json').read_text(encoding='utf-8'))
            if (self.metadata.get('schema') != 'yugo-smpl-physx-v1'
                or self.metadata['body_names'] != self.smpl.names
                or self.metadata['dof_names'] != self.contract['dof_names']
                or hashlib.sha256((self.assets/'smpl_humanoid.xml').read_bytes()).hexdigest() != self.metadata['source_xml_sha256']):
                raise ValueError('Physics asset has incompatible body ordering')
            options = ort.SessionOptions()
            options.intra_op_num_threads = options.inter_op_num_threads = 1
            self.policy = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
            if self.policy.get_inputs()[0].shape != [1,1003] or self.policy.get_outputs()[0].shape != [1,69]:
                raise ValueError('SMPL actor must have 1003 inputs and 69 actions')
            self.offset = np.asarray(self.contract['pd_action_offset'])
            self.scale = np.asarray(self.contract['pd_action_scale'])
            self.mode = mode
            self.clip = MotionClip(Path(clip_path) if clip_path else self.assets/'smpl_amass/walk.npz', self.smpl.names) if mode == 'CLIP' else None
            self.frames = MotionBuffer(delay=.1)
            p,q = self.smpl.fk()
            self.rest = p,q,np.zeros_like(p),np.zeros_like(p)
            self.reference_started = False
            status('Starting standalone native PhysX')
            PhysX.set_cpu_mode(True)
            ovstage.population.register_usd_schemas([str(ovphysx.codeless_schema_root())])
            source_bytes = (self.assets/'smpl_physx.usda').read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != self.metadata['usda_sha256']:
                raise ValueError('Physics scene checksum mismatch; reinstall the addon')
            source = source_bytes.decode('utf-8')
            source = re.sub(r'(physxScene:solverType\s*=\s*)"[^"]+"', lambda m:m[1]+json.dumps(solver), source)
            source = re.sub(r'(physx(?:Articulation|RigidBody):solverPositionIterationCount\s*=\s*)\d+', lambda m:m[1]+str(iterations), source)
            # Scene bounds clamp every articulation; honor the UI selection here
            # as well as on the SMPL articulation itself.
            source = re.sub(r'(physxScene:(?:min|max)PositionIterationCount\s*=\s*)\d+', lambda m:m[1]+str(iterations), source)
            source = re.sub(r'(physxScene:timeStepsPerSecond\s*=\s*)\d+', lambda m:m[1]+str(self.physics_hz), source)
            scene_spec,scene_config = None,{}
            if scene_inputs_path:
                from incremental_scene import normalize
                config_path = Path(scene_inputs_path)
                if config_path.stat().st_size > 16*1024*1024:
                    raise ValueError('PhysX scene inputs exceed 16 MiB')
                scene_config = json.loads(config_path.read_text(encoding='utf-8'))
                scene_spec = normalize(scene_config)
                from yugo_physx_runtime.protocol import scene_budget
                scene_budget(scene_spec)
            stage_file = Path(self.temp.name)/'scene.usda'
            stage_file.write_text(source, encoding='utf-8')
            self.physx = PhysX(config=PhysXConfig(num_threads=1, ovstage_read_pool_max_mb=16))
            self.stage = ovstage.Stage('Yugo-PhysX-SMPL')
            ovstage.population.open_usd(self.stage,str(stage_file),ordinal=1,domains=ovstage.PopulationDomain.PHYSICS)
            self.stage.advance_write_floor(ordinal=1).wait()
            self.physx.attach_ovstage(self.stage,read_ordinal=1)
            self.physx.wait_all()
            self.physx.warmup()
            pattern = self.metadata.get('articulation_pattern','/World/SMPL/Pelvis')
            types = ('ARTICULATION_LINK_POSE','ARTICULATION_LINK_VELOCITY','ARTICULATION_ROOT_POSE',
                     'ARTICULATION_ROOT_VELOCITY','ARTICULATION_DOF_POSITION','ARTICULATION_DOF_VELOCITY',
                     'ARTICULATION_DOF_POSITION_TARGET','ARTICULATION_DOF_VELOCITY_TARGET',
                     'ARTICULATION_DOF_STIFFNESS','ARTICULATION_DOF_DAMPING','ARTICULATION_DOF_MAX_FORCE',
                     'ARTICULATION_DOF_ARMATURE','ARTICULATION_BODY_COM_POSE',
                     'ARTICULATION_BODY_MASS','ARTICULATION_BODY_INERTIA','ARTICULATION_LINK_WRENCH')
            # 0.6.3 keeps the binding API supported. Its metadata is essential for
            # validating DOF identities rather than guessing native traversal order.
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                for name in types:
                    self.bindings[name] = self.physx.create_tensor_binding(pattern=pattern,tensor_type=getattr(TensorType,name),raise_if_empty=True)
            binding = self.bindings['ARTICULATION_DOF_POSITION']
            self.native_body_names = binding.body_names
            self.native_dof_names = binding.dof_names
            if binding.shape != (1,69) or len(self.native_body_names) != 24 or binding.is_fixed_base:
                raise ValueError('PhysX must expose one floating-base 24-body / 69-DOF articulation')
            self.body_order = np.array([self.native_body_names.index(name) for name in self.smpl.names])
            semantic = [self.semantic_dof(name) for name in self.native_dof_names]
            if len(set(semantic)) != 69 or set(semantic) != set(self.contract['dof_names']):
                raise ValueError('PhysX DOF identities do not match the SMPL actor: '+repr(self.native_dof_names))
            self.dof_order = np.array([semantic.index(name) for name in self.contract['dof_names']])
            self.buffers = {name:np.zeros(b.shape,dtype=np.float32) for name,b in self.bindings.items()}
            self.root_index = int(self.body_order[0])
            self.supported_mass = float(self.read('ARTICULATION_BODY_MASS').sum())
            self.root_local_com = self.read('ARTICULATION_BODY_COM_POSE')[0,self.root_index,:3].astype(np.float64)
            self.root_local_inertia = self.read('ARTICULATION_BODY_INERTIA')[0,self.root_index].reshape(3,3).astype(np.float64)
            if self.supported_mass <= 0 or np.min(np.linalg.eigvalsh(self.root_local_inertia)) <= 0:
                raise ValueError('Root support requires positive native mass and pelvis inertia')
            if self.bindings['ARTICULATION_LINK_WRENCH'].shape != (1,24,9):
                raise ValueError('Root support requires a native per-link world wrench binding')
            from point_targets import PointTargets
            self.point_targets = PointTargets(self)
            # Match MimicKit's explicit spring/damper root support semantics.
            # These forces supplement the learned joint controller; strength 0
            # leaves the freely simulated pelvis entirely unsupported.
            self.root_support_settings = {'root_linear_frequency_hz':6.,'root_angular_frequency_hz':25.,
                'damping_ratio':1.,'max_root_acceleration_m_s2':120.,'max_root_torque_nm':3000.,
                'gravity_compensation':True,'supported_mass_kg':self.supported_mass,
                'max_root_force_n':120.*self.supported_mass}
            self.angle_factor = 180 / np.pi if self.metadata.get('tensor_angular_units') == 'degrees' else 1.
            # Gain tensors are native PhysX N*m/rad; USD authoring handles its
            # degree-based gain convention. A runtime readback checks this below.
            for tensor, key in (('ARTICULATION_DOF_STIFFNESS','stiffness'),('ARTICULATION_DOF_DAMPING','damping')):
                actual = self.read(tensor)[0,self.dof_order]
                if not np.allclose(actual,self.contract[key],rtol=.002,atol=.002):
                    raise ValueError('PhysX '+key+' does not match training gains: '+repr(actual[:6]))
            from incremental_scene import IncrementalScene
            self.scene_inputs = IncrementalScene(self.physx,self.stage)
            if scene_spec is not None:
                self.scene_inputs.reconcile(scene_config,scene_spec)
            self.reset()
            status('SMPL AMASS actor and standalone PhysX ready')
        except Exception:
            self.close()
            raise

    @staticmethod
    def semantic_dof(name):
        tail = name.rsplit('/',1)[-1]
        match = re.fullmatch(r'(.+?)(?:_[xyz])?:(\d)',tail)
        if match:
            return match[1]+'_'+'xyz'[int(match[2])]
        return tail

    def read(self, name):
        array = self.buffers[name]
        self.bindings[name].read(array)
        if not self.np.isfinite(array).all():
            raise ValueError('PhysX returned non-finite '+name)
        return array

    def write(self, name, value):
        array = self.buffers[name]
        array[:] = value
        if not self.np.isfinite(array).all():
            raise ValueError('Invalid '+name)
        self.bindings[name].write(array)

    def joint_write(self, name, value, angular=False):
        array = self.np.zeros((1,69),dtype=self.np.float32)
        array[0,self.dof_order] = self.np.asarray(value)*(self.angle_factor if angular else 1.)
        self.write(name,array)

    def reference(self, message):
        from yugo_physx_runtime.protocol import pose
        pose(message)
        self.frames.push(time.monotonic(), message['positions'],message['rotations'],
                         message.get('velocities'),message.get('angular_velocities'))
        self.reference_started = True

    def scene_update(self,message):
        if self.scene_inputs is not None:
            self.scene_inputs.update(message)

    def scene_reconcile(self,config):
        """Apply a validated incremental edit without resetting the actor."""
        from incremental_scene import normalize
        from yugo_physx_runtime.protocol import scene_budget
        spec = normalize(config)
        scene_budget(spec)
        return self.scene_inputs.reconcile(config,spec)

    @property
    def root_support_strength(self):
        return self._root_support_strength

    def root_support(self, strength):
        """Set explicit physical pelvis support; never copy reference poses."""
        if isinstance(strength,bool):
            raise ValueError('Root support strength must be between 0 and 2')
        strength = float(strength)
        if not math.isfinite(strength) or not 0 <= strength <= 2:
            raise ValueError('Root support strength must be between 0 and 2')
        previous = self._root_support_strength
        self._root_support_strength = strength
        if not strength:
            self.last_root_force[:] = self.last_root_torque[:] = 0
            if previous:
                self.write('ARTICULATION_LINK_WRENCH',self.np.zeros((1,24,9)))

    set_root_support = root_support

    def set_point_targets(self,message):
        self.point_targets.set(message)

    @property
    def point_targets_enabled(self):
        return self.point_targets.enabled

    @property
    def point_targets_strength(self):
        return self.point_targets.settings['strength']

    @property
    def point_targets_strength(self):
        return self.point_targets.settings['strength']

    def _apply_root_support(self, seconds, wall, dt):
        if not self._root_support_strength:
            return
        np,tm = self.np,self.tm
        p,q,v,w = self.state()
        tp,tq,tv,tw = self.target(seconds,wall)
        actual_offset = tm.rotate(q[0],self.root_local_com)
        desired_offset = tm.rotate(tq[0],self.root_local_com)
        actual_com = p[0]+actual_offset
        desired_com = tp[0]+desired_offset
        desired_velocity = tv[0]+np.cross(tw[0],desired_offset)
        frequency = 2*np.pi*min(self.root_support_settings['root_linear_frequency_hz'],.12/dt)
        acceleration = frequency**2*(desired_com-actual_com)+2*frequency*(desired_velocity-v[0])
        # Gravity in the verified SMPL scene is world Z-down at 9.81 m/s².
        acceleration += np.array([0.,0.,9.81])
        acceleration *= self._root_support_strength
        magnitude = float(np.linalg.norm(acceleration))
        limit = self.root_support_settings['max_root_acceleration_m_s2']
        if magnitude > limit:
            acceleration *= limit/magnitude
        force = self.supported_mass*acceleration
        orientation_error = tm.to_exp(tm.multiply(tq[0],tm.conjugate(q[0])))
        angular_frequency = 2*np.pi*min(self.root_support_settings['root_angular_frequency_hz'],.12/dt)
        angular_acceleration = angular_frequency**2*orientation_error+2*angular_frequency*(tw[0]-w[0])
        rotation = tm.rotate(q[0],np.eye(3)).T
        inertia = rotation@self.root_local_inertia@rotation.T
        torque = self._root_support_strength*(inertia@angular_acceleration+np.cross(w[0],inertia@w[0]))
        magnitude = float(np.linalg.norm(torque))
        limit = self.root_support_settings['max_root_torque_nm']
        if magnitude > limit:
            torque *= limit/magnitude
        wrench = np.zeros((1,24,9),dtype=np.float32)
        wrench[0,self.root_index,:3] = force
        wrench[0,self.root_index,3:6] = torque
        wrench[0,self.root_index,6:9] = actual_com
        self.write('ARTICULATION_LINK_WRENCH',wrench)
        self.last_root_force[:],self.last_root_torque[:] = force,torque

    def target(self, seconds, wall=None):
        if self.clip:
            return self.clip.sample(seconds)
        if not self.frames.frames:
            return self.rest
        return self.frames.sample(time.monotonic() if wall is None else wall)

    def reset(self):
        self.seconds, self.calls, self.lost = 0.,0,0
        self.inference_ms = self.physics_ms = 0.
        self.previous = self.np.zeros(69,dtype=self.np.float32)
        if not self.clip and self.frames.frames:
            # Reset starts at the latest explicit pose, rather than a pose from
            # the delayed history. Rebuild that history as a stationary sample.
            stamp,p,q,_,_ = self.frames.frames[-1]
            self.frames.clear()
            self.frames.push(stamp,p,q,self.np.zeros_like(p),self.np.zeros_like(p))
        p,q,v,w = self.target(0)
        parents = self.smpl.parents[1:]
        local = self.tm.multiply(self.tm.conjugate(q[parents]),q[1:])
        position = self.tm.to_exp(local).reshape(69)
        velocity = self.np.zeros(69)
        if self.clip:
            nq = self.target(.001)[1]
            nl = self.tm.multiply(self.tm.conjugate(nq[parents]),nq[1:])
            velocity = (self.tm.to_exp(nl).reshape(69)-position)/.001
        # Only Reset writes the base state. Optional support uses native forces.
        self.joint_write('ARTICULATION_DOF_POSITION',position,angular=True)
        self.joint_write('ARTICULATION_DOF_VELOCITY',velocity,angular=True)
        self.joint_write('ARTICULATION_DOF_POSITION_TARGET',position,angular=True)
        self.joint_write('ARTICULATION_DOF_VELOCITY_TARGET',self.np.zeros(69),angular=True)
        self.write('ARTICULATION_ROOT_POSE',self.np.concatenate((p[0],q[0,[1,2,3,0]]))[None,:])
        self.write('ARTICULATION_ROOT_VELOCITY',self.np.concatenate((v[0],w[0]))[None,:])
        self.write('ARTICULATION_LINK_WRENCH',self.np.zeros((1,24,9)))
        self.last_root_force[:] = self.last_root_torque[:] = 0
        if self.point_targets is not None:
            self.point_targets.reset_diagnostics()
        if self.scene_inputs is not None:
            self.scene_inputs.reset()

    def state(self):
        pose = self.read('ARTICULATION_LINK_POSE')[0,self.body_order].astype(self.np.float64)
        vel = self.read('ARTICULATION_LINK_VELOCITY')[0,self.body_order].astype(self.np.float64)
        return pose[:,:3],pose[:,[6,3,4,5]],vel[:,:3],vel[:,3:]

    def step(self, before_substep=None):
        current = self.state()
        target = self.target(self.seconds+self.dt,time.monotonic()+self.dt)
        obs = self.tm.observations(current,target,self.previous)
        begin = time.perf_counter()
        raw = self.policy.run(None,{self.policy.get_inputs()[0].name:obs})[0][0]
        self.inference_ms = (time.perf_counter()-begin)*1000
        if raw.shape != (69,) or not self.np.isfinite(raw).all():
            raise ValueError('SMPL actor returned invalid actions')
        self.previous[:] = raw
        targets = self.offset+self.scale*self.np.tanh(raw)
        self.joint_write('ARTICULATION_DOF_POSITION_TARGET',targets,angular=True)
        begin = time.perf_counter()
        support_wall = time.monotonic()
        native_dt = 1/self.physics_hz
        for index in range(self.substeps):
            self._apply_root_support(self.seconds+(index+1)*native_dt,support_wall+(index+1)*native_dt,native_dt)
            if self.point_targets is not None:
                self.point_targets.before_step(native_dt,self.seconds+(index+1)*native_dt,support_wall+(index+1)*native_dt)
            # Native wrench writes accumulate until PhysX advances and clears
            # forces. A disturbance callback writes its additional wrench;
            # it must not include this support again or it would be doubled.
            if before_substep is not None:
                before_substep(index)
            if self.scene_inputs is not None:
                p,q,_,_ = self.state()
                self.scene_inputs.before_step(1/self.physics_hz,{'names':self.smpl.names,'positions':p,'rotations':q})
            self.physx.step_sync(1/self.physics_hz)
        self.physics_ms = (time.perf_counter()-begin)*1000
        self.seconds += self.dt
        self.calls += 1
        snapshot = self.snapshot()
        self.lost = self.lost+1 if snapshot['tracking_error_m'] > .5 else 0
        # A fall or a missed reference is a valid physical state. Keep running
        # until the user pauses or resets; only invalid native/model data fails.
        return snapshot

    def snapshot(self):
        p,q,_,_ = self.state()
        tp,tq,tv,tw = self.target(self.seconds)
        result = {'type':'state','positions':p.tolist(),'rotations':q.tolist(),
                'reference_pose':{'positions':tp.tolist(),'rotations':tq.tolist(),
                                  'velocities':tv.tolist(),'angular_velocities':tw.tolist()},
                'seconds':self.seconds,'policy_calls':self.calls,
                'root_height_m':float(p[0,2]),
                'tracking_error_m':float(self.np.linalg.norm(p-tp,axis=-1).mean()),
                'inference_ms':self.inference_ms,'physics_ms':self.physics_ms,
                'backend':self.backend_name,'physics_hz':self.physics_hz,'policy_hz':30,
                'learned_policy_loaded':True,'no_root_support':not bool(self._root_support_strength),
                'root_support':bool(self._root_support_strength),'root_support_strength':self._root_support_strength,
                'root_support_force_n':self.last_root_force.tolist(),'root_support_torque_nm':self.last_root_torque.tolist(),
                'root_support_settings':dict(self.root_support_settings),
                'tracking_lost':bool(self.lost>=15),
                'clip_finished':bool(self.clip and self.seconds >= self.clip.duration)}
        if self.scene_inputs is not None:
            result['extra_scene'] = self.scene_inputs.snapshot()
        if self.point_targets is not None:
            result['point_targets'] = self.point_targets.snapshot((tp,tq,tv,tw))
        return result

    def close(self):
        self.point_targets = None
        if getattr(self,'scene_inputs',None) is not None:
            try: self.scene_inputs.close()
            except Exception: pass
            self.scene_inputs = None
        for binding in list(getattr(self,'bindings',{}).values()):
            try: binding.destroy()
            except Exception: pass
        self.bindings = {}
        if getattr(self,'physx',None):
            try: self.physx.detach_ovstage()
            except Exception: pass
        if getattr(self,'stage',None):
            try: self.stage.destroy()
            except Exception: pass
            self.stage = None
        if getattr(self,'physx',None):
            try: self.physx.destroy()
            except Exception: pass
            self.physx = None
        if getattr(self,'temp',None):
            self.temp.cleanup()
