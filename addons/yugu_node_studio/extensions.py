"""Public feature declarations, invocation adapters, and transform ownership.

Only the core keeps workflow tickets. Callback contexts are main-thread, one-call
views; action state and originating event payloads are strict JSON snapshots.
"""

from dataclasses import dataclass, field
import importlib
import json
import math
import pkgutil
import time
import uuid

import bpy

from . import scheduler
from .workflow_nodes import _Action as ActionNodeMixin


@dataclass(frozen=True)
class ActionResult:
    status: str
    state: object = None
    error: str = ""

    @classmethod
    def running(cls, state):
        return cls("running", state)

    @classmethod
    def end(cls):
        return cls("end")

    @classmethod
    def failed(cls, error):
        return cls("failed", error=str(error))

    @classmethod
    def cancelled(cls):
        return cls("cancelled")


@dataclass(frozen=True)
class ActionSpec:
    start: object
    update: object = None
    cancel: object = None
    inputs: tuple = ("Enter",)
    configuration: object = None


@dataclass(frozen=True)
class CardSpec:
    label: str
    description: str = ""
    draw: object = None


@dataclass(frozen=True)
class ModuleSpec:
    module_id: str
    label: str = ""
    dependencies: tuple = ()
    classes: tuple = ()
    evaluators: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)
    menu_entries: tuple = ()
    cards: dict = field(default_factory=dict)
    motion: object = None
    system: object = None
    on_register: object = None
    on_unregister: object = None
    cleanup: object = None
    recover: object = None


class ResourceConflict(ValueError):
    """Another public feature owner already controls a requested transform."""


_modules = {}
_evaluators = {}
_actions = {}
_classes = {}
_claims = {}
_epoch = uuid.uuid4().hex
_lifecycle_errors = []
_pending_recovery = {}


def _record_error(source, error):
    _lifecycle_errors.append({"source": source, "error": str(error)})
    del _lifecycle_errors[:-64]
    print(f"Studio cleanup {source} failed: {error}")


def lifecycle_errors():
    return json_snapshot(_lifecycle_errors)


def _cleanup_call(source, callback, *args):
    if callable(callback):
        try:
            callback(*args)
        except Exception as error:
            _record_error(source, error)


def json_snapshot(value):
    """Copy strict, finite JSON without coercing tuples, keys, or RNA handles."""
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise TypeError("JSON keys must be strings")
            for child in item.values():
                check(child)
            return
        raise TypeError("Action payload and state must contain only JSON data")

    encoded = json.dumps(value, allow_nan=False)
    check(value)
    return json.loads(encoded)


def object_ref(obj):
    """A JSON reference safe to retain across callbacks; no Blender RNA."""
    if not isinstance(obj, bpy.types.ID):
        raise TypeError("A Blender data block is required")
    return {"pointer": int(obj.as_pointer()), "session_uid": int(obj.session_uid)}


def _resolve(reference, database):
    if not reference:
        return None
    return next((item for item in database
                 if item.session_uid == reference["session_uid"]
                 and item.as_pointer() == reference["pointer"]), None)


def resolve_object(reference):
    return _resolve(reference, bpy.data.objects)


def node_owner(module_id, node, scene=None):
    """Stable owner for a module's persistent node service in this lifecycle."""
    if module_id not in _modules:
        raise ValueError("Register the module before creating its owner")
    scene_uid = int(scene.session_uid) if scene is not None else 0
    return f"{_epoch}:{module_id}:{node.id_data.session_uid}:{node.runtime_id}:{scene_uid}"


def _prune_claims():
    live = {(obj.session_uid, obj.as_pointer()) for obj in bpy.data.objects}
    for key in tuple(_claims):
        if key[0] != _epoch or key[1:3] not in live:
            _claims.pop(key, None)


