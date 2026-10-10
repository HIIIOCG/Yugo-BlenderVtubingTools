"""Reversible host adapters, with inert fall-through when wrapped by another addon."""
import importlib
import bpy
from . import data

_bindings = []
_token = None


def base(required=True):
    cls = bpy.types.NodeTree.bl_rna_get_subclass_py(data.TREE)
    if cls:
        package = importlib.import_module(cls.__module__.rsplit(".", 1)[0])
        if all(hasattr(package, name) for name in ("runtime", "data", "flow", "value_nodes")):
            return package
    if required:
        raise RuntimeError("Enable Yugo Node 0.8.8 or later first")
    return None


def context(tree, scene):
    return base().runtime.EvaluationContext(tree, scene, 0.0)


def outputs(node, **values):
    result = dict(values)
    for name, value in values.items():
        socket = node.outputs.get(name)
        if socket:
            result[socket.identifier] = value
    return result


def is_action(socket):
    return (not socket.is_output and socket.bl_idname == data.EVENT
            and socket.name in data.ACTIONS.get(socket.node.bl_idname, ()))


def uninstall():
    global _token
    if _token:
        _token["active"] = False
    for owner, name, original, wrapper in reversed(_bindings):
        if getattr(owner, name, None) is wrapper:
            setattr(owner, name, original)
    _bindings.clear()
    _token = None


def install():
    global _token
    uninstall()
    host = base()
    token = _token = {"active": True}
    evaluate_original = host.runtime._evaluate_node
    action_original = host.flow._action_input
    receive_original = host.flow._Pulse._receive
    draw_original = host.flow.YUGUS_NS_update_event.draw

    def evaluate(ctx, node):
        if token["active"] and node.bl_idname in data.NODE_IDS:
            if node.bl_idname in data.INPUT_NODES:
                from . import physics_inputs
                try:
                    return physics_inputs.evaluate(ctx, node)
                except Exception as error:
                    node.status = str(error)
                    raise
            from . import runtime
            return runtime.evaluate(ctx, node)
        return evaluate_original(ctx, node)

    def accepts(socket):
        return (token["active"] and is_action(socket)) or action_original(socket)

    def receive(pulse, socket, path=()):
        if not token["active"] or not is_action(socket):
            return receive_original(pulse, socket, path)
        flow = host.flow
        node = socket.node
        uid = flow._uid(node)
        if not flow._enabled(node) or not flow.workflow.running(pulse.run_id):
            return
        flow.workflow.note(pulse.run_id, node)
        if uid in path:
            pulse.fail(node, ValueError("Event cycle"))
            return
        key = (uid, socket.name)
        if key in pulse.executed:
            return
        pulse.executed.add(key)
        pulse.context.cache.clear()
        try:
            from . import runtime
            runtime.action(pulse.context, node, socket.name)
            pulse.context.cache.clear()
            if node.bl_idname != data.PHYSICS:
                pulse.emit(node.outputs.get("Done"), (*path, uid))
        except Exception as error:
            node.status = str(error)
            pulse.fail(node, error)
        finally:
            pulse.context.cache.clear()

    def draw(socket, ctx, layout, node, text):
        if token["active"] and is_action(socket):
            label = socket.name
            if node.bl_idname == data.PHYSICS and socket.name == 'Play':
                from . import runtime
                if runtime.play_requested(node):
                    label = 'Pause'
            op = layout.operator("yugo_physx_tracker.action", text=label)
            op.tree_name, op.node_name, op.action = node.id_data.name, node.name, socket.name
        else:
            draw_original(socket, ctx, layout, node, text)

    for owner, name, original, wrapper in (
        (host.runtime, "_evaluate_node", evaluate_original, evaluate),
        (host.flow, "_action_input", action_original, accepts),
        (host.flow._Pulse, "_receive", receive_original, receive),
        (host.flow.YUGUS_NS_update_event, "draw", draw_original, draw),
    ):
        setattr(owner, name, wrapper)
        _bindings.append((owner, name, original, wrapper))
