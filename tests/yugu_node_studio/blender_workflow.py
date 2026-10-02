"""Real Blender invocation, snapshot, completion, concurrency and cancellation tests."""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, workflow, runtime
from yugu_node_studio.constants import TREE_ID


class Graph:
    def __init__(self, name):
        flow.clear()
        self.main = bpy.data.node_groups.new(name, TREE_ID)
        self.definition = bpy.data.node_groups.new(name + " definition", TREE_ID)
        self.definition.is_workflow_definition = True
        self.source = self.main.nodes.new("YUGUS_NODE_test_event")
        self.cancel = self.main.nodes.new("YUGUS_NODE_test_event")
        self.call = self.main.nodes.new("YUGUS_NODE_action_flow")
        self.call.workflow_tree = self.definition
        self.entry = self.definition.nodes.new("YUGUS_NODE_flow_entry")
        self.end = self.definition.nodes.new("YUGUS_NODE_flow_end")
        self.link(self.source, "Trigger", self.call)
        self.link(self.cancel, "Trigger", self.call, "Cancel")

    def node(self, kind, seconds=None, label=None, main=False):
        node = (self.main if main else self.definition).nodes.new("YUGUS_NODE_" + kind)
        if seconds is not None:
            node.seconds = seconds
        if label is not None:
            node.label = label
        return node

    def link(self, source, output, target, input_name="Enter"):
        return source.id_data.links.new(source.outputs[output], target.inputs[input_name], verify_limits=False)

    def fire(self, now, value):
        return flow.emit_event(self.main, self.source, "Trigger", value, now=now)

    def cancel_run(self, now, rid):
        flow.emit_event(self.main, self.cancel, "Trigger", {"cancel_run_id": rid}, now=now)

    def completed(self, label):
        return [entry for entry in workflow.logs if entry["label"] == label and entry["state"] == "end"]

    def close(self):
        flow.clear()
        bpy.data.node_groups.remove(self.main)
        bpy.data.node_groups.remove(self.definition)


def serial_snapshot_test():
    graph = Graph("Workflow serial snapshot")
    try:
        wait = graph.node("wait_action", .2)
        test = graph.node("test_action", .1, "body")
        after = graph.node("test_action", 0, "after", main=True)
        graph.link(graph.entry, "Start", wait)
        graph.link(wait, "End", test)
        graph.link(test, "End", graph.end)
        graph.link(graph.call, "End", after)
        original = {"key": "F1", "nested": {"value": 7}}
        rid = graph.fire(0, original)
        original["nested"]["value"] = 99
        assert workflow.status(rid)["state"] == "running"
        assert workflow.evaluate(flow.PullContext(graph.main, bpy.context.scene, 0, 0), graph.call)["Running"] == 1
        flow.tick(.19)
        assert not graph.completed("body")
        flow.tick(.2)
        assert not graph.completed("after"), "Action Flow emitted End before its action completed"
        flow.tick(.29)
        assert workflow.status(rid)["state"] == "running"
        flow.tick(.300001)
        assert workflow.status(rid)["state"] == "end"
        assert len(graph.completed("body")) == len(graph.completed("after")) == 1
        assert graph.completed("after")[0]["payload"]["nested"]["value"] == 7
        assert workflow.evaluate(flow.PullContext(graph.main, bpy.context.scene, .3, 0), graph.call)["Running"] == 0
    finally:
        graph.close()


def concurrent_cancel_test():
    graph = Graph("Workflow concurrent cancel")
    try:
        wait = graph.node("wait_action", .2)
        test = graph.node("test_action", 0, "done")
        graph.link(graph.entry, "Start", wait)
        graph.link(wait, "End", test)
        graph.link(test, "End", graph.end)
        a = graph.fire(0, {"event": "a"})
        b = graph.fire(.05, {"event": "b"})
        c = graph.fire(.08, {"event": "c"})
        graph.cancel_run(.1, b)
        assert workflow.status(b)["state"] == "cancelled"
        assert workflow.status(a)["state"] == workflow.status(c)["state"] == "running"
        flow.tick(.21)
        assert workflow.status(a)["state"] == "end" and workflow.status(c)["state"] == "running"
        flow.tick(.29)
        assert workflow.status(c)["state"] == "end"
        assert [entry["payload"]["event"] for entry in graph.completed("done")] == ["a", "c"]
    finally:
        graph.close()


