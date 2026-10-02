"""Verify the actual Studio ZIP with real timers and a fresh GUI result marker."""
import argparse
import json
from pathlib import Path
import subprocess
import os
from node_environment import ROOT, TESTS, isolated_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = isolated_environment(output / 'profile')
    environment['NODE_GUI_OUTPUT'] = str(output)
    environment['NODE_PACKAGE_PATH'] = str(args.package.resolve(strict=True))
    options = {}
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options['startupinfo'] = startup
    binary = str(args.blender.resolve(strict=True))
    # Provision only this disposable profile so first-run setup cannot obscure evidence.
    seed = [binary, '--background', '--factory-startup', '--disable-autoexec',
            '--python-exit-code', '1', '--python-expr',
            'import bpy; bpy.context.preferences.view.show_splash=False; bpy.ops.wm.save_userpref()']
    seeded = subprocess.run(seed, cwd=ROOT, env=environment, capture_output=True, timeout=30, **options)
    (output / 'profile-seed.log').write_bytes(seeded.stdout + seeded.stderr)
    if seeded.returncode:
        raise SystemExit('Could not prepare disposable GUI preferences.')
    command = [binary, '--disable-autoexec',
               '--python-exit-code', '1', '--python', str(TESTS / 'studio_live_gui.py')]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=90, **options)
    content = result.stdout + result.stderr
    (output / 'gui.log').write_bytes(content)
    report = output / 'gui-result.json'
    errors = (b'Traceback (most recent call last)', b'ERROR Python script error',
              b'could not find function', b'Yugo scheduler callback')
    if result.returncode or any(marker in content for marker in errors) or not report.is_file():
        print(content.decode('utf-8', errors='replace')[-5000:])
        raise SystemExit('Studio GUI did not produce fresh passing evidence.')
    record = json.loads(report.read_text(encoding='utf-8'))
    print(json.dumps(record, indent=2))
    if record.get('status') != 'passed':
        raise SystemExit('Studio real-timer/GUI check failed.')


if __name__ == '__main__':
    main()
