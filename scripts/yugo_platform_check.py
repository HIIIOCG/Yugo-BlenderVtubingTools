"""Build or qualify one universal ZIP through Blender's real extension installer."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tomllib
import uuid
import zipfile
from node_environment import ROOT, isolated_environment


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--platform', choices=('windows-x64', 'linux-x64', 'macos-arm64'))
    args = parser.parse_args()
    folder = args.output.resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                    + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    environment = isolated_environment(folder)
    # Test ordinary product imports, irrespective of the invoking shell's overrides.
    environment.pop('MUJOCO_GL', None)
    environment.pop('PYGLFW_LIBRARY', None)
    binary = args.blender.resolve(strict=True)
    source = ROOT / 'addons/yugo'
    manifest = tomllib.loads((source / 'blender_manifest.toml').read_text(encoding='utf-8'))
    archive = args.package.resolve(strict=True) if args.package else folder / f"yugo-{manifest['version']}.zip"
    steps = []
    options = {}
    if sys.platform == 'win32':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options['startupinfo'] = startup

    def run(label, arguments, marker=None):
        command = [str(binary), '--background', *arguments]
        process = subprocess.run(command, env=environment, cwd=ROOT, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=180,
                                 **options)
        content = process.stdout + '\n' + process.stderr
        (folder / f'{label}.log').write_text(content, encoding='utf-8')
        steps.append({'label': label, 'command': command, 'returncode': process.returncode})
        print(f'{label}: exit {process.returncode}', flush=True)
        if process.returncode or (marker and marker not in content):
            print(content, flush=True)
            raise RuntimeError(f'{label} did not pass')

    try:
        if not args.package:
            run('source-validate', ['--factory-startup', '--command', 'extension', 'validate', str(source)])
            run('build', ['--factory-startup', '--command', 'extension', 'build',
                          '--source-dir', str(source), '--output-dir', str(folder)])
        assert archive.stat().st_size < 50 * 1024**2, 'Unexpected package size'
        run('zip-validate', ['--factory-startup', '--command', 'extension', 'validate', str(archive)])
        files = {path.relative_to(source).as_posix(): path for path in source.rglob('*')
                 if path.is_file() and not any(part.startswith('.') or part == '__pycache__'
                                              for part in path.relative_to(source).parts)
                 and path.suffix not in {'.pyc', '.pyo', '.zip'}}
        with zipfile.ZipFile(archive) as package:
            names = [name for name in package.namelist() if not name.endswith('/')]
            assert len(names) == len(set(names)) and set(names) == set(files), 'ZIP file set differs'
            assert package.testzip() is None
            for name in names:
                assert package.read(name) == files[name].read_bytes(), name
        if not args.build_only:
            expected = args.platform or {'win32': 'windows-x64', 'linux': 'linux-x64',
                                        'darwin': 'macos-arm64'}[sys.platform]
            environment.update(YUGO_EXPECTED_VERSION=manifest['version'],
                               YUGO_EXPECTED_PLATFORM=expected, YUGO_TEST_PACKAGE=str(archive),
                               YUGO_PLATFORM_RECORD=str(folder / 'installed.json'))
            repository = Path(environment['BLENDER_USER_EXTENSIONS']) / 'user_default'
            run('repo-add', ['--factory-startup', '--command', 'extension', 'repo-add',
                             'user_default', '--name', 'Task local', '--directory', str(repository), '--clear-all'])
            run('install-enable', ['--command', 'extension', 'install-file', '--repo', 'user_default',
                                   '--enable', str(archive)])
            run('restart-inspect', ['--disable-autoexec', '--python-exit-code', '1', '--python',
                                    str(ROOT / 'tests/yugo_node/blender_platform_install.py')],
                'YUGO_PLATFORM_INSTALL_OK')
        record = {'status': 'passed', 'source_commit': subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                  'version': manifest['version'], 'platform': args.platform,
                  'host_machine': platform.machine(), 'package': str(archive),
                  'package_sha256': sha256(archive), 'package_bytes': archive.stat().st_size,
                  'source_files_sha256': {name: sha256(path) for name, path in sorted(files.items())},
                  'byte_comparison': 'passed', 'build_only': args.build_only,
                  'runtime_status': 'not_run' if args.build_only else 'passed',
                  'source_dirty': bool(subprocess.check_output(
                      ['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip()),
                  'steps': steps}
        (folder / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        if args.build_only:
            # Expose the same archive to every native runner and the publication job.
            args.output.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(archive, args.output / archive.name)
            (args.output / 'platform-build.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({'status': 'passed', 'record': str(folder / 'result.json'),
                          'package': str(archive), 'sha256': record['package_sha256']}), flush=True)
    except Exception:
        (folder / 'failure.json').write_text(json.dumps({'status': 'failed', 'steps': steps}, indent=2) + '\n')
        raise


if __name__ == '__main__':
    main()
