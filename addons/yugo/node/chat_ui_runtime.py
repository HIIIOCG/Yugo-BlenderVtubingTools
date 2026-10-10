"""Owned scene messages, pure output snapshots, and separate scrolling transforms.

Only Enter or an explicit button creates objects. Generated ownership is saved
on native objects, so file loads and Undo can reconstruct the list without
retaining stale RNA in Python. Templates and unrelated scene objects are never
deleted by list operations.
"""
import math
import time
import unicodedata
import uuid

import bpy
from mathutils import Matrix, Vector

from . import chat_ui_nodes as declarations
from .constants import TREE_ID
from .font_metrics import measure, measurement_scope

OWNER = "yugo_chat_owner"
ROW = "yugo_chat_row"
ROLE = "yugo_chat_role"
SOURCE = "yugo_chat_source"
ORDER = "yugo_chat_order"
TEXT = "yugo_chat_text"
AUTHOR = "yugo_chat_author"
_states = {}  # Owner -> names, signatures and primitive animation values only.
_snapshots = {}  # (owner, invocation) -> serializable object reference and count.
_retiring = False


def _uid(node):
    from .nodes import ensure_node_id
    return ensure_node_id(node)


def resolve_object(value):
    if isinstance(value, bpy.types.Object):
        return value
    if not isinstance(value, declarations.ObjectRef):
        return None
    obj = bpy.data.objects.get(value.name)
    if not value.token:
        return obj
    if obj and obj.get(ROW) == value.token and obj.get(ROLE) == "message":
        return obj
    return next((item for item in bpy.data.objects
                 if item.get(ROW) == value.token and item.get(ROLE) == "message"), None)


def resolve_collection(value):
    if isinstance(value, bpy.types.Collection):
        return value
    return bpy.data.collections.get(value.name) if isinstance(value, declarations.CollectionRef) else None


def _discover(node):
    uid = _uid(node)
    return sorted((o for o in bpy.data.objects if o.get(OWNER) == uid and o.get(ROLE) == "layout"),
                  key=lambda o: int(o.get(ORDER, 0)))


def _state(node):
    uid = _uid(node)
    if uid not in _states:
        layouts = _discover(node)
        _states[uid] = {"layouts": [o.name for o in layouts], "motions": {},
                        "signature": None, "last_sync": -math.inf,
                        "bounds": {}, "layout_signature": None, "avatar_restored": False}
    return _states[uid]


def _layouts(node):
    state = _state(node)
    uid = _uid(node)
    result = [bpy.data.objects.get(name) for name in state["layouts"]]
    if any(o is None or o.get(OWNER) != uid or o.get(ROLE) != "layout" for o in result):
        result = _discover(node)
        state["layouts"] = [o.name for o in result]
    return result


def _message(layout):
    return next((o for o in layout.children
                 if o.get(ROLE) == "message" and o.get(ROW) == layout.get(ROW)
                 and o.get(OWNER) == layout.get(OWNER)), None)


def rows(node):
    return [message for layout in _layouts(node) if (message := _message(layout)) is not None]


def _settings(node, context):
    style = resolve_collection(context.input(node, "Style", node.inputs["Style"].default_value))
    parent = resolve_object(context.input(node, "Parent", node.inputs["Parent"].default_value))
    if style is None:
        raise ValueError("Choose a Style collection")
    if parent is None:
        raise ValueError("Choose a Parent object")
    sources = list(style.all_objects)
    if not sources:
        raise ValueError("Style collection is empty")
    if parent in sources or any(o.get(OWNER) for o in sources):
        raise ValueError("Style must contain template objects, separate from generated messages and Parent")
    if parent.library:
        raise ValueError("Parent must be a local object")
    text = node.text_object
    author = node.name_object
    if text is None:
        text = next((o for o in sources if o.name == "ChatText" and o.type == "FONT"), None)
    if author is None:
        author = next((o for o in sources if o.name == "UserName" and o.type == "FONT"), None)
    if text not in sources or text.type != "FONT":
        raise ValueError("Choose a Text Object inside Style")
    if author is not None and (author not in sources or author.type != "FONT" or author == text):
        raise ValueError("User Name Object must be a different text object inside Style")
    return style, parent, sources, text, author


