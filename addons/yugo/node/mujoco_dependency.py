"""Load the official engine, including wheels in an unpacked development tree.

Blender's extension installer normally installs the manifest wheels. The local
source checkout also works without modifying Blender's Python installation.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import stat
import sys
import sysconfig
import tempfile
import zipfile

_engine = None
SUPPORTED_VERSIONS = frozenset({'3.3.7', '3.5.0'})
REQUIRED_API = (
    'MjModel', 'MjData', 'mj_forward', 'mj_step', 'mj_resetData',
    'mj_name2id', 'mj_objectVelocity', 'mj_versionString',
)
COMMON_WHEELS = (
    'absl_py-2.5.0-py3-none-any.whl',
    'etils-1.14.0-py3-none-any.whl',
    'fsspec-2026.9.0-py3-none-any.whl',
    'pyopengl-3.1.10-py3-none-any.whl',
    'typing_extensions-4.16.0-py3-none-any.whl',
    'zipp-4.1.1-py3-none-any.whl',
)
NATIVE_WHEELS = {
    ('win32', 'x86_64'): (
        'mujoco-3.3.7-cp313-cp313-win_amd64.whl',
        'glfw-2.10.2-py2.py3-none-win_amd64.whl',
    ),
    ('linux', 'x86_64'): (
        'mujoco-3.3.7-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        'glfw-2.10.2-py2.py3-none-manylinux2014_x86_64.whl',
    ),
    ('darwin', 'arm64'): (
        'mujoco-3.3.7-cp313-cp313-macosx_11_0_arm64.whl',
        'glfw-2.10.2-py2.py3-none-macosx_11_0_arm64.whl',
    ),
}


def _selected_wheels(directory, *, system=None, machine=None, python_version=None):
    """Select this release's exact ABI/architecture pair and pure dependencies."""
    system = sys.platform if system is None else system
    machine = platform.machine() if machine is None else machine
    machine = machine.lower()
    machine = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    python_version = sys.version_info[:2] if python_version is None else python_version
    if (tuple(python_version) != (3, 13) or sys.implementation.name != 'cpython'
            or sys.maxsize <= 2**32 or sysconfig.get_config_var('Py_GIL_DISABLED')):
        raise RuntimeError('The bundled MuJoCo engine requires 64-bit CPython 3.13 '
                           '(Blender 5.1 / 5.2), without the free-threaded ABI')
    native = NATIVE_WHEELS.get((system, machine))
    if native is None:
        raise RuntimeError(f'No bundled MuJoCo engine for {system} / {machine}. '
                           'Supported hosts: Windows x64, Linux x64 and Apple Silicon macOS')
    wheels = tuple(sorted(Path(directory) / name for name in (*COMMON_WHEELS, *native)))
    missing = [path.name for path in wheels if not path.is_file()]
    if missing:
        raise RuntimeError('Bundled MuJoCo dependencies are missing: ' + ', '.join(missing)
                           + '. Reinstall the complete Yugo extension')
    return wheels


def _cache_wheels(wheels):
    """Cache only selected wheels; foreign native packages never share a cache."""
    digest = hashlib.sha256()
    for path in wheels:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    destination = Path(tempfile.gettempdir()) / 'yugo_node_mujoco' / digest.hexdigest()[:20]
    marker = destination / '.complete'
    if not marker.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        try:
            for path in wheels:
                with zipfile.ZipFile(path) as archive:
                    root = destination.resolve()
                    for member in archive.infolist():
                        # ZipInfo normalizes backslashes on Windows; inspect
                        # the original archive name before trusting that path.
                        name = member.orig_filename
                        target = (root / name).resolve()
                        if (not name or name != member.filename or '\\' in name or ':' in name
                                or not target.is_relative_to(root)
                                or stat.S_ISLNK(member.external_attr >> 16)):
                            raise RuntimeError('MuJoCo wheel contains an invalid path')
                    archive.extractall(destination)
        except (OSError, zipfile.BadZipFile) as error:
            raise RuntimeError(f'Cannot unpack bundled MuJoCo dependencies: {error}') from error
        marker.write_text('Official MuJoCo wheel runtime\n', encoding='utf-8')
    return destination


@contextmanager
def _packaged_glfw_library():
    """Avoid pyGLFW launching Blender as Python during its library search.

    Select only the library next to the already-resolved Python package. Honor
    explicit user settings and loaded consumers, and restore our temporary
    setting even when import fails. The MuJoCo rendering backend is untouched.
    """
    if (sys.platform not in {'linux', 'darwin'} or 'bpy' not in sys.modules
            or 'glfw' in sys.modules or os.environ.get('PYGLFW_LIBRARY')):
        yield
        return
    spec = importlib.util.find_spec('glfw')
    if spec is None or not spec.origin:
        yield
        return
    package = Path(spec.origin).resolve().parent
    if sys.platform == 'darwin':
        library = package / 'libglfw.3.dylib'
    else:
        variant = os.environ.get('PYGLFW_LIBRARY_VARIANT', '').lower()
        if variant not in {'x11', 'wayland'}:
            variant = 'wayland' if os.environ.get('XDG_SESSION_TYPE') == 'wayland' else 'x11'
        library = package / variant / 'libglfw.so'
    if not library.is_file() or not library.resolve().is_relative_to(package):
        raise RuntimeError('The GLFW native library is missing from its Python package: '
                           f'{library}. Reinstall the complete Yugo extension')
    value = str(library)
    previous = os.environ.get('PYGLFW_LIBRARY')
    os.environ['PYGLFW_LIBRARY'] = value
    try:
        yield
    finally:
        # Respect a consumer that deliberately changed the setting during import.
        if os.environ.get('PYGLFW_LIBRARY') == value:
            if previous is None:
                os.environ.pop('PYGLFW_LIBRARY', None)
            else:
                os.environ['PYGLFW_LIBRARY'] = previous


