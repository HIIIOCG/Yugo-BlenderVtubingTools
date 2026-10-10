"""Append the complete built-in chat style and bind only its actual local IDs.

An imported asset belongs to one Chat List. Saved pointers and role tags allow
that node to reuse the asset after renaming or reopening a file. Names elsewhere
in the scene never determine which collection or object is bound.
"""
from pathlib import Path
import re
import time

import bpy


IMPORT_COLLECTION = "import_collection"
ASSET_OWNER = "yugo_chat_asset_owner"
ASSET_ROOT = "yugo_chat_asset_root"
ASSET_ROLE = "yugo_chat_asset_role"
ROOT_NAME = "Floating UI"
_ROOT_VERSION = "floating_ui_v1"
_ROLE_OBJECTS = {"text": ("ChatText", "FONT"), "name": ("UserName", "FONT"),
                 "panel": ("ChatPanel", "MESH"), "icon": ("UserIcon", "MESH")}
_DATA_GROUPS = ("objects", "collections", "node_groups", "curves", "meshes",
                "materials", "fonts", "images", "libraries")


def _original_name(name):
    return re.sub(r"\.\d{3,}$", "", str(name)).casefold()


def _tree(root):
    pending, seen = [root], set()
    while pending:
        collection = pending.pop()
        pointer = collection.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        yield collection
        pending.extend(collection.children)


def _find(items, role, original_name, kind=None):
    tagged = [item for item in items if item.get(ASSET_ROLE) == role]
    candidates = tagged or [item for item in items
                            if _original_name(item.name) == original_name.casefold()]
    candidates = [item for item in candidates if kind is None or item.type == kind]
    if len(candidates) != 1:
        raise ValueError(f'Floating UI needs one {original_name} {kind or "collection"}')
    return candidates[0]


def _bindings(root):
    collections = tuple(_tree(root))
    style = _find(collections, "style", "UI_Style")
    scene_branch = _find(collections, "scene", "UI_3D")
    if style == scene_branch or root in (style, scene_branch):
        raise ValueError("Floating UI must have separate UI_Style and UI_3D branches")
    parent = _find(tuple(scene_branch.all_objects), "parent", "UI_Transform", "EMPTY")
    objects = tuple(style.all_objects)
    roles = {role: _find(objects, role, name, kind)
             for role, (name, kind) in _ROLE_OBJECTS.items()}
    if parent in objects:
        raise ValueError("Floating UI Parent must be outside UI_Style")
    materials = tuple(material for material in roles["icon"].data.materials if material)
    material = _find(materials, "icon_material", "UserPanel")
    return style, scene_branch, parent, roles, material


def _images(materials):
    images, trees, pending = {}, set(), [material.node_tree for material in materials
                                       if material and material.node_tree]
    while pending:
        tree = pending.pop()
        if tree.as_pointer() in trees:
            continue
        trees.add(tree.as_pointer())
        for node in tree.nodes:
            image = getattr(node, "image", None)
            if image is not None:
                images[image.as_pointer()] = image
            nested = getattr(node, "node_tree", None)
            if nested is not None:
                pending.append(nested)
    return tuple(images.values())


def _pack_images(style, preset):
    materials = {material for obj in style.all_objects if hasattr(obj.data, "materials")
                 for material in obj.data.materials if material}
    for image in _images(materials):
        if image.source != "FILE":
            continue
        if not image.packed_file:
            # The saved template uses an external image. Resolve against the
            # installed asset folder before the template's original machine path.
            # Blender's // prefix denotes a path relative to the main file;
            # Windows Path would interpret //folder/file.jpg as a UNC share
            # and return an empty .name for that valid Blender image path.
            filename = image.filepath.replace("\\", "/").rsplit("/", 1)[-1]
            if not filename and _original_name(image.name) == "user_icon.jpg":
                filename = "User_icon.jpg"
            if not filename:
                dimensions = tuple(image.size)
                if min(dimensions) <= 0 or not image.has_data:
                    raise ValueError(f'Floating UI image is unavailable: {image.name}')
                image.pack()
                continue
            paths = [preset.parent / "assets" / filename, preset.parent / filename,
                     Path(bpy.path.abspath(image.filepath)), preset.parent.parent / filename]
            path = next((candidate for candidate in paths if candidate.is_file()), None)
            if path is None:
                raise ValueError(f'Floating UI image is unavailable: {filename}')
            image.filepath = str(path.resolve())
            image.reload()
            image.pack()
        # Accessing dimensions loads Blender's lazy image buffer in background
        # mode; checking has_data first would reject a valid packed file.
        dimensions = tuple(image.size)
        if min(dimensions) <= 0 or not image.has_data:
            raise ValueError(f'Floating UI image could not be loaded: {image.name}')


