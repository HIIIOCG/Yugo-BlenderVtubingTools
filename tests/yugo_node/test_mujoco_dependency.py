"""Dependency selection and embedded-host import checks without importing bpy."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'addons/yugo/node/mujoco_dependency.py'
spec = importlib.util.spec_from_file_location('tested_mujoco_dependency', SOURCE)
dependency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependency)

NATIVE_SHA256 = {
    'mujoco-3.3.7-cp313-cp313-win_amd64.whl': '11f3f6154aa7f72f5929f150800c7098904f91eb0b7cdefacb6fdf7edd3704f8',
    'mujoco-3.3.7-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl': '1657312f4f4591ade7c7a21187065e3ab16e6a45d9c8c6abe8772fdc4b585789',
    'mujoco-3.3.7-cp313-cp313-macosx_11_0_arm64.whl': 'cc640062891fb54f1f1606bb5748b239f1616de38487c777f09d1775ccdcacc3',
    'glfw-2.10.2-py2.py3-none-win_amd64.whl': '15fd0666cd8f1b0ecb535c3abb712b8ddda053bf8d16de2353b1c6ced0e65402',
    'glfw-2.10.2-py2.py3-none-manylinux2014_x86_64.whl': '82daf1f87b2a48815637dc6f6720d4a55a57ba1d3683322dc20dc28065754f97',
    'glfw-2.10.2-py2.py3-none-macosx_11_0_arm64.whl': 'c000b8a5e5fb4374b2c18d12347255ccb92001b9bcf80ef74c56072acbb98fe3',
}


def temporary_directory():
    directory = ROOT / 'artifacts/mujoco-dependency-unit/temp'
    directory.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=directory, prefix='fixture-')


def make_wheel(path, member=None, payload=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    package = path.name.split('-')[0]
    with zipfile.ZipFile(path, 'w') as archive:
        # ZipInfo's constructor normalizes backslashes on Windows. Assign after
        # construction so malformed-path cases exercise the actual ZIP bytes.
        entry = zipfile.ZipInfo('placeholder')
        entry.filename = member or package + '/native.marker'
        archive.writestr(entry, payload or path.name)


def engine(version='3.3.7'):
    result = SimpleNamespace(__version__=version, mjtObj=SimpleNamespace(mjOBJ_BODY=1))
    for name in dependency.REQUIRED_API:
        setattr(result, name, lambda *_args: None)
    class Model:
        @staticmethod
        def from_xml_string(_xml):
            return object()
    result.MjModel = Model
    result.mj_versionString = lambda: version
    return result


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'wheels'
        for name in (*dependency.COMMON_WHEELS, *NATIVE_SHA256):
            make_wheel(self.directory / name)
        make_wheel(self.directory / 'numpy-9.0-py3-none-any.whl')
        make_wheel(self.directory / 'mujoco-3.3.7-cp312-cp312-win_amd64.whl')

    def select(self, system, machine):
        return dependency._selected_wheels(self.directory, system=system,
                                           machine=machine, python_version=(3, 13))

    def test_each_host_selects_one_engine_and_glfw_without_foreign_or_numpy_files(self):
        cases = [('win32', 'AMD64', 'win_amd64'),
                 ('linux', 'x86_64', 'manylinux'),
                 ('darwin', 'arm64', 'macosx_11_0_arm64'),
                 ('darwin', 'aarch64', 'macosx_11_0_arm64')]
        for system, machine, suffix in cases:
            with self.subTest(system=system, machine=machine):
                selected = self.select(system, machine)
                self.assertEqual(len(selected), 8)
                native = [path for path in selected if not path.name.endswith('-any.whl')]
                self.assertEqual(len(native), 2)
                self.assertEqual({path.name.split('-')[0] for path in native}, {'mujoco', 'glfw'})
                self.assertTrue(all(suffix in path.name for path in native))
                self.assertFalse(any('numpy' in path.name or 'cp312' in path.name for path in selected))

    def test_unsupported_cpu_os_and_python_fail_before_extraction(self):
        for system, machine in [('darwin', 'x86_64'), ('win32', 'arm64'),
                                ('linux', 'aarch64'), ('freebsd', 'x86_64')]:
            with self.subTest(system=system, machine=machine), self.assertRaisesRegex(RuntimeError, 'No bundled MuJoCo'):
                self.select(system, machine)
        for version in [(3, 12), (3, 14)]:
            with self.assertRaisesRegex(RuntimeError, 'CPython 3.13'):
                dependency._selected_wheels(self.directory, system='linux', machine='x86_64', python_version=version)
        with mock.patch.object(dependency.sysconfig, 'get_config_var', return_value=1), self.assertRaisesRegex(RuntimeError, 'free-threaded'):
            self.select('linux', 'x86_64')
        with mock.patch.object(dependency.sys, 'maxsize', 2**31), self.assertRaisesRegex(RuntimeError, '64-bit'):
            self.select('win32', 'AMD64')

    def test_missing_selected_dependency_has_actionable_error(self):
        (self.directory / 'glfw-2.10.2-py2.py3-none-manylinux2014_x86_64.whl').unlink()
        with self.assertRaisesRegex(RuntimeError, 'dependencies are missing.*glfw.*Reinstall'):
            self.select('linux', 'x86_64')

    def test_cache_contains_only_selected_native_bytes_and_separates_architectures(self):
        caches = []
        with mock.patch.object(dependency.tempfile, 'gettempdir', return_value=self.temp.name):
            for system, machine in [('win32', 'AMD64'), ('linux', 'x86_64'), ('darwin', 'arm64')]:
                selected = self.select(system, machine)
                destination = dependency._cache_wheels(selected)
                caches.append(destination)
                expected = next(path.name for path in selected if path.name.startswith('mujoco-'))
                self.assertEqual((destination / 'mujoco/native.marker').read_text(), expected)
                self.assertFalse((destination / 'numpy').exists())
                self.assertTrue((destination / '.complete').is_file())
                self.assertEqual(dependency._cache_wheels(selected), destination)
            self.assertEqual(len(set(caches)), 3)
            old = caches[0]
            make_wheel(self.directory / 'mujoco-3.3.7-cp313-cp313-macosx_11_0_arm64.whl', payload='changed foreign bytes')
            self.assertEqual(dependency._cache_wheels(self.select('win32', 'AMD64')), old)
            make_wheel(self.directory / 'mujoco-3.3.7-cp313-cp313-win_amd64.whl', payload='changed selected bytes')
            self.assertNotEqual(dependency._cache_wheels(self.select('win32', 'AMD64')), old)

    def test_corrupt_or_escaping_wheel_never_marks_cache_complete(self):
        for member in ['../../escape', '/absolute', 'C:/outside', 'back\\slash']:
            with self.subTest(member=member), mock.patch.object(dependency.tempfile, 'gettempdir', return_value=self.temp.name):
                path = self.directory / 'bad.whl'
                make_wheel(path, member=member)
                with zipfile.ZipFile(path) as archive:
                    self.assertEqual(archive.infolist()[0].orig_filename, member)
                with self.assertRaisesRegex(RuntimeError, 'invalid path'):
                    dependency._cache_wheels([path])
        path = self.directory / 'corrupt.whl'
        path.write_bytes(b'not a ZIP')
        with mock.patch.object(dependency.tempfile, 'gettempdir', return_value=self.temp.name), self.assertRaisesRegex(RuntimeError, 'Cannot unpack'):
            dependency._cache_wheels([path])
        self.assertFalse(list(Path(self.temp.name).rglob('.complete')))
        self.assertFalse(list(Path(self.temp.name).rglob('escape')))

    def test_symbolic_link_wheel_is_rejected(self):
        path = self.directory / 'link.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            entry = zipfile.ZipInfo('glfw/native')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, '/outside')
        with mock.patch.object(dependency.tempfile, 'gettempdir', return_value=self.temp.name), self.assertRaisesRegex(RuntimeError, 'invalid path'):
            dependency._cache_wheels([path])


class PackagedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / 'glfw'
        for name in ['x11/libglfw.so', 'wayland/libglfw.so', 'libglfw.3.dylib']:
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test library; never loaded')
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, {}, clear=True).start()
        mock.patch.dict(sys.modules, {'bpy': object()}).start()
        self.find_spec = mock.patch.object(dependency.importlib.util, 'find_spec', return_value=SimpleNamespace(origin=str(self.package / '__init__.py'))).start()

    def test_embedded_linux_selects_package_x11_and_restores_environment_on_failure(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ValueError, 'failed import'):
                with dependency._packaged_glfw_library():
                    self.assertEqual(Path(os.environ['PYGLFW_LIBRARY']), self.package / 'x11/libglfw.so')
                    self.assertNotIn('MUJOCO_GL', os.environ)
                    raise ValueError('failed import')
        self.assertNotIn('PYGLFW_LIBRARY', os.environ)

    def test_wayland_session_and_explicit_variant_match_official_selection(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            os.environ['XDG_SESSION_TYPE'] = 'wayland'
            with dependency._packaged_glfw_library():
                self.assertEqual(Path(os.environ['PYGLFW_LIBRARY']), self.package / 'wayland/libglfw.so')
            os.environ['PYGLFW_LIBRARY_VARIANT'] = 'X11'
            with dependency._packaged_glfw_library():
                self.assertEqual(Path(os.environ['PYGLFW_LIBRARY']), self.package / 'x11/libglfw.so')

    def test_mac_library_and_empty_existing_setting_are_restored(self):
        os.environ['PYGLFW_LIBRARY'] = ''
        with mock.patch.object(sys, 'platform', 'darwin'):
            with dependency._packaged_glfw_library():
                self.assertEqual(Path(os.environ['PYGLFW_LIBRARY']), self.package / 'libglfw.3.dylib')
        self.assertEqual(os.environ['PYGLFW_LIBRARY'], '')

    def test_explicit_native_library_and_existing_consumer_are_left_alone(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            os.environ['PYGLFW_LIBRARY'] = 'consumer-choice'
            with dependency._packaged_glfw_library():
                self.assertEqual(os.environ['PYGLFW_LIBRARY'], 'consumer-choice')
            self.find_spec.assert_not_called()
            del os.environ['PYGLFW_LIBRARY']
            with mock.patch.dict(sys.modules, {'glfw': object()}):
                with dependency._packaged_glfw_library():
                    self.assertNotIn('PYGLFW_LIBRARY', os.environ)
            self.find_spec.assert_not_called()

    def test_a_consumer_environment_change_is_not_overwritten(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            with dependency._packaged_glfw_library():
                os.environ['PYGLFW_LIBRARY'] = 'new-consumer-choice'
        self.assertEqual(os.environ['PYGLFW_LIBRARY'], 'new-consumer-choice')

    def test_missing_library_does_not_launch_blender_as_python(self):
        (self.package / 'x11/libglfw.so').unlink()
        with mock.patch.object(sys, 'platform', 'linux'), self.assertRaisesRegex(RuntimeError, 'native library is missing'):
            with dependency._packaged_glfw_library():
                self.fail('Missing package must not reach the pyGLFW search')

    def test_mujoco_import_honors_render_backend_and_existing_engine(self):
        for backend in ['egl', 'osmesa', 'disable']:
            with self.subTest(backend=backend), mock.patch.object(sys, 'platform', 'linux'), mock.patch.dict(os.environ, {'MUJOCO_GL': backend}), mock.patch.object(dependency.importlib, 'import_module', return_value=engine()), mock.patch.object(dependency, '_packaged_glfw_library') as scoped:
                dependency._import_mujoco()
                scoped.assert_not_called()
                self.assertEqual(os.environ['MUJOCO_GL'], backend)
        with mock.patch.object(sys, 'platform', 'darwin'), mock.patch.object(dependency.importlib, 'import_module', return_value=engine()), mock.patch.object(dependency, '_packaged_glfw_library') as scoped:
            dependency._import_mujoco()
            scoped.assert_not_called()
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.dict(sys.modules, {'mujoco': engine()}), mock.patch.object(dependency.importlib, 'import_module', return_value=engine()), mock.patch.object(dependency, '_packaged_glfw_library') as scoped:
            dependency._import_mujoco()
            scoped.assert_not_called()

    def test_all_official_linux_glfw_backend_aliases_use_embedded_host_resolution(self):
        for backend in ['', 'glfw', 'glx', 'enable', 'enabled', 'on', 'true', '1', ' GLFW ']:
            with self.subTest(backend=backend), mock.patch.object(sys, 'platform', 'linux'), mock.patch.dict(os.environ, {'MUJOCO_GL': backend}), mock.patch.object(dependency.importlib, 'import_module', return_value=engine()), mock.patch.object(dependency, '_packaged_glfw_library') as scoped:
                dependency._import_mujoco()
                scoped.assert_called_once()
                self.assertEqual(os.environ['MUJOCO_GL'], backend)


class EngineTests(unittest.TestCase):
    def setUp(self):
        dependency._engine = None
        self.addCleanup(setattr, dependency, '_engine', None)

    def test_supported_installed_engine_is_reused_without_extraction_or_module_replacement(self):
        supplied = engine('3.5.0')
        with mock.patch.dict(sys.modules, {'numpy': object()}), mock.patch.object(dependency, '_import_mujoco', return_value=supplied) as load, mock.patch.object(dependency, '_host_allocator') as allocator, mock.patch.object(dependency, '_cache_wheels') as cache:
            self.assertIs(dependency.load_mujoco(), supplied)
            self.assertIs(dependency.load_mujoco(), supplied)
            load.assert_called_once()
            allocator.assert_called_once_with(supplied)
            cache.assert_not_called()

    def test_missing_secondary_dependency_and_native_load_failures_are_not_hidden(self):
        for failure, message in [(ModuleNotFoundError('missing', name='glfw'), 'dependency is missing: glfw'),
                                 (OSError('wrong architecture'), 'Cannot load.*wrong architecture')]:
            with self.subTest(error=failure), mock.patch.dict(sys.modules, {'numpy': object()}), mock.patch.object(dependency, '_import_mujoco', side_effect=failure), mock.patch.object(dependency, '_cache_wheels') as cache, self.assertRaisesRegex(RuntimeError, message):
                dependency.load_mujoco()
            cache.assert_not_called()

    def test_fallback_import_uses_selected_cache_and_preserves_blender_numpy(self):
        with temporary_directory() as folder:
            supplied = engine()
            numpy = object()
            location = str(Path(folder))
            with mock.patch.dict(sys.modules, {'numpy': numpy}), mock.patch.object(dependency, '_import_mujoco', side_effect=[ModuleNotFoundError('missing', name='mujoco'), supplied]), mock.patch.object(dependency, '_selected_wheels', return_value=('selected-native-wheel',)) as selected, mock.patch.object(dependency, '_cache_wheels', return_value=Path(folder)) as cache, mock.patch.object(dependency, '_host_allocator'):
                try:
                    self.assertIs(dependency.load_mujoco(), supplied)
                    self.assertIs(sys.modules['numpy'], numpy)
                    selected.assert_called_once()
                    cache.assert_called_once_with(('selected-native-wheel',))
                    self.assertIn(location, sys.path)
                finally:
                    if location in sys.path:
                        sys.path.remove(location)

    def test_version_native_pair_and_required_cpu_api_are_validated(self):
        with self.assertRaisesRegex(RuntimeError, 'loaded 9.0'):
            dependency._check_version(engine('9.0'))
        supplied = engine()
        supplied.mj_versionString = lambda: '3.5.0'
        with self.assertRaisesRegex(RuntimeError, 'native runtime 3.5.0'):
            dependency._check_version(supplied)
        supplied = engine()
        supplied.mj_step = None
        with self.assertRaisesRegex(RuntimeError, 'missing required CPU APIs: mj_step'):
            dependency._check_version(supplied)


class DistributionTests(unittest.TestCase):
    def test_actual_manifest_and_original_wheel_hashes_cover_only_supported_hosts(self):
        addon = ROOT / 'addons/yugo'
        manifest = tomllib.loads((addon / 'blender_manifest.toml').read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['platforms']), {'windows-x64', 'linux-x64', 'macos-arm64'})
        expected = set(dependency.COMMON_WHEELS) | set(NATIVE_SHA256)
        self.assertEqual({Path(name).name for name in manifest['wheels']}, expected)
        notice = (addon / 'node/NOTICE-MUJOCO.txt').read_text(encoding='utf-8')
        for filename, checksum in NATIVE_SHA256.items():
            path = addon / 'node/wheels' / filename
            with self.subTest(wheel=filename):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
                self.assertIn(checksum, notice)
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertTrue(any('LICENSE' in name.upper() for name in archive.namelist()))
                    metadata = next(name for name in archive.namelist() if name.endswith('.dist-info/WHEEL'))
                    text = archive.read(metadata).decode()
                    self.assertIn('cp313-cp313' if filename.startswith('mujoco-') else 'py3-none', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
