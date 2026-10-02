"""Install and synchronize Yugo's bundled custom MToon preview material."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import bpy
from bpy.types import Context, Material, Node, NodeSocket, NodeTree

from . import scheduler


TEMPLATE_ALPHA_NAME = "_MToonCustom_Alpha"
TEMPLATE_DIFF_NAME = "_MToonCustom_Diff"
TEMPLATE_TAG_KEY = "yugus_mtoon_template"
CUSTOM_SUFFIX = "_MToonCustom"
CUSTOM_FRAME_NAME = "Frame_MToonCustom"
NODE_TAG_KEY = "yugus_mtoon_custom_node"
GROUP_TAG_KEY = "yugus_mtoon_custom_group"
# 0.1.21 stored Shadow Mix on every optimized node tree and read it from the
# depsgraph handler. Keep the key only to migrate those files once.
LEGACY_SHADOW_MIX_KEY = "yugus_mtoon_shadow_mix"
OFFICIAL_OUTPUT_NODE_NAME = "Mtoon1Material.Mtoon1Output"
OFFICIAL_OUTPUT_GROUP_NAME = "VRM Add-on MToon 1.0 Output Revision 1"
CUSTOM_OUTPUT_NAME = "_MToonCustom Output"

FLOAT_NODE_TO_SOCKET = {
    "Shading Toony_MToonCustom": "Shading Toony",
}

COLOR_NODE_TO_SOCKET = {
    "Lit Color_MToonCustom": "Lit Color",
    "Shade Color_MToonCustom": "Shade Color",
    "MatCap Factor_MToonCustom": "MatCap Factor",
}

IMAGE_NODE_TO_NATIVE_NODE = {
    "Lit Color Texture_MToonCustom": "Mtoon1BaseColorTexture.Image",
    "Shade Color Texture_MToonCustom": "Mtoon1ShadeMultiplyTexture.Image",
    "MatCap Texture_MToonCustom": "Mtoon1MatcapTexture.Image",
}

_SYNC_IN_PROGRESS = False


def _preset_path() -> Path:
    return Path(__file__).with_name("preset.blend")


def _is_shader_group_node(node: Node) -> bool:
    return node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None


def _find_official_output_node(material: Material) -> Optional[Node]:
    node_tree = material.node_tree
    if node_tree is None:
        return None

    named_node = node_tree.nodes.get(OFFICIAL_OUTPUT_NODE_NAME)
    if (
        named_node is not None
        and _is_shader_group_node(named_node)
        and named_node.node_tree.name == OFFICIAL_OUTPUT_GROUP_NAME
    ):
        return named_node

    return next(
        (
            node
            for node in node_tree.nodes
            if _is_shader_group_node(node)
            and node.node_tree.name == OFFICIAL_OUTPUT_GROUP_NAME
        ),
        None,
    )


def is_mtoon_material(material: Optional[Material]) -> bool:
    if material is None or material.node_tree is None:
        return False

    extension = getattr(material, "vrm_addon_extension", None)
    mtoon1 = getattr(extension, "mtoon1", None)
    if mtoon1 is not None:
        try:
            if bool(mtoon1.enabled) and not bool(mtoon1.is_outline_material):
                return True
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass

    # The exact native output group is a strong compatibility fallback and also
    # keeps the add-on testable without importing VRM Add-on internals.
    return _find_official_output_node(material) is not None


def _alpha_mode_requires_blended_render(alpha_mode: object) -> bool:
    # VRM Add-on stores the identifiers as BLEND and MASK. Accept their UI
    # labels as well so migrated/custom property data remains compatible.
    return str(alpha_mode).strip().upper() in {
        "BLEND",
        "MASK",
        "TRANSPARENT",
        "CUTOUT",
    }


def _mtoon_uses_alpha_blend(
    material: Material,
    official_node: Optional[Node] = None,
) -> bool:
    """Return whether this MToon material is intended to use alpha blending."""
    extension = getattr(material, "vrm_addon_extension", None)
    mtoon1 = getattr(extension, "mtoon1", None)
    if mtoon1 is not None:
        try:
            if bool(mtoon1.enabled) and not bool(mtoon1.is_outline_material):
                return _alpha_mode_requires_blended_render(mtoon1.alpha_mode)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass

    # Compatibility fallback for older/imported transparent MToon node layouts
    # and for files opened without the VRM property group currently registered.
    if official_node is None:
        official_node = _find_official_output_node(material)
    if official_node is None:
        return False
    alpha_mode = official_node.inputs.get("Alpha Mode")
    if alpha_mode is not None:
        # The official output stores Opaque=0, Cutout=1, Transparent=2.
        # Mode is authoritative even if the Lit Color alpha is less than one.
        return int(alpha_mode.default_value) in {1, 2}
    lit_color = official_node.inputs.get("Lit Color")
    if lit_color is None:
        return False
    try:
        return float(lit_color.default_value[3]) < 0.999999
    except (AttributeError, IndexError, TypeError, ValueError):
        return False


def _set_blended_render_method(
    material: Material,
    official_node: Optional[Node] = None,
) -> bool:
    if not _mtoon_uses_alpha_blend(material, official_node):
        return False

    # Blender 5.x exposes Eevee's Render Method through this property.
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    # Compatibility fallback for Blender versions before surface_render_method.
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    else:
        return False
    return True


def _mtoon_is_double_sided(material: Material) -> bool:
    if not is_mtoon_material(material):
        return False
    mtoon1 = getattr(getattr(material, "vrm_addon_extension", None), "mtoon1", None)
    if mtoon1 is not None:
        try:
            return bool(mtoon1.double_sided)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    # The VRM getter itself is `not material.use_backface_culling`. This also
    # preserves imported double-sided settings without a loaded VRM Add-on.
    return not material.use_backface_culling


def _configure_backface_culling(material: Material, double_sided: bool) -> None:
    for prop in material.bl_rna.properties:
        if (prop.identifier.startswith("use_backface_culling")
                and prop.type == "BOOLEAN" and not prop.is_readonly):
            setattr(material, prop.identifier, True)
    if double_sided:
        # Read the original MToon flag before entering this function: the VRM
        # getter aliases this very Blender camera-culling property.
        material.use_backface_culling = False


def configure_selected_backface_culling(context: Context) -> int:
    materials = _selected_materials(context)
    for material in materials:
        double_sided = _mtoon_is_double_sided(material)
        _configure_backface_culling(material, double_sided)
    return len(materials)


def _selected_materials(context: Context) -> list[Material]:
    materials: list[Material] = []
    seen: set[int] = set()

    for obj in context.selected_objects:
        if not hasattr(obj, "material_slots"):
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            pointer = material.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            materials.append(material)

    return materials


def _selected_mtoon_materials(context: Context) -> list[Material]:
    return [material for material in _selected_materials(context) if is_mtoon_material(material)]


def selected_object_count(context: Context) -> int:
    return len(tuple(getattr(context, "selected_objects", ()) or ()))


def selected_mtoon_material_count(context: Context) -> int:
    return len(_selected_mtoon_materials(context))


def _append_template_material(template_name: str) -> Material:
    preset_path = _preset_path()
    if not preset_path.is_file():
        raise RuntimeError(f"Preset file not found: {preset_path}")

    with bpy.data.libraries.load(
        str(preset_path),
        link=False,
        reuse_local_id=False,
    ) as (data_from, data_to):
        if template_name not in data_from.materials:
            raise RuntimeError(
                f'Preset has no material named "{template_name}"'
            )
        data_to.materials = [template_name]

    template = data_to.materials[0]
    if not isinstance(template, Material) or template.node_tree is None:
        raise RuntimeError(f'Failed to append material "{template_name}"')
    return template


def _copy_id_properties(source: Node, target: Node) -> None:
    for key in source.keys():
        if key == "_RNA_UI":
            continue
        try:
            target[key] = source[key]
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            continue


def _copy_node_rna_properties(source: Node, target: Node) -> None:
    excluded = {
        "rna_type",
        "type",
        "inputs",
        "outputs",
        "internal_links",
        "dimensions",
        "parent",
        "select",
        "name",
        "is_active_output",
    }

    for prop in source.bl_rna.properties:
        identifier = prop.identifier
        if identifier in excluded or prop.is_readonly:
            continue
        try:
            value = getattr(source, identifier)
            if prop.type == "POINTER":
                if isinstance(value, bpy.types.ID):
                    setattr(target, identifier, value)
                continue
            if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                continue
            if prop.is_array:
                value = tuple(value)
            setattr(target, identifier, value)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue

    target.label = source.label
    target.location = source.location
    target.width = source.width
    target.height = source.height
    target.hide = source.hide
    target.mute = source.mute
    target.use_custom_color = source.use_custom_color
    target.color = source.color
    _copy_id_properties(source, target)


def _configure_dynamic_node(source: Node, target: Node) -> None:
    if source.bl_idname != "ShaderNodeMix" or target.bl_idname != "ShaderNodeMix":
        return

    # ShaderNodeMix keeps Float, Vector, Color and Rotation sockets at the same
    # time and merely hides the inactive variants. Configure its mode before
    # copying defaults or links so they resolve to the same socket family.
    target.data_type = source.data_type
    target.factor_mode = source.factor_mode
    target.blend_type = source.blend_type


def _socket_with_identifier(sockets: object, identifier: str) -> Optional[NodeSocket]:
    return next(
        (socket for socket in sockets if socket.identifier == identifier),
        None,
    )


def _copy_socket_default(source: NodeSocket, target: NodeSocket) -> None:
    if not hasattr(source, "default_value") or not hasattr(target, "default_value"):
        return
    try:
        value = source.default_value
        if isinstance(value, (str, bytes)):
            target.default_value = value
            return
        try:
            target.default_value = tuple(value)
        except TypeError:
            target.default_value = value
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return


def _copy_node_socket_defaults(source: Node, target: Node) -> None:
    for source_sockets, target_sockets in (
        (source.inputs, target.inputs),
        (source.outputs, target.outputs),
    ):
        for source_socket in source_sockets:
            target_socket = _socket_with_identifier(
                target_sockets,
                source_socket.identifier,
            )
            if target_socket is not None:
                _copy_socket_default(source_socket, target_socket)


def _remove_nodes(node_tree: NodeTree, nodes: list[Node]) -> None:
    # Remove children before frames so Blender never has to implicitly reparent
    # a node while this list is being processed.
    ordered = sorted(nodes, key=lambda node: node.bl_idname == "NodeFrame")
    for node in ordered:
        if node.id_data == node_tree:
            node_tree.nodes.remove(node)


def _custom_frame_nodes(source_tree: NodeTree) -> tuple[Node, list[Node]]:
    frame = next(
        (
            node
            for node in source_tree.nodes
            if node.bl_idname == "NodeFrame"
            and (node.name == CUSTOM_FRAME_NAME or node.label == CUSTOM_FRAME_NAME)
        ),
        None,
    )
    if frame is None:
        raise RuntimeError(f'Preset frame "{CUSTOM_FRAME_NAME}" was not found')

    def belongs_to_frame(node: Node) -> bool:
        current: Optional[Node] = node
        while current is not None:
            if current == frame:
                return True
            current = current.parent
        return False

    nodes = [node for node in source_tree.nodes if belongs_to_frame(node)]
    return frame, nodes


def _node_layout_offset(
    source_frame: Node,
    target_tree: NodeTree,
    ignored_nodes: list[Node],
) -> tuple[float, float]:
    ignored_pointers = {node.as_pointer() for node in ignored_nodes}
    target_nodes = [
        node
        for node in target_tree.nodes
        if node.as_pointer() not in ignored_pointers
    ]
    if not target_nodes:
        return (0.0, 0.0)
    target_max_x = max(float(node.location.x + node.width) for node in target_nodes)
    return (target_max_x + 300.0 - float(source_frame.location.x), 0.0)


def _clone_custom_nodes(
    source_tree: NodeTree,
    target_tree: NodeTree,
) -> tuple[dict[Node, Node], list[Node]]:
    source_frame, source_nodes = _custom_frame_nodes(source_tree)
    previous_nodes = [
        node for node in target_tree.nodes if bool(node.get(NODE_TAG_KEY))
    ]
    offset_x, offset_y = _node_layout_offset(
        source_frame,
        target_tree,
        previous_nodes,
    )
    node_map: dict[Node, Node] = {}
    created_nodes: list[Node] = []
    desired_names: dict[Node, str] = {}

    try:
        for source_node in source_nodes:
            target_node = target_tree.nodes.new(source_node.bl_idname)
            _copy_node_rna_properties(source_node, target_node)
            _configure_dynamic_node(source_node, target_node)
            _copy_node_socket_defaults(source_node, target_node)
            target_node[NODE_TAG_KEY] = True
            target_node.select = False
            node_map[source_node] = target_node
            created_nodes.append(target_node)
            desired_names[target_node] = source_node.name

        # Parent after every node exists, then restore source-local coordinates.
        for source_node, target_node in node_map.items():
            if source_node.parent is not None:
                parent = node_map.get(source_node.parent)
                if parent is None:
                    raise RuntimeError(
                        f'Parent of preset node "{source_node.name}" is outside '
                        f'"{CUSTOM_FRAME_NAME}"'
                    )
                target_node.parent = parent
                target_node.location = source_node.location
            else:
                target_node.location = (
                    float(source_node.location.x) + offset_x,
                    float(source_node.location.y) + offset_y,
                )

        copied_link_count = 0
        for source_link in source_tree.links:
            from_node = node_map.get(source_link.from_node)
            to_node = node_map.get(source_link.to_node)
            if from_node is None or to_node is None:
                continue
            from_socket = _socket_with_identifier(
                from_node.outputs,
                source_link.from_socket.identifier,
            )
            to_socket = _socket_with_identifier(
                to_node.inputs,
                source_link.to_socket.identifier,
            )
            if from_socket is None or to_socket is None:
                raise RuntimeError(
                    "Preset socket identifier mismatch: "
                    f'"{source_link.from_node.name}".'
                    f'"{source_link.from_socket.identifier}" -> '
                    f'"{source_link.to_node.name}".'
                    f'"{source_link.to_socket.identifier}"'
                )
            if (
                from_socket.bl_idname != source_link.from_socket.bl_idname
                or to_socket.bl_idname != source_link.to_socket.bl_idname
            ):
                raise RuntimeError(
                    "Preset socket type mismatch: "
                    f'"{source_link.from_node.name}" -> '
                    f'"{source_link.to_node.name}"'
                )
            if bool(getattr(from_socket, "is_unavailable", False)) or bool(
                getattr(to_socket, "is_unavailable", False)
            ):
                raise RuntimeError(
                    "Preset link resolved to an inactive socket: "
                    f'"{source_link.from_node.name}" -> '
                    f'"{source_link.to_node.name}"'
                )
            target_tree.links.new(from_socket, to_socket)
            copied_link_count += 1

        expected_link_count = sum(
            1
            for link in source_tree.links
            if link.from_node in node_map and link.to_node in node_map
        )
        if copied_link_count != expected_link_count:
            raise RuntimeError(
                f"Copied {copied_link_count} of {expected_link_count} preset links"
            )
    except Exception:
        _remove_nodes(target_tree, created_nodes)
        raise

    # Only discard the previous block after the complete new frame and every
    # internal link have been created successfully.
    _remove_nodes(target_tree, previous_nodes)
    for target_node, desired_name in desired_names.items():
        target_node.name = desired_name

    return node_map, created_nodes


def _find_created_node(created_nodes: list[Node], name_or_label: str) -> Optional[Node]:
    return next(
        (
            node
            for node in created_nodes
            if node.name == name_or_label or node.label == name_or_label
        ),
        None,
    )


def _sync_parameter_values(
    node_tree: NodeTree,
    official_node: Node,
    created_nodes: list[Node],
) -> None:
    for custom_node_name, official_socket_name in FLOAT_NODE_TO_SOCKET.items():
        custom_node = _find_created_node(created_nodes, custom_node_name)
        official_socket = official_node.inputs.get(official_socket_name)
        if custom_node is None or official_socket is None or not custom_node.outputs:
            continue
        custom_socket = custom_node.outputs[0]
        if not hasattr(custom_socket, "default_value"):
            continue
        try:
            value = official_socket.default_value
            if custom_socket.default_value != value:
                custom_socket.default_value = value
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue

    for custom_node_name, native_node_name in IMAGE_NODE_TO_NATIVE_NODE.items():
        custom_node = _find_created_node(created_nodes, custom_node_name)
        native_node = node_tree.nodes.get(native_node_name)
        if (
            custom_node is None
            or native_node is None
            or custom_node.bl_idname != "ShaderNodeTexImage"
            or native_node.bl_idname != "ShaderNodeTexImage"
        ):
            continue
        try:
            if custom_node.image != native_node.image:
                custom_node.image = native_node.image
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue

    for custom_node_name, official_socket_name in COLOR_NODE_TO_SOCKET.items():
        custom_node = _find_created_node(created_nodes, custom_node_name)
        official_socket = official_node.inputs.get(official_socket_name)
        if custom_node is None or official_socket is None or not custom_node.outputs:
            continue
        custom_socket = custom_node.outputs[0]
        if not hasattr(custom_socket, "default_value"):
            continue
        try:
            value = tuple(official_socket.default_value)
            if tuple(custom_socket.default_value) != value:
                custom_socket.default_value = value
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue


def _apply_shadow_mix_once(
    material: Material,
    official_node: Node,
    shadow_mix: float,
) -> None:
    """Bake Shadow Mix into the native MToon UI value exactly once."""

    factor = max(0.0, min(1.0, float(shadow_mix)))
    extension = getattr(material, "vrm_addon_extension", None)
    mtoon1 = getattr(extension, "mtoon1", None)
    extensions = getattr(mtoon1, "extensions", None)
    mtoon = getattr(extensions, "vrmc_materials_mtoon", None)
    if mtoon is not None and hasattr(mtoon, "shade_color_factor"):
        try:
            color = tuple(float(value) for value in mtoon.shade_color_factor)
            if len(color) >= 3:
                mtoon.shade_color_factor = tuple(
                    component * factor for component in color[:3]
                )
                return
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass

    # Compatibility fallback for imported/custom MToon node layouts and tests
    # that do not have the VRM Add-on PropertyGroup attached.
    socket = official_node.inputs.get("Shade Color")
    if socket is None or not hasattr(socket, "default_value"):
        return
    try:
        color = tuple(socket.default_value)
        if len(color) >= 4:
            socket.default_value = (
                color[0] * factor,
                color[1] * factor,
                color[2] * factor,
                color[3],
            )
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass


def _migrate_legacy_shadow_mix(
    material: Material,
    official_node: Node,
) -> bool:
    node_tree = material.node_tree
    if node_tree is None or LEGACY_SHADOW_MIX_KEY not in node_tree:
        return False
    try:
        factor = float(node_tree.get(LEGACY_SHADOW_MIX_KEY, 0.5))
        _apply_shadow_mix_once(material, official_node, factor)
    finally:
        try:
            del node_tree[LEGACY_SHADOW_MIX_KEY]
        except (KeyError, ReferenceError, RuntimeError):
            pass
    return True


def _sync_material(material: Material) -> None:
    node_tree = material.node_tree
    if node_tree is None or not bool(node_tree.get(GROUP_TAG_KEY)):
        return
    official_node = _find_official_output_node(material)
    if official_node is None:
        return
    created_nodes = [
        node for node in node_tree.nodes if bool(node.get(NODE_TAG_KEY))
    ]
    migrated = _migrate_legacy_shadow_mix(material, official_node)
    _sync_parameter_values(node_tree, official_node, created_nodes)
    if migrated:
        # Setting the native MToon UI value can let VRM Add-on reactivate its
        # own output. Migration ends by restoring Yugo's custom output once.
        _activate_custom_output(node_tree, created_nodes)


def _sync_installed_materials(_scene: object = None, _depsgraph: object = None) -> None:
    global _SYNC_IN_PROGRESS
    if _SYNC_IN_PROGRESS:
        return
    _SYNC_IN_PROGRESS = True
    try:
        for material in bpy.data.materials:
            _sync_material(material)
    finally:
        _SYNC_IN_PROGRESS = False


def _system_update(_context, _delta_time) -> None:
    _sync_installed_materials()


def _remove_legacy_depsgraph_handlers() -> None:
    """Remove MToon callbacks left by Yugo 0.1.22 or a script reload."""

    handlers = bpy.app.handlers.depsgraph_update_post
    for handler in tuple(handlers):
        if (
            getattr(handler, "__module__", "") == __name__
            and getattr(handler, "__name__", "") == "_sync_installed_materials"
        ):
            handlers.remove(handler)


def _activate_custom_output(target_tree: NodeTree, created_nodes: list[Node]) -> Node:
    custom_output = next(
        (
            node
            for node in created_nodes
            if node.bl_idname == "ShaderNodeOutputMaterial"
        ),
        None,
    )
    if custom_output is None:
        raise RuntimeError("The preset has no Material Output node")

    custom_output.name = CUSTOM_OUTPUT_NAME
    custom_output.label = CUSTOM_OUTPUT_NAME
    custom_output.target = "EEVEE"
    for node in target_tree.nodes:
        node.select = False
        if node.bl_idname == "ShaderNodeOutputMaterial":
            node.is_active_output = node == custom_output
    custom_output.select = True
    target_tree.nodes.active = custom_output
    return custom_output


def _rename_material(material: Material) -> None:
    if material.name.casefold().endswith(CUSTOM_SUFFIX.casefold()):
        return
    base_name = re.sub(r"_mtoon", "", material.name, flags=re.IGNORECASE)
    base_name = base_name.rstrip(" _-.") or "Material"
    material.name = base_name + CUSTOM_SUFFIX


def install_custom_shader(
    material: Material,
    template: Material,
    shadow_mix: float = 0.5,
) -> None:
    if material.node_tree is None or template.node_tree is None:
        raise RuntimeError("Material node tree is unavailable")

    official_node = _find_official_output_node(material)
    if official_node is None:
        raise RuntimeError("Native VRM MToon output group was not found")

    double_sided = _mtoon_is_double_sided(material)
    _set_blended_render_method(material, official_node)

    already_installed = bool(material.node_tree.get(GROUP_TAG_KEY))
    if not _migrate_legacy_shadow_mix(material, official_node) and not already_installed:
        # Shadow Mix is an Optimize-time preprocessing step. Apply it to the
        # native MToon value before copying any custom nodes, then never read
        # the operator value from the per-frame/depsgraph synchronization path.
        _apply_shadow_mix_once(material, official_node, shadow_mix)

    _node_map, created_nodes = _clone_custom_nodes(
        template.node_tree,
        material.node_tree,
    )
    _sync_parameter_values(material.node_tree, official_node, created_nodes)
    _activate_custom_output(material.node_tree, created_nodes)
    material.node_tree[GROUP_TAG_KEY] = True
    _configure_backface_culling(material, double_sided)
    _rename_material(material)
    material.node_tree.update_tag()


def optimize_selected_mtoon_materials(
    context: Context,
    shadow_mix: float = 0.5,
) -> tuple[int, list[str]]:
    materials = _selected_mtoon_materials(context)
    if not materials:
        return 0, ["No MToon materials were found on selected objects"]

    templates: dict[str, Material] = {}
    optimized = 0
    errors: list[str] = []
    try:
        for material in materials:
            try:
                template_name = (
                    TEMPLATE_ALPHA_NAME if _mtoon_uses_alpha_blend(material)
                    else TEMPLATE_DIFF_NAME
                )
                if template_name not in templates:
                    templates[template_name] = _append_template_material(template_name)
                template = templates[template_name]
                install_custom_shader(material, template, shadow_mix)
                material.node_tree[TEMPLATE_TAG_KEY] = template_name
                optimized += 1
            except Exception as exc:  # Keep processing the other selected materials.
                errors.append(f"{material.name}: {exc}")
    finally:
        # The copied group nodes retain their node group dependencies. The template
        # material itself is not needed in the user's material list.
        for template in templates.values():
            # Preset library materials may use a fake user. These newly appended
            # carriers can be removed; cloned nodes own their group dependencies.
            bpy.data.materials.remove(template)

    return optimized, errors


def register() -> None:
    _remove_legacy_depsgraph_handlers()
    scheduler.add_system("mtoon", _system_update, priority=100)


def unregister() -> None:
    scheduler.remove_system("mtoon")
    _remove_legacy_depsgraph_handlers()
