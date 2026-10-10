"""Row-owned avatar materials; URLs and local images are event snapshots.

Only native object material slots are changed. Shared meshes, template
materials, and another row's avatar remain untouched. Image downloads belong to
image_runtime; polling reads its cache and never starts or retries a download.
"""
import re

import bpy


OWNER = "yugo_chat_owner"
ROW = "yugo_chat_row"
SOURCE = "yugo_chat_source"
URL = "yugo_avatar_url"
IMAGE = "yugo_avatar_image"
MATERIAL = "yugo_avatar_material"
TARGET = "yugo_avatar_target"
READY = "yugo_avatar_ready"


def _uid(node):
    return str(node.runtime_id or node.name)


def _alive(obj):
    try:
        return obj is not None and bpy.data.objects.get(obj.name) == obj
    except (ReferenceError, AttributeError):
        return False


def _ref(url):
    from .image_nodes import UrlImageRef
    return UrlImageRef(url)


def store(layout, value):
    """Persist the event's original input on its owned layout, never upstream."""
    for key in (URL, IMAGE):
        if key in layout:
            del layout[key]
    layout[READY] = False
    if isinstance(value, bpy.types.Image):
        layout[IMAGE] = value
    else:
        from .image_nodes import UrlImageRef
        if isinstance(value, UrlImageRef) and value.url:
            layout[URL] = str(value.url)


def _source_name(obj):
    return obj.name.split(".")[0].casefold()


def _icon(node, message, mapping):
    configured = getattr(node, "icon_object", None)
    if mapping is not None:
        if configured in mapping:
            return configured, mapping[configured]
        return next(((source, clone) for source, clone in mapping.items()
                     if source.type == "MESH" and _source_name(source) == "usericon"), (None, None))
    pairs = []
    for clone in message.children_recursive:
        source = clone.get(SOURCE)
        if isinstance(source, bpy.types.Object) and clone.type == "MESH":
            if source == configured:
                return source, clone
            pairs.append((source, clone))
    return next(((source, clone) for source, clone in pairs if _source_name(source) == "usericon"), (None, None))


def _slot(node, source):
    configured = getattr(node, "icon_material", None)
    if configured:
        for index, slot in enumerate(source.material_slots):
            if slot.material == configured:
                return index, slot.material
    for index, slot in enumerate(source.material_slots):
        if slot.material and slot.material.name.split(".")[0].casefold() == "userpanel":
            return index, slot.material
    return None, None


def _texture_nodes(material):
    if not material.node_tree:
        return []
    nodes = [node for node in material.node_tree.nodes if node.type == "TEX_IMAGE"]
    tagged = [node for node in nodes if node.get(TARGET)]
    if tagged:
        return tagged
    matches = []
    for node in nodes:
        image = node.image
        if image is None:
            continue
        name = re.sub(r"\.\d{3}$", "", image.name).casefold()
        filepath = (image.filepath or "").replace("\\", "/").split("/")[-1].casefold()
        if name == "user_icon.jpg" or filepath == "user_icon.jpg":
            matches.append(node)
    return matches or (nodes if len(nodes) == 1 else [])


def _set_image(material, image):
    if not isinstance(image, bpy.types.Image):
        return False
    changed = False
    for texture in _texture_nodes(material):
        if not texture.get(TARGET):
            texture[TARGET] = True
        if texture.image != image:
            texture.image = image
            changed = True
    return changed


def _input_image(layout, request=False):
    image = layout.get(IMAGE)
    if isinstance(image, bpy.types.Image):
        return image
    url = str(layout.get(URL, "") or "")
    if not url:
        return None
    from . import image_runtime
    return image_runtime.request(_ref(url)) if request else image_runtime.get(_ref(url))


def apply(node, message, mapping=None):
    """Create this row's isolated material, requesting its URL once on apply."""
    if not _alive(message) or message.parent is None:
        return None
    layout = message.parent
    if not layout.get(URL) and not isinstance(layout.get(IMAGE), bpy.types.Image):
        # A row without an avatar input keeps every template material shared.
        layout[READY] = True
        return None
    source, clone = _icon(node, message, mapping)
    if source is None or clone is None:
        return None
    index, template = _slot(node, source)
    if template is None or index >= len(clone.material_slots):
        return None
    layout[READY] = False
    owned = template.copy()
    owned.name = f"{template.name} · {message.name} Avatar"
    owned[OWNER], owned[ROW], owned[MATERIAL] = _uid(node), str(message.get(ROW, "")), True
    if layout.get(URL):
        owned[URL] = str(layout[URL])
    if isinstance(layout.get(IMAGE), bpy.types.Image):
        owned[IMAGE] = layout[IMAGE]
    for texture in _texture_nodes(owned):
        if not texture.get(TARGET):
            texture[TARGET] = True
    slot = clone.material_slots[index]
    previous = slot.material
    previous_link = slot.link
    try:
        # OBJECT linking leaves clone.data.materials (shared with Style) intact.
        slot.link = "OBJECT"
        slot.material = owned
        image = _input_image(layout, request=True)
        _set_image(owned, image)
        if isinstance(image, bpy.types.Image):
            layout[READY] = True
    except Exception:
        slot.material = previous
        slot.link = previous_link
        dispose((owned,))
        raise
    if previous != template:
        dispose((previous,))
    return owned


def poll(node, message):
    """Apply only a cached result belonging to this live row's saved input."""
    if not _alive(message) or message.parent is None:
        return False
    layout = message.parent
    if layout.get(READY):
        return False
    image = _input_image(layout)
    if not isinstance(image, bpy.types.Image):
        return False
    uid, token = _uid(node), str(message.get(ROW, ""))
    changed = False
    visited = set()
    for clone in message.children_recursive:
        for slot in clone.material_slots:
            material = slot.material
            if material is None or material.as_pointer() in visited:
                continue
            visited.add(material.as_pointer())
            if material.get(MATERIAL) and material.get(OWNER) == uid and material.get(ROW) == token:
                changed = _set_image(material, image) or changed
    layout[READY] = True
    return changed


def dispose(materials):
    """Remove unused owned avatar copies; retain templates and image datablocks."""
    for material in set(materials):
        try:
            if (isinstance(material, bpy.types.Material) and material.get(MATERIAL)
                    and material.get(OWNER) and material.users == 0 and not material.library):
                bpy.data.materials.remove(material)
        except ReferenceError:
            pass
