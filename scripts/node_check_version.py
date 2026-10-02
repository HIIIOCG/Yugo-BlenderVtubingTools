"""Validate Studio metadata independently of the legacy Yugo release route."""
import argparse
import ast
import re
import tomllib
from node_environment import ADDON


def version():
    manifest = tomllib.loads((ADDON / 'blender_manifest.toml').read_text(encoding='utf-8'))
    value = manifest['version']
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Studio requires a numeric semantic version.')
    if manifest['id'] != 'yugu_node_studio':
        raise ValueError('Studio package identity must remain yugu_node_studio.')
    module = ast.parse((ADDON / '__init__.py').read_text(encoding='utf-8-sig'))
    info = next(ast.literal_eval(node.value) for node in module.body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                target.id == 'bl_info' for target in node.targets))
    if info['version'] != tuple(map(int, value.split('.'))):
        raise ValueError('Studio manifest and bl_info versions differ.')
    if info['blender'] != tuple(map(int, manifest['blender_version_min'].split('.'))):
        raise ValueError('Studio minimum Blender versions differ.')
    for name in ('__init__.py', 'blender_manifest.toml', 'preset.blend', 'README.md', 'LICENSE'):
        if not (ADDON / name).is_file():
            raise ValueError('Required Studio file is missing: ' + name)
    for path in ADDON.rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    parser.add_argument('--print-version', action='store_true')
    args = parser.parse_args()
    value = version()
    if args.tag and args.tag != 'studio-v' + value:
        parser.error('Studio tags must be studio-v' + value + '; legacy v* tags build Yugo.')
    print(value if args.print_version else 'Yugu Node Studio ' + value + ' metadata is consistent')


if __name__ == '__main__':
    main()
