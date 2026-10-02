"""Real Blender input graphs plus the Windows hook/message-loop smoke test."""
from pathlib import Path
from types import SimpleNamespace
import ctypes
import os
import sys
import time

import bpy
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, input_nodes, input_runtime as inputs, global_keyboard as backend, scheduler
from yugu_node_studio.constants import TREE_ID


def fallback_emit(tree, source, output_name, payload, now=None):
    pulse = flow._Pulse(tree, bpy.context.scene, now, 0.0, set(), payload=payload)
    pulse.emit(source.outputs[output_name])
    return pulse.run_id


class Graph:
    def __init__(self, name):
        inputs.clear()
        flow.clear()
        self.tree = bpy.data.node_groups.new(name, TREE_ID)
        self.records = []
        self.original_emit = getattr(flow, "emit_event", None)
        dispatch = self.original_emit or fallback_emit

        def emit(tree, source, output_name, payload, now=None):
            self.records.append((source.name, output_name, dict(payload), now))
            return dispatch(tree, source, output_name, payload, now=now)

        flow.emit_event = emit
        self.keyboard = self.node("keyboard_data")
        assert not self.keyboard.listen
        self.keyboard.listen = True
        self.at(0)

    def close(self):
        inputs.clear()
        flow.clear()
        if self.original_emit is None:
            del flow.emit_event
        else:
            flow.emit_event = self.original_emit
        bpy.data.node_groups.remove(self.tree)

    def node(self, kind):
        return self.tree.nodes.new("YUGUS_NODE_" + kind)

    def link(self, source, output, target, input):
        return self.tree.links.new(source.outputs[output], target.inputs[input])

    def key(self, key="A", shortcut=None, mode=None, allowed=""):
        node = self.node("key_state")
        node.key = key
        if shortcut is not None:
            node.match_mode = "SHORTCUT"
            node.shortcut = shortcut
        node.allowed_modes = allowed
        self.link(self.keyboard, "Data", node, "Keyboard")
        if mode is not None:
            self.link(mode, "Mode", node, "Mode")
        return node

    def events(self, source):
        node = self.node("state_events")
        self.link(source, "State", node, "State")
        return node

    def at(self, now, *events, delta=1 / 60):
        for key, down in events:
            inputs.feed_event(key, down, now)
        inputs.tick(bpy.context, delta, now=now, manage_backend=False)

    def value(self, node, name="State", now=100):
        return flow.PullContext(self.tree, bpy.context.scene, now, 0).evaluate(node).get(name)

    def edges(self, node):
        return [(output, data.get("key"), data.get("state")) for name, output, data, _now in self.records if name == node.name and output != "Changed"]


def rapid_and_repeat_test():
    graph = Graph("Input rapid edges")
    try:
        key = graph.key()
        event = graph.events(key)
        graph.at(0)
        graph.at(0.1, ("A", True), ("A", False))
        assert graph.edges(event) == [("On", "A", True), ("Off", "A", False)]
        assert not graph.value(key)
        ids = [data["event_id"] for name, output, data, _ in graph.records if name == event.name and output != "Changed"]
        assert len(set(ids)) == 2
        graph.at(0.2, ("A", True), ("A", True), ("A", True))
        assert graph.edges(event)[-1] == ("On", "A", True) and len(graph.edges(event)) == 3
        key.monitor_open = True  # Opening a monitor is not a new key press.
        graph.at(0.3)
        assert len(graph.edges(event)) == 3
        graph.at(0.4, ("A", False))
        assert len(graph.edges(event)) == 4
        print("INPUT_RAPID_REPEAT_OK")
    finally:
        graph.close()


