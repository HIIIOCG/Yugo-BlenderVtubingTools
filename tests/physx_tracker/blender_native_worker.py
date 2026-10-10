"""Selected source/ZIP alone supplies CPU PhysX, model and complete protocol."""
from pathlib import Path
import time

from _bootstrap import OUTPUT, TRACKER, finish, load

yugo, host, addon = load()
executable, launch_mode = addon.runtime.worker_executable()
client = addon.runtime.Client(executable, [], OUTPUT / 'native-worker.log', launch_mode=launch_mode)
assert str(TRACKER / 'backend/worker.py') in list(map(str, client.process.args))
events = []


def until(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        events.extend(client.drain())
        for index, event in enumerate(events):
            if event['type'] == 'error':
                raise AssertionError(event)
            if predicate(event):
                return events.pop(index)
        assert client.process.poll() is None, f"Worker exited: {client.process.returncode}"
        time.sleep(.01)
    raise TimeoutError('Native worker did not return the expected protocol state')


def command(name, **values):
    client.send({'type': name, **values})


try:
    ready = until(lambda event: event['type'] == 'ready')
    initial = until(lambda event: event['type'] == 'state')
    assert initial['seconds'] == 0 and not initial['running']
    assert initial['learned_policy_loaded'] and initial['no_root_support']
    assert len(initial['positions']) == 24 and len(initial['rotations']) == 24
    assert not initial['point_targets']['enabled'] and initial['point_targets']['count'] == 0
    goal = {'id': 'head', 'position': initial['reference_pose']['positions'][13],
            'reference_offset': [.03, 0., 0.]}
    command('point_targets', targets=[goal], strength=.75, frequency_hz=3.,
            damping_ratio=1., max_force_n=40.)
    configured = until(lambda event: event['type'] == 'state' and event['point_targets']['enabled'])
    assert configured['seconds'] == 0 and configured['policy_calls'] == 0
    assert configured['positions'] == initial['positions']
    command('step')
    stepped = until(lambda event: event['type'] == 'state' and event['seconds'] > 0)
    assert not stepped['running'] and stepped['policy_calls'] > 0
    command('reset')
    reset = until(lambda event: event['type'] == 'state' and event['seconds'] == 0)
    assert reset['point_targets']['enabled']
    command('play')
    walked = until(lambda event: event['type'] == 'state' and event['seconds'] >= 2.)
    assert walked['running']
    command('pause')
    paused = until(lambda event: event['type'] == 'state' and not event['running'] and event['seconds'] >= 2.)
    assert paused['root_height_m'] > .5 and paused['tracking_error_m'] < .5, paused
    point = paused['point_targets']['points'][0]
    assert point['direct_torque_nm'] == [0., 0., 0.]
    assert sum(value * value for value in point['force_n']) <= 40.0001 ** 2
    command('point_targets', targets=[goal], strength=0., frequency_hz=3.,
            damping_ratio=1., max_force_n=40.)
    disabled = until(lambda event: event['type'] == 'state' and not event['point_targets']['enabled'])
    assert disabled['seconds'] == paused['seconds']
    assert disabled['policy_calls'] == paused['policy_calls']
    assert disabled['positions'] == paused['positions']
    command('shutdown')
    client.process.wait(timeout=10)
    assert client.process.returncode == 0
    finish('native_worker', worker_path=str(TRACKER / 'backend/worker.py'),
           worker_executable=executable, backend=ready['backend'], learned_policy=True,
           paused_start=True, step_reset_play_pause=True, point_targets_bounded_force=True,
           shutdown_returncode=client.process.returncode,
           final_seconds=paused['seconds'], root_height_m=paused['root_height_m'],
           tracking_error_m=paused['tracking_error_m'])
finally:
    client.close()
    client.process.wait(timeout=8)