def busy_policy_and_fast_sync_test():
    for policy in ("IGNORE", "RESTART", "PARALLEL"):
        graph = Graph("Workflow busy " + policy)
        try:
            graph.call.busy_policy = policy
            test = graph.node("test_action", .1, "busy")
            graph.link(graph.entry, "Start", test)
            graph.link(test, "End", graph.end)
            a = graph.fire(0, {"event": "a"})
            b = graph.fire(.01, {"event": "b"})
            flow.tick(.12)
            expected = ["a"] if policy == "IGNORE" else ["b"] if policy == "RESTART" else ["a", "b"]
            assert [entry["payload"]["event"] for entry in graph.completed("busy")] == expected, policy
            assert workflow.status(a)["state"] == ("cancelled" if policy == "RESTART" else "end")
            assert workflow.status(b)["state"] == "end"
        finally:
            graph.close()
    graph = Graph("Workflow fast sync")
    try:
        test = graph.node("test_action", 0, "fast")
        graph.link(graph.entry, "Start", test)
        graph.link(test, "End", graph.end)
        ids = [graph.fire(0, {"index": index}) for index in range(20)]
        assert len(set(ids)) == 20 and all(workflow.status(rid)["state"] == "end" for rid in ids)
        assert [entry["payload"]["index"] for entry in graph.completed("fast")] == list(range(20))
    finally:
        graph.close()


def parallel_join_test():
    graph = Graph("Workflow parallel join")
    try:
        parallel = graph.node("flow_parallel")
        first = graph.node("test_action", .1, "branch-a")
        second = graph.node("test_action", .3, "branch-b")
        join = graph.node("flow_wait_all")
        joined = graph.node("test_action", 0, "joined")
        graph.link(graph.entry, "Start", parallel)
        graph.link(parallel, "Branch 1", first)
        graph.link(parallel, "Branch 2", second)
        graph.link(first, "End", join, "Branch 1")
        graph.link(second, "End", join, "Branch 2")
        graph.link(join, "End", joined)
        graph.link(joined, "End", graph.end)
        a = graph.fire(0, {"event": "a"})
        b = graph.fire(.05, {"event": "b"})
        flow.tick(.11)
        assert not graph.completed("joined")
        flow.tick(.16)
        assert not graph.completed("joined"), "Wait All mixed separate invocations"
        flow.tick(.31)
        assert [entry["payload"]["event"] for entry in graph.completed("joined")] == ["a"]
        assert workflow.status(a)["state"] == "end" and workflow.status(b)["state"] == "running"
        flow.tick(.36)
        assert [entry["payload"]["event"] for entry in graph.completed("joined")] == ["a", "b"]
        assert workflow.status(b)["state"] == "end"
        first.seconds = second.seconds = 0
        for index in range(200):
            completed = graph.fire(1 + index * .01, {"event": str(index)})
            assert workflow.status(completed)["state"] == "end"
        assert not workflow._joins and not workflow._tasks, "Completed Parallel joins leaked"
        assert len(workflow.history()) <= 256 and len(workflow.logs) <= 512
    finally:
        graph.close()


def repeat_completion_and_budget_test():
    graph = Graph("Workflow repeat completion")
    try:
        repeat = graph.node("flow_repeat")
        repeat.count = 3
        body = graph.node("test_action", .1, "repeat-body")
        graph.link(graph.entry, "Start", repeat)
        graph.link(repeat, "Body", body)
        graph.link(repeat, "End", graph.end)
        rid = graph.fire(0, {"key": "R"})
        for index in range(1, 4):
            flow.tick(index * .100001)
            assert len(graph.completed("repeat-body")) == index
            assert workflow.status(rid)["state"] == ("end" if index == 3 else "running")
        assert [entry["metadata"]["iteration"] for entry in graph.completed("repeat-body")] == [1, 2, 3]
        body.seconds = 0
        repeat.count = 10000
        large = graph.fire(1, {"key": "large"})
        assert workflow.status(large)["state"] == "running", "Huge synchronous Repeat monopolized one event"
        graph.cancel_run(1.01, large)
        flow.tick(2)
        assert workflow.status(large)["state"] == "cancelled"
        assert len(workflow.history()) < 800 and len(workflow.logs) <= 512
    finally:
        graph.close()


