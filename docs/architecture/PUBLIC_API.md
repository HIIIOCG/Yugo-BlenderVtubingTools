# Studio feature API

Task #3 adds a public extension contract to the qualified Studio 0.6.0 baseline
imported by #2. It preserves existing package, node and socket identities, socket
order, graph persistence, and legacy runtime behavior. Existing modules have not
been renamed or moved. Floating UI and object physics are separate consumers of
this contract, with their own feature PRs.

## Declare a bundled feature

A package in `addons/yugu_node_studio/modules/<feature>/` exports a `SPEC`
`ModuleSpec` or a `create_spec()` function returning one. Discovery loads package
names in sorted order and registers declared dependencies first. Missing or cyclic
dependencies and duplicate IDs fail registration. A feature does not edit core
dispatch files.

```python
from ...extensions import ModuleSpec, ActionSpec, ActionResult, CardSpec

def create_spec():
    return ModuleSpec(
        module_id="example", label="Example",
        classes=(ExampleValue, ExampleAction),
        evaluators={ExampleValue.bl_idname: evaluate_value},
        actions={ExampleAction.bl_idname: ActionSpec(
            start, update, cancel,
            configuration=lambda node: {"duration": node.duration})},
        menu_entries=((ExampleAction.bl_idname, "Example Action"),),
        cards={ExampleAction.bl_idname: CardSpec("Example", "Run the action.")},
        motion=update_services, cleanup=cleanup_services, recover=recover_saved,
    )
```

`ModuleSpec` fields are `module_id`, `label`, `dependencies`, `classes`,
`evaluators`, `actions`, `menu_entries`, `cards`, `motion`, `system`,
`on_register`, `on_unregister`, `cleanup`, and `recover`. Class tuples keep their
declared registration order. Evaluators and actions may only dispatch classes
owned by that module. `register_module(spec)` / `unregister_module(module_id)`
are also public for controlled adapters and tests.

Import and registration can run under Blender's `RestrictBlend` extension
contract. Imports and `on_register()` must not read `bpy.data` or `bpy.context`,
create scene geometry, start another timer, or reconcile scenes. Defer scene work
until an explicit action or a scheduler callback. Unregister and cleanup must
also tolerate restricted or unavailable scene data. Core teardown records failing
consumer callbacks in `lifecycle_errors()` and continues retiring other modules,
classes, callbacks, and ownership claims.
First-enable `recover("register")` is deferred until the module's first action or
scheduler callback; core performs it before that callback can create fresh output.

## Nodes and values

Use the existing `YUGUS_NodeBase` from `nodes`, `TREE_ID` / `SOCKET_TRIGGER`
from `constants`, and event socket `EVENT = "YUGUS_NS_update_event"` from `flow`.
New feature IDs are additional identities; do not rename existing IDs.

For actions, the public `extensions.ActionNodeMixin` provides the established
`busy_policy`, `last_summary`, `last_run_id`, and `init_action()`. Combine it with
`YUGUS_NodeBase` and `bpy.types.Node`. `init_action()` creates Enter/Cancel event
inputs, End/Cancelled/Failed event outputs, and a Running `SOCKET_TRIGGER` value
output. New feature-specific inputs can follow these existing sockets.

Value evaluators have signature `evaluate(evaluation_context, node) -> dict`.
They use `evaluation_context.input(node, "Input", fallback)` for downstream
pulls and return output names and/or socket identifiers as keys. Native motion
and typed value objects remain supported; only event snapshots and retained
action state must be JSON. Registered action nodes with a Running output get
core Running evaluation unless they declare their own evaluator.

## Events and actual action completion

`flow.emit_event(tree, source, output_name, payload=None, now=None)` emits one
event and returns its run ID, or `None` while the scheduler is suspended. It
copies and validates the event before dispatch. A source sends events explicitly;
evaluating a value does not cause an event to repeat on every tick.

`ActionSpec(start, update=None, cancel=None, inputs=("Enter",),
configuration=None)` declares the public action callbacks:

- `start(context) -> ActionResult` runs once for each accepted invocation.
- `update(context, state) -> ActionResult` polls a running invocation after the
  feature's motion callback. A running result requires an update callback.
- `cancel(context, state, reason)` releases consumer state on non-successful
  retirement. Its return value is ignored; it runs at most once per ticket.
- `configuration(node) -> JSON` captures feature settings that invalidate an
  invocation. Include every action configuration property and target identity;
  upstream input topology and enabled/muted link state are also captured by core.

Results are `ActionResult.running(state)`, `ActionResult.end()`,
`ActionResult.failed(error)`, or `ActionResult.cancelled()`. Running state is a
strict finite JSON snapshot: dictionaries with string keys, lists, finite
numbers, strings, booleans, and null. Tuples, non-finite numbers, Blender RNA,
contexts, solvers, callbacks, and other Python objects are rejected. A long-lived
service may keep its own main-thread runtime registry, while its ticket stores
only a JSON key into that registry and primitive progress data.