def claim_transforms(owner, objects):
    """Atomically claim all object transforms, or leave every claim unchanged.

    This protocol arbitrates participating feature writers. It does not change
    transforms or arbitrate the existing bone/shape-key Apply protocol.
    """
    if not isinstance(owner, str) or not owner:
        raise ValueError("A non-empty owner is required")
    requested = set()
    for obj in objects:
        if not isinstance(obj, bpy.types.Object) or resolve_object(object_ref(obj)) is None:
            raise ValueError("Claim an existing original Blender object")
        requested.add((_epoch, int(obj.session_uid), int(obj.as_pointer()), "transform"))
    _prune_claims()
    if any(key in _claims and _claims[key] != owner for key in requested):
        raise ResourceConflict("Another feature already controls an object transform")
    for key in requested:
        _claims[key] = owner


def release_owner(owner):
    for key in tuple(_claims):
        if _claims[key] == owner:
            _claims.pop(key, None)


def claimed_transforms(owner=None):
    """Primitive ownership diagnostics, useful for integration tests."""
    _prune_claims()
    return [{"owner": value, "session_uid": key[1], "pointer": key[2]}
            for key, value in _claims.items() if owner is None or owner == value]


def registered_modules():
    return tuple(_modules)


def menu_entries():
    return tuple((spec.label or spec.module_id, spec.menu_entries)
                 for spec in _modules.values() if spec.menu_entries)


def card_entries():
    return tuple((node_id, card.label, card.description)
                 for spec in _modules.values() for node_id, card in spec.cards.items())


def card_spec(node_id):
    return next((spec.cards[node_id] for spec in _modules.values() if node_id in spec.cards), None)


def action_spec(node_id):
    pair = _actions.get(node_id)
    return pair[1] if pair else None


def prepare_module(module_id):
    """Run first-enable recovery only when an action/scheduler has scene access."""
    reason = _pending_recovery.pop(module_id, None)
    spec = _modules.get(module_id)
    if reason and spec:
        _cleanup_call(module_id + ".recover", spec.recover, reason)


def _module_callback(spec, callback):
    def invoke(context, delta_time):
        prepare_module(spec.module_id)
        callback(context, delta_time)
    return invoke


def configuration_signature(node):
    spec = action_spec(node.bl_idname)
    if spec is None:
        return None
    config = json_snapshot(spec.configuration(node)) if spec.configuration else None
    links, pending, visited = [], [node], set()
    while pending:
        current = pending.pop()
        if current.as_pointer() in visited:
            continue
        visited.add(current.as_pointer())
        for socket in current.inputs:
            for link in socket.links:
                source = link.from_node
                links.append([str(getattr(current, "runtime_id", "") or current.name), socket.identifier,
                              str(getattr(source, "runtime_id", "") or source.name),
                              link.from_socket.identifier, bool(source.mute),
                              bool(getattr(source, "enabled", True)), bool(link.is_valid), bool(link.is_muted)])
                pending.append(source)
    links.sort()
    return json.dumps({"configuration": config, "links": links}, sort_keys=True, allow_nan=False)


def evaluate(context, node):
    callback = _evaluators.get(node.bl_idname)
    if callback is not None:
        result = callback(context, node)
        if not isinstance(result, dict):
            raise TypeError("A value evaluator must return an output dictionary")
        return result
    if node.bl_idname in _actions and node.outputs.get("Running") is not None:
        from . import runtime, workflow
        return runtime._output(node, "Running", float(bool(workflow.running_for(node.id_data, node))))
    return None