def legacy_definition_parallel_snapshot_test():
    for kind in ("flow_delay", "flow_wait_until"):
        graph = Graph("Workflow scoped " + kind)
        try:
            wait = graph.node(kind)
            if kind == "flow_delay":
                wait.seconds = .2
            else:
                wait.inputs["Condition"].default_value = 0
            test = graph.node("test_action", 0, "legacy")
            graph.link(graph.entry, "Start", wait)
            graph.link(wait, "End", test)
            graph.link(test, "End", graph.end)
            a = graph.fire(0, {"event": "a"})
            b = graph.fire(.05, {"event": "b"})
            flow.tick(.21)
            if kind == "flow_delay":
                assert workflow.status(a)["state"] == "end" and workflow.status(b)["state"] == "running"
                flow.tick(.26)
            else:
                assert not graph.completed("legacy")
                wait.inputs["Condition"].default_value = 1
                flow.tick(.26)
            assert [entry["payload"]["event"] for entry in graph.completed("legacy")] == ["a", "b"]
        finally:
            graph.close()


def failure_rewire_and_definition_sleep_test():
    graph = Graph("Workflow failed and rewired")
    try:
        test = graph.node("test_action", .1, "failure")
        test.fail = True
        graph.link(graph.entry, "Start", test)
        graph.link(test, "End", graph.end)
        failed = graph.node("test_action", 0, "caught", main=True)
        graph.link(graph.call, "Failed", failed)
        flow.tick(0)
        assert not workflow.history(), "Definition or manual event source auto-ran"
        rid = graph.fire(0, {"event": "fail"})
        flow.tick(.11)
        assert workflow.status(rid)["state"] == "failed"
        assert "Test Action failed" in graph.call.last_summary
        assert graph.main.last_error == "Test Action failed"
        assert len(graph.completed("caught")) == 1
        test.fail = False
        test.seconds = .5
        changed = graph.fire(1, {"event": "rewire"})
        for link in tuple(test.outputs["End"].links):
            graph.definition.links.remove(link)
        flow.tick(1.1)
        assert workflow.status(changed)["state"] == "cancelled"
        flow.tick(2)
        assert not any(entry["state"] == "end" and entry["payload"].get("event") == "rewire" for entry in workflow.logs)
        flow.clear()
        assert not workflow.history() and not workflow._tasks and not workflow._callbacks
    finally:
        graph.close()


def dead_end_definition_test():
    graph = Graph("Workflow disconnected Flow End")
    try:
        failed = graph.node("test_action", 0, "dead-end-caught", main=True)
        graph.link(graph.call, "Failed", failed)
        rid = graph.fire(0, {"event": "dead-end"})
        assert workflow.status(rid)["state"] == "failed"
        assert "Flow End" in workflow.status(rid)["error"]
        assert "Flow End" in graph.call.last_summary
        assert len(graph.completed("dead-end-caught")) == 1
        assert not workflow._tasks
        flow.tick(1)
        assert workflow.status(rid)["state"] == "failed" and not workflow._tasks
    finally:
        graph.close()


def main():
    addon.register()
    try:
        for test in (serial_snapshot_test, concurrent_cancel_test, busy_policy_and_fast_sync_test,
                     parallel_join_test, repeat_completion_and_budget_test,
                     legacy_definition_parallel_snapshot_test, failure_rewire_and_definition_sleep_test,
                     dead_end_definition_test):
            test()
            print("WORKFLOW_PASS: " + test.__name__)
        print("YUGU_WORKFLOW_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