Core retains the original event once. Every callback gets a defensive payload
copy, so external mutations and mutations inside a callback cannot change a
waiting invocation or its downstream continuation. Read changing values through
`context.input()` only when intended; snapshot inputs into JSON state during
start when they must remain fixed throughout a wait.

The callback context exposes `node`, `tree`, `scene`, `now`, `delta_time`,
`run_id`, `ticket_id`, `owner`, `input_name`, defensive `payload`, and
`input(name, fallback=None)`. It exists for one callback only. During cancellation
after deletion, node/tree/scene may be `None`. Do not retain the context or its
RNA. Callback state is independently copied on every update/cancel.

End is routed only after the handler reports completed work. For an insertion
action, End can mean a row was successfully inserted while independent queue
motion continues; a move action must stay running until its movement completes.
Failed and Cancelled are distinct outcomes, using the existing workflow engine's
continuation semantics. A positive `timeout_seconds` node property produces
Failed with `Action timed out` and invokes consumer cancellation cleanup.

Existing policies apply to extension nodes: `PARALLEL` keeps invocations
independent, `IGNORE` ignores new input while a task is running, and `RESTART`
cancels previous invocations before starting again. Cancel input uses existing
`cancel_run_id` / `target_run_id` payload selection. Deletion, mute, configuration
and link changes cancel watched tasks. Stale/deleted/reconfigured endpoints do
not emit continuations into a changed graph. Clear/load/undo/unregister cancels
pending work without emitting across Blender's data replacement.

Consumers never manipulate `_Pulse`, `_runs`, `_tasks`, or the core completion
queue. `workflow.status(run_id)`, `workflow.history()`, and `flow.emit_event()`
remain public observability and event entry points. `extensions.cancel_node(node,
reason)` permits prompt cancellation from a feature's settings/free callback.

## Scheduler, ownership and lifecycle

All callbacks run on Blender's main thread through the existing scheduler.
Flow routes at motion priority 100, declared feature `motion(context, delta_time)`
callbacks run at 200, and core action polling/completion draining runs at 300.
Feature `system(context, delta_time)` runs on the existing 2 Hz loop. Features
must not add independent timers, lifecycle handlers, or receiver threads through
this API. `extensions.poll(now=...)` permits deterministic core tests.

`claim_transforms(owner, objects)` validates the whole requested set and either
claims every object transform or raises `ResourceConflict` without partial
claims. Keys include the current lifecycle epoch, data-block session identity,
and native pointer. Rename does not transfer ownership; dead objects cannot keep
claims. `object_ref(data_block)` produces a JSON pointer/session reference, and
`resolve_object(reference)` resolves a retained object reference afresh.

An action uses `context.claim_transforms(objects)` / `context.release()` with
its per-ticket owner; core releases those claims on every outcome. For persistent
services use `node_owner(module_id, node, scene)` and
`claim_transforms(owner, objects)` / `release_owner(owner)`. Persistent services
must reconcile node deletion, mute, topology/configuration change and manual
output deletion in their shared scheduler callback, restoring their owned data
before release. Generated-output services claim only generated copies and do not
claim or edit templates/anchors. Core clears remaining claims during lifecycle
cleanup. Claims coordinate participating object writers within this Studio
process; existing bone/shape Apply arbitration stays unchanged.

Before Load or Undo/Redo replaces RNA, core suspends its scheduler, cancels
tickets, then calls `cleanup(reason)` on modules in reverse registration order.
After replacement it calls `recover(reason)` before resuming. Recover should
remove only that feature's saved generated output, using its ownership tags.
No module should broadly remove another owner's scene data. Unregister removes
callbacks, invokes cleanup/on_unregister, and unregisters classes in reverse.

Menus are exposed through `menu_entries()`. `CardSpec(label, description="",
draw=None)` declares a linear Enter→End action step; `draw(layout, node)` may show
feature-specific settings. The existing card parser still requires advanced
failure/cancellation paths and unsupported branching graphs to use the graph
editor. Cards are views of the actual node graph, not separate execution state.

## Validation

`tests/yugu_node_studio/blender_public_api.py` is a synthetic vertical consumer,
not a shipped feature. It exercises values, immutable origin snapshots during a
real asynchronous wait, End/Failed/Cancelled routing, policies, invalidation,
strict JSON, timeout, atomic claims, cards, scheduler order, save/reopen,
lifecycle, failing consumer cleanup, and restricted registration/unregistration.
Run it through `scripts/node_run_checks.py` against the exact built Studio ZIP,
together with workflow, cards, and package regressions.
