"""Invocation-scoped asynchronous actions, using flow's existing dispatcher."""

from collections import deque
from copy import deepcopy
import json
import uuid

import bpy

from .constants import TREE_ID

_runs = {}
_tasks = {}
_callbacks = deque()
logs = []
_order = 0
HANDOFF_KEY = "_yugo_handoff_owners"  # Private continuation metadata, never event payload.
ACTION_TYPES = {
    "YUGUS_NODE_action_flow", "YUGUS_NODE_flow_end", "YUGUS_NODE_wait_action",
    "YUGUS_NODE_test_action", "YUGUS_NODE_flow_repeat",
}


def is_definition(tree):
    return bool(getattr(tree, "is_workflow_definition", tree.get("is_workflow_definition", False)))


def _node(tree_name, uid):
    tree = bpy.data.node_groups.get(tree_name)
    if tree is None:
        return None
    return next((node for node in tree.nodes if str(getattr(node, "runtime_id", "") or node.name) == uid), None)


def begin(tree, payload=None, *, parent=None, continuation=None, explicit=False, metadata=None):
    """Copy the originating event once; asynchronous steps never resample it."""
    rid = uuid.uuid4().hex
    value = deepcopy(payload if payload is not None else {})
    # Payloads are data snapshots, never Blender RNA or mutable UI contexts.
    json.dumps(value)
    _runs[rid] = {
        "run_id": rid, "root_run_id": _runs[parent]["root_run_id"] if parent in _runs else rid,
        "parent": parent, "tree": tree.name, "payload": value,
        "metadata": deepcopy(metadata or {}), "continuation": deepcopy(continuation),
        "state": "running", "error": "", "depth": 0, "pending": set(),
        "explicit": explicit, "ended": False, "watch": {},
    }
    return rid


def status(run_id):
    run = _runs.get(run_id)
    if run is None:
        return None
    return {key: deepcopy(run[key]) for key in (
        "run_id", "root_run_id", "parent", "tree", "payload", "state", "error",
    )}


def history():
    return [status(rid) for rid in _runs]


def running(run_id):
    return run_id in _runs and _runs[run_id]["state"] == "running"


def payload(run_id):
    return deepcopy(_runs[run_id]["payload"])


def remember_output(run_id, section, key, value):
    """Retain generated output identity through Wait, calls and continuations.

    Only JSON data is accepted; callers never put native Blender objects in a
    workflow payload. Existing event data remains untouched.
    """
    run = _runs.get(run_id)
    if run is None:
        return
    copied = deepcopy(value)
    json.dumps(copied)
    if not isinstance(run["payload"], dict):
        run["payload"] = {"incoming": run["payload"]}
    values = run["payload"].setdefault(section, {})
    if not isinstance(values, dict):
        raise ValueError("Output snapshot section must be a dictionary")
    values[str(key)] = copied


def metadata(run_id):
    return deepcopy(_runs[run_id]["metadata"])


def note(run_id, node):
    if not running(run_id):
        return
    from . import flow
    _runs[run_id]["watch"].setdefault((node.id_data.name, flow._uid(node)), flow._control_signature(node))


def enter_dispatch(run_id):
    if running(run_id):
        _runs[run_id]["depth"] += 1


def leave_dispatch(run_id):
    if run_id in _runs:
        _runs[run_id]["depth"] = max(0, _runs[run_id]["depth"] - 1)
        _maybe_finish(run_id)


def mark_end(run_id):
    if running(run_id):
        _runs[run_id]["ended"] = True


def _maybe_finish(run_id):
    run = _runs.get(run_id)
    if run and run["state"] == "running" and run["depth"] == 0 and not run["pending"]:
        if run["explicit"] and not run["ended"]:
            finish(run_id, "failed", "Workflow stopped before reaching Flow End")
        else:
            finish(run_id, "end")


def finish(run_id, state, error=""):
    if not running(run_id):
        return
    run = _runs[run_id]
    run["state"], run["error"] = state, str(error)
    for child_id, child in tuple(_runs.items()):
        if child["parent"] == run_id and running(child_id):
            finish(child_id, "failed" if state == "failed" else "cancelled", error)
    for tid in tuple(run["pending"]):
        task = _tasks.pop(tid, None)
        if task is not None:
            _callbacks.append({"kind": "action_done", "task": task, "state": state, "error": str(error)})
    run["pending"].clear()
    if run["continuation"]:
        _callbacks.append({"kind": "child_done", "run_id": run_id, "continuation": run["continuation"], "state": state, "error": str(error)})


def cancel_run(run_id, now=None):
    finish(run_id, "cancelled")


