"""Windows keyboard events on a dedicated hook/message-loop thread.

The callback never touches Blender and always calls the next hook. The bounded
queue contains primitive key events only. Native state is sampled outside the
callback, because Windows updates asynchronous state after the hook returns.
"""
from collections import deque
from dataclasses import dataclass
import sys
import threading
import time


@dataclass(frozen=True)
class KeyEvent:
    sequence: int
    key: str
    down: bool
    repeat: bool
    timestamp: float
    reset_keys: tuple | None = None


_queue = deque()
_lock = threading.Lock()
_thread = None
_thread_id = 0
_ready = threading.Event()
_stop = threading.Event()
_running = False
_error = ""
_sequence = 0
_held = set()
_callback_error = ""
_callback_count = 0
MAX_EVENTS = 8192

VK_NAMES = {
    0x08: "BACK_SPACE", 0x09: "TAB", 0x0D: "RET", 0x1B: "ESC",
    0x20: "SPACE", 0x21: "PAGE_UP", 0x22: "PAGE_DOWN", 0x23: "END",
    0x24: "HOME", 0x25: "LEFT_ARROW", 0x26: "UP_ARROW",
    0x27: "RIGHT_ARROW", 0x28: "DOWN_ARROW", 0x2D: "INSERT", 0x2E: "DEL",
    0x5B: "LEFT_OSKEY", 0x5C: "RIGHT_OSKEY", 0x5D: "APP",
    0xA0: "LEFT_SHIFT", 0xA1: "RIGHT_SHIFT", 0xA2: "LEFT_CTRL",
    0xA3: "RIGHT_CTRL", 0xA4: "LEFT_ALT", 0xA5: "RIGHT_ALT",
    0x6A: "NUMPAD_ASTERIX", 0x6B: "NUMPAD_PLUS", 0x6D: "NUMPAD_MINUS",
    0x6E: "NUMPAD_PERIOD", 0x6F: "NUMPAD_SLASH",
    0xBA: "SEMI_COLON", 0xBB: "EQUAL", 0xBC: "COMMA", 0xBD: "MINUS",
    0xBE: "PERIOD", 0xBF: "SLASH", 0xC0: "ACCENT_GRAVE",
    0xDB: "LEFT_BRACKET", 0xDC: "BACK_SLASH", 0xDD: "RIGHT_BRACKET",
    0xDE: "QUOTE",
}
VK_NAMES.update({value: chr(value) for value in range(0x30, 0x3A)})
VK_NAMES.update({value: chr(value) for value in range(0x41, 0x5B)})
VK_NAMES.update({0x70 + index: f"F{index + 1}" for index in range(24)})
VK_NAMES.update({0x60 + index: f"NUMPAD_{index}" for index in range(10)})


def key_name(vk, scan=0, flags=0):
    if vk == 0x10:
        return "RIGHT_SHIFT" if scan == 0x36 else "LEFT_SHIFT"
    if vk == 0x11:
        return "RIGHT_CTRL" if flags & 1 else "LEFT_CTRL"
    if vk == 0x12:
        return "RIGHT_ALT" if flags & 1 else "LEFT_ALT"
    if vk == 0x0D and flags & 1:
        return "NUMPAD_ENTER"
    return VK_NAMES.get(vk, f"VK_{vk:02X}")


def _apis():
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    result = ctypes.c_ssize_t
    callback = ctypes.WINFUNCTYPE(result, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback, wintypes.HINSTANCE, wintypes.DWORD]
    user.SetWindowsHookExW.restype = wintypes.HANDLE
    user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user.CallNextHookEx.restype = result
    user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    user.UnhookWindowsHookEx.restype = wintypes.BOOL
    user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user.GetMessageW.restype = ctypes.c_int
    user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user.PeekMessageW.restype = wintypes.BOOL
    user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostThreadMessageW.restype = wintypes.BOOL
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    return ctypes, wintypes, user, kernel, callback


def snapshot():
    """Current physical keys, including keys already held before listening."""
    if sys.platform != "win32":
        return frozenset()
    _ctypes, _types, user, _kernel, _callback = _apis()
    return frozenset(key_name(vk) for vk in VK_NAMES if user.GetAsyncKeyState(vk) & 0x8000)


def _append(key, down, timestamp):
    global _sequence
    with _lock:
        repeat = down and key in _held
        if down:
            _held.add(key)
        else:
            _held.discard(key)
        _sequence += 1
        if len(_queue) >= MAX_EVENTS:
            # Reset rather than dropping a release and leaving a stuck key.
            _queue.clear()
            _queue.append(KeyEvent(_sequence, "", False, False, timestamp, tuple(_held)))
        else:
            _queue.append(KeyEvent(_sequence, key, down, repeat, timestamp))


def _message_loop():
    global _running, _thread_id, _error
    hook = None
    try:
        ctypes, types, user, kernel, callback_type = _apis()

        class KeyboardStruct(ctypes.Structure):
            _fields_ = [("vkCode", types.DWORD), ("scanCode", types.DWORD),
                        ("flags", types.DWORD), ("time", types.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        @callback_type
        def callback(code, message, pointer):
            global _callback_error, _callback_count
            _callback_count += 1
            if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                try:
                    item = ctypes.cast(pointer, ctypes.POINTER(KeyboardStruct)).contents
                    _append(key_name(item.vkCode, item.scanCode, item.flags),
                            message in (0x100, 0x104), time.monotonic())
                except Exception as error:
                    # A hook exception must never suppress another application's input.
                    _callback_error = str(error)
            return user.CallNextHookEx(None, code, message, pointer)

        message = types.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        _thread_id = kernel.GetCurrentThreadId()
        with _lock:
            _held.update(snapshot())
        hook = user.SetWindowsHookExW(13, callback, kernel.GetModuleHandleW(None), 0)
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        _running = True
        _ready.set()
        while not _stop.is_set():
            value = user.GetMessageW(ctypes.byref(message), None, 0, 0)
            if value <= 0:
                break
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
    except Exception as error:
        _error = str(error)
    finally:
        if hook:
            user.UnhookWindowsHookEx(hook)
        _running = False
        _thread_id = 0
        _ready.set()


def start():
    global _thread, _error
    if is_running():
        return True
    if sys.platform != "win32":
        _error = "Global keyboard listening requires Windows"
        return False
    stop()
    _error = ""
    _stop.clear()
    _ready.clear()
    _thread = threading.Thread(target=_message_loop, name="Yugu global keyboard", daemon=True)
    _thread.start()
    _ready.wait(2.0)
    if not _running:
        stop()
    return _running


def stop():
    global _thread
    _stop.set()
    if _thread_id and sys.platform == "win32":
        _ctypes, _types, user, _kernel, _callback = _apis()
        user.PostThreadMessageW(_thread_id, 0x12, 0, 0)
    if _thread is not None and _thread is not threading.current_thread():
        _thread.join(2.0)
        if not _thread.is_alive():
            _thread = None
    with _lock:
        _queue.clear()
        _held.clear()


def drain():
    with _lock:
        values = list(_queue)
        _queue.clear()
    return values


def is_running():
    return _running and _thread is not None and _thread.is_alive()


def status():
    return "Listening · passthrough" if is_running() else (_error or "Stopped")
