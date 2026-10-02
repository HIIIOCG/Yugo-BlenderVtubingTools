"""Exercise both real product CLIs and mutually exclusive release tag routes."""
from pathlib import Path
import fnmatch
import re
import subprocess
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReleaseRouting(unittest.TestCase):
    def test_product_tag_cli(self):
        for addon, script, prefix in [('yugu_node_studio', 'node_check_version.py', 'studio-v'),
                                      ('yugo', 'check_version.py', 'v')]:
            metadata = tomllib.loads((ROOT / 'addons' / addon / 'blender_manifest.toml').read_text(encoding='utf-8'))
            version = metadata['version']
            def run(tag):
                return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / script), '--tag', tag,
                                       '--print-version'], capture_output=True, text=True)
            valid = run(prefix + version)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(valid.stdout.strip(), version)
            for invalid in ['v' + version if prefix == 'studio-v' else 'studio-v' + version,
                            prefix + '999.0.0']:
                self.assertNotEqual(run(invalid).returncode, 0, invalid)

    def test_actual_workflow_tag_routes(self):
        routes = {}
        for filename in ('release.yml', 'node-release.yml'):
            source = (ROOT / '.github/workflows' / filename).read_text(encoding='utf-8')
            pattern = re.search(r'tags:\s*\["([^"]+)"\]', source)
            self.assertIsNotNone(pattern, filename)
            routes[filename] = pattern.group(1)
        for tag, expected in [('v0.1.26', 'release.yml'), ('studio-v0.7.0', 'node-release.yml')]:
            matches = [name for name, pattern in routes.items() if fnmatch.fnmatchcase(tag, pattern)]
            self.assertEqual(matches, [expected])


if __name__ == '__main__':
    unittest.main()
