"""Install MainFX compositor frames and synchronize their exposed values."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

import bpy


TEMPLATE_TREE_NAME = "_CompCustom"
MAINFX_PREFIX = "mainfx_"
MATTE_NODE_NAMES = {"mainfx_matte", "mainfx_mattle"}
NODE_GENERATION_KEY = "yugus_composing_mainfx_generation"
ACTIVE_GENERATION_KEY = "yugus_composing_mainfx_active_generation"
OUTPUT_NODE_KEY = "yugus_composing_output"
VIEWER_NODE_KEY = "yugus_composing_viewer"

MAINFX_BINDINGS = (
    (
        "scene_color_blend",
        "MainFX_SceneColorBlend",
        (("intensity", "Mix"), ("blur", "Blur")),
    ),
    (
        "ambient_occlusion",
        "MainFX_AmbientOcclusion",
        (("intensity", "Mix"),),
    ),
    (
        "rim_light",
        "MainFX_RimLight",
        (
            ("intensity", "Mix"),
            ("color", "Color"),
            ("blur", "Blur"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
    (
        "separated_rim",
        "SeparatedRim",
        (
            ("intensity", "Mix"),
            ("blur", "Blur"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
    (
        "secondary_shadow",
        "MainFX_SeconderyShadow",
        (
            ("intensity", "Mix"),
            ("color", "Color"),
            ("blur", "Blur"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
    (
        "separated_shadows",
        "SeparatedShadows",
        (
            ("intensity", "Mix"),
            ("blur", "Blur"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
    (
        "gradient_color",
        "MainFX_GradientColor",
        (
            ("intensity", "Mix"),
            ("color", "Color"),
            ("range", "Range"),
            ("rotation", "Rotation"),
        ),
    ),
    (
        "dark_bloom",
        "MainFX_DarkBloom",
        (
            ("intensity", "Mix"),
            ("color", "Color"),
            ("range", "Range"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
    (
        "light_bloom",
        "MainFX_LightBloom",
        (
            ("intensity", "Mix"),
            ("color", "Color"),
            ("range", "Range"),
            ("offset_x", "X"),
            ("offset_y", "Y"),
        ),
    ),
)

_SYNCING_FROM_NODES = False


def _preset_path() -> Path:
    return Path(__file__).resolve().parent.parent / "preset.blend"


def _semantic_name(node) -> str:
    return (node.label or node.name).strip()


def _is_named(node, name: str) -> bool:
    expected = name.casefold()
    return node.name.casefold() == expected or node.label.casefold() == expected


def _active_avatar(scene):
    avatars = getattr(scene, "yugus_avatars", None)
    avatar_index = int(getattr(scene, "yugus_avatar_index", -1))
    if avatars is not None and 0 <= avatar_index < len(avatars):
        return avatars[avatar_index]
    return None


def _active_armature(scene, context=None):
    avatar = _active_avatar(scene)
    if avatar is not None:
        armature = getattr(avatar, "armature", None)
        if armature is not None and armature.type == "ARMATURE":
            return armature

    legacy = getattr(scene, "yugus_vrm_armature", None)
    if legacy is not None and legacy.type == "ARMATURE":
        return legacy

    if context is not None:
        active = getattr(context, "active_object", None)
        if active is not None and active.type == "ARMATURE":
            return active
        for candidate in getattr(context, "selected_objects", ()):
            if candidate.type == "ARMATURE":
                return candidate
    return None


def _bound_meshes(scene, armature) -> list:
    meshes = []
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if any(
            modifier.type == "ARMATURE" and modifier.object == armature
            for modifier in obj.modifiers
        ):
            meshes.append(obj)
    return sorted(meshes, key=lambda obj: obj.name.casefold())


def _enable_cryptomatte_passes(scene) -> None:
    for view_layer in scene.view_layers:
        view_layer.use_pass_cryptomatte_object = True
        view_layer.use_pass_cryptomatte_material = True
        view_layer.use_pass_ambient_occlusion = True
        view_layer.use_pass_z = True


def _append_template_tree():
    preset_path = _preset_path()
    if not preset_path.is_file():
        raise RuntimeError(f"Preset file not found: {preset_path}")

    with bpy.data.libraries.load(
        str(preset_path),
        link=False,
        reuse_local_id=True,
    ) as (data_from, data_to):
        if TEMPLATE_TREE_NAME not in data_from.node_groups:
            raise RuntimeError(
                f'Preset has no compositor node group named "{TEMPLATE_TREE_NAME}"'
            )
        data_to.node_groups = [TEMPLATE_TREE_NAME]

    tree = data_to.node_groups[0]
    if tree is None or tree.bl_idname != "CompositorNodeTree":
        raise RuntimeError(f'Failed to append compositor group "{TEMPLATE_TREE_NAME}"')
    return tree


def _mainfx_source_nodes(source_tree) -> list:
    frames = [
        node
        for node in source_tree.nodes
        if node.bl_idname == "NodeFrame"
        and _semantic_name(node).casefold().startswith(MAINFX_PREFIX)
    ]
    if not frames:
        raise RuntimeError("No MainFX frames were found in _CompCustom")
    frame_pointers = {frame.as_pointer() for frame in frames}

    def belongs_to_mainfx(node) -> bool:
        current = node
        while current is not None:
            if current.as_pointer() in frame_pointers:
                return True
            current = current.parent
        return False

    return [node for node in source_tree.nodes if belongs_to_mainfx(node)]


def _copy_id_properties(source, target) -> None:
    for key in source.keys():
        if key == "_RNA_UI":
            continue
        try:
            target[key] = source[key]
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            continue


def _copy_node_properties(source, target) -> None:
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
        "location",
        "layer",
        "scene",
        "view",
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
    target.width = source.width
    target.height = source.height
    target.hide = source.hide
    target.mute = source.mute
    target.use_custom_color = source.use_custom_color
    target.color = source.color
    _copy_id_properties(source, target)


def _configure_dynamic_node(source, target, scene) -> None:
    if source.bl_idname == "ShaderNodeMix" and target.bl_idname == "ShaderNodeMix":
        # Mix exposes several socket families simultaneously. Configure the
        # active family before defaults and links are resolved.
        target.data_type = source.data_type
        target.factor_mode = source.factor_mode
        target.blend_type = source.blend_type
    elif (
        source.bl_idname == "ShaderNodeMath"
        and target.bl_idname == "ShaderNodeMath"
    ):
        target.operation = source.operation
        target.use_clamp = source.use_clamp
    elif (
        source.bl_idname == "CompositorNodeCryptomatteV2"
        and target.bl_idname == "CompositorNodeCryptomatteV2"
    ):
        target.scene = scene


def _socket_by_identifier(sockets, identifier: str):
    return next((socket for socket in sockets if socket.identifier == identifier), None)


def _copy_socket_defaults(source, target) -> None:
    for source_sockets, target_sockets in (
        (source.inputs, target.inputs),
        (source.outputs, target.outputs),
    ):
        for source_socket in source_sockets:
            target_socket = _socket_by_identifier(
                target_sockets, source_socket.identifier
            )
            if (
                target_socket is None
                or not hasattr(source_socket, "default_value")
                or not hasattr(target_socket, "default_value")
            ):
                continue
            try:
                value = source_socket.default_value
                try:
                    value = tuple(value)
                except TypeError:
                    pass
                target_socket.default_value = value
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue


def _remove_nodes(tree, nodes) -> None:
    for node in sorted(nodes, key=lambda item: item.bl_idname == "NodeFrame"):
        if node.id_data == tree:
            tree.nodes.remove(node)


def _layout_offset(target_tree, source_nodes) -> tuple[float, float]:
    old_nodes = [node for node in target_tree.nodes if NODE_GENERATION_KEY in node]
    ignored = {node.as_pointer() for node in old_nodes}
    remaining = [
        node for node in target_tree.nodes if node.as_pointer() not in ignored
    ]
    target_x = (
        max(float(node.location.x + node.width) for node in remaining) + 300.0
        if remaining
        else 0.0
    )
    source_frames = [node for node in source_nodes if node.bl_idname == "NodeFrame"]
    source_x = min(float(node.location.x) for node in source_frames)
    return target_x - source_x, 0.0


def _find_or_create_group_output(tree):
    output = next(
        (node for node in tree.nodes if node.bl_idname == "NodeGroupOutput"),
        None,
    )
    if output is None:
        output = tree.nodes.new("NodeGroupOutput")
        output.label = "YugoComp Output"
        output[OUTPUT_NODE_KEY] = True

    image_input = output.inputs.get("Image")
    if image_input is None:
        tree.interface.new_socket(
            name="Image",
            in_out="OUTPUT",
            socket_type="NodeSocketColor",
        )
        image_input = output.inputs.get("Image")
    if image_input is None:
        raise RuntimeError("Could not create the scene compositor Image output")
    return output, image_input


def _find_or_create_viewer(tree, group_output):
    viewer = next(
        (
            node
            for node in tree.nodes
            if node.bl_idname == "CompositorNodeViewer"
            and bool(node.get(VIEWER_NODE_KEY, False))
        ),
        None,
    )
    created = viewer is None
    if viewer is None:
        viewer = tree.nodes.new("CompositorNodeViewer")
        viewer.name = "YugoComp Viewer"
        viewer.label = "YugoComp Viewer"
        viewer[VIEWER_NODE_KEY] = True
        viewer.location = (
            float(group_output.location.x),
            float(group_output.location.y) - 180.0,
        )

    image_input = viewer.inputs.get("Image")
    if image_input is None:
        raise RuntimeError("Could not create the YugoComp Viewer Image input")
    viewer.mute = False
    viewer.hide = False
    viewer.select = True
    tree.nodes.active = viewer
    return viewer, image_input, created


def _scene_compositor_tree(scene, create: bool = False):
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None and create:
        tree = bpy.data.node_groups.new(
            name=f"{scene.name} YugoComp",
            type="CompositorNodeTree",
        )
        scene.compositing_node_group = tree
    return tree


def _clone_mainfx(scene, source_tree) -> str:
    scene.use_nodes = True
    target_tree = _scene_compositor_tree(scene, create=True)
    source_nodes = _mainfx_source_nodes(source_tree)
    source_set = {node.as_pointer() for node in source_nodes}
    generation = uuid.uuid4().hex
    offset_x, offset_y = _layout_offset(target_tree, source_nodes)
    node_map = {}
    created = []

    try:
        for source in source_nodes:
            target = target_tree.nodes.new(source.bl_idname)
            _copy_node_properties(source, target)
            _configure_dynamic_node(source, target, scene)
            _copy_socket_defaults(source, target)
            target[NODE_GENERATION_KEY] = generation
            target.select = False
            node_map[source] = target
            created.append(target)

        for source, target in node_map.items():
            if source.parent is not None:
                target.parent = node_map[source.parent]
                target.location = source.location
            else:
                target.location = (
                    float(source.location.x) + offset_x,
                    float(source.location.y) + offset_y,
                )

        for link in source_tree.links:
            from_node = node_map.get(link.from_node)
            to_node = node_map.get(link.to_node)
            if from_node is None or to_node is None:
                continue
            from_socket = _socket_by_identifier(
                from_node.outputs, link.from_socket.identifier
            )
            to_socket = _socket_by_identifier(to_node.inputs, link.to_socket.identifier)
            if from_socket is None or to_socket is None:
                raise RuntimeError(
                    f'Preset socket mismatch: "{link.from_node.name}" -> '
                    f'"{link.to_node.name}"'
                )
            dynamic_reroute = (
                from_node.bl_idname == "NodeReroute"
                or to_node.bl_idname == "NodeReroute"
            )
            if (
                (
                    not dynamic_reroute
                    and (
                        from_socket.bl_idname != link.from_socket.bl_idname
                        or to_socket.bl_idname != link.to_socket.bl_idname
                    )
                )
                or bool(getattr(from_socket, "is_unavailable", False))
                or bool(getattr(to_socket, "is_unavailable", False))
            ):
                raise RuntimeError(
                    f'Preset socket type mismatch: "{link.from_node.name}" -> '
                    f'"{link.to_node.name}"'
                )
            target_tree.links.new(from_socket, to_socket)

        input_links = [
            link
            for link in source_tree.links
            if link.to_node.as_pointer() in source_set
            and link.from_node.as_pointer() not in source_set
            and link.from_node.bl_idname == "CompositorNodeRLayers"
        ]
        render_layer_map = {}
        for source_link in input_links:
            source_pointer = source_link.from_node.as_pointer()
            render_layers = render_layer_map.get(source_pointer)
            if render_layers is None:
                render_layers = target_tree.nodes.new("CompositorNodeRLayers")
                render_layers.label = "YugoComp Render Layers"
                render_layers[NODE_GENERATION_KEY] = generation
                render_layers.scene = scene
                render_layers.location = (
                    float(node_map[source_link.to_node].location.x) - 260.0,
                    float(node_map[source_link.to_node].location.y),
                )
                if scene.view_layers:
                    render_layers.layer = scene.view_layers[0].name
                created.append(render_layers)
                render_layer_map[source_pointer] = render_layers
            from_socket = _socket_by_identifier(
                render_layers.outputs, source_link.from_socket.identifier
            )
            if from_socket is None:
                from_socket = next(
                    (
                        socket
                        for socket in render_layers.outputs
                        if socket.name == source_link.from_socket.name
                        and socket.bl_idname == source_link.from_socket.bl_idname
                    ),
                    None,
                )
            to_socket = _socket_by_identifier(
                node_map[source_link.to_node].inputs,
                source_link.to_socket.identifier,
            )
            if from_socket is None or to_socket is None:
                raise RuntimeError("Could not connect MainFX to Render Layers")
            target_tree.links.new(from_socket, to_socket)

        output_link = next(
            (
                link
                for link in source_tree.links
                if link.from_node.as_pointer() in source_set
                and link.to_node.as_pointer() not in source_set
                and link.to_node.bl_idname == "NodeGroupOutput"
            ),
            None,
        )
        if output_link is None:
            raise RuntimeError("MainFX has no output connected to the group output")
        output_socket = _socket_by_identifier(
            node_map[output_link.from_node].outputs,
            output_link.from_socket.identifier,
        )
        if output_socket is None:
            raise RuntimeError("Could not connect MainFX to the scene output")
        group_output, image_input = _find_or_create_group_output(target_tree)
        target_tree.links.new(output_socket, image_input)
        viewer, viewer_input, viewer_created = _find_or_create_viewer(
            target_tree,
            group_output,
        )
        if viewer_created:
            created.append(viewer)
        for link in tuple(viewer_input.links):
            target_tree.links.remove(link)
        target_tree.links.new(output_socket, viewer_input)
        target_tree.nodes.active = viewer

        old_nodes = [
            node
            for node in target_tree.nodes
            if NODE_GENERATION_KEY in node
            and node.get(NODE_GENERATION_KEY) != generation
        ]
        _remove_nodes(target_tree, old_nodes)
        target_tree[ACTIVE_GENERATION_KEY] = generation
        target_tree.update_tag()
        return generation
    except Exception:
        _remove_nodes(target_tree, created)
        raise


def _active_mainfx_nodes(scene):
    tree = _scene_compositor_tree(scene)
    if tree is None:
        return ()
    generation = tree.get(ACTIVE_GENERATION_KEY, "")
    return tuple(
        node
        for node in tree.nodes
        if generation and node.get(NODE_GENERATION_KEY) == generation
    )


def _named_active_node(scene, name: str):
    return next((node for node in _active_mainfx_nodes(scene) if _is_named(node, name)), None)


def _node_output_value(node):
    if node is None or not node.outputs:
        return None
    socket = node.outputs[0]
    if not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    try:
        return tuple(value)
    except TypeError:
        return value


def _set_node_output_value(node, value) -> None:
    if node is None or not node.outputs or not hasattr(node.outputs[0], "default_value"):
        return
    node.outputs[0].default_value = value
    node.id_data.update_tag()


def _push_mainfx_values(scene) -> None:
    settings = scene.yugus_composing
    for attribute, prefix, fields in MAINFX_BINDINGS:
        effect = getattr(settings, attribute)
        for field, suffix in fields:
            if field == "offset_x":
                value = float(effect.offset[0])
            elif field == "offset_y":
                value = float(effect.offset[1])
            else:
                value = getattr(effect, field)
                if field == "color":
                    value = tuple(value)
            _set_node_output_value(
                _named_active_node(scene, f"{prefix}_{suffix}"),
                value,
            )


def _binding_for_effect(scene, effect):
    settings = getattr(scene, "yugus_composing", None)
    if settings is None:
        return None
    pointer = effect.as_pointer()
    for attribute, prefix, fields in MAINFX_BINDINGS:
        candidate = getattr(settings, attribute)
        if candidate.as_pointer() == pointer:
            return prefix, fields
    return None


def update_effect_property(scene, effect, property_name: str) -> None:
    if _SYNCING_FROM_NODES:
        return
    settings = getattr(scene, "yugus_composing", None)
    if settings is None or not settings.initialized:
        return
    binding = _binding_for_effect(scene, effect)
    if binding is None:
        return
    prefix, _fields = binding

    if property_name == "offset":
        for index, suffix in enumerate(("X", "Y")):
            node = _named_active_node(scene, f"{prefix}_{suffix}")
            _set_node_output_value(node, float(effect.offset[index]))
        return

    suffix = {
        "intensity": "Mix",
        "color": "Color",
        "blur": "Blur",
        "range": "Range",
        "rotation": "Rotation",
    }[property_name]
    node = _named_active_node(scene, f"{prefix}_{suffix}")
    value = getattr(effect, property_name)
    if property_name == "color":
        value = tuple(value)
    _set_node_output_value(node, value)


def _pull_mainfx_values(scene) -> None:
    global _SYNCING_FROM_NODES
    settings = scene.yugus_composing
    _SYNCING_FROM_NODES = True
    try:
        for attribute, prefix, fields in MAINFX_BINDINGS:
            effect = getattr(settings, attribute)
            offset = list(effect.offset) if hasattr(effect, "offset") else None
            for field, suffix in fields:
                value = _node_output_value(
                    _named_active_node(scene, f"{prefix}_{suffix}")
                )
                if value is None:
                    continue
                if field == "offset_x":
                    offset[0] = float(value)
                elif field == "offset_y":
                    offset[1] = float(value)
                else:
                    setattr(effect, field, value)
            if offset is not None:
                effect.offset = offset
    finally:
        _SYNCING_FROM_NODES = False


def _reset_mainfx_mattes(scene, object_names: list[str]) -> int:
    matte_id = ", ".join(object_names)
    count = 0
    for node in _active_mainfx_nodes(scene):
        if node.bl_idname != "CompositorNodeCryptomatteV2":
            continue
        if not any(_is_named(node, name) for name in MATTE_NODE_NAMES):
            continue
        node.source = "RENDER"
        node.scene = scene
        try:
            node.layer_name = f"{scene.view_layers[0].name}.CryptoObject"
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        node.matte_id = matte_id
        count += 1
    return count


def _enabled_avatar_meshes(scene) -> list:
    meshes = []
    seen = set()
    for avatar in getattr(scene, "yugus_avatars", ()):
        if not bool(getattr(avatar, "composing_enabled", False)):
            continue
        armature = getattr(avatar, "armature", None)
        if armature is None or armature.type != "ARMATURE":
            continue
        for mesh in _bound_meshes(scene, armature):
            pointer = mesh.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            meshes.append(mesh)
    return sorted(meshes, key=lambda item: item.name.casefold())


def sync_matte_ids(scene) -> tuple[int, int]:
    meshes = _enabled_avatar_meshes(scene)
    matte_count = _reset_mainfx_mattes(scene, [mesh.name for mesh in meshes])
    return len(meshes), matte_count


def _install_shared_mainfx(scene, *, pull_preset_values: bool) -> None:
    _enable_cryptomatte_passes(scene)
    template = _append_template_tree()
    try:
        _clone_mainfx(scene, template)
    finally:
        if template.users == 0:
            bpy.data.node_groups.remove(template)

    if pull_preset_values:
        _pull_mainfx_values(scene)
    else:
        _push_mainfx_values(scene)


def set_avatar_enabled(scene, avatar, enabled: bool, context=None) -> tuple[int, int]:
    settings = scene.yugus_composing
    if enabled:
        armature = getattr(avatar, "armature", None)
        if armature is None or armature.type != "ARMATURE":
            raise RuntimeError("Select an armature in the Studio Composer character list first")
        meshes = _bound_meshes(scene, armature)
        if not meshes:
            raise RuntimeError(
                f'No mesh objects use an Armature modifier targeting "{armature.name}"'
            )

        if not settings.initialized:
            # This persistent Scene flag is the first-use marker. Preset values
            # are pulled into the shared UI exactly once after a successful install.
            _install_shared_mainfx(scene, pull_preset_values=True)
            settings.initialized = True
        elif not _active_mainfx_nodes(scene):
            # Repair manually removed data without resetting the saved shared UI.
            _install_shared_mainfx(scene, pull_preset_values=False)

    if not settings.initialized:
        return 0, 0

    mesh_count, matte_count = sync_matte_ids(scene)
    if matte_count == 0:
        raise RuntimeError("No MainFX_mattle Cryptomatte nodes were installed")
    return mesh_count, matte_count