def _task(pulse, node, kind, **values):
    global _order
    from . import flow
    _order += 1
    tid = uuid.uuid4().hex
    task_metadata = deepcopy(pulse.metadata)
    handoff = sorted({str(uid) for uid in getattr(pulse.context, "handoff_owners", ())})
    if handoff:
        task_metadata[HANDOFF_KEY] = handoff
    else:
        task_metadata.pop(HANDOFF_KEY, None)
    task = {"id": tid, "kind": kind, "run_id": pulse.run_id, "tree": node.id_data.name,
            "node": flow._uid(node), "metadata": task_metadata,
            "signature": flow._control_signature(node), "created": pulse.now, "order": _order, **values}
    _tasks[tid] = task
    _runs[pulse.run_id]["pending"].add(tid)
    note(pulse.run_id, node)
    if hasattr(node, "last_run_id"):
        node.last_run_id = pulse.run_id
    node.last_summary = "Running"
    return task


def _log(task, state, now):
    entry = {"run_id": task["run_id"], "root_run_id": _runs[task["run_id"]]["root_run_id"],
             "node": task["node"], "tree": task["tree"], "label": task.get("label", ""),
             "payload": payload(task["run_id"]), "state": state, "time": now,
             "metadata": deepcopy(task["metadata"])}
    logs.append(entry)
    if len(logs) > 512:
        del logs[:-512]
    node = _node(task["tree"], task["node"])
    if node is not None and hasattr(node, "payload_log"):
        node.payload_log = json.dumps(entry, ensure_ascii=False, sort_keys=True)


def complete_task(task_id, state="end", error="", now=0.0):
    task = _tasks.pop(task_id, None)
    if task is None:
        return
    run = _runs[task["run_id"]]
    if task["kind"] == "timer" and task.get("test"):
        _log(task, state, now)
    _callbacks.append({"kind": "action_done", "task": task, "state": state, "error": str(error)})
    if state != "end":
        finish(task["run_id"], state, error)


def settle(task):
    run = _runs.get(task["run_id"])
    if run:
        run["pending"].discard(task["id"])
        _maybe_finish(task["run_id"])


def legacy_hold(pulse, node):
    return _task(pulse, node, "legacy")["id"]


def running_for(tree, node):
    from . import flow
    uid = flow._uid(node)
    return sum(task["tree"] == tree.name and task["node"] == uid for task in _tasks.values())


def evaluate(context, node):
    from .availability import require_available
    require_available(node)
    if node.bl_idname not in ACTION_TYPES or node.outputs.get("Running") is None:
        return None
    from . import runtime
    return runtime._output(node, "Running", 1.0 if running_for(node.id_data, node) else 0.0)


def active_in(tree):
    return any(run["tree"] == tree.name and run["state"] == "running" for run in _runs.values())


def _busy(pulse, node):
    from . import flow
    pending = [task for task in _tasks.values() if task["tree"] == node.id_data.name and task["node"] == flow._uid(node)]
    policy = getattr(node, "busy_policy", "PARALLEL")
    if pending and policy == "IGNORE":
        node.last_summary = "Running · ignored Enter"
        return True
    if pending and policy == "RESTART":
        for task in pending:
            finish(task.get("child", task["run_id"]), "cancelled")
    return False


def _cancel(pulse, node):
    from . import flow
    pending = [task for task in _tasks.values() if task["tree"] == node.id_data.name and task["node"] == flow._uid(node)]
    target = pulse.payload.get("cancel_run_id", pulse.payload.get("target_run_id")) if isinstance(pulse.payload, dict) else None
    if target:
        pending = [task for task in pending if target in {task["run_id"], task.get("child"), _runs[task["run_id"]]["root_run_id"]}]
    elif any(task["run_id"] == pulse.run_id for task in pending):
        pending = [task for task in pending if task["run_id"] == pulse.run_id]
    else:
        pending = sorted(pending, key=lambda task: task["order"])[-1:]
    for task in pending:
        finish(task.get("child", task["run_id"]), "cancelled")


def _child(task, tree, metadata_value, *, explicit=False):
    rid = begin(tree, payload(task["run_id"]), parent=task["run_id"],
                continuation={"task": task["id"]}, explicit=explicit, metadata=metadata_value)
    task.setdefault("children", set()).add(rid)
    return rid


def _start_repeat(task, now):
    task["iteration"] += 1
    tree = bpy.data.node_groups.get(task["tree"])
    metadata_value = {**task["metadata"], "iteration": task["iteration"]}
    rid = _child(task, tree, metadata_value)
    _callbacks.append({"kind": "dispatch", "tree": task["tree"], "node": task["node"], "output": "Body", "run_id": rid, "metadata": metadata_value, "now": now})


