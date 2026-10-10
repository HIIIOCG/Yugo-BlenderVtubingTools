"""Add a self-contained demo graph without changing existing source objects."""
import uuid
import math
import bpy
from mathutils import Vector
from . import bridge, data, model, runtime, skeleton


def create(context, tree, cursor=(0, 0)):
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish Edit Mode before creating the demo')
    units = context.scene.unit_settings.scale_length
    if not math.isfinite(units) or units <= 0:
        raise ValueError('Scene unit scale must be positive')
    selected_objects = tuple(context.selected_objects)
    active_object = context.view_layer.objects.active
    previous_mode = context.mode
    selected_nodes = [(node, node.select) for node in tree.nodes]
    active_node = tree.nodes.active
    created = []
    source = armature = None
    success = False
    try:
        if previous_mode == 'POSE':
            bpy.ops.object.mode_set(mode='OBJECT')
        smpl = model.Skeleton()
        positions, _ = smpl.fk()
        armature = bpy.data.armatures.new('PhysX Demo Source')
        source = bpy.data.objects.new(armature.name, armature)
        source[data.OWNER] = 'demo-'+uuid.uuid4().hex
        context.scene.collection.objects.link(source)
        for obj in selected_objects:
            obj.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
        bpy.ops.object.mode_set(mode='EDIT')
        for i, name in enumerate(smpl.names):
            bone = armature.edit_bones.new(name)
            bone.head = Vector(positions[i])/units
            bone.tail = bone.head+Vector((0, 0, .05/units))
            if smpl.parents[i] >= 0:
                bone.parent = armature.edit_bones[smpl.names[smpl.parents[i]]]
        bpy.ops.object.mode_set(mode='OBJECT')
        for kind, x in (('YUGUS_NODE_bone_map_value', -700), (data.SKELETON, -350),
                        (data.PROTO, 0), (data.PHYSICS, 350)):
            node = tree.nodes.new(kind)
            created.append(node)
            node.location = (cursor[0]+x, cursor[1])
        mapping, actor, tracker, physics = created
        mapping.armature = source
        for entry in mapping.bone_mappings:
            body = next((body for body, humanoid in skeleton.BODY_MAP.items() if humanoid == entry.humanoid), None)
            if body:
                entry.bone_name = body
        tracker.reference_mode = 'CLIP'
        tree.links.new(mapping.outputs['Bone Map'], actor.inputs['Bone Map'])
        tree.links.new(actor.outputs['Actor'], tracker.inputs['Actor'])
        tree.links.new(tracker.outputs['Controller'], physics.inputs['Controller'])
        runtime.action(bridge.context(tree, context.scene), actor, 'Setup')
        for node, _ in selected_nodes:
            node.select = False
        for node in created:
            node.select = True
        tree.nodes.active = physics
        success = True
        return tuple(created)
    finally:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if not success:
            for node in reversed(created):
                tree.nodes.remove(node)
            if source is not None:
                bpy.data.objects.remove(source, do_unlink=True)
            if armature is not None and armature.users == 0:
                bpy.data.armatures.remove(armature)
            for node, selected in selected_nodes:
                node.select = selected
            tree.nodes.active = active_node
        for obj in tuple(context.selected_objects):
            obj.select_set(False)
        for obj in selected_objects:
            obj.select_set(True)
        context.view_layer.objects.active = active_object
        if previous_mode == 'POSE' and active_object:
            bpy.ops.object.mode_set(mode='POSE')