def _import_mujoco():
    if 'mujoco' in sys.modules:
        return importlib.import_module('mujoco')
    # macOS uses CGL; Linux may have an explicitly selected EGL/OSMesa backend.
    # Only the default Linux GLFW import needs our embedded-host workaround.
    backend = os.environ.get('MUJOCO_GL', '').lower().strip()
    if sys.platform == 'linux' and backend in {'', 'glfw', 'glx', 'enable', 'enabled', 'on', 'true', '1'}:
        with _packaged_glfw_library():
            return importlib.import_module('mujoco')
    return importlib.import_module('mujoco')


def _host_allocator(engine):
    """Use MuJoCo's official paired hooks for Blender's Windows CRT host.

    Blender's host allocator can intercept the wheel's aligned allocation.
    Explicit UCRT allocation preserves MuJoCo's required 64-byte alignment.
    Keep callbacks pinned on the engine module for native model lifetimes,
    including extension reloads. Existing application hooks remain authoritative.
    """
    if sys.platform != "win32" or "bpy" not in sys.modules:
        return
    if (engine.get_mju_user_malloc() is not None
            or engine.get_mju_user_free() is not None):
        return
    try:
        probe = engine.MjModel.from_xml_string("<mujoco/>")
    except Exception as error:
        if "Could not allocate memory" not in str(error):
            raise RuntimeError(f"Cannot initialize MuJoCo in Blender: {error}") from error
    else:
        del probe
        return
    import ctypes
    crt = ctypes.CDLL("ucrtbase.dll")
    allocate = crt._aligned_malloc
    allocate.argtypes = (ctypes.c_size_t, ctypes.c_size_t)
    allocate.restype = ctypes.c_void_p
    release = crt._aligned_free
    release.argtypes = (ctypes.c_void_p,)
    release.restype = None
    malloc_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t)
    free_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    malloc_hook = malloc_type(lambda size: allocate(max(int(size), 64), 64))
    free_hook = free_type(lambda pointer: release(pointer))
    engine._yugo_allocator_refs = (crt, allocate, release, malloc_hook, free_hook)
    engine.set_mju_user_malloc(malloc_hook)
    engine.set_mju_user_free(free_hook)


def load_mujoco():
    global _engine
    if _engine is not None:
        _check_version(_engine)
        return _engine
    # Use Blender's NumPy first, so importing the physics engine cannot replace
    # an ABI dependency already in use by Blender or another extension.
    import numpy  # noqa: F401
    try:
        engine = _import_mujoco()
        _check_version(engine)
        _host_allocator(engine)
        _engine = engine
        return engine
    except ModuleNotFoundError as error:
        if error.name != "mujoco":
            raise RuntimeError(f"MuJoCo dependency is missing: {error.name}") from error
    except (ImportError, OSError) as error:
        raise RuntimeError(f"Cannot load the official MuJoCo engine: {error}") from error
    wheel_dir = Path(__file__).with_name("wheels")
    destination = _cache_wheels(_selected_wheels(wheel_dir))
    location = str(destination)
    if location not in sys.path:
        sys.path.append(location)
    importlib.invalidate_caches()
    try:
        engine = _import_mujoco()
        _check_version(engine)
        _host_allocator(engine)
    except (ImportError, OSError) as error:
        raise RuntimeError(f"Cannot load the official MuJoCo engine: {error}") from error
    _engine = engine
    return engine


def _check_version(engine):
    """Reuse a supported live module; never unload another extension's engine."""
    version = str(getattr(engine, '__version__', 'unknown'))
    if version not in SUPPORTED_VERSIONS:
        raise RuntimeError('Yugo Node supports CPU MuJoCo 3.3.7 and 3.5.0; '
                           f'loaded {version} instead')
    missing = [name for name in REQUIRED_API if not callable(getattr(engine, name, None))]
    model = getattr(engine, 'MjModel', None)
    if not callable(getattr(model, 'from_xml_string', None)):
        missing.append('MjModel.from_xml_string')
    if getattr(getattr(engine, 'mjtObj', None), 'mjOBJ_BODY', None) is None:
        missing.append('mjtObj.mjOBJ_BODY')
    if sys.platform == 'win32' and 'bpy' in sys.modules:
        missing.extend(name for name in ('get_mju_user_malloc', 'get_mju_user_free',
                                         'set_mju_user_malloc', 'set_mju_user_free')
                       if not callable(getattr(engine, name, None)))
    if missing:
        raise RuntimeError(f'MuJoCo {version} is missing required CPU APIs: {", ".join(missing)}')
    native_version = engine.mj_versionString()
    if isinstance(native_version, bytes):
        native_version = native_version.decode('ascii')
    if native_version != version:
        raise RuntimeError(f'MuJoCo Python {version} uses native runtime {native_version}; '
                           'restart Blender with a matching MuJoCo runtime')