def _snapshot():
    return {name: {item.as_pointer() for item in getattr(bpy.data, name)}
            for name in _DATA_GROUPS}


def _rollback(previous):
    # Only IDs created by this synchronous append are eligible for cleanup.
    for name in _DATA_GROUPS:
        items = getattr(bpy.data, name)
        for item in tuple(items):
            if item.as_pointer() not in previous[name]:
                items.remove(item, do_unlink=True)


def _saved_root(node, uid):
    try:
        root = node.get(IMPORT_COLLECTION)
        if (isinstance(root, bpy.types.Collection) and root.library is None
                and root.get(ASSET_OWNER) == uid
                and root.get(ASSET_ROOT) == _ROOT_VERSION):
            _bindings(root)
            return root
    except (ReferenceError, ValueError):
        pass
    return None


def import_floating_ui(node, context):
    """Append/reuse this node's asset, bind its roles, and hide its template.

    Linked inputs are rejected before any IDs are appended so no unused asset
    can be created while the graph would continue reading a different style.
    """
    if context is None or getattr(context, "scene", None) is None:
        raise ValueError("Import Floating UI needs an active scene")
    for name in ("Style", "Parent"):
        socket = node.inputs.get(name)
        if socket is None:
            raise ValueError(f"Chat List is missing its {name} input")
        if socket.is_linked:
            raise ValueError(f"Unlink {name} before importing Floating UI")
    from .nodes import ensure_node_id
    uid = ensure_node_id(node)
    root = _saved_root(node, uid)
    reused = root is not None
    if root is None:
        preset = Path(__file__).with_name("assets") / "floating_ui.blend"
        if not preset.is_file():
            raise ValueError("Built-in Floating UI preset is unavailable")
        previous = _snapshot()
        try:
            with bpy.data.libraries.load(str(preset), link=False, reuse_local_id=False) as (source, target):
                name = next((name for name in source.collections
                             if name.casefold() == ROOT_NAME.casefold()), None)
                if name is None:
                    raise ValueError("Built-in preset has no Floating UI collection")
                target.collections = [name]
            root = target.collections[0]
            if root is None:
                raise ValueError("Floating UI could not be appended")
            style, branch, parent, roles, material = _bindings(root)
            _pack_images(style, preset)
            root[ASSET_OWNER], root[ASSET_ROOT] = uid, _ROOT_VERSION
            style[ASSET_ROLE], branch[ASSET_ROLE] = "style", "scene"
            parent[ASSET_ROLE] = "parent"
            for role, obj in roles.items():
                obj[ASSET_ROLE] = role
            material[ASSET_ROLE] = "icon_material"
        except Exception:
            _rollback(previous)
            raise
    style, _branch, parent, roles, material = _bindings(root)
    if root not in tuple(context.scene.collection.children):
        context.scene.collection.children.link(root)
    node[IMPORT_COLLECTION] = root
    node.inputs["Style"].default_value = style
    node.inputs["Parent"].default_value = parent
    node.text_object, node.name_object = roles["text"], roles["name"]
    node.panel_object, node.icon_object = roles["panel"], roles["icon"]
    node.icon_material = material
    set_style_hidden(node, context)
    node.last_summary = "Floating UI ready" if reused else "Floating UI imported"
    return root


def set_style_hidden(node, context):
    """Set only the effective Style collection's viewport/render visibility."""
    if context is None or getattr(context, "scene", None) is None:
        return None
    socket = node.inputs.get("Style")
    if socket is None:
        return None
    from .chat_ui_runtime import resolve_collection
    from .flow import PullContext
    pull = PullContext(node.id_data, context.scene, time.monotonic(), 0)
    style = resolve_collection(pull.input(node, "Style", socket.default_value))
    if style is None:
        return None
    if style.library is not None:
        raise ValueError("Style collection must be local to change visibility")
    style.hide_viewport = style.hide_render = bool(node.hide_style)
    return style