def _context(node, scene=None, now=None):
    from .flow import PullContext
    return PullContext(node.id_data, scene or bpy.context.scene,
                       time.monotonic() if now is None else now, 1 / 60)


def _collection(node, parent, scene):
    uid = _uid(node)
    collection = next((c for c in bpy.data.collections
                       if c.get(OWNER) == uid and c.get(ROLE) == "collection"), None)
    if collection is None:
        collection = bpy.data.collections.new(f"Chat List · {node.name}")
        collection[OWNER], collection[ROLE] = uid, "collection"
    container = next((c for c in parent.users_collection if c.get(OWNER) != uid), scene.collection)
    if collection.name not in container.children:
        container.children.link(collection)
    return collection


def _tag(obj, uid, token, role, order=0):
    obj[OWNER], obj[ROW], obj[ROLE], obj[ORDER] = uid, token, role, order


def _remove_objects(objects, fallback=None):
    """Detach user-added children and remove only the explicit owned objects."""
    objects = list(objects)
    selected = set(objects)
    curves = set()
    avatar_materials = set()
    for obj in objects:
        for child in tuple(obj.children):
            if child not in selected:
                world = child.matrix_world.copy()
                child.parent = fallback
                child.matrix_world = world
        if obj.type == "FONT" and obj.data and obj.data.get(OWNER):
            curves.add(obj.data)
        for slot in obj.material_slots:
            if slot.material and slot.material.get("yugo_avatar_material"):
                avatar_materials.add(slot.material)
    for obj in reversed(objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for curve in curves:
        if curve.users == 0 and not curve.use_fake_user:
            bpy.data.curves.remove(curve)
    from .chat_ui_avatar import dispose
    dispose(avatar_materials)


def _remove_row(node, layout):
    uid, token = _uid(node), layout.get(ROW)
    objects = [o for o in bpy.data.objects if o.get(OWNER) == uid and o.get(ROW) == token]
    _remove_objects(objects, layout.parent)
    state = _state(node)
    state["motions"].pop(token, None)
    state["bounds"].pop(token, None)
    state["layouts"] = [o.name for o in _discover(node)]


def _remap_references(obj, mapping):
    for block in tuple(obj.modifiers) + tuple(obj.constraints):
        for prop in block.bl_rna.properties:
            if prop.type != "POINTER" or prop.is_readonly:
                continue
            try:
                old = getattr(block, prop.identifier)
                if isinstance(old, bpy.types.Object) and old in mapping:
                    setattr(block, prop.identifier, mapping[old])
            except (AttributeError, TypeError, RuntimeError):
                pass
        for target in getattr(block, "targets", ()):
            if target.target in mapping:
                target.target = mapping[target.target]
        from .chat_ui_modifier import remap
        remap(block, mapping)
    if obj.animation_data:
        for curve in obj.animation_data.drivers:
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if isinstance(target.id, bpy.types.Object) and target.id in mapping:
                        target.id = mapping[target.id]


def _clone_style(node, message, collection, sources, text_source, author_source, text, author):
    clones = []
    mapping = {}
    try:
        for source in sources:
            clone = source.copy()
            clone.name = f"{message.name}_{source.name}"
            clones.append(clone)
            _tag(clone, _uid(node), message.get(ROW), "clone")
            clone[SOURCE] = source
            if source.type == "FONT":
                clone.data = source.data.copy()
                clone.data.use_fake_user = False
                clone.data[OWNER] = _uid(node)
                if source == text_source:
                    clone.data.body = text
                    if node.text_width > 0:
                        clone.data.text_boxes[0].width = node.text_width
                        clone.data.text_boxes[0].height = 0
                elif source == author_source:
                    clone.data.body = author
                    if node.name_width > 0:
                        clone.data.text_boxes[0].width = node.name_width
                        clone.data.text_boxes[0].height = 0
            collection.objects.link(clone)
            mapping[source] = clone
        for source, clone in mapping.items():
            if source.parent in mapping:
                clone.parent = mapping[source.parent]
                clone.matrix_parent_inverse = source.matrix_parent_inverse.copy()
                clone.matrix_basis = source.matrix_basis.copy()
            else:
                clone.parent = message
                clone.matrix_parent_inverse = Matrix.Identity(4)
                clone.matrix_basis = source.matrix_world.copy()
            _remap_references(clone, mapping)
        fit = node.fit_panel and node.panel_object in mapping
        text_bounds = _wrap_font(mapping[text_source], text, node.text_width, bounds_needed=fit)
        if author_source:
            _wrap_font(mapping[author_source], author, node.name_width)
        if fit:
            _fit_panel(mapping[node.panel_object], mapping[text_source], node.panel_object, text_source, text_bounds)
        from .chat_ui_avatar import apply as apply_avatar
        apply_avatar(node, message, mapping)
        return clones
    except Exception:
        _remove_objects(clones, message)
        raise


def _clusters(text):
    """Keep combining marks, common emoji sequences and flag pairs together."""
    result = []
    for char in text:
        number = ord(char)
        regional = 0x1F1E6 <= number <= 0x1F1FF
        attach = bool(result) and (unicodedata.combining(char) or char == "\u200d"
                    or result[-1].endswith("\u200d") or 0xFE00 <= number <= 0xFE0F
                    or 0x1F3FB <= number <= 0x1F3FF
                    or (regional and len(result[-1]) == 1 and 0x1F1E6 <= ord(result[-1]) <= 0x1F1FF))
        if attach:
            result[-1] += char
        else:
            result.append(char)
    return result


def _font_width(obj, text=None):
    if text is not None:
        obj.data.body = text
    if not obj.data.body:
        return 0.0
    bounds = measure(obj)
    return max(p[0] for p in bounds) - min(p[0] for p in bounds)


def _wrap_font(obj, text, width, bounds_needed=False):
    """Blender text boxes do not break unspaced CJK/URLs; insert measured breaks.

    Native wrapping is retained whenever it already fits. The original message
    remains on the Layout object; only the display body's line breaks change.
    Width measurements use the actual font geometry, independent of Parent scale.
    """
    if width <= 0 or not text:
        return measure(obj) if bounds_needed else None
    native_bounds = measure(obj)
    native_width = max(p[0] for p in native_bounds) - min(p[0] for p in native_bounds)
    if native_width <= width + 1e-6:
        return native_bounds
    box = obj.data.text_boxes[0]
    box.width = 0.0
    lines = []
    for paragraph in text.split("\n"):
        remaining = _clusters(paragraph)
        if not remaining:
            lines.append("")
        while remaining:
            measured = _font_width(obj, "".join(remaining))
            if measured <= width + 1e-6:
                lines.append("".join(remaining))
                break
            count = max(1, min(len(remaining) - 1, int(len(remaining) * width / max(measured, 1e-9) * 0.95)))
            # Proportional estimation handles most lines with one extra measure;
            # binary reduction is bounded for irregular fonts or wide emoji.
            if _font_width(obj, "".join(remaining[:count])) > width + 1e-6:
                low, high = 1, count
                while low < high:
                    middle = (low + high + 1) // 2
                    if _font_width(obj, "".join(remaining[:middle])) <= width + 1e-6:
                        low = middle
                    else:
                        high = middle - 1
                count = low
            # Prefer a word boundary without dropping the original whitespace.
            boundary = max((i + 1 for i, c in enumerate(remaining[:count]) if c.isspace()), default=0)
            if boundary >= count // 2 and boundary > 0:
                count = boundary
            lines.append("".join(remaining[:count]))
            remaining = remaining[count:]
    obj.data.body = "\n".join(lines)
    box.width = width
    return measure(obj) if bounds_needed else None


def _fit_panel(panel, text, source_panel, source_text, text_bounds=None):
    """Grow a designated panel downward, retaining its original upper edge."""
    low = min(p[1] for p in source_panel.bound_box)
    high = max(p[1] for p in source_panel.bound_box)
    height = high - low
    if height < 1e-8:
        return
    original = source_panel.matrix_world.inverted_safe() @ source_text.matrix_world
    original_bottom = min((original @ Vector(p))[1] for p in measure(source_text))
    padding = max(0.0, original_bottom - low)
    # The clones retain their template-relative transforms. Their scene matrices
    # are not evaluated until the complete row is ready, so use that same
    # relative transform with the isolated font geometry instead.
    bottom = min((original @ Vector(p))[1] for p in (text_bounds or measure(text))) - padding
    factor = (high - min(low, bottom)) / height
    if factor <= 1 + 1e-6:
        return
    shift = panel.matrix_basis.to_3x3() @ Vector((0, high * (1 - factor), 0))
    panel.location += shift
    panel.scale.y *= factor


def _bounds(node, layout):
    """Measure in message coordinates so a user's entry animation cannot reflow rows."""
    message = _message(layout)
    axis = {"X": 0, "Y": 1, "Z": 2}[node.direction[0]]
    cached = _state(node)["bounds"].setdefault(str(layout.get(ROW)), {})
    if axis in cached:
        return cached[axis]
    points = []
    if message:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        inverse = message.matrix_world.inverted_safe()
        for obj in message.children_recursive:
            if obj.get(OWNER) != _uid(node) or obj.get(ROW) != layout.get(ROW) or obj.get(ROLE) != "clone":
                continue
            if obj.type == "EMPTY":
                continue
            evaluated = obj.evaluated_get(depsgraph)
            matrix = inverse @ evaluated.matrix_world
            points.extend((matrix @ Vector(corner))[axis] for corner in evaluated.bound_box)
    if not points:
        points = [0.0, 0.01]
    cached[axis] = min(points), max(points)
    return cached[axis]


def _reflow(node, now, new_token=None):
    state = _state(node)
    layouts = _layouts(node)
    state["layout_signature"] = (node.direction, float(node.gap))
    axis = {"X": 0, "Y": 1, "Z": 2}[node.direction[0]]
    positive = node.direction.endswith("POS")
    previous = None
    offset = 0.0
    for layout in layouts:
        low, high = _bounds(node, layout)
        if previous is not None:
            prev_low, prev_high = previous
            offset += (prev_high - low + node.gap) if positive else (prev_low - high - node.gap)
        previous = low, high
        target = Vector((0, 0, 0))
        target[axis] = offset
        token = layout.get(ROW)
        motion = state["motions"].get(token)
        if token == new_token or node.scroll_seconds <= 0:
            layout.location = target
            state["motions"].pop(token, None)
        elif motion is None or Vector(motion["target"]) != target:
            start = layout.location.copy()
            if (start - target).length > 1e-7:
                state["motions"][token] = {"start": tuple(start), "target": tuple(target),
                                          "time": float(now), "seconds": float(node.scroll_seconds)}
        # Preserve an in-progress scroll when its target has not changed.


def _value_signature(value):
    if isinstance(value, bpy.types.ID):
        return value.as_pointer()
    if isinstance(value, (str, int, bool, float, type(None))):
        return value
    try:
        return tuple(value)
    except TypeError:
        return None


def _rna_signature(block):
    result = []
    for prop in block.bl_rna.properties:
        if prop.is_readonly or prop.type == "COLLECTION":
            continue
        try:
            result.append((prop.identifier, _value_signature(getattr(block, prop.identifier))))
        except (AttributeError, RuntimeError, TypeError):
            pass
    return tuple(result)


def _signature(node, settings):
    from .chat_ui_modifier import signature as modifier_signature
    style, parent, sources, text, author = settings
    objects = []
    for obj in sources:
        font = (_rna_signature(obj.data), tuple((b.x, b.y, b.width, b.height) for b in obj.data.text_boxes)) if obj.type == "FONT" else None
        objects.append((obj.as_pointer(), obj.data.as_pointer() if obj.data else None,
                        tuple(v for row in obj.matrix_world for v in row),
                        obj.parent.as_pointer() if obj.parent else None,
                        tuple(tuple(v) for v in obj.bound_box), font,
                        tuple(m.as_pointer() if m else None for m in obj.data.materials) if obj.data and hasattr(obj.data, "materials") else (),
                        tuple((_rna_signature(m), modifier_signature(m)) for m in obj.modifiers),
                        tuple((_rna_signature(c), modifier_signature(c)) for c in obj.constraints)))
    return (style.as_pointer(), parent.as_pointer(), text.as_pointer(), author.as_pointer() if author else None,
            node.text_width, node.name_width, node.fit_panel,
            node.panel_object.as_pointer() if node.panel_object else None,
            node.icon_object.as_pointer() if node.icon_object else None,
            node.icon_material.as_pointer() if node.icon_material else None,
            tuple(objects))


def refresh(node, context=None, force=True):
    context = context or _context(node)
    state = _state(node)
    layouts = _layouts(node)
    settings = _settings(node, context)
    signature = _signature(node, settings)
    if not force and state["signature"] == signature:
        return
    style, parent, sources, text_source, author_source = settings
    collection = _collection(node, parent, context.scene)
    with measurement_scope():
        for layout in layouts:
            message = _message(layout)
            if message is None:
                continue
            old = [o for o in bpy.data.objects if o.get(OWNER) == _uid(node)
                   and o.get(ROW) == layout.get(ROW) and o.get(ROLE) == "clone"]
            # Build first so a copy failure leaves the existing visible message intact.
            _clone_style(node, message, collection, sources, text_source, author_source,
                         str(layout.get(TEXT, "")), str(layout.get(AUTHOR, "")))
            _remove_objects(old, message)
            if layout.parent != parent:
                layout.parent = parent
                layout.matrix_parent_inverse = Matrix.Identity(4)
    state["bounds"].clear()
    bpy.context.view_layer.update()
    state["signature"] = _signature(node, settings)
    _reflow(node, getattr(context, "now", time.monotonic()))
    _summary(node)


def _summary(node):
    messages = rows(node)
    node.last_summary = f"Messages: {len(messages)} / {node.max_messages}"
    from .runtime import _set_monitor
    _set_monitor(node, [node.last_summary, f"Newest: {messages[-1].name}" if messages else "No messages"])


def _remember(node, context, message, count):
    value = {"name": message.name if message else "", "token": message.get(ROW, "") if message else "", "count": count}
    run_id = getattr(context, "run_id", None)
    if run_id:
        _snapshots[(_uid(node), str(run_id))] = value
        from . import workflow
        workflow.remember_output(run_id, "chat_ui", _uid(node), value)
    payload = getattr(context, "payload", None)
    if isinstance(payload, dict):
        payload.setdefault("chat_ui", {})[_uid(node)] = dict(value)
    if hasattr(context, "cache"):
        # Other pure nodes may already have read Count/Message Empty in this
        # invocation. Retire derived values too after an explicit scene action.
        context.cache.clear()


def append(node, context):
    from .availability import require_available
    require_available(node)
    """Append exactly one sampled message; flow emits End after this returns."""
    if node.mute:
        return None
    settings = _settings(node, context)
    style, parent, sources, text_source, author_source = settings
    text = str(context.input(node, "Text", node.inputs["Text"].default_value) or "")
    author = str(context.input(node, "User Name", node.inputs["User Name"].default_value) or "")
    avatar = context.input(node, "Avatar", None)
    state = _state(node)
    now = float(getattr(context, "now", time.monotonic()))
    if node.sync_style and _layouts(node):
        refresh(node, context, force=False)
    collection = _collection(node, parent, context.scene)
    order = max((int(o.get(ORDER, 0)) for o in _layouts(node)), default=0) + 1
    token = uuid.uuid4().hex
    layout = bpy.data.objects.new(f"Chat_{order:04d}_Layout", None)
    message = bpy.data.objects.new(f"Chat_{order:04d}", None)
    _tag(layout, _uid(node), token, "layout", order)
    _tag(message, _uid(node), token, "message", order)
    layout[TEXT], layout[AUTHOR] = text, author
    layout.empty_display_size = 0.025
    message.empty_display_size = 0.05
    message.empty_display_type = "PLAIN_AXES"
    try:
        collection.objects.link(layout)
        collection.objects.link(message)
        layout.parent = parent
        message.parent = layout
        from .chat_ui_avatar import store as store_avatar
        store_avatar(layout, avatar)
        with measurement_scope():
            _clone_style(node, message, collection, sources, text_source, author_source, text, author)
    except Exception:
        _remove_objects([layout, message], parent)
        raise
    state["layouts"].append(layout.name)
    while len(_layouts(node)) > node.max_messages:
        _remove_row(node, _layouts(node)[0])
    bpy.context.view_layer.update()
    state["signature"] = _signature(node, settings)
    _reflow(node, now, token)
    _remember(node, context, message, len(rows(node)))
    _summary(node)
    return declarations.ObjectRef(message.name, token)


def reset(node, context=None):
    uid = _uid(node)
    for layout in tuple(_discover(node)):
        _remove_row(node, layout)
    # Include orphaned clones after a manual root deletion, but never templates.
    orphaned = [o for o in bpy.data.objects if o.get(OWNER) == uid]
    _remove_objects(orphaned)
    for collection in tuple(bpy.data.collections):
        if collection.get(OWNER) == uid and collection.get(ROLE) == "collection" and not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
    _states.pop(uid, None)
    if context:
        _remember(node, context, None, 0)
    _summary(node)


def _snapshot(node, context):
    payload = getattr(context, "payload", {})
    if isinstance(payload, dict) and isinstance(payload.get("chat_ui"), dict):
        found = payload["chat_ui"].get(_uid(node))
        if isinstance(found, dict):
            return found
    from . import workflow
    run_id = getattr(context, "run_id", None)
    seen = set()
    while run_id and run_id not in seen:
        found = _snapshots.get((_uid(node), str(run_id)))
        if found is not None:
            return found
        seen.add(run_id)
        record = workflow.status(run_id)
        if record:
            found = record["payload"].get("chat_ui", {}).get(_uid(node)) if isinstance(record["payload"], dict) else None
            if found is not None:
                return found
        run_id = record.get("parent") if record else None
    return None


def evaluate(context, node):
    from .runtime import _output
    if node.bl_idname == declarations.OBJECT_VALUE:
        return _output(node, "Object", declarations.ObjectRef(node.object.name) if node.object and not node.mute else None)
    if node.bl_idname == declarations.COLLECTION_VALUE:
        return _output(node, "Collection", declarations.CollectionRef(node.collection.name) if node.collection and not node.mute else None)
    if node.bl_idname != declarations.NODE:
        return None
    value = _snapshot(node, context)
    if node.mute:
        value = {"name": "", "token": "", "count": 0}
    elif value is None:
        messages = rows(node)
        message = messages[-1] if messages else None
        value = {"name": message.name if message else "", "token": message.get(ROW, "") if message else "", "count": len(messages)}
    ref = declarations.ObjectRef(value["name"], value["token"]) if value.get("name") else None
    return {**_output(node, "Message Empty", ref), **_output(node, "Count", float(value["count"]))}


def control(node, action, context):
    from .availability import require_available
    require_available(node)
    if action == "IMPORT":
        from .chat_ui_assets import import_floating_ui
        import_floating_ui(node, context)
        return
    if node.mute:
        raise ValueError("Unmute Chat List first")
    pull = _context(node, context.scene)
    if action == "REFRESH":
        refresh(node, pull)
        return
    if action == "CLEAR":
        reset(node, pull)
        return
    if action != "TEST":
        raise ValueError("Unknown Chat List control")
    # A test message must work even when Text/Name are linked to an idle chat source.
    original = pull.input
    number = max((int(o.get(ORDER, 0)) for o in _layouts(node)), default=0) + 1
    pull.input = lambda target, name, fallback: (
        f"測試留言 {number} · Hello!" if target == node and name == "Text" else
        f"Viewer {number}" if target == node and name == "User Name" else original(target, name, fallback))
    from . import workflow, flow
    run_id = workflow.begin(node.id_data, {"source": "chat_list_test"})
    pull.run_id, pull.payload = run_id, workflow.payload(run_id)
    try:
        append(node, pull)
    except Exception as error:
        workflow.finish(run_id, "failed", str(error))
        raise
    pulse = flow._Pulse(node.id_data, context.scene, pull.now, 0, set(), context=pull, run_id=run_id)
    pulse.emit(node.outputs.get("End"), (_uid(node),))


def tick(_context_unused=None, delta_time=1 / 60, now=None):
    from . import scheduler, workflow
    if _retiring or scheduler.is_suspended():
        return
    now = time.monotonic() if now is None else float(now)
    for key in tuple(_snapshots):
        if not workflow.running(key[1]):
            _snapshots.pop(key, None)
    for tree in tuple(bpy.data.node_groups):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tuple(tree.nodes):
            if node.bl_idname != declarations.NODE or node.mute:
                continue
            state = _state(node)
            layouts = _layouts(node)
            if layouts and not state["avatar_restored"]:
                from .image_nodes import UrlImageRef
                from . import image_runtime
                for layout in layouts:
                    url = str(layout.get("yugo_avatar_url", ""))
                    if url:
                        image_runtime.request(UrlImageRef(url))
                state["avatar_restored"] = True
            while len(layouts) > node.max_messages:
                _remove_row(node, layouts[0])
                layouts = _layouts(node)
                _reflow(node, now)
                _summary(node)
            if layouts and now - state["last_sync"] >= 0.5:
                state["last_sync"] = now
                try:
                    if node.sync_style:
                        refresh(node, _context(node, now=now), force=False)
                    if state["layout_signature"] != (node.direction, float(node.gap)):
                        _reflow(node, now)
                except Exception as error:
                    node.last_summary = f"Error · {error}"
            for layout in layouts:
                from .chat_ui_avatar import poll as poll_avatar
                message = _message(layout)
                if message:
                    poll_avatar(node, message)
                token = layout.get(ROW)
                motion = state["motions"].get(token)
                if motion is None:
                    continue
                t = min(max((now - motion["time"]) / max(motion["seconds"], 1e-9), 0.0), 1.0)
                smooth = t * t * (3 - 2 * t)
                target_location = Vector(motion["start"]).lerp(Vector(motion["target"]), smooth)
                if (layout.location - target_location).length_squared > 1e-14:
                    layout.location = target_location
                if t >= 1:
                    state["motions"].pop(token, None)


def forget(node):
    from . import scheduler
    uid = _uid(node)
    if not _retiring and not scheduler.is_suspended():
        try:
            reset(node)
        except (ReferenceError, RuntimeError, AttributeError):
            pass
    _states.pop(uid, None)
    for key in tuple(_snapshots):
        if key[0] == uid:
            _snapshots.pop(key, None)


def copied(node, _source):
    _states.pop(_uid(node), None)


def clear():
    _states.clear()
    _snapshots.clear()


def register():
    global _retiring
    from . import scheduler
    _retiring = False
    clear()
    scheduler.add_motion("chat_ui_layout", tick, priority=110)


def unregister():
    global _retiring
    from . import scheduler
    _retiring = True
    scheduler.remove_motion("chat_ui_layout")
    clear()
