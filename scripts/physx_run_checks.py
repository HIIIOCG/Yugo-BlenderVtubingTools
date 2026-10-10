"""Qualify PhysX Tracker source and an actual ZIP with private Blender profiles."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import uuid
import zipfile

from node_environment import ROOT, isolated_environment

ADDON = ROOT / 'addons/yugo_physx_tracker'
TESTS = ROOT / 'tests/physx_tracker'
CHECKS = {
    'lifecycle': 'restricted enable, event adapter, re-enable, saved graph, cleanup',
    'native_worker': 'real CPU PhysX/model; paused start, step/reset/play/pause/shutdown',
    'host_pipeline': 'real native worker; event-linked Bind/Apply, source safety, load/unload',
}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def runtime_files():
    return {path.relative_to(ADDON).as_posix(): path for path in ADDON.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts
            and path.suffix != '.pyc'}


def extract_package(package, destination):
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None, 'ZIP integrity failed'
        for item in archive.infolist():
            name = item.filename
            assert name and '\\' not in name and ':' not in name and not name.startswith('/'), name
            target = (destination / name).resolve()
            assert target.is_relative_to(destination), name
            assert not stat.S_ISLNK(item.external_attr >> 16), name
            assert name.casefold() not in seen, f'Duplicate ZIP entry: {name}'
            seen.add(name.casefold())
        archive.extractall(destination)
    return destination


def preflight(package, output):
    files = runtime_files()
    manifest = tomllib.loads((ADDON / 'blender_manifest.toml').read_text(encoding='utf-8'))
    assert manifest['id'] == 'yugo_physx_tracker', manifest
    result = {'source': str(ADDON), 'version': manifest['version'],
              'source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                                       text=True, timeout=30).strip(),
              'source_files_sha256': {name: digest(path) for name, path in sorted(files.items())}}
    extracted = None
    if package:
        extracted = extract_package(package, output / 'extracted-package')
        with zipfile.ZipFile(package) as archive:
            entries = [name for name in archive.namelist() if not name.endswith('/')]
            assert set(entries) == set(files), ('ZIP/source file sets differ',
                    sorted(set(files) - set(entries)), sorted(set(entries) - set(files)))
            for name in entries:
                assert digest(extracted / name) == result['source_files_sha256'][name], f'ZIP/source differs: {name}'
        result.update(package=str(package), package_sha256=digest(package),
                      package_matches_source=True, extracted=str(extracted))
    (output / 'artifact.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result, extracted


def run_check(binary, tracker, mode, name, folder, timeout):
    folder.mkdir(parents=True)
    snapshot = folder / 'test-snapshot'
    snapshot.mkdir()
    bootstrap = snapshot / '_bootstrap.py'
    script = snapshot / f'blender_{name}.py'
    shutil.copy2(TESTS / '_bootstrap.py', bootstrap)
    shutil.copy2(TESTS / script.name, script)
    environment = isolated_environment(folder)
    environment.update(PHYSX_TEST_REPOSITORY=str(ROOT), PHYSX_TEST_TRACKER=str(tracker),
                       PHYSX_TEST_OUTPUT=str(folder / 'output'), PHYSX_TEST_MODE=mode)
    command = [str(binary), '--background', '--factory-startup', '--disable-autoexec',
               '--python-exit-code', '1', '--python-expr',
               'import sys, runpy, bpy\n'
               'sys.dont_write_bytecode = True\n'
               f'sys.path.insert(0, {str(snapshot)!r})\n'
               "bpy.context.preferences.filepaths.file_preview_type = 'NONE'\n"
               f'runpy.run_path({str(script)!r}, run_name="__main__")']
    record = {'mode': mode, 'test': name, 'command': command, 'script_sha256': digest(script),
              'bootstrap_sha256': digest(bootstrap), 'log': str(folder / 'test.log')}
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        process = subprocess.run(command, cwd=folder, env=environment, timeout=timeout,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace', **options)
        log = process.stdout
        passed = (process.returncode == 0 and f'PHYSX_{name.upper()}_PASS ' in log
                  and 'PHYSX_TEST_SOURCE_OK: ' in log and 'Traceback (most recent call last):' not in log)
        record.update(status='passed' if passed else 'failed', returncode=process.returncode)
        if not passed:
            record['reason'] = 'Nonzero exit, traceback, missing source verification or completion marker'
    except subprocess.TimeoutExpired as error:
        log = error.stdout or b''
        if isinstance(log, bytes):
            log = log.decode('utf-8', errors='replace')
        record.update(status='failed', reason=f'Timed out after {timeout:g} seconds')
    except OSError as error:
        log = str(error)
        record.update(status='failed', reason='Could not launch isolated Blender')
    Path(record['log']).write_text(log, encoding='utf-8')
    print(f'{mode}/{name}: {record["status"]}', flush=True)
    if record['status'] != 'passed':
        print(log[-7000:], flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path, help='Required absolute Blender 5.1+ executable')
    parser.add_argument('--package', type=Path, help='Actual tracker ZIP; adds checks of its extracted contents')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/physx-checks')
    parser.add_argument('--test', action='append', choices=tuple(CHECKS))
    parser.add_argument('--mode', action='append', choices=('source', 'package'))
    parser.add_argument('--timeout', type=float, default=240.)
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.list:
        for name, description in CHECKS.items():
            print(f'{name}: {description}; synthetic fixture; source and ZIP')
        return 0
    if args.blender is None or not args.blender.is_absolute() or not args.blender.is_file():
        parser.error('--blender must be an existing absolute Blender executable')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be a positive finite number')
    modes = list(dict.fromkeys(args.mode or (['source', 'package'] if args.package else ['source'])))
    if 'package' in modes and args.package is None:
        parser.error('--mode package requires --package')
    package = args.package.resolve(strict=True) if args.package else None
    binary = args.blender.resolve(strict=True)
    version = subprocess.check_output([str(binary), '--version'], text=True, encoding='utf-8', timeout=30).splitlines()[0]
    match = re.match(r'Blender (\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (5, 1):
        parser.error(f'Blender 5.1+ required; found {version}')
    output = args.output.resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    report = {'repository': str(ROOT), 'blender': str(binary), 'version': version,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'tests': []}
    report_path = output / 'results.json'
    try:
        artifact, extracted = preflight(package, output)
        report['artifact'] = artifact
        for mode in modes:
            tracker = ADDON if mode == 'source' else extracted
            for name in dict.fromkeys(args.test or CHECKS):
                report['tests'].append(run_check(binary, tracker, mode, name, output / mode / name, args.timeout))
                report['status'] = 'passed' if all(item['status'] == 'passed' for item in report['tests']) else 'failed'
                report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        final = {name: digest(path) for name, path in runtime_files().items()}
        if final != artifact['source_files_sha256']:
            report.update(status='failed', source_changed_during_run=True)
        if package and digest(package) != artifact['package_sha256']:
            report.update(status='failed', package_changed_during_run=True)
    except (AssertionError, OSError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        report.update(status='failed', error=str(error))
        print(str(error), flush=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Results: {report_path}', flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
