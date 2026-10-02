"""Studio-only paths and isolated subprocess environments."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'addons/yugu_node_studio'
TESTS = ROOT / 'tests/yugu_node_studio'


def isolated_environment(folder: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME'):
        environment.pop(name, None)
    environment['PYTHONNOUSERSITE'] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    profile = folder / 'profile'
    for name, child in (('BLENDER_USER_RESOURCES', 'resources'),
                        ('BLENDER_USER_CONFIG', 'config'),
                        ('BLENDER_USER_SCRIPTS', 'scripts'),
                        ('BLENDER_USER_DATAFILES', 'datafiles'),
                        ('BLENDER_USER_EXTENSIONS', 'extensions'),
                        ('LOCALAPPDATA', 'local-appdata'), ('XDG_CACHE_HOME', 'cache'),
                        ('TEMP', 'temp'), ('TMP', 'temp'), ('TMPDIR', 'temp')):
        path = profile / child
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    return environment
