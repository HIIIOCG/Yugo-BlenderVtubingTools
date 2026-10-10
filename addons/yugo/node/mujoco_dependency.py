"""Load the official engine, including wheels in an unpacked development tree.

Blender's extension installer normally installs the manifest wheels. The local
source checkout also works without modifying Blender's Python installation.
"""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
import sys
import tempfile
import zipfile

_engine = None
SUPPORTED_VERSIONS = frozenset({'3.3.7', '3.5.0'})
REQUIRED_API = (
    'MjModel', 'MjData', 'mj_forward', 'mj_step', 'mj_resetData',
    'mj_name2id', 'mj_objectVelocity', 'mj_versionString',
)


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
        engine = importlib.import_module("mujoco")
        _check_version(engine)
        _host_allocator(engine)
        _engine = engine
        return engine
    except ModuleNotFoundError as error:
        if error.name != "mujoco":
            raise RuntimeError(f"MuJoCo dependency is missing: {error.name}") from error
    wheel_dir = Path(__file__).with_name("wheels")
    wheels = sorted(wheel_dir.glob("*.whl"))
    engine_wheels = [path for path in wheels if path.name.startswith("mujoco-")]
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if (sys.platform != "win32" or not engine_wheels
            or not any(f"-{tag}-{tag}-win_amd64" in path.name for path in engine_wheels)):
        raise RuntimeError("MuJoCo needs a compatible official wheel. Install the "
                            "Yugo Node for Windows / Blender 5.1–5.2.")
    digest = hashlib.sha256()
    for path in wheels:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    destination = Path(tempfile.gettempdir()) / "yugo_node_mujoco" / digest.hexdigest()[:20]
    marker = destination / ".complete"
    if not marker.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        for path in wheels:
            if path.name.startswith("numpy-"):
                continue
            with zipfile.ZipFile(path) as archive:
                # Only local packaged wheels are used; verify paths before
                # extraction rather than accepting paths outside the cache.
                root = destination.resolve()
                for member in archive.namelist():
                    target = (destination / member).resolve()
                    if not target.is_relative_to(root):
                        raise RuntimeError("MuJoCo wheel contains an invalid path")
                archive.extractall(destination)
        marker.write_text("Official MuJoCo wheel runtime\n", encoding="utf-8")
    location = str(destination)
    if location not in sys.path:
        sys.path.append(location)
    importlib.invalidate_caches()
    try:
        engine = importlib.import_module("mujoco")
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