class ActionContext:
    """Ephemeral main-thread view. Never store this context or its RNA in state."""
    def __init__(self, task, now):
        from . import flow, workflow
        self.ticket_id = task["id"]
        self.run_id = task["run_id"]
        self.owner = task["owner"]
        self.input_name = task["input_name"]
        self.now = float(now)
        self.delta_time = max(0.0, self.now - task.get("updated", task["created"]))
        self.tree = _resolve(task["tree_ref"], bpy.data.node_groups)
        self.scene = _resolve(task["scene_ref"], bpy.data.scenes)
        self.node = next((node for node in self.tree.nodes if str(getattr(node, "runtime_id", "")) == task["node"]), None) if self.tree else None
        self._payload = workflow.payload(self.run_id)
        self._evaluation = flow.PullContext(self.tree, self.scene, self.now, self.delta_time) if self.tree and self.scene else None
        if self._evaluation:
            self._evaluation.run_id, self._evaluation.payload = self.run_id, self.payload

    @property
    def payload(self):
        return json_snapshot(self._payload)

    def input(self, name, fallback=None):
        if self._evaluation is None or self.node is None:
            raise ValueError("Action input is unavailable during teardown")
        return self._evaluation.input(self.node, name, fallback)

    def claim_transforms(self, objects):
        claim_transforms(self.owner, objects)

    def release(self):
        release_owner(self.owner)


def apply_result(task, result, now):
    from . import workflow
    if not isinstance(result, ActionResult) or result.status not in {"running", "end", "failed", "cancelled"}:
        raise TypeError("Action callbacks must return ActionResult")
    if result.status == "running":
        spec = action_spec(task["node_type"])
        if spec is None or not callable(spec.update):
            raise ValueError("A running action requires an update callback")
        task["state"] = json_snapshot(result.state)
        task["updated"] = float(now)
    else:
        workflow.complete_task(task["id"], result.status, result.error, now)


def retire_task(task, status, reason="", now=None):
    """Core-only ticket retirement; cancellation hook runs at most once."""
    if task.get("kind") != "extension":
        return
    now = time.monotonic() if now is None else now
    try:
        if status != "end" and not task.get("cancel_called"):
            task["cancel_called"] = True
            spec = action_spec(task["node_type"])
            if spec and callable(spec.cancel):
                spec.cancel(ActionContext(task, now), json_snapshot(task["state"]), reason or status)
    except Exception as error:
        # A failed consumer cleanup must not prevent other owners from retiring.
        _record_error("action:" + task["node_type"], error)
    finally:
        release_owner(task["owner"])


def cancel_node(node, reason="node changed"):
    from . import workflow
    for task in tuple(workflow._tasks.values()):
        if task.get("kind") == "extension" and task["tree_ref"] == object_ref(node.id_data) and task["node"] == str(node.runtime_id):
            workflow.finish(task["run_id"], "cancelled", reason)


def register_module(spec):
    if not isinstance(spec, ModuleSpec) or not spec.module_id or spec.module_id in _modules:
        raise ValueError("A unique ModuleSpec.module_id is required")
    if any(name not in _modules for name in spec.dependencies):
        raise ValueError("Register module dependencies first")
    ids = [cls.bl_idname for cls in spec.classes if hasattr(cls, "bl_idname")]
    if len(ids) != len(set(ids)) or any(node_id in _classes or hasattr(bpy.types, node_id) for node_id in ids):
        raise ValueError("Module class IDs must be unique")
    if any(node_id in _evaluators for node_id in spec.evaluators) or any(node_id in _actions for node_id in spec.actions):
        raise ValueError("Duplicate evaluator or action declaration")
    if any(not isinstance(value, ActionSpec) or not callable(value.start) for value in spec.actions.values()):
        raise TypeError("Actions require callable ActionSpec.start")
    if any(node_id not in ids for node_id in (*spec.evaluators, *spec.actions)):
        raise ValueError("Modules may only dispatch their own declared classes")
    if any(not isinstance(value, CardSpec) for value in spec.cards.values()):
        raise TypeError("Cards require CardSpec")
    if any(node_id not in spec.actions for node_id in spec.cards):
        raise ValueError("Cards must declare registered actions")
    registered = []
    try:
        for cls in spec.classes:
            bpy.utils.register_class(cls)
            registered.append(cls)
        _modules[spec.module_id] = spec
        _pending_recovery[spec.module_id] = "register"
        _classes.update({node_id: spec.module_id for node_id in ids})
        _evaluators.update(spec.evaluators)
        _actions.update({node_id: (spec.module_id, value) for node_id, value in spec.actions.items()})
        if callable(spec.on_register):
            spec.on_register()
        if callable(spec.motion):
            scheduler.add_motion(f"module:{spec.module_id}", _module_callback(spec, spec.motion), priority=200)
        if callable(spec.system):
            scheduler.add_system(f"module:{spec.module_id}", _module_callback(spec, spec.system), priority=200)
    except Exception:
        if spec.module_id in _modules:
            unregister_module(spec.module_id)
        else:
            for cls in reversed(registered):
                bpy.utils.unregister_class(cls)
        raise


