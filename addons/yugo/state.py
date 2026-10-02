"""Shared accessors and stable locators for Yugo's per-avatar data model."""

import uuid

import bpy


def active_avatar(scene):
    avatars = getattr(scene, "yugo_avatars", ())
    index = int(getattr(scene, "yugo_avatar_index", -1))
    if 0 <= index < len(avatars):
        return avatars[index]
    return None


def active_armature(scene):
    avatar = active_avatar(scene)
    return getattr(avatar, "armature", None) if avatar is not None else None


def active_layers(scene):
    avatar = active_avatar(scene)
    return getattr(avatar, "motion_layers", ()) if avatar is not None else ()


def iter_avatars(scene):
    return tuple(getattr(scene, "yugo_avatars", ()))


def find_avatar_for_layer(scene, layer):
    try:
        pointer = layer.as_pointer()
    except (AttributeError, ReferenceError):
        return None
    for avatar in iter_avatars(scene):
        for candidate in avatar.motion_layers:
            try:
                if candidate.as_pointer() == pointer:
                    return avatar
            except ReferenceError:
                continue
    return None


def find_avatar_for_keybind(scene, keybind):
    try:
        pointer = keybind.as_pointer()
    except (AttributeError, ReferenceError):
        return None
    for avatar in iter_avatars(scene):
        for layer in avatar.motion_layers:
            for candidate in layer.keybinds:
                try:
                    if candidate.as_pointer() == pointer:
                        return avatar
                except ReferenceError:
                    continue
    return None


def layers_for_armature(scene, armature):
    if armature is None:
        return ()
    try:
        pointer = armature.as_pointer()
    except (AttributeError, ReferenceError):
        return ()
    for avatar in iter_avatars(scene):
        try:
            if avatar.armature is not None and avatar.armature.as_pointer() == pointer:
                return avatar.motion_layers
        except ReferenceError:
            continue
    return ()


def ensure_runtime_id(item):
    try:
        if not item.runtime_id:
            item.runtime_id = uuid.uuid4().hex
        return str(item.runtime_id)
    except (AttributeError, ReferenceError, RuntimeError):
        return ""


def layer_locator(scene, layer):
    try:
        scene_name = scene.name
        layer_pointer = layer.as_pointer()
    except (AttributeError, ReferenceError):
        return None
    for avatar_index, avatar in enumerate(iter_avatars(scene)):
        for layer_index, candidate in enumerate(avatar.motion_layers):
            try:
                if candidate.as_pointer() != layer_pointer:
                    continue
                return {
                    "scene": scene_name,
                    "avatar_id": ensure_runtime_id(avatar),
                    "avatar_index": avatar_index,
                    "layer_id": ensure_runtime_id(candidate),
                    "layer_index": layer_index,
                    "source_type": str(candidate.source_type),
                    "armature_name": (
                        avatar.armature.name if avatar.armature is not None else ""
                    ),
                }
            except ReferenceError:
                continue
    return None


def resolve_layer_locator(locator):
    if not locator:
        return None
    scene = bpy.data.scenes.get(locator.get("scene", ""))
    if scene is None:
        return None
    avatars = scene.yugo_avatars
    avatar = next(
        (
            item
            for item in avatars
            if locator.get("avatar_id")
            and str(getattr(item, "runtime_id", "")) == locator["avatar_id"]
        ),
        None,
    )
    if avatar is None:
        index = int(locator.get("avatar_index", -1))
        if 0 <= index < len(avatars):
            avatar = avatars[index]
            if not avatar.runtime_id and locator.get("avatar_id"):
                avatar.runtime_id = locator["avatar_id"]
    if avatar is None:
        return None

    layers = avatar.motion_layers
    layer = next(
        (
            item
            for item in layers
            if locator.get("layer_id")
            and str(getattr(item, "runtime_id", "")) == locator["layer_id"]
        ),
        None,
    )
    if layer is None:
        index = int(locator.get("layer_index", -1))
        if 0 <= index < len(layers):
            candidate = layers[index]
            if str(candidate.source_type) == locator.get("source_type", ""):
                layer = candidate
                if not layer.runtime_id and locator.get("layer_id"):
                    layer.runtime_id = locator["layer_id"]
    if layer is None:
        return None
    return scene, avatar, layer, avatar.armature
