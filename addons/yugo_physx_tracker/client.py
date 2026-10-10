"""Private portable worker process, with nonblocking Blender lifecycle cleanup."""
from collections import deque
from pathlib import Path
import os
import subprocess
import threading
import time
from . import protocol


def launch_command(executable, args, launch_mode='PYTHON', worker_path=None):
    worker = Path(worker_path) if worker_path else Path(__file__).parent/'backend/worker.py'
    if launch_mode == 'PYTHON':
        return [str(executable), '-u', str(worker), *map(str, args)]
    if launch_mode == 'BLENDER':
        return [str(executable), '--background', '--factory-startup', '--python', str(worker), '--', *map(str, args)]
    raise ValueError('Worker launch mode must be PYTHON or BLENDER')


class Client:
    def __init__(self, executable, args, log_path, launch_mode='PYTHON', worker_path=None):
        if not Path(executable).is_file():
            raise ValueError('Worker executable is missing; check addon preferences')
        self.events = deque()
        self.commands = deque()
        self.condition = threading.Condition()
        self._events_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.closed = False
        self.started = time.monotonic()
        self.log_path = str(log_path)
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        command = launch_command(executable, args, launch_mode, worker_path)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, 'w', encoding='utf-8')
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log, env=env, text=True, encoding='utf-8', errors='replace',
                bufsize=1, creationflags=flags)
        except Exception:
            self.log.close()
            raise
        self.reader = threading.Thread(target=self._read, name='Yugo PhysX reader', daemon=True)
        self.writer = threading.Thread(target=self._write, name='Yugo PhysX writer', daemon=True)
        self.reader.start()
        self.writer.start()

    def _event(self, event):
        with self._events_lock:
            if event.get('type') == 'state' and self.events and self.events[-1].get('type') == 'state':
                self.events[-1] = event
            else:
                if len(self.events) >= 64:
                    # Retain lifecycle/errors by discarding a stale snapshot first.
                    stale = next((i for i, item in enumerate(self.events) if item.get('type') == 'state'), None)
                    if stale is not None:
                        del self.events[stale]
                    else:
                        self.events.popleft()
                self.events.append(event)

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(protocol.LIMIT+len(protocol.PREFIX)+1)
                if not line:
                    break
                if len(line.encode('utf-8')) > protocol.LIMIT+len(protocol.PREFIX):
                    raise ValueError('PhysX output line exceeds limit')
                if line.startswith(protocol.PREFIX):
                    self._event(protocol.decode(line[len(protocol.PREFIX):]))
                else:
                    self.log.write(line)
                    self.log.flush()
            if not self.closed:
                self._event({'type':'error', 'fatal':True, 'message':'PhysX worker stopped. See '+self.log_path})
        except Exception as error:
            if not self.closed:
                self._event({'type':'error', 'fatal':True, 'message':str(error)})

    def _write(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.commands or self.closed)
                    if self.closed:
                        return
                    command = self.commands.popleft()
                with self._write_lock:
                    self.process.stdin.write(protocol.encode(command))
                    self.process.stdin.flush()
        except (OSError, ValueError) as error:
            if not self.closed:
                self._event({'type':'error', 'fatal':True, 'message':str(error)})

    def send(self, command):
        protocol.command(command)
        protocol.encode(command)
        with self.condition:
            if self.closed or self.process.poll() is not None:
                raise RuntimeError('PhysX is disconnected')
            if command['type'] in {'reference','point_targets'} and self.commands and self.commands[-1]['type'] == command['type']:
                self.commands[-1] = command
            else:
                if len(self.commands) >= 64:
                    raise RuntimeError('PhysX command queue is full')
                self.commands.append(command)
            self.condition.notify()

    def drain(self):
        with self._events_lock:
            events = list(self.events)
            self.events.clear()
        return events

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.commands.clear()
            self.condition.notify_all()
        def reap():
            try:
                if self.process.poll() is None:
                    try:
                        with self._write_lock:
                            self.process.stdin.write(protocol.encode({'type':'shutdown'}))
                            self.process.stdin.flush()
                        self.process.wait(timeout=2)
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait()
            finally:
                self.reader.join(timeout=1)
                self.writer.join(timeout=1)
                for stream in (self.process.stdin, self.process.stdout, self.log):
                    stream.close()
        threading.Thread(target=reap, name='Yugo PhysX cleanup', daemon=True).start()