def unregister_module(module_id):
    spec = _modules.get(module_id)
    if spec is None:
        return
    if any(module_id in other.dependencies for other in _modules.values() if other is not spec):
        raise ValueError("Unregister dependent modules first")
    from . import workflow
    for task in tuple(workflow._tasks.values()):
        if task.get("module_id") == module_id:
            workflow.finish(task["run_id"], "cancelled", "module unregistered")
    scheduler.remove_motion(f"module:{module_id}")
    scheduler.remove_system(f"module:{module_id}")
    _cleanup_call(module_id + ".cleanup", spec.cleanup, "unregister")
    _cleanup_call(module_id + ".on_unregister", spec.on_unregister)
    for node_id in spec.evaluators:
        _evaluators.pop(node_id, None)
    for node_id in spec.actions:
        _actions.pop(node_id, None)
    for cls in reversed(spec.classes):
        _classes.pop(getattr(cls, "bl_idname", ""), None)
        _cleanup_call(module_id + ".class:" + cls.__name__, bpy.utils.unregister_class, cls)
    _modules.pop(module_id, None)
    _pending_recovery.pop(module_id, None)
    for owner in set(_claims.values()):
        if f":{module_id}:" in owner:
            release_owner(owner)


def cleanup(reason):
    global _epoch
    for spec in reversed(tuple(_modules.values())):
        _cleanup_call(spec.module_id + ".cleanup", spec.cleanup, reason)
    _claims.clear()
    _epoch = uuid.uuid4().hex


def recover(reason):
    for spec in tuple(_modules.values()):
        _pending_recovery.pop(spec.module_id, None)
        _cleanup_call(spec.module_id + ".recover", spec.recover, reason)


def poll(_context=None, _delta_time=0.0, *, now=None):
    if scheduler.is_suspended():
        return
    from . import flow, workflow
    now = time.monotonic() if now is None else float(now)
    for module_id in tuple(_pending_recovery):
        prepare_module(module_id)
    workflow.tick(now, advance=False)
    workflow.poll_extensions(now)
    _prune_claims()
    flow.drain_pending(now)


def register():
    from . import modules
    pending = {}
    for module in sorted(pkgutil.iter_modules(modules.__path__), key=lambda item: item.name):
        if module.name.startswith("_"):
            continue
        imported = importlib.import_module(f"{modules.__name__}.{module.name}")
        spec = imported.create_spec() if callable(getattr(imported, "create_spec", None)) else getattr(imported, "SPEC", None)
        if not isinstance(spec, ModuleSpec) or spec.module_id in pending:
            raise ValueError("Bundled modules must export a unique SPEC or create_spec()")
        pending[spec.module_id] = spec
    try:
        while pending:
            ready = [name for name, spec in pending.items() if all(dep in _modules for dep in spec.dependencies)]
            if not ready:
                raise ValueError("Missing or cyclic bundled module dependencies")
            for name in ready:
                register_module(pending.pop(name))
        scheduler.add_motion("extensions:actions", poll, priority=300)
    except Exception:
        unregister()
        raise


def unregister():
    scheduler.remove_motion("extensions:actions")
    for module_id in reversed(tuple(_modules)):
        unregister_module(module_id)
    cleanup("unregister")