def shortcut_and_logic_test():
    graph = Graph("Input chords and logic")
    try:
        raw = graph.key()
        plain = graph.key(shortcut="A")
        chord = graph.key(shortcut="Ctrl+A")
        loose = graph.key(shortcut="Ctrl+A")
        loose.exact_modifiers = False
        second = graph.key("B")
        logic = graph.node("state_logic")
        graph.link(raw, "State", logic, "A")
        graph.link(second, "State", logic, "B")
        event = graph.events(logic)
        graph.at(0)
        graph.at(0.1, ("LEFT_CTRL", True), ("A", True))
        assert graph.value(raw) and graph.value(chord) and not graph.value(plain)
        graph.at(0.2, ("B", True))
        assert graph.value(chord) and graph.value(logic)
        graph.at(0.3, ("LEFT_SHIFT", True))
        assert not graph.value(chord) and graph.value(loose)
        graph.at(0.4, ("LEFT_CTRL", False))
        assert graph.value(raw) and not graph.value(chord)
        graph.at(0.5, ("B", False), ("A", False), ("LEFT_SHIFT", False))
        assert graph.edges(event) == [("On", "B", True), ("Off", "B", False)]
        logic.operation = "OR"
        graph.at(0.6, ("A", True))
        assert graph.value(logic)
        logic.operation = "XOR"
        graph.at(0.7, ("B", True))
        assert not graph.value(logic)
        logic.operation = "NOT"
        graph.at(0.8, ("A", False))
        assert graph.value(logic)
        print("INPUT_CHORD_LOGIC_OK")
    finally:
        graph.close()


def toggle_and_value_test():
    graph = Graph("Input event memory and easing")
    try:
        key = graph.key()
        event = graph.events(key)
        toggle = graph.node("state_toggle")
        graph.link(event, "On", toggle, "Toggle")
        value = graph.node("state_value")
        value.attack = value.release = 1
        value.on_value = -2
        graph.link(toggle, "State", value, "State")
        graph.at(0)
        graph.at(0.1, ("A", True), delta=0.25)
        assert graph.value(toggle), graph.tree.last_error
        assert abs(graph.value(value, "Value") + 0.5) < 1e-6
        context = flow.PullContext(graph.tree, bpy.context.scene, 0.1, 0)
        assert context.evaluate(toggle)["State"]
        assert inputs.handle_action(toggle, "Reset", context, 0.1)
        assert not context.evaluate(toggle)["State"], "Action did not invalidate cached state"
        graph.at(0.2, ("A", False), ("A", True), delta=0.25)
        assert graph.value(toggle)
        for _ in range(4):
            assert graph.value(value, "Value") == -1.0, "Pull accelerated Attack"
        graph.at(0.3, ("A", False), ("A", True), delta=0.25)
        assert not graph.value(toggle)
        assert graph.value(value, "Value") == -0.5
        toggle.mute = True
        assert not graph.value(toggle)
        print("INPUT_TOGGLE_VALUE_OK")
    finally:
        graph.close()


def mode_scope_and_lock_test():
    graph = Graph("Input scoped lock and fresh press")
    try:
        actor = graph.node("control_mode")
        actor.modes = "Idle, Stage 1, Stage 2"
        light = graph.node("control_mode")
        key = graph.key(mode=actor, allowed="Stage 1")
        independent = graph.key("L", mode=light)
        event = graph.events(key)
        light_event = graph.events(independent)
        match = graph.node("mode_match")
        match.mode_name = "Stage 1"
        graph.link(actor, "Mode", match, "Mode")
        graph.at(0)
        graph.at(0.1, ("A", True))  # Forbidden mode: discarded, never queued.
        inputs.set_mode(actor, "Stage 1", now=0.2)
        assert graph.value(match) and not graph.value(key)
        assert not graph.edges(event)
        graph.at(0.3, ("A", False), ("A", True))
        assert graph.edges(event) == [("On", "A", True)]
        inputs.set_locked(actor, True, now=0.4)
        graph.at(0.5, ("A", False), ("A", True), ("L", True))
        assert graph.value(independent) and not graph.value(key)
        assert graph.edges(light_event) == [("On", "L", True)]
        inputs.set_locked(actor, False, now=0.6)
        count = len(graph.edges(event))
        graph.at(0.7)
        assert not graph.value(key) and len(graph.edges(event)) == count
        graph.at(0.8, ("A", False), ("A", True))
        assert graph.value(key) and graph.edges(event)[-1][0] == "On"
        assert inputs.cycle_mode(actor, now=0.9).name == "Stage 2"
        assert not graph.value(match)
        setter = graph.node("set_mode")
        setter.mode_name = "Stage 1"
        graph.link(actor, "Mode", setter, "Mode")
        context = flow.PullContext(graph.tree, bpy.context.scene, 1, 0)
        assert inputs.handle_action(setter, "Enter", context, 1)
        assert inputs.mode_state(actor).name == "Stage 1"
        print("INPUT_SCOPED_LOCK_OK")
    finally:
        graph.close()


