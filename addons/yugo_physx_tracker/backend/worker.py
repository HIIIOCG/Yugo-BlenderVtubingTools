"""Private, bounded stdin/stdout worker. Native PhysX stays outside Blender."""
import argparse
import json
from collections import deque
from pathlib import Path
import os
import sys
import threading
import time
import traceback
import types

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'vendor'))
sys.path.insert(0,str(root/'backend'))
package = types.ModuleType('yugo_physx_runtime')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
from yugo_physx_runtime import protocol

pipe = sys.stdout
sys.stdout = sys.stderr

def send(message):
    pipe.write(protocol.PREFIX+protocol.encode(message))
    pipe.flush()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets',default=str(root/'assets'))
    parser.add_argument('--model',default='')
    parser.add_argument('--clip',default='')
    parser.add_argument('--mode',choices=['CLIP','LIVE'],default='CLIP')
    parser.add_argument('--physics-hz',type=int,default=120)
    parser.add_argument('--solver',choices=['TGS','PGS'],default='TGS')
    parser.add_argument('--iterations',type=int,default=8)
    parser.add_argument('--scene-inputs',default='')
    parser.add_argument('--scene-directory',default='')
    arguments = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(arguments)
    from physx_backend import Backend
    backend = Backend(args.assets,args.model,args.clip,args.mode,
                      lambda message:send({'type':'status','message':message}),
                      args.physics_hz,args.solver,args.iterations,scene_inputs_path=args.scene_inputs)
    queue,condition = deque(),threading.Condition()
    def read():
        try:
            while True:
                line = sys.stdin.readline(protocol.LIMIT+1)
                command = protocol.decode(line) if line else {'type':'shutdown'}
                protocol.command(command)
                with condition:
                    if command['type'] in {'reference','scene_update','root_support','point_targets'} and queue and queue[-1]['type']==command['type']:
                        queue[-1] = command
                    else:
                        if len(queue)>=128: raise RuntimeError('PhysX command queue is full')
                        queue.append(command)
                    condition.notify()
                if not line: return
        except Exception as error:
            send({'type':'error','message':str(error),'fatal':True})
            with condition:
                queue.append({'type':'shutdown'})
                condition.notify()
    threading.Thread(target=read,daemon=True).start()
    running,next_step = False,time.monotonic()
    send({'type':'ready','backend':backend.backend_name,'physics_hz':backend.physics_hz,'policy_hz':30})
    send({**backend.snapshot(),'running':False})
    try:
        while True:
            with condition:
                commands = list(queue)
                queue.clear()
            for command in commands:
                kind = command['type']
                if kind=='shutdown': return
                if kind=='scene_reconcile':
                    try:
                        if not args.scene_directory:
                            raise ValueError('Dynamic scene directory is unavailable')
                        directory = Path(args.scene_directory).resolve()
                        path = Path(command['path']).resolve()
                        if not path.is_relative_to(directory) or path.suffix.lower() != '.json':
                            raise ValueError('Scene configuration is outside its private directory')
                        if path.stat().st_size > 16*1024*1024:
                            raise ValueError('PhysX scene inputs exceed 16 MiB')
                        def invalid_constant(_value):
                            raise ValueError('Non-finite scene configuration')
                        config = json.loads(path.read_text(encoding='utf-8'),parse_constant=invalid_constant)
                        backend.scene_reconcile(config)
                        send({'type':'scene_applied','revision':command['revision']})
                        send({**backend.snapshot(),'running':running})
                    except Exception as error:
                        traceback.print_exc()
                        # Invalid edits retain the last valid physical scene.
                        # A native failure which cannot roll back must fail hard.
                        if getattr(error,'fatal',False):
                            raise
                        send({'type':'scene_rejected','revision':command['revision'],'message':str(error)})
                    continue
                try:
                    if kind=='reference':
                        backend.reference(command)
                        if not running:
                            send({**backend.snapshot(),'running':False})
                        continue
                    if kind=='scene_update':
                        backend.scene_update(command)
                        continue
                    if kind=='play':
                        running,next_step = True,time.monotonic()
                    elif kind=='root_support':
                        backend.set_root_support(command['strength'])
                    elif kind=='point_targets':
                        backend.set_point_targets(command)
                    elif kind=='pause': running = False
                    elif kind=='reset':
                        running = False
                        backend.reset()
                        next_step = time.monotonic()
                    elif kind=='step':
                        running = False
                        backend.step()
                    else: raise ValueError('Unknown PhysX command: '+kind)
                    send({**backend.snapshot(),'running':running})
                except Exception as error:
                    running = False
                    traceback.print_exc()
                    send({'type':'error','message':str(error),'fatal':False})
            now = time.monotonic()
            if running and now>=next_step:
                try:
                    send({**backend.step(),'running':True})
                    next_step = max(next_step+backend.dt,time.monotonic()-backend.dt)
                except Exception as error:
                    running = False
                    traceback.print_exc()
                    send({'type':'error','message':str(error),'fatal':False})
            with condition:
                if not queue:
                    condition.wait(timeout=max(.001,min(.03,next_step-time.monotonic())) if running else .03)
    finally:
        backend.close()

if __name__=='__main__':
    try: main()
    except Exception as error:
        traceback.print_exc()
        send({'type':'error','message':str(error),'fatal':True})
        sys.exit(1)
