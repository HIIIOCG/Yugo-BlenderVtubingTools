"""Validate one-shot parsing against the installed official VRM Add-on."""

from pathlib import Path
import sys

import bpy


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent
for path in (PACKAGE_PARENT, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio
from yugu_node_studio import runtime, vrm_cache, flow
from yugu_node_studio.constants import TREE_ID
from blender_integration import make_armature, make_face


def main():
    assert hasattr(bpy.types.Armature, "vrm_addon_extension")
    yugu_node_studio.register()
    try:
        armature = make_armature()
        face, material = make_face()
        extension = armature.data.vrm_addon_extension
        extension.spec_version = "1.0"

        human_bones = extension.vrm1.humanoid.human_bones
        human_bones.hips.node.bone_name = "hips"
        human_bones.head.node.bone_name = "head"

        extension.vrm1.meta.name = "Snapshot Test Avatar"
        extension.vrm1.meta.version = "1.2.3"
        author = extension.vrm1.meta.authors.add()
        author.value = "Yugu"

        expression = extension.vrm1.expressions.custom.add()
        expression.custom_name = "smileCustom"
        morph = expression.morph_target_binds.add()
        morph.node.mesh_object_name = face.name
        morph.index = "Smile"
        morph.weight = 0.75
        color = expression.material_color_binds.add()
        color.material = material
        color.type = "color"
        color.target_value = (1.0, 0.2, 0.1, 1.0)
        texture = expression.texture_transform_binds.add()
        texture.material = material
        texture.scale = (1.2, 0.8)
        texture.offset = (0.1, -0.2)

        spring_bone = extension.spring_bone1
        collider = spring_bone.colliders.add()
        collider.uuid = "collider-1"
        collider.node.bone_name = "head"
        collider.shape_type = "Sphere"
        collider.shape.sphere.offset = (0.0, 0.0, 0.1)
        collider.shape.sphere.radius = 0.2
        collider_group = spring_bone.collider_groups.add()
        collider_group.uuid = "group-1"
        collider_group.vrm_name = "Head Colliders"
        reference = collider_group.colliders.add()
        reference.collider_uuid = collider.uuid
        spring = spring_bone.springs.add()
        spring.vrm_name = "Hair"
        spring.center.bone_name = "head"
        group_reference = spring.collider_groups.add()
        group_reference.collider_group_uuid = collider_group.uuid
        for bone_name in ("hairRoot", "hairTip"):
            joint = spring.joints.add()
            joint.node.bone_name = bone_name
            joint.stiffness = 0.8
            joint.drag_force = 0.4
            joint.gravity_power = 0.5
            joint.gravity_dir = (0.0, 0.0, -1.0)

        snapshot = vrm_cache.build_snapshot(armature)
        assert snapshot.vrm_version == "1.0"
        assert snapshot.bone_map.by_humanoid["hips"] == "hips"
        definition = snapshot.expression_map.definitions["smilecustom"]
        assert definition.morph_targets[0].shape_key_name == "Smile"
        assert definition.material_targets[0].material_name == material.name
        assert any(target.bind_type == "textureTransform" for target in definition.material_targets)
        assert snapshot.spring_data.springs[0]["joints"][0]["bone"] == "hairRoot"
        assert snapshot.spring_data.colliders[0]["bone"] == "head"
        assert snapshot.spring_data.armature_name == armature.name
        assert snapshot.meta.values["name"] == "Snapshot Test Avatar"

        tree = bpy.data.node_groups.new("Yugu Spring Snapshot", TREE_ID)
        data_node = tree.nodes.new("YUGUS_NODE_vrm_data")
        data_node.armature = armature
        runtime_snapshot = vrm_cache.get_snapshot(data_node)
        assert runtime_snapshot is not None
        spring_node = tree.nodes.new("YUGUS_NODE_spring_simulation")
        tree.links.new(
            data_node.outputs["Spring Data"], spring_node.inputs["Spring Data"]
        )
        previous_enable_animation = bool(spring_bone.enable_animation)
        apply_node = tree.nodes.new("YUGUS_NODE_apply_rotation")
        for index in range(6):
            result = flow.spring_value(spring_node, runtime_snapshot.spring_data, (index + 1)/24, 1/24)
            flow.apply(apply_node, result)
            bpy.context.view_layer.update()
        assert not spring_bone.enable_animation
        assert flow._springs[str(spring_node.runtime_id)]["steps"] == 15
        assert "Completed steps: 15" in runtime.spring_debug_report(spring_node, bpy.context.scene)
        assert bpy.ops.yugu_node_studio.flow_spring_reload(
            tree_name=tree.name, node_name=spring_node.name
        ) == {"FINISHED"}
        assert str(spring_node.runtime_id) not in flow._springs
        flow.release(str(apply_node.runtime_id))
        assert bool(spring_bone.enable_animation) == previous_enable_animation

        # The official VRM 0.x schema intentionally spells this field
        # ``stiffiness``. Validate the real PropertyGroup, collider bone name,
        # and branching root expansion instead of relying only on test doubles.
        extension.spec_version = "0.0"
        secondary = extension.vrm0.secondary_animation
        vrm0_collider_group = secondary.collider_groups.add()
        vrm0_collider_group.uuid = "vrm0-collider-group"
        vrm0_collider_group.node.bone_name = "head"
        collider_object = bpy.data.objects.new("YuguVrm0Collider", None)
        bpy.context.scene.collection.objects.link(collider_object)
        collider_object.empty_display_type = "SPHERE"
        collider_object.empty_display_size = 0.2
        vrm0_collider = vrm0_collider_group.colliders.add()
        vrm0_collider.bpy_object = collider_object

        vrm0_group = secondary.bone_groups.add()
        vrm0_group.comment = "Legacy Hair"
        vrm0_group.stiffiness = 0.9
        vrm0_group.gravity_power = 0.5
        vrm0_group.gravity_dir = (0.0, 0.0, -1.0)
        vrm0_group.drag_force = 0.3
        vrm0_group.center.bone_name = "head"
        vrm0_group.hit_radius = 0.05
        vrm0_root = vrm0_group.bones.add()
        vrm0_root.bone_name = "hairRoot"
        vrm0_collider_reference = vrm0_group.collider_groups.add()
        vrm0_collider_reference.collider_group_uuid = vrm0_collider_group.uuid

        vrm0_snapshot = vrm_cache.build_snapshot(armature)
        assert vrm0_snapshot.vrm_version == "0.0"
        assert abs(vrm0_snapshot.spring_data.springs[0]["stiffness"] - 0.9) < 1.0e-6
        assert vrm0_snapshot.spring_data.colliders[0]["bone"] == "head"
        vrm0_solver = runtime.NativeSpringSolver(
            armature, vrm0_snapshot.spring_data
        )
        assert len(vrm0_solver.springs) == 1
        assert tuple(
            joint.bone_name for joint in vrm0_solver.springs[0].joints
        ) == ("hairRoot", "hairTip", "hairSide")
        for index in range(3):
            result = flow.spring_value(spring_node, vrm0_snapshot.spring_data, (index + 1)/12, 1/12)
            flow.apply(apply_node, result)
            bpy.context.view_layer.update()
        assert flow._springs[str(spring_node.runtime_id)]["steps"] == 15
        assert "hairSide" in runtime.spring_debug_report(spring_node, bpy.context.scene)
        flow.release(str(apply_node.runtime_id))
        print("YUGUS_NODE_VRM_SNAPSHOT_OK")
    finally:
        yugu_node_studio.unregister()


if __name__ == "__main__":
    main()
