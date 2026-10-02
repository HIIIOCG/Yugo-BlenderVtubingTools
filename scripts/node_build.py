"""Build, validate and hash an installable Studio ZIP from this repository."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
from node_check_version import version
from node_environment import ROOT, ADDON, isolated_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--require-clean', action='store_true', help='Reject uncommitted addon changes for release builds.')
    args = parser.parse_args()
    binary = args.blender.resolve(strict=True)
    if not binary.is_file() or not args.blender.is_absolute():
        parser.error('--blender must be an absolute executable path.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    value = version()
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    changes = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all', '--', 'addons/yugu_node_studio'],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if args.require_clean and changes:
        raise SystemExit('Release builds require committed, clean Studio source.')
    environment = isolated_environment(output / 'build')
    commands = (
        [binary, '--command', 'extension', 'validate', ADDON],
        [binary, '--command', 'extension', 'build', '--source-dir', ADDON, '--output-dir', output],
        [binary, '--command', 'extension', 'validate', output / ('yugu_node_studio-' + value + '.zip')],
    )
    for index, command in enumerate(commands):
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=environment,
                                capture_output=True, timeout=120)
        (output / 'build' / ('extension-' + str(index) + '.log')).write_bytes(result.stdout + b'\n' + result.stderr)
        if result.returncode:
            raise SystemExit(result.stdout.decode('utf-8', errors='replace') + result.stderr.decode('utf-8', errors='replace'))
    archive = output / ('yugu_node_studio-' + value + '.zip')
    if not 0 < archive.stat().st_size < 50 * 1024 * 1024:
        raise SystemExit('Studio archive size is outside its expected bound.')
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise SystemExit('Studio ZIP integrity check failed.')
        names = package.namelist()
        expected = {path.relative_to(ADDON).as_posix() for path in ADDON.rglob('*')
                    if path.is_file() and '__pycache__' not in path.parts
                    and '.git' not in path.parts and 'tests' not in path.parts
                    and path.suffix != '.pyc'}
        actual = {name for name in names if not name.endswith('/')}
        if actual != expected or len(actual) != len([name for name in names if not name.endswith('/')]):
            raise SystemExit('Studio package file set differs from source: missing=' +
                             str(sorted(expected - actual)) + ', extra=' + str(sorted(actual - expected)))
        if any(name.startswith(('addons/', 'tests/', 'scripts/', '.dev/', '.github/')) or
               '__pycache__' in name or name.endswith(('.pyc', '.blend1')) for name in names):
            raise SystemExit('Non-runtime files entered the Studio ZIP.')
        for name in names:
            if not name.endswith('/') and package.read(name) != (ADDON / name).read_bytes():
                raise SystemExit('Studio package does not match this source: ' + name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='utf-8')
    record = {'package': str(archive), 'source': str(ADDON), 'version': value,
              'source_commit': revision, 'source_dirty': bool(changes),
              'source_files_sha256': {name: hashlib.sha256((ADDON / name).read_bytes()).hexdigest() for name in sorted(actual)},
              'entries': len(names), 'bytes': archive.stat().st_size, 'sha256': checksum,
              'source_validate': 'passed', 'zip_validate': 'passed', 'byte_comparison': 'passed'}
    (output / 'studio-build.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: item for key, item in record.items() if key != 'source_files_sha256'}, indent=2))


if __name__ == '__main__':
    main()
