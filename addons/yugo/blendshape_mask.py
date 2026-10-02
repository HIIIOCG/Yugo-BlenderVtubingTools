"""VRM expression discovery and per-layer BlendShape Mask helpers."""

from .avatar_status import ARKIT_BLENDSHAPES
from .ifacialmocap.protocol import expression_aliases, normalize_expression_name
from .vrm import get_vrm_extension


ARKIT_NORMALIZED_NAMES = {
    normalize_expression_name(name) for name in ARKIT_BLENDSHAPES
}

VRM1_PRESET_EXPRESSIONS = (
    ("happy", "happy"),
    ("angry", "angry"),
    ("sad", "sad"),
    ("relaxed", "relaxed"),
    ("surprised", "surprised"),
    ("aa", "aa"),
    ("ih", "ih"),
    ("ou", "ou"),
    ("ee", "ee"),
    ("oh", "oh"),
    ("blink", "blink"),
    ("blink_left", "blinkLeft"),
    ("blink_right", "blinkRight"),
    ("look_up", "lookUp"),
    ("look_down", "lookDown"),
    ("look_left", "lookLeft"),
    ("look_right", "lookRight"),
    ("neutral", "neutral"),
)


def _has_vrm0_bind(expression):
    for bind in getattr(expression, "binds", ()):
        mesh = getattr(bind, "mesh", None)
        if getattr(mesh, "mesh_object_name", "") and getattr(bind, "index", ""):
            return True
    return False


def _has_vrm1_bind(expression):
    for bind in getattr(expression, "morph_target_binds", ()):
        node = getattr(bind, "node", None)
        if getattr(node, "mesh_object_name", "") and getattr(bind, "index", ""):
            return True
    return False


def iter_bound_expressions(armature):
    """Return configured ``(display_name, expression)`` pairs for VRM 0.x/1.0."""

    extension = get_vrm_extension(armature)
    if extension is None:
        return []

    version = str(getattr(extension, "spec_version", ""))
    expressions = []
    if version == "0.0":
        master = getattr(getattr(extension, "vrm0", None), "blend_shape_master", None)
        for expression in getattr(master, "blend_shape_groups", ()):
            name = str(getattr(expression, "name", "") or "").strip()
            if name and _has_vrm0_bind(expression):
                expressions.append((name, expression))
    elif version == "1.0":
        vrm1_expressions = getattr(
            getattr(extension, "vrm1", None), "expressions", None
        )
        preset = getattr(vrm1_expressions, "preset", None)
        for property_name, display_name in VRM1_PRESET_EXPRESSIONS:
            expression = getattr(preset, property_name, None)
            if expression is not None and _has_vrm1_bind(expression):
                expressions.append((display_name, expression))
        for expression in getattr(vrm1_expressions, "custom", ()):
            name = str(getattr(expression, "custom_name", "") or "").strip()
            if name and _has_vrm1_bind(expression):
                expressions.append((name, expression))

    unique = {}
    for name, expression in expressions:
        normalized = normalize_expression_name(name)
        if normalized and normalized not in unique:
            unique[normalized] = (name, expression)
    return list(unique.values())


def refresh_layer_mask(layer, armature):
    """Replace a layer's mask with the avatar's bound expressions."""

    previous_weights = {
        item.normalized_name: float(item.weight) for item in layer.blendshape_masks
    }
    previous_smoothing = {
        item.normalized_name: float(item.smooth) for item in layer.blendshape_masks
    }
    expressions = iter_bound_expressions(armature)
    layer.blendshape_masks.clear()
    for name, _expression in expressions:
        normalized = normalize_expression_name(name)
        item = layer.blendshape_masks.add()
        item.expression_name = name
        item.normalized_name = normalized
        item.is_arkit = normalized in ARKIT_NORMALIZED_NAMES
        item.weight = previous_weights.get(normalized, 1.0)
        item.smooth = previous_smoothing.get(normalized, 0.0)
    return len(expressions)


def build_mask_lookup(layer):
    return {
        item.normalized_name: item
        for item in layer.blendshape_masks
        if item.normalized_name
    }


def resolve_mask_item(channel_name, mask_lookup):
    for alias in expression_aliases(channel_name):
        item = mask_lookup.get(normalize_expression_name(alias))
        if item is not None:
            return item
    return None
