"""Bake VRM MToon preview outlines into independent mesh objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bmesh
import bpy
from bpy.types import Collection, Context, Material, NodesModifier, Object


OUTLINE_NODE_GROUP_NAME = "VRM Add-on MToon 1.0 Outline Geometry Revision 1"
OUTLINE_COLLECTION_NAME = "Outline"
OUTLINE_MATERIAL_NAME = "Black_OutlineFast"
OUTLINE_OBJECT_SUFFIX = "_OutlineMesh"
MERGE_DISTANCE = 0.0001

# Tests can point at a small generated library without changing production lookup.
PRESET_PATH_OVERRIDE: Optional[Path] = None


@dataclass(frozen=True)
class ShapeKeySpec:
    name: str
    relative_key_name: str
    interpolation: str
    mute: bool
    slider_min: float
    slider_max: float
    value: float
    vertex_group: str


def _preset_path() -> Path:
    if PRESET_PATH_OVERRIDE is not None:
        return PRESET_PATH_OVERRIDE

    package_preset = Path(__file__).with_name("preset.blend")
    if package_preset.is_file():
        return package_preset

    # The development workspace keeps the shared preset beside the yugo folders.
    workspace_preset = Path(__file__).parent.parent / "preset.blend"
    if workspace_preset.is_file():
        return workspace_preset
    return package_preset


def _append_outline_material() -> Material:
    preset_path = _preset_path()
    if not preset_path.is_file():
        raise RuntimeError(f"Outline preset not found: {preset_path}")

    with bpy.data.libraries.load(str(preset_path), link=False) as (data_from, data_to):
        if OUTLINE_MATERIAL_NAME not in data_from.materials:
            raise RuntimeError(
                f'Preset "{preset_path}" has no material named '
                f'"{OUTLINE_MATERIAL_NAME}"'
            )
        data_to.materials = [OUTLINE_MATERIAL_NAME]

    material = data_to.materials[0]
    if not isinstance(material, Material):
        raise RuntimeError(f'Failed to append material "{OUTLINE_MATERIAL_NAME}"')
    return material


def is_mtoon_outline_modifier(modifier: object) -> bool:
    return (
        isinstance(modifier, NodesModifier)
        and modifier.node_group is not None
        and modifier.node_group.name == OUTLINE_NODE_GROUP_NAME
    )


def _input_sockets(modifier: NodesModifier) -> list[tuple[str, str]]:
    node_group = modifier.node_group
    if node_group is None:
        return []

    sockets: list[tuple[str, str]] = []
    for item in node_group.interface.items_tree:
        if item.item_type != "SOCKET" or getattr(item, "in_out", None) != "INPUT":
            continue
        sockets.append((item.name, item.identifier))
    return sockets


def _input_identifier(
    modifier: NodesModifier,
    name: str,
    fallback_index: int,
) -> str:
    sockets = _input_sockets(modifier)
    for socket_name, identifier in sockets:
        if socket_name == name:
            return identifier
    if fallback_index < len(sockets):
        return sockets[fallback_index][1]
    raise RuntimeError(
        f'Unsupported MToon outline node interface on modifier "{modifier.name}"'
    )


def _get_modifier_input(
    modifier: NodesModifier,
    name: str,
    fallback_index: int,
) -> object:
    identifier = _input_identifier(modifier, name, fallback_index)
    if bpy.app.version >= (5, 2):
        socket = getattr(getattr(modifier.properties, "inputs", None), identifier, None)
        return getattr(socket, "value", None)
    return modifier.get(identifier)


def _set_modifier_input(
    modifier: NodesModifier,
    name: str,
    fallback_index: int,
    value: object,
) -> None:
    identifier = _input_identifier(modifier, name, fallback_index)
    if bpy.app.version >= (5, 2):
        socket = getattr(getattr(modifier.properties, "inputs", None), identifier, None)
        if socket is None:
            raise RuntimeError(
                f'Input "{name}" is unavailable on modifier "{modifier.name}"'
            )
        socket.type = "VALUE"
        socket.value = value
        return
    modifier[identifier] = value


def _source_armature(obj: Object) -> Optional[Object]:
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    for modifier in obj.modifiers:
        if modifier.type != "ARMATURE":
            continue
        armature = getattr(modifier, "object", None)
        if isinstance(armature, Object) and armature.type == "ARMATURE":
            return armature
    return None


def _collection_container(context: Context, armature: Optional[Object]) -> Collection:
    if armature is not None and armature.users_collection:
        return armature.users_collection[0]
    return context.scene.collection


def _outline_collection(context: Context, armature: Optional[Object]) -> Collection:
    container = _collection_container(context, armature)
    existing = container.children.get(OUTLINE_COLLECTION_NAME)
    if existing is not None:
        return existing

    collection = bpy.data.collections.new(OUTLINE_COLLECTION_NAME)
    container.children.link(collection)
    return collection


def _remove_object_and_mesh(obj: Object) -> None:
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _merge_by_distance(context: Context, obj: Object) -> None:
    """Merge the prepared copy while Blender propagates topology to every key."""

    selected_before = tuple(context.selected_objects)
    active_before = context.view_layer.objects.active
    try:
        for selected in selected_before:
            selected.select_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        obj.active_shape_key_index = 0
        obj.show_only_shape_key = False

        if bpy.ops.object.mode_set(mode="EDIT") != {"FINISHED"}:
            raise RuntimeError("Could not enter Edit Mode for Merge by Distance")
        bpy.ops.mesh.select_all(action="SELECT")
        if bpy.ops.mesh.remove_doubles(threshold=MERGE_DISTANCE) != {"FINISHED"}:
            raise RuntimeError("Merge by Distance failed")
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise RuntimeError("Could not leave Edit Mode after Merge by Distance")
        obj.data.update()
    finally:
        if obj.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(False)
        for selected in selected_before:
            if selected.name in context.view_layer.objects:
                selected.select_set(True)
        if (
            active_before is not None
            and active_before.name in context.view_layer.objects
        ):
            context.view_layer.objects.active = active_before


def _shape_key_specs(obj: Object) -> tuple[bool, float, list[ShapeKeySpec]]:
    shape_keys = getattr(obj.data, "shape_keys", None)
    if shape_keys is None:
        return True, 0.0, []

    specs = [
        ShapeKeySpec(
            name=key.name,
            relative_key_name=key.relative_key.name,
            interpolation=key.interpolation,
            mute=key.mute,
            slider_min=key.slider_min,
            slider_max=key.slider_max,
            value=key.value,
            vertex_group=key.vertex_group,
        )
        for key in shape_keys.key_blocks
    ]
    return shape_keys.use_relative, shape_keys.eval_time, specs


def _remove_all_shape_keys(obj: Object) -> None:
    shape_keys = getattr(obj.data, "shape_keys", None)
    if shape_keys is None:
        return
    for key in reversed(tuple(shape_keys.key_blocks)):
        obj.shape_key_remove(key)


def _set_mesh_to_shape_key(obj: Object, shape_key_index: int) -> None:
    shape_keys = getattr(obj.data, "shape_keys", None)
    if shape_keys is None:
        if shape_key_index != 0:
            raise RuntimeError(f'Invalid Shape Key index on "{obj.name}"')
        return
    if not 0 <= shape_key_index < len(shape_keys.key_blocks):
        raise RuntimeError(f'Invalid Shape Key index on "{obj.name}"')

    key = shape_keys.key_blocks[shape_key_index]
    coordinates = [0.0] * (len(key.data) * 3)
    key.data.foreach_get("co", coordinates)
    _remove_all_shape_keys(obj)
    if len(obj.data.vertices) * 3 != len(coordinates):
        raise RuntimeError(f'Shape Key topology mismatch on "{obj.name}"')
    obj.data.vertices.foreach_set("co", coordinates)
    obj.data.update()


def _realize_instances_inside_modifier(modifier: NodesModifier) -> bpy.types.NodeTree:
    source_group = modifier.node_group
    if source_group is None:
        raise RuntimeError(f'Outline modifier "{modifier.name}" has no node group')

    node_group = source_group.copy()
    try:
        node_group.name = f"__YugoOutlineRealized__{modifier.name}"
        group_output = next(
            (
                node
                for node in node_group.nodes
                if node.bl_idname == "NodeGroupOutput" and node.is_active_output
            ),
            None,
        )
        if group_output is None:
            raise RuntimeError(
                f'Outline modifier "{modifier.name}" has no group output'
            )
        geometry_input = group_output.inputs.get("Geometry")
        if geometry_input is None or not geometry_input.links:
            raise RuntimeError(
                f'Outline modifier "{modifier.name}" has no geometry output'
            )

        output_link = geometry_input.links[0]
        from_socket = output_link.from_socket
        node_group.links.remove(output_link)
        realize_instances = node_group.nodes.new("GeometryNodeRealizeInstances")
        node_group.links.new(from_socket, realize_instances.inputs["Geometry"])
        node_group.links.new(realize_instances.outputs["Geometry"], geometry_input)
        modifier.node_group = node_group
        return node_group
    except Exception:
        if node_group.users == 0:
            bpy.data.node_groups.remove(node_group)
        raise


def _keep_material_faces(mesh: bpy.types.Mesh, material: Material) -> None:
    material_indices = {
        index for index, slot_material in enumerate(mesh.materials) if slot_material == material
    }
    if not material_indices:
        evaluated_materials = [slot.name if slot is not None else None for slot in mesh.materials]
        raise RuntimeError(
            "Evaluated MToon outline has no replacement material faces; "
            f"materials={evaluated_materials}, replacement={material.name}"
        )

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        remove_faces = [
            face for face in bm.faces if face.material_index not in material_indices
        ]
        if remove_faces:
            bmesh.ops.delete(bm, geom=remove_faces, context="FACES")

        loose_vertices = [vertex for vertex in bm.verts if not vertex.link_faces]
        if loose_vertices:
            bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
        if not bm.faces:
            raise RuntimeError("Evaluated MToon outline contains no outline faces")

        for face in bm.faces:
            face.material_index = 0
        bm.to_mesh(mesh)
    finally:
        bm.free()

    mesh.materials.clear()
    mesh.materials.append(material)
    mesh.validate(clean_customdata=False)
    mesh.update()


def _evaluated_outline_mesh(
    context: Context,
    template: Object,
    shape_key_index: int,
    outline_material: Material,
) -> bpy.types.Mesh:
    """Evaluate one merged Shape Key through a cycle-free outline modifier."""

    reference = template.copy()
    reference.data = template.data.copy()
    reference.name = f"__YugoOutlineReference__{template.name}"
    context.scene.collection.objects.link(reference)
    reference.hide_viewport = False
    reference.hide_render = False
    reference.hide_set(False)

    evaluator: Optional[Object] = None
    result_mesh: Optional[bpy.types.Mesh] = None
    try:
        for modifier in tuple(reference.modifiers):
            reference.modifiers.remove(modifier)
        _set_mesh_to_shape_key(reference, shape_key_index)

        evaluator = template.copy()
        evaluator.data = reference.data.copy()
        evaluator.name = f"__YugoOutlineEvaluate__{template.name}"
        context.scene.collection.objects.link(evaluator)
        evaluator.hide_viewport = False
        evaluator.hide_render = False
        evaluator.hide_set(False)

        for modifier in evaluator.modifiers:
            if not isinstance(modifier, NodesModifier) or modifier.node_group is None:
                raise RuntimeError(
                    f'Unexpected modifier "{modifier.name}" on outline evaluator'
                )
            _set_modifier_input(modifier, "Object", 13, reference)

        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = evaluator.evaluated_get(depsgraph)
        result_mesh = bpy.data.meshes.new_from_object(
            evaluated,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        _keep_material_faces(result_mesh, outline_material)
        return result_mesh
    except Exception:
        if result_mesh is not None and result_mesh.users == 0:
            bpy.data.meshes.remove(result_mesh)
        raise
    finally:
        if evaluator is not None:
            _remove_object_and_mesh(evaluator)
        _remove_object_and_mesh(reference)


def _drive_from_source(
    target: object,
    property_name: str,
    source_id: bpy.types.ID,
    source_path: str,
) -> None:
    fcurve = target.driver_add(property_name)
    driver = fcurve.driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.name = "source_value"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id_type = source_id.id_type
    variable.targets[0].id = source_id
    variable.targets[0].data_path = source_path


def _restore_shape_keys(
    result: Object,
    source: Object,
    specs: list[ShapeKeySpec],
    shape_meshes: list[bpy.types.Mesh],
    *,
    use_relative: bool,
    eval_time: float,
) -> None:
    if not specs:
        return
    if len(specs) != len(shape_meshes):
        raise RuntimeError("Internal Shape Key result count mismatch")

    vertex_count = len(result.data.vertices)
    for mesh in shape_meshes:
        if len(mesh.vertices) != vertex_count:
            raise RuntimeError(
                "Outline topology changed between Shape Keys: "
                f"expected {vertex_count} vertices, got {len(mesh.vertices)}"
            )

    result_blocks: dict[str, bpy.types.ShapeKey] = {}
    for index, (spec, mesh) in enumerate(zip(specs, shape_meshes)):
        block = result.shape_key_add(name=spec.name, from_mix=False)
        result_blocks[spec.name] = block
        if index:
            coordinates = [0.0] * (vertex_count * 3)
            mesh.vertices.foreach_get("co", coordinates)
            block.data.foreach_set("co", coordinates)
        block.interpolation = spec.interpolation
        block.mute = spec.mute
        block.slider_min = spec.slider_min
        block.slider_max = spec.slider_max
        block.value = spec.value
        if spec.vertex_group and result.vertex_groups.get(spec.vertex_group):
            block.vertex_group = spec.vertex_group

    result_shape_keys = result.data.shape_keys
    source_shape_keys = source.data.shape_keys
    if result_shape_keys is None or source_shape_keys is None:
        raise RuntimeError("Failed to rebuild outline Shape Keys")

    result_shape_keys.use_relative = use_relative
    result_shape_keys.eval_time = eval_time
    for spec in specs:
        block = result_blocks[spec.name]
        relative = result_blocks.get(spec.relative_key_name)
        if relative is not None:
            block.relative_key = relative

    if use_relative:
        for spec in specs[1:]:
            source_block = source_shape_keys.key_blocks.get(spec.name)
            if source_block is None:
                continue
            _drive_from_source(
                result_blocks[spec.name],
                "value",
                source_shape_keys,
                source_block.path_from_id("value"),
            )
    else:
        _drive_from_source(
            result_shape_keys,
            "eval_time",
            source_shape_keys,
            "eval_time",
        )

    result.active_shape_key_index = min(
        source.active_shape_key_index,
        len(specs) - 1,
    )


def _bake_object(
    context: Context,
    source: Object,
    outline_material: Material,
) -> tuple[Object, set[Material]]:
    outline_modifiers = [
        modifier for modifier in source.modifiers if is_mtoon_outline_modifier(modifier)
    ]
    if not outline_modifiers:
        raise RuntimeError(f'"{source.name}" has no VRM MToon outline modifier')

    template = source.copy()
    template.data = source.data.copy()
    template.name = f"__YugoOutlineBake__{source.name}"
    context.scene.collection.objects.link(template)
    template.hide_viewport = False
    template.hide_render = False
    template.hide_set(False)

    base_materials: set[Material] = set()
    result: Optional[Object] = None
    shape_meshes: list[bpy.types.Mesh] = []
    temporary_node_groups: list[bpy.types.NodeTree] = []
    try:
        # Blender propagates this topology edit through all Shape Keys on the
        # private copy. It closes tiny VRM import seams without touching source.
        _merge_by_distance(context, template)
        use_relative, eval_time, shape_specs = _shape_key_specs(template)

        for modifier_name in list(template.modifiers.keys()):
            modifier = template.modifiers.get(modifier_name)
            if modifier is None:
                continue
            if not is_mtoon_outline_modifier(modifier):
                template.modifiers.remove(modifier)

        temporary_outline_modifiers = list(template.modifiers)
        for modifier in temporary_outline_modifiers:
            temporary_node_groups.append(_realize_instances_inside_modifier(modifier))
            base_material = _get_modifier_input(modifier, "Material", 1)
            if isinstance(base_material, Material):
                base_materials.add(base_material)
            _set_modifier_input(modifier, "Outline Material", 2, outline_material)

        shape_count = len(shape_specs) if shape_specs else 1
        for shape_key_index in range(shape_count):
            shape_meshes.append(
                _evaluated_outline_mesh(
                    context,
                    template,
                    shape_key_index,
                    outline_material,
                )
            )

        result_mesh = shape_meshes[0]

        result = source.copy()
        result.data = result_mesh
        result.name = f"{source.name}{OUTLINE_OBJECT_SUFFIX}"
        result_mesh.name = f"{result.name}_Mesh"
        for modifier_name in list(result.modifiers.keys()):
            modifier = result.modifiers.get(modifier_name)
            if modifier is not None and is_mtoon_outline_modifier(modifier):
                result.modifiers.remove(modifier)

        _restore_shape_keys(
            result,
            source,
            shape_specs,
            shape_meshes,
            use_relative=use_relative,
            eval_time=eval_time,
        )

        collection = _outline_collection(context, _source_armature(source))
        collection.objects.link(result)
        result["yugus_outline_source"] = source.name
        return result, base_materials
    except Exception:
        if result is not None:
            _remove_object_and_mesh(result)
        elif shape_meshes and shape_meshes[0].users == 0:
            bpy.data.meshes.remove(shape_meshes[0])
        raise
    finally:
        # Non-basis evaluated meshes have been copied into Shape Key blocks.
        for mesh in shape_meshes[1:]:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        _remove_object_and_mesh(template)
        for node_group in temporary_node_groups:
            if node_group.users == 0:
                bpy.data.node_groups.remove(node_group)


def _disable_outline_preview(materials: set[Material]) -> None:
    for material in materials:
        extension = getattr(material, "vrm_addon_extension", None)
        mtoon1 = getattr(extension, "mtoon1", None)
        extensions = getattr(mtoon1, "extensions", None)
        mtoon = getattr(extensions, "vrmc_materials_mtoon", None)
        if mtoon is None or not hasattr(mtoon, "enable_outline_preview"):
            continue
        mtoon.enable_outline_preview = False


def apply_selected_outlines(context: Context) -> tuple[list[Object], list[str]]:
    if context.mode != "OBJECT":
        return [], ["Apply Outline is only available in Object Mode"]

    sources = [
        obj
        for obj in tuple(context.selected_objects)
        if obj.type == "MESH"
        and any(is_mtoon_outline_modifier(modifier) for modifier in obj.modifiers)
    ]
    if not sources:
        return [], ["No selected mesh has a VRM MToon outline modifier"]

    outline_material = _append_outline_material()
    created: list[Object] = []
    source_materials: set[Material] = set()
    try:
        for source in sources:
            try:
                result, materials = _bake_object(context, source, outline_material)
            except Exception as exc:
                raise RuntimeError(f'{source.name}: {exc}') from exc
            created.append(result)
            source_materials.update(materials)
    except Exception:
        for result in reversed(created):
            _remove_object_and_mesh(result)
        if outline_material.users == 0:
            bpy.data.materials.remove(outline_material)
        raise

    _disable_outline_preview(source_materials)
    for source in sources:
        for modifier_name in list(source.modifiers.keys()):
            modifier = source.modifiers.get(modifier_name)
            if modifier is not None and is_mtoon_outline_modifier(modifier):
                source.modifiers.remove(modifier)
    context.view_layer.update()
    return created, []


def register() -> None:
    pass


def unregister() -> None:
    pass
