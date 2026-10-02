"""Run portable Studio regressions in separate Blender processes/profiles."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from node_environment import ROOT, TESTS, isolated_environment

EXCLUDED = {'blender_shapes_diagnostic.py': 'Preserved-behavior diagnostic, not a passing regression.',
            'blender_vrm_snapshot.py': 'Requires separately provisioned official VRM extension.',
            'blender_outline_multi_material.py': 'Requires separately provisioned official VRM extension.'}
LAST = ['blender_stage_one_demo.py', 'blender_stage_one_saved.py', 'blender_package.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--test', action='append')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    all_tests = sorted(path.name for path in TESTS.glob('blender_*.py'))
    if args.list:
        print('\n'.join(name + (' [optional: ' + EXCLUDED[name] + ']' if name in EXCLUDED else '') for name in all_tests))
        return 0
    if args.blender is None or args.package is None:
        parser.error('--blender and --package are required when running checks.')
    binary = args.blender.resolve(strict=True)
    package = args.package.resolve(strict=True)
    if not args.blender.is_absolute() or not binary.is_file():
        parser.error('--blender must be an absolute executable path.')
    selected = args.test or [name for name in all_tests if name not in EXCLUDED]
    if any(name not in all_tests for name in selected):
        parser.error('Unknown test; use --list.')
    if not args.test:
        selected = [name for name in selected if name not in LAST] + LAST
    output = (args.output or ROOT / 'artifacts' / ('node-checks-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    generated = output / 'generated'
    generated.mkdir(exist_ok=True)
    records = []
    tests = [(path.stem, [sys.executable, '-B', str(path)]) for path in sorted(TESTS.glob('test_*.py'))]
    tests += [(name, [str(binary), '--background', '--factory-startup', '--disable-autoexec',
                      '--python-exit-code', '1', '--python', str(TESTS / name)]) for name in selected]
    for name, command in tests:
        if name in EXCLUDED:
            records.append({'test': name, 'status': 'not_run', 'reason': EXCLUDED[name]})
            continue
        folder = output / name.removesuffix('.py')
        folder.mkdir(parents=True, exist_ok=True)
        environment = isolated_environment(folder)
        environment['YUGUS_TEST_OUTPUT'] = str(generated)
        environment['NODE_PACKAGE_PATH'] = str(package)
        log = folder / 'test.log'
        try:
            result = subprocess.run(command, cwd=folder, env=environment, capture_output=True,
                                    timeout=180, text=True, encoding='utf-8', errors='replace')
            content = result.stdout + '\n' + result.stderr
            log.write_text(content, encoding='utf-8')
            passed = result.returncode == 0 and 'Traceback (most recent call last)' not in content
            if name.endswith('.py'):
                passed = passed and 'NODE_TEST_SOURCE_OK:' in content
            records.append({'test': name, 'status': 'passed' if passed else 'failed',
                            'returncode': result.returncode, 'command': command, 'log': str(log)})
        except subprocess.TimeoutExpired:
            passed = False
            records.append({'test': name, 'status': 'failed', 'reason': 'Timed out', 'command': command})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed and log.exists():
            print(log.read_text(encoding='utf-8')[-5000:], flush=True)
    if not args.test:
        records += [{'test': name, 'status': 'not_run', 'reason': reason} for name, reason in EXCLUDED.items()]
    passed = all(record['status'] == 'passed' for record in records if record['status'] != 'not_run')
    (output / 'results.json').write_text(json.dumps({'status': 'passed' if passed else 'failed',
        'source': str(ROOT / 'addons/yugu_node_studio'), 'package': str(package), 'tests': records}, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
