"""Collection-based chat display; the caller owns event delivery and scheduling.

This module never imports Studio, registers timers, performs network requests, or
changes the source template. Call all methods from Blender's main thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import time
import uuid

import bpy
from mathutils import Matrix, Vector

from .model import Message, StackModel

OWNER = "floating_ui_owner"
ROLE = "floating_ui_role"


class TemplateError(ValueError):
    """The artist's template cannot be bound to a message display."""


def load_template(filepath: str, collection_name: str = "Floating UI"):
    """Append one Collection from a .blend library without placing it in a scene."""
    path = Path(bpy.path.abspath(filepath)).resolve()
    if path.suffix.lower() != ".blend" or not path.is_file():
        raise TemplateError("Choose an existing .blend file containing the UI template.")
    if not collection_name:
        raise TemplateError("Enter the Collection name inside the .blend file.")
    with bpy.data.libraries.load(str(path), link=False) as (source, destination):
        if collection_name not in source.collections:
            raise TemplateError(f"Collection '{collection_name}' is not in {path.name}.")
        destination.collections = [collection_name]
    collection = destination.collections[0]
    if collection is None:
        raise TemplateError("Blender could not append the selected Collection.")
    return collection


def _binding(objects, name, role, object_type, *, required=True):
    if name:
        candidates = [obj for obj in objects if obj.name == name]
    else:
        candidates = [obj for obj in objects if obj.get(ROLE) == role]
        if not candidates and role == "message":
            candidates = [obj for obj in objects if obj.type == "FONT" and obj.get(ROLE) != "author"]
    if not candidates and not required:
        return None
    if len(candidates) != 1 or candidates[0].type != object_type:
        raise TemplateError(
            f"Select one {object_type} object for '{role}', or set its custom property "
            f"{ROLE} = '{role}'."
        )
    return candidates[0]


def _avatar_binding(icon, node_name):
    candidates = []
    for index, slot in enumerate(icon.material_slots):
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and (
                not node_name or node.name == node_name or node.label == node_name
            ):
                candidates.append((index, node.name, node.label))
    if not node_name:
        tagged = [entry for entry in candidates if entry[2] == "floating_ui_avatar"]
        if tagged:
            candidates = tagged
    if len(candidates) != 1:
        raise TemplateError(
            "The icon needs one top-level Image Texture node. Select its node name "
            "or label it 'floating_ui_avatar'."
        )
    return candidates[0][:2]


def _authored_world(obj):
    """Static template transforms also work for unlinked appended Collections.

    Such source objects have no evaluated depsgraph matrix_world yet. Preserve
    the authored object-parent hierarchy instead of using that cached matrix.
    """
    basis = obj.matrix_basis.copy()
    if obj.parent is None:
        return basis
    if obj.parent_type != "OBJECT":
        raise TemplateError("Template parenting must use objects, not bones or vertices.")
    return _authored_world(obj.parent) @ obj.matrix_parent_inverse @ basis


@dataclass
class Instance:
    key: str
    collection: object
    root: object
    objects: dict = field(default_factory=dict)
    message_object: object = None
    icon_object: object = None
    owned: list = field(default_factory=list)