def topology_lifecycle_and_cycle_test():
    graph = Graph("Input topology and cleanup")
    try:
        key = graph.key()
        event = graph.events(key)
        graph.at(0)
        graph.at(0.1, ("A", True))
        count = len(graph.edges(event))
        graph.tree.links.remove(event.inputs["State"].links[0])
        graph.at(0.2)
        graph.link(key, "State", event, "State")
        graph.at(0.3)
        assert len(graph.edges(event)) == count, "Reconnect held state produced a fresh On"
        graph.keyboard.mute = True
        assert not graph.value(key)
        graph.at(0.4)
        graph.keyboard.mute = False
        graph.at(0.5)
        assert not graph.value(key), "Re-enabled source accepted already-held key"
        graph.at(0.6, ("A", False), ("A", True))
        assert graph.value(key)
        definition = bpy.data.node_groups.new("Input definition", TREE_ID)
        definition.is_workflow_definition = True
        try:
            source = definition.nodes.new("YUGUS_NODE_keyboard_data")
            source.listen = True
            graph.at(0.7)
            assert str(source.runtime_id) not in inputs._frames, "Definition auto-polled"
        finally:
            bpy.data.node_groups.remove(definition)
        first = graph.node("control_mode")
        second = graph.node("control_mode")
        original = flow.emit_event
        calls = []

        def cycle_emit(tree, node, output, payload, now=None):
            if output == "Changed":
                calls.append(node.name)
                inputs.cycle_mode(second if node == first else first, now)

        flow.emit_event = cycle_emit
        try:
            try:
                inputs.cycle_mode(first, now=1)
            except ValueError as error:
                assert "cycle" in str(error)
            else:
                raise AssertionError("Mode event cycle was not bounded")
            assert len(calls) == inputs.MAX_EVENT_DEPTH
            assert inputs._emit_depth == 0
        finally:
            flow.emit_event = original
        inputs.clear()
        assert not graph.keyboard.listen and not inputs._down and not inputs._frames
        assert not inputs._modes and not inputs._toggles and not backend.is_running()
        print("INPUT_LIFECYCLE_CYCLE_OK")
    finally:
        graph.close()


def event_fan_in_test():
    graph = Graph("Input event fan-in")
    try:
        first = graph.events(graph.key("A"))
        second = graph.events(graph.key("B"))
        toggle = graph.node("state_toggle")
        mode = graph.node("control_mode")
        setter = graph.node("set_mode")
        for node in (toggle, mode, setter):
            for socket in node.inputs:
                if socket.bl_idname == input_nodes.EVENT:
                    assert socket.link_limit == 4095
        for source in (first, second):
            graph.link(source, "On", toggle, "Toggle")
            graph.link(source, "On", mode, "Cycle")
        assert len(toggle.inputs["Toggle"].links) == 2
        assert len(mode.inputs["Cycle"].links) == 2
        graph.at(0)
        graph.at(0.1, ("A", True), ("B", True))
        assert not graph.value(toggle), "Two event sources did not independently toggle memory"
        assert inputs.mode_state(mode).name == "Stage 2"
        print("INPUT_EVENT_FAN_IN_OK")
    finally:
        graph.close()


