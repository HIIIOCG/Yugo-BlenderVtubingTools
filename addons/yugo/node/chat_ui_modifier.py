"""Version-independent modifier inputs used by Chat List style copies.

Blender 5.1 stores Geometry Nodes inputs as modifier ID properties. Blender
5.2 exposes RNA input wrappers under ``properties.inputs`` instead. Never
write through the template: ``remap`` receives only a generated copy.
"""
import math

import bpy


_READ_ERRORS = (AttributeError, TypeError, RuntimeError, ReferenceError, KeyError)


def _legacy_items(block):
    try:
        keys = tuple(block.keys())
    except _READ_ERRORS:
        return ()
    result = []
    for key in keys:
        try:
            result.append((key, block[key]))
        except _READ_ERRORS:
            pass
    return tuple(result)


def _input_wrappers(block):
    try:
        inputs = block.properties.inputs
        properties = inputs.bl_rna.properties
    except _READ_ERRORS:
        return ()
    result = []
    for prop in properties:
        if prop.identifier == "rna_type" or prop.type != "POINTER":
            continue
        try:
            wrapper = getattr(inputs, prop.identifier)
            if wrapper is not None:
                result.append((prop.identifier, wrapper))
        except _READ_ERRORS:
            pass
    return tuple(result)


def _value(value):
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.as_pointer())
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, (str, bool, int, type(None))):
        return value
    try:
        if hasattr(value, "keys"):
            return tuple((key, _value(value[key])) for key in sorted(value.keys()))
        return tuple(_value(item) for item in value)
    except _READ_ERRORS:
        return None


def _fields(block):
    result = []
    for prop in block.bl_rna.properties:
        if prop.is_readonly or prop.type == "COLLECTION":
            continue
        try:
            result.append((prop.identifier, _value(getattr(block, prop.identifier))))
        except _READ_ERRORS:
            pass
    return tuple(result)


def remap(block, mapping):
    """Retarget copied Geometry Nodes Object inputs; retain external objects."""
    changed = False
    for key, value in _legacy_items(block):
        if not isinstance(value, bpy.types.Object) or value not in mapping:
            continue
        try:
            block[key] = mapping[value]
            changed = True
        except _READ_ERRORS:
            pass
    for _identifier, wrapper in _input_wrappers(block):
        try:
            value = wrapper.value
            if isinstance(value, bpy.types.Object) and value in mapping:
                wrapper.value = mapping[value]
                changed = True
        except _READ_ERRORS:
            pass
    return changed


def signature(block):
    """Capture hidden node inputs and constraint target collections stably."""
    legacy = tuple(sorted((key, _value(value)) for key, value in _legacy_items(block)))
    inputs = tuple((identifier, _fields(wrapper)) for identifier, wrapper in _input_wrappers(block))
    try:
        targets = tuple(_fields(target) for target in block.targets)
    except _READ_ERRORS:
        targets = ()
    return (legacy, inputs, targets)
