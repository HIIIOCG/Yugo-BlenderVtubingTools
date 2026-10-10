"""Measure native text geometry without evaluating the user's scene.

A measurement scope lazily creates one temporary scene and one plain FONT
object. Its dependency graph evaluates only the font being measured. Source
font data is borrowed for the scope; objects, parents, and modifiers are not.
No window scene changes and no temporary data survives scope exit.
"""
from contextlib import contextmanager
import threading

import bpy

_active = None


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Font geometry must be measured on Blender's main thread")


class _Measurements:
    def __init__(self):
        self.scene = None
        self.probe = None
        self.empty = None

    def bounds(self, obj):
        if obj.type != "FONT" or obj.data is None:
            raise ValueError("Font measurement requires a FONT object")
        if self.scene is None:
            self.scene = bpy.data.scenes.new("Yugo temporary font metrics")
            try:
                self.empty = bpy.data.curves.new("Yugo temporary empty font", "FONT")
                self.probe = bpy.data.objects.new("Yugo temporary font probe", self.empty)
                self.scene.collection.objects.link(self.probe)
            except Exception:
                self.close()
                raise
        if self.probe.data != obj.data:
            self.probe.data = obj.data
        try:
            layer = self.scene.view_layers[0]
            layer.update()
            evaluated = self.probe.evaluated_get(layer.depsgraph)
            return tuple(tuple(float(value) for value in point) for point in evaluated.bound_box)
        finally:
            # Rollback may delete a copied curve before its outer scope exits.
            # Retain the reusable object/scene, but release each borrowed font
            # immediately after copying its bounds into ordinary Python values.
            # FONT.data=None is silently ignored in Blender 5.1 and 5.2.
            # An owned empty curve releases the borrowed user reliably.
            self.probe.data = self.empty

    def close(self):
        if self.probe is not None:
            bpy.data.objects.remove(self.probe, do_unlink=True)
            self.probe = None
        if self.scene is not None:
            bpy.data.scenes.remove(self.scene)
            self.scene = None
        if self.empty is not None:
            bpy.data.curves.remove(self.empty)
            self.empty = None


@contextmanager
def measurement_scope():
    """Reuse one isolated graph for an entire wrapping/style-copy batch."""
    global _active
    _main_thread()
    if _active is not None:
        yield _active
        return
    session = _Measurements()
    _active = session
    try:
        yield session
    finally:
        _active = None
        session.close()


def measure(obj):
    """Return the native FONT data's local bounding box as plain tuples."""
    _main_thread()
    if _active is not None:
        return _active.bounds(obj)
    with measurement_scope() as session:
        return session.bounds(obj)
