"""Check the genuinely installed extension after a new Blender launch."""
from pathlib import Path
import addon_utils
import hashlib
import importlib
import json
import math
import os
import platform
import sys
import tomllib
import zipfile

import bpy

module_name = 'bl_ext.user_default.yugo'
addon = sys.modules.get(module_name)
assert addon is not None, 'Installed Yugo was not enabled on restart'
directory = Path(addon.__file__).resolve().parent
manifest = tomllib.loads((directory / 'blender_manifest.toml').read_text(encoding='utf-8'))
assert manifest['version'] == os.environ['YUGO_EXPECTED_VERSION'], manifest
assert module_name in bpy.context.preferences.addons
actual_platform = {'win32': 'windows-x64', 'linux': 'linux-x64',
                   'darwin': 'macos-arm64'}[sys.platform]
assert actual_platform == os.environ['YUGO_EXPECTED_PLATFORM']
assert platform.machine().lower() in ({'arm64', 'aarch64'} if sys.platform == 'darwin'
                                     else {'amd64', 'x86_64'}), platform.machine()
assert actual_platform in manifest['platforms']
archive = Path(os.environ['YUGO_TEST_PACKAGE'])
with zipfile.ZipFile(archive) as package:
    for name in package.namelist():
        if not name.endswith('/'):
            assert (directory / name).read_bytes() == package.read(name), name
tree_class = bpy.types.NodeTree.bl_rna_get_subclass_py('YUGUS_NT_humanoid_motion')
assert tree_class is addon.node.nodes.YUGUS_NT_humanoid_motion
for identifier in ('YUGO_PT_avatar', 'YUGO_PT_node_start'):
    assert bpy.types.Panel.bl_rna_get_subclass_py(identifier) is not None, identifier
area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
with bpy.context.temp_override(area=area, region=region):
    assert bpy.ops.yugu_node_studio.open_editor() == {'FINISHED'}
    assert area.ui_type == 'YUGUS_NT_humanoid_motion'
    assert area.spaces.active.edit_tree is not None
    clocks = [node for node in area.spaces.active.edit_tree.nodes
              if node.bl_idname == 'YUGUS_NODE_hz_trigger']
    assert clocks and all(node.mute for node in clocks), 'Starter clock was not paused'
import numpy
numpy_before = numpy
assert not os.environ.get('MUJOCO_GL'), 'Expected ordinary MuJoCo GL import selection'
dependency = importlib.import_module(module_name + '.node.mujoco_dependency')
engine = dependency.load_mujoco()
assert sys.modules['numpy'] is numpy_before
assert engine.__version__ == '3.3.7', engine.__version__
assert engine.mj_versionString() == '3.3.7'
model = engine.MjModel.from_xml_string('''<mujoco>
<option timestep="0.005" gravity="0 0 -9.81"/>
<worldbody><body pos="0 0 2"><freejoint/>
<geom type="sphere" size="0.1" density="1000"/>
</body></worldbody></mujoco>''')
data = engine.MjData(model)
initial_height = float(data.qpos[2])
for _ in range(100):
    engine.mj_step(model, data)
assert math.isfinite(float(data.qpos[2]))
assert float(data.qpos[2]) < initial_height - 0.5, data.qpos
assert abs(float(data.time) - 0.5) < 1e-8
entries = []
for candidate in addon_utils.modules(refresh=True):
    if 'yugo' in candidate.__name__.lower() or 'yugu' in candidate.__name__.lower():
        entries.append({'module': candidate.__name__, 'file': candidate.__file__,
                        'version': addon_utils.module_bl_info(candidate).get('version'),
                        'enabled': candidate.__name__ in bpy.context.preferences.addons})
record = {'status': 'passed', 'platform': actual_platform, 'machine': platform.machine(),
          'blender': bpy.app.version_string, 'python': sys.version,
          'version': manifest['version'], 'module': module_name,
          'module_file': str(addon.__file__), 'entries': entries,
          'package_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'mujoco_version': engine.__version__, 'mujoco_file': engine.__file__,
          'host_allocator_override': bool(getattr(engine, '_yugo_allocator_refs', None)),
          'simulation_time': float(data.time), 'simulation_height': float(data.qpos[2]),
          'mujoco_gl_override': os.environ.get('MUJOCO_GL'),
          'editor_ui_type': area.ui_type,
          'graph': area.spaces.active.edit_tree.name}
Path(os.environ['YUGO_PLATFORM_RECORD']).write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2), flush=True)
print('YUGO_PLATFORM_INSTALL_OK', flush=True)