def receive(pulse, node, socket, path):
    """Return True only for workflow nodes handled by this invocation engine."""
    from .availability import require_available
    require_available(node)
    kind = node.bl_idname
    if kind in {"YUGUS_NODE_flow_delay", "YUGUS_NODE_flow_wait_until"} and is_definition(node.id_data):
        if socket.name == "Reset":
            _cancel(pulse, node)
        elif kind == "YUGUS_NODE_flow_delay":
            _task(pulse, node, "scoped_delay", deadline=pulse.now + max(0., float(node.seconds)))
        else:
            _task(pulse, node, "scoped_condition")
        return True
    if kind not in ACTION_TYPES:
        return False
    if socket.name == "Cancel":
        _cancel(pulse, node)
        return True
    if kind == "YUGUS_NODE_flow_end":
        mark_end(pulse.run_id)
        return True
    if _busy(pulse, node):
        return True
    if kind in {"YUGUS_NODE_wait_action", "YUGUS_NODE_test_action"}:
        task = _task(pulse, node, "timer", deadline=pulse.now + max(0., float(node.seconds)),
                     test=kind == "YUGUS_NODE_test_action", fail=bool(getattr(node, "fail", False)), label=str(getattr(node, "label", "Wait")))
        if task["test"]:
            _log(task, "running", pulse.now)
        if task["deadline"] <= pulse.now:
            complete_task(task["id"], "failed" if task["fail"] else "end", "Test Action failed" if task["fail"] else "", pulse.now)
    elif kind == "YUGUS_NODE_action_flow":
        tree = node.workflow_tree
        if tree is None or tree.bl_idname != TREE_ID or not is_definition(tree):
            raise ValueError("Select a workflow definition")
        entries = [entry for entry in tree.nodes if entry.bl_idname == "YUGUS_NODE_flow_entry" and not entry.mute]
        if len(entries) != 1 or not any(end.bl_idname == "YUGUS_NODE_flow_end" for end in tree.nodes):
            raise ValueError("Workflow needs one Flow Entry and a Flow End")
        ancestor = pulse.run_id
        while ancestor in _runs:
            if _runs[ancestor]["tree"] == tree.name:
                raise ValueError("Recursive Action Flow")
            ancestor = _runs[ancestor]["parent"]
        task = _task(pulse, node, "call")
        child_id = _child(task, tree, task["metadata"], explicit=True)
        task["child"] = child_id
        node.last_run_id = child_id
        from . import flow
        _callbacks.append({"kind": "dispatch", "tree": tree.name, "node": flow._uid(entries[0]), "output": "Start", "run_id": child_id, "metadata": task["metadata"], "now": pulse.now})
    elif kind == "YUGUS_NODE_flow_repeat":
        task = _task(pulse, node, "repeat", count=max(1, int(node.count)), iteration=0)
        _start_repeat(task, pulse.now)
    return True


def child_done(callback, now):
    task = _tasks.get(callback["continuation"]["task"])
    if task is None:
        return
    task["children"].discard(callback["run_id"])
    state = callback["state"]
    if state != "end":
        for rid in tuple(task["children"]):
            finish(rid, state, callback["error"])
        complete_task(task["id"], state, callback["error"], now)
    elif task["kind"] == "repeat" and task["iteration"] < task["count"]:
        _start_repeat(task, now)
    else:
        complete_task(task["id"], now=now)


def next_callback():
    return _callbacks.popleft() if _callbacks else None


def tick(now, *, advance=True):
    from . import flow
    for rid, run in tuple(_runs.items()):
        if not running(rid):
            continue
        for (tree_name, uid), signature in tuple(run["watch"].items()):
            node = _node(tree_name, uid)
            if node is None or not flow._enabled(node) or flow._control_signature(node) != signature:
                finish(rid, "cancelled")
                break
    if not advance:
        return
    for tid, task in tuple(_tasks.items()):
        if task["kind"] == "scoped_condition":
            node = _node(task["tree"], task["node"])
            if node is not None:
                context = flow.PullContext(node.id_data, bpy.context.scene, now, max(0., now - task["created"]))
                context.run_id, context.payload = task["run_id"], payload(task["run_id"])
                try:
                    if float(context.input(node, "Condition", 1.)) > 1e-6:
                        complete_task(tid, now=now)
                except Exception as error:
                    complete_task(tid, "failed", str(error), now)
        elif task["kind"] in {"timer", "scoped_delay"} and now + 1e-6 >= task["deadline"]:
            failed = task.get("fail", False)
            complete_task(tid, "failed" if failed else "end", "Test Action failed" if failed else "", now)


def prune():
    keep = {rid for rid, run in _runs.items() if run["state"] == "running"}
    keep.update(task["run_id"] for task in _tasks.values())
    for callback in _callbacks:
        if callback.get("run_id"):
            keep.add(callback["run_id"])
        if callback.get("task"):
            keep.add(callback["task"]["run_id"])
    for rid in tuple(keep):
        parent = _runs.get(rid, {}).get("parent")
        while parent in _runs:
            keep.add(parent)
            parent = _runs[parent]["parent"]
    completed = [rid for rid in _runs if rid not in keep]
    for rid in completed[:-256]:
        _runs.pop(rid, None)


def clear():
    _runs.clear()
    _tasks.clear()
    _callbacks.clear()
    logs.clear()