class FloatingUIService:
    """One display owner, an editable Collection template, and bounded live rows."""

    def __init__(
        self, scene, template, anchor=None, max_messages=8, spacing=1.0,
        move_duration=.25, text_object="", icon_object="", avatar_node="",
    ):
        if not isinstance(scene, bpy.types.Scene):
            raise TemplateError("Choose a scene for the generated UI.")
        if not isinstance(template, bpy.types.Collection):
            raise TemplateError("Choose a UI template Collection.")
        if template.get(OWNER):
            raise TemplateError("Choose the source template, not a generated live row.")
        if anchor is not None and not isinstance(anchor, bpy.types.Object):
            raise TemplateError("The anchor must be a Blender object.")
        objects = tuple(template.all_objects)
        self.message_source = _binding(objects, text_object, "message", "FONT")
        self.icon_source = _binding(objects, icon_object, "icon", "MESH")
        self.author_source = _binding(objects, "", "author", "FONT", required=False)
        if self.author_source == self.message_source:
            raise TemplateError("Message and author must use different Text objects.")
        self.avatar_slot, self.avatar_node = _avatar_binding(self.icon_source, avatar_node)
        self._avatar_node_request = avatar_node
        self.model = StackModel(max_messages, spacing, move_duration)
        self.scene = scene
        self.template = template
        self.anchor = anchor
        self.owner = uuid.uuid4().hex
        self.instances: dict[str, Instance] = {}
        self.last_now = 0.0
        self.closed = False
        self._avatar_images = []
        self.output = bpy.data.collections.new("Floating UI • Live")
        self.output[OWNER] = self.owner
        scene.collection.children.link(self.output)
        self.panel_root = bpy.data.objects.new("Floating UI • Anchor", None)
        self.panel_root[OWNER] = self.owner
        self.output.objects.link(self.panel_root)
        if anchor is not None:
            self.panel_root.parent = anchor
            self.panel_root.matrix_parent_inverse = Matrix.Identity(4)
            self.panel_root.matrix_basis = Matrix.Identity(4)

    def _time(self, now):
        if now is None:
            now = max(self.last_now, time.monotonic())
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("Update time must be finite.")
        return now

    def _check(self):
        if self.closed:
            raise RuntimeError("This Floating UI display has been cleaned up.")
        if not self.is_valid():
            raise RuntimeError("The generated display was removed; recreate the preview.")

    def is_valid(self):
        """Detect manual deletion, including a partially deleted generated row."""
        if self.closed:
            return False
        try:
            source_objects = set(self.template.all_objects)
            if self.message_source not in source_objects or self.icon_source not in source_objects:
                return False
            if self.author_source is not None and self.author_source not in source_objects:
                return False
            if self.output.get(OWNER) != self.owner or self.panel_root.get(OWNER) != self.owner:
                return False
            for instance in self.instances.values():
                for block in (instance.root, instance.collection, *instance.objects.values()):
                    if block.get(OWNER) != self.owner:
                        return False
            return True
        except ReferenceError:
            return False

    def _copy_data(self, block, instance):
        copy = block.copy()
        copy[OWNER] = self.owner
        copy.use_fake_user = False
        instance.owned.append(copy)
        if hasattr(copy, "animation_data_clear"):
            copy.animation_data_clear()
        return copy

    def _clone(self, message):
        # Artists can edit a template's shader between arrivals. Resolve its
        # replacement slot again before allocating a row or changing the queue.
        self.avatar_slot, self.avatar_node = _avatar_binding(self.icon_source, self._avatar_node_request)
        collection = bpy.data.collections.new("Floating UI • " + message.id[:32])
        collection[OWNER] = self.owner
        self.output.children.link(collection)
        root = bpy.data.objects.new("Floating UI • Row", None)
        root[OWNER] = self.owner
        collection.objects.link(root)
        root.parent = self.panel_root
        instance = Instance(message.id, collection, root)
        try:
            source_objects = tuple(self.template.all_objects)
            object_map = {}
            material_map = {}
            for source in source_objects:
                obj = source.copy()
                obj[OWNER] = self.owner
                # Track immediately so a later data/material failure can roll
                # back even this not-yet-linked object.
                instance.objects[source.name] = obj
                obj.animation_data_clear()
                if obj.data is not None:
                    obj.data = self._copy_data(source.data, instance)
                # Copying geometry ensures material slots cannot mutate template data.
                for slot in obj.material_slots:
                    if slot.material is not None:
                        original = slot.material
                        key = (original, source == self.icon_source)
                        if key not in material_map:
                            material_map[key] = self._copy_data(original, instance)
                        slot.material = material_map[key]
                object_map[source] = obj
            collection_map = {self.template: collection}

            def clone_membership(source_collection):
                destination = collection_map[source_collection]
                for source in source_collection.objects:
                    destination.objects.link(object_map[source])
                for child in source_collection.children:
                    copied = bpy.data.collections.new(child.name + " • Live")
                    copied[OWNER] = self.owner
                    copied.hide_render = child.hide_render
                    copied.hide_viewport = child.hide_viewport
                    destination.children.link(copied)
                    collection_map[child] = copied
                    clone_membership(child)

            clone_membership(self.template)
            offset = Matrix.Translation(-Vector(self.template.instance_offset))
            for source, obj in object_map.items():
                if source.parent in object_map:
                    obj.parent = object_map[source.parent]
                    obj.matrix_parent_inverse = source.matrix_parent_inverse.copy()
                    obj.matrix_basis = source.matrix_basis.copy()
                else:
                    obj.parent = root
                    obj.matrix_parent_inverse = Matrix.Identity(4)
                    obj.matrix_basis = offset @ _authored_world(source)
                # Remap object references in constraints/modifiers to this row.
                for holder in tuple(obj.constraints) + tuple(obj.modifiers):
                    for prop in holder.bl_rna.properties:
                        if prop.type == "POINTER" and not prop.is_readonly:
                            target = getattr(holder, prop.identifier, None)
                            if isinstance(target, bpy.types.Object) and target in object_map:
                                setattr(holder, prop.identifier, object_map[target])
            instance.message_object = object_map[self.message_source]
            instance.icon_object = object_map[self.icon_source]
            instance.message_object.data.body = message.text
            if self.author_source is not None:
                object_map[self.author_source].data.body = message.author
            if message.avatar_path:
                image = self._image(message.avatar_path)
                material = instance.icon_object.material_slots[self.avatar_slot].material
                material.node_tree.nodes[self.avatar_node].image = image
            return instance
        except Exception:
            self._destroy(instance)
            self._prune_images()
            raise

    def _image(self, path):
        filepath = str(Path(bpy.path.abspath(path)).resolve())
        if not Path(filepath).is_file():
            raise ValueError("The avatar image file does not exist: " + filepath)
        # Reuse an existing image, but never mutate its pixels/path/packing.
        existing = {image.as_pointer() for image in bpy.data.images}
        image = bpy.data.images.load(filepath, check_existing=True)
        if image.get(OWNER) and image.get(OWNER) != self.owner:
            # Another display owns this image's cleanup. Keep our own reference
            # independent so teardown ordering cannot leave an orphaned image.
            image = bpy.data.images.load(filepath, check_existing=False)
        if image.as_pointer() not in existing:
            image[OWNER] = self.owner
            self._avatar_images.append(image)
        return image

    def push(self, payload, now=None):
        """Consume one event snapshot. Repeated active IDs are ignored."""
        self._check()
        message = Message.from_payload(payload)
        now = self._time(now)
        if message.id in self.instances:
            return message.id
        # Validate/build before changing the visible message list.
        instance = self._clone(message)
        try:
            evicted = self.model.add(message, now)
        except Exception:
            self._destroy(instance)
            self._prune_images()
            raise
        self.instances[message.id] = instance
        for key in evicted:
            self._destroy(self.instances.pop(key))
        self.update(now)
        return message.id

    def update(self, now=None):
        """Advance animation from the caller's clock; does not wait or schedule."""
        self._check()
        now = self._time(now)
        for key, y in self.model.positions(now).items():
            self.instances[key].root.location.y = y
        self.last_now = now
        self._prune_images()

    def remove(self, message_id, now=None):
        self._check()
        now = self._time(now)
        if not self.model.remove(message_id, now):
            return False
        self._destroy(self.instances.pop(message_id))
        self.update(now)
        return True

    def _destroy(self, instance):
        def remove_collection(collection):
            try:
                children = tuple(collection.children)
            except ReferenceError:
                return
            for child in children:
                remove_collection(child)
            try:
                if collection.get(OWNER) == self.owner:
                    bpy.data.collections.remove(collection)
            except ReferenceError:
                pass

        for obj in tuple(instance.objects.values()) + (instance.root,):
            try:
                if obj.get(OWNER) == self.owner:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        remove_collection(instance.collection)
        # Geometry can hold material users after its Object is removed. Repeat
        # to release dependencies in order, retaining genuinely adopted data.
        remaining = list(instance.owned)
        while remaining:
            pending = []
            removed = False
            for block in remaining:
                try:
                    if block.users == 0 and block.get(OWNER) == self.owner:
                        bpy.data.batch_remove(ids=(block,))
                        removed = True
                    else:
                        pending.append(block)
                except ReferenceError:
                    removed = True
            if not removed:
                break
            remaining = pending

    def _prune_images(self):
        live = []
        for image in self._avatar_images:
            try:
                if image.users == 0 and image.get(OWNER) == self.owner:
                    bpy.data.images.remove(image)
                else:
                    live.append(image)
            except ReferenceError:
                pass
        self._avatar_images = live

    def clear(self):
        """Remove this owner's rows, retaining the template and anchor."""
        if self.closed:
            return
        for instance in tuple(self.instances.values()):
            self._destroy(instance)
        self.instances.clear()
        self.model.clear()
        self._prune_images()

    def cleanup(self):
        """Idempotent teardown, including after Blender invalidates saved RNA."""
        if self.closed:
            return
        self.clear()
        for block, database in ((self.panel_root, bpy.data.objects), (self.output, bpy.data.collections)):
            try:
                if block.get(OWNER) == self.owner:
                    database.remove(block, do_unlink=True)
            except ReferenceError:
                pass
        self.closed = True


def cleanup_saved_outputs():
    """Preview recovery: remove only tagged generated outputs after load/undo.

    This is called explicitly by preview lifecycle hooks, never at module import.
    Templates are untagged and are retained. Adopted datablocks with other users
    are retained as well.
    """
    for obj in tuple(bpy.data.objects):
        if obj.get(OWNER):
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        if collection.get(OWNER):
            bpy.data.collections.remove(collection)
    for database in (bpy.data.curves, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in tuple(database):
            if block.get(OWNER) and block.users == 0:
                database.remove(block)