def windows_hook_test():
    if os.environ.get("YUGUS_TEST_NATIVE_KEYBOARD") != "1":
        print("INPUT_NATIVE_KEYBOARD_SKIPPED: opt in with YUGUS_TEST_NATIVE_KEYBOARD=1")
        return
    if sys.platform != "win32":
        print("INPUT_WINDOWS_HOOK_SKIPPED_NONWINDOWS")
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    from ctypes import wintypes as types
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentThreadId.restype = types.DWORD
    user.GetThreadDesktop.argtypes = [types.DWORD]
    user.GetThreadDesktop.restype = types.HANDLE
    user.OpenInputDesktop.argtypes = [types.DWORD, types.BOOL, types.DWORD]
    user.OpenInputDesktop.restype = types.HANDLE
    user.GetUserObjectInformationW.argtypes = [types.HANDLE, ctypes.c_int, types.LPVOID, types.DWORD, ctypes.POINTER(types.DWORD)]
    user.GetUserObjectInformationW.restype = types.BOOL
    user.CloseDesktop.argtypes = [types.HANDLE]

    def desktop_name(handle):
        buffer = ctypes.create_unicode_buffer(128)
        required = types.DWORD()
        assert user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(required))
        return buffer.value

    input_desktop = user.OpenInputDesktop(0, False, 1)
    assert input_desktop
    try:
        same_desktop = desktop_name(user.GetThreadDesktop(kernel.GetCurrentThreadId())) == desktop_name(input_desktop)
    finally:
        user.CloseDesktop(input_desktop)
    if not same_desktop:
        assert backend.start(), backend.status()
        assert backend.is_running() and backend._thread_id
        backend.stop()
        print("INPUT_WINDOWS_HOOK_INSTALLED_CAPTURE_SKIPPED_DIFFERENT_SANDBOX_DESKTOP")
        return
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    class KeyboardInput(ctypes.Structure):
        _fields_ = [("vk", types.WORD), ("scan", types.WORD), ("flags", types.DWORD),
                    ("time", types.DWORD), ("extra", ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", types.LONG), ("dy", types.LONG), ("data", types.DWORD),
                    ("flags", types.DWORD), ("time", types.DWORD), ("extra", ctypes.c_size_t)]

    class InputUnion(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", types.DWORD), ("value", InputUnion)]

    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    user.SendInput.argtypes = [types.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user.SendInput.restype = types.UINT

    def send(up=False):
        item = Input(1, InputUnion(keyboard=KeyboardInput(0x87, 0, 2 if up else 0, 0, 0)))
        assert user.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) == 1, ctypes.get_last_error()
    # F24 avoids text entry or ordinary application shortcuts.
    vk = 0x87
    assert not user.GetAsyncKeyState(vk) & 0x8000, "F24 physically held; cannot run isolated test"
    try:
        assert backend.start(), backend.status()
        assert backend._thread_id and backend.is_running()
        backend.drain()
        send()
        deadline = time.monotonic() + 2
        observed = []
        while time.monotonic() < deadline:
            observed.extend(backend.drain())
            if any(item.key == "F24" and item.down for item in observed):
                break
            time.sleep(0.01)
        assert any(item.key == "F24" and item.down for item in observed), observed
        # Async key state is set only after the hook returns. Nonzero proves
        # this callback did not swallow the key or break the next hook chain.
        assert user.GetAsyncKeyState(vk) & 0x8000, "Global listener swallowed F24"
        assert "F24" in backend.snapshot()
        send(True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            observed.extend(backend.drain())
            if any(item.key == "F24" and not item.down for item in observed):
                break
            time.sleep(0.01)
        events = [item for item in observed if item.key == "F24"]
        assert [item.down for item in events] == [True, False], events
        assert events[0].sequence < events[1].sequence
        assert not user.GetAsyncKeyState(vk) & 0x8000
        backend.stop()
        assert not backend.is_running() and not backend.drain()
        assert backend.start(), "Native listener could not restart"
        backend.stop()
        print("INPUT_WINDOWS_GLOBAL_PASSTHROUGH_OK")
    finally:
        send(True)
        backend.stop()


with RestrictBlend():
    addon.register()
try:
    assert scheduler._motion_callbacks["input_values"][0] == 5
    for test in (rapid_and_repeat_test, shortcut_and_logic_test, toggle_and_value_test,
                 mode_scope_and_lock_test, topology_lifecycle_and_cycle_test, event_fan_in_test, windows_hook_test):
        test()
    print("YUGU_INPUT_NODES_OK")
finally:
    addon.unregister()
