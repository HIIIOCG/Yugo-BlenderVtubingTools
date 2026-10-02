"""Build an original, unstarted ragdoll scene; no external avatar is needed."""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]


def make_character(name="Ragdoll Demo"):
    """Return a small original humanoid armature with rigid weighted skin pieces."""
    points = {
        "hips": ((0, 0, 1.02), (0, 0, 1.22), None),
        "spine": ((0, 0, 1.22), (0, 0, 1.44), "hips"),
        "chest": ((0, 0, 1.44), (0, 0, 1.66), "spine"),
        "neck": ((0, 0, 1.66), (0, 0, 1.79), "chest"),
        "head": ((0, 0, 1.79), (0, 0, 2.08), "neck"),
        "leftUpperArm": ((0, 0, 1.66), (0.40, 0, 1.63), "chest"),
        "leftLowerArm": ((0.40, 0, 1.63), (0.72, 0, 1.61), "leftUpperArm"),
        "leftHand": ((0.72, 0, 1.61), (0.88, 0, 1.61), "leftLowerArm"),
        "rightUpperArm": ((0, 0, 1.66), (-0.40, 0, 1.63), "chest"),
        "rightLowerArm": ((-0.40, 0, 1.63), (-0.72, 0, 1.61), "rightUpperArm"),
        "rightHand": ((-0.72, 0, 1.61), (-0.88, 0, 1.61), "rightLowerArm"),
        "leftUpperLeg": ((0, 0, 1.02), (0.16, 0, 0.58), "hips"),
        "leftLowerLeg": ((0.16, 0, 0.58), (0.16, 0, 0.15), "leftUpperLeg"),
        "leftFoot": ((0.16, 0, 0.15), (0.16, -0.23, 0.09), "leftLowerLeg"),
        "rightUpperLeg": ((0, 0, 1.02), (-0.16, 0, 0.58), "hips"),
        "rightLowerLeg": ((-0.16, 0, 0.58), (-0.16, 0, 0.15), "rightUpperLeg"),
        "rightFoot": ((-0.16, 0, 0.15), (-0.16, -0.23, 0.09), "rightLowerLeg"),
    }
    data = bpy.data.armatures.new(name)
    armature = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for bone_name, (head, tail, parent) in points.items():
        bone = data.edit_bones.new(bone_name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
            bone.use_connect = (bone.parent.tail - bone.head).length < 1e-6
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.show_in_front = True
    armature.display_type = "WIRE"
    materials = []
    for material_name, color in (
        ("Demo Coral", (0.82, 0.22, 0.12, 1)),
        ("Demo Teal", (0.04, 0.48, 0.50, 1)),
        ("Demo Gold", (0.94, 0.62, 0.13, 1)),
    ):
        material = bpy.data.materials.new(material_name)
        material.diffuse_color = color
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = color
        shader.inputs["Roughness"].default_value = 0.6
        materials.append(material)
    for bone_name, (head, tail, _) in points.items():
        a, b = Vector(head), Vector(tail)
        delta = b - a
        radius = 0.09 if "Leg" in bone_name else 0.07
        if bone_name in {"hips", "spine", "chest"}:
            radius = 0.15
        elif bone_name == "head":
            radius = 0.18
        data.bones[bone_name]["ragdoll_radius"] = radius
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, location=(a + b) * 0.5)
        skin = bpy.context.object
        skin.name = "Demo Skin - " + bone_name
        skin.rotation_mode = "QUATERNION"
        skin.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(delta.normalized())
        skin.scale = (radius, radius, delta.length * 0.5 + radius * 0.45)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        group = skin.vertex_groups.new(name=bone_name)
        group.add(list(range(len(skin.data.vertices))), 1.0, "REPLACE")
        modifier = skin.modifiers.new("Ragdoll Skin", "ARMATURE")
        modifier.object = armature
        skin.parent = armature
        material_index = 0 if bone_name.startswith("left") else 1 if bone_name.startswith("right") else 2
        skin.data.materials.append(materials[material_index])
        for polygon in skin.data.polygons:
            polygon.use_smooth = True
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    return armature


def make_graph(armature):
    tree = bpy.data.node_groups.new("Ragdoll Motion Demo", "YUGORAG_NT_preview")
    tree.use_fake_user = True
    source = tree.nodes.new("YUGORAG_N_pose_source")
    source.armature = armature
    source.bone_names = ""
    source.location = (-520, 80)
    source.label = "1. Original demo skeleton"
    ragdoll = tree.nodes.new("YUGORAG_N_ragdoll")
    ragdoll.location = (-170, 80)
    ragdoll.label = "2. Hit, fall, recover"
    ragdoll.impulse_bone = "hips"
    ragdoll.impulse = (3, 0, 4)
    ragdoll.ground_z = 0.0
    ragdoll.min_hold = 0.8
    ragdoll.settle_time = 0.3
    ragdoll.timeout = 8.0
    ragdoll.recovery_duration = 1.5
    ragdoll.auto_recover = True
    apply = tree.nodes.new("YUGORAG_N_apply")
    apply.location = (190, 80)
    apply.label = "3. Start 60 Hz preview"
    apply.hz = "60"
    apply.preview_seconds = 30.0
    trigger = tree.nodes.new("YUGORAG_N_hz")
    trigger.location = (-170, 360)
    trigger.label = "4. Wall-clock update rate"
    trigger.hz = "60"
    tree.links.new(source.outputs["Pose"], ragdoll.inputs["Pose"])
    tree.links.new(ragdoll.outputs["Motion"], apply.inputs["Motion"])
    tree.links.new(trigger.outputs["Tick"], apply.inputs["Tick"])
    return tree, source, ragdoll, apply


def load_source_package():
    sys.path.insert(0, str(ROOT / "addons"))
    addon = importlib.import_module("yugo_ragdoll")
    expected = (ROOT / "addons" / "yugo_ragdoll").resolve()
    assert Path(addon.__file__).resolve().parent == expected
    addon.register()
    return addon


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "yugo-ragdoll-motion-demo.blend")
    parser.add_argument("--render", type=Path, help="Optional original demo preview PNG")
    args = parser.parse_args(argv)
    addon = load_source_package()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    armature = make_character()
    tree, _, ragdoll, apply = make_graph(armature)
    bpy.ops.mesh.primitive_plane_add(size=12)
    ground = bpy.context.object
    ground.name = "Demo Ground (z = 0)"
    ground_material = bpy.data.materials.new("Demo Ground")
    ground_material.diffuse_color = (0.07, 0.10, 0.15, 1)
    ground_material.use_nodes = True
    ground_material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = ground_material.diffuse_color
    ground.data.materials.append(ground_material)
    bpy.ops.object.light_add(type="AREA", location=(2, -3, 5))
    bpy.context.object.data.energy = 750
    bpy.context.object.data.shape = "DISK"
    bpy.context.object.data.size = 4
    bpy.ops.object.camera_add(location=(3.7, -5.5, 2.7))
    camera = bpy.context.object
    camera.data.lens = 65
    camera.rotation_euler = (Vector((0, 0, 1.1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera
    bpy.context.scene.frame_set(42, subframe=0.25)
    bpy.context.scene["ragdoll_demo_material"] = "Original procedural humanoid, authored for task #14; GPL-3.0-or-later"
    bpy.context.scene["ragdoll_demo_started"] = False
    text = bpy.data.texts.new("Ragdoll Quick Start")
    text.write(
        "Ragdoll Motion Node demo\n\n"
        "Install the companion Yugo Ragdoll Lab ZIP before opening this file.\n"
        "This is an original procedural character; no avatar or VRM add-on is required.\n\n"
        "Layout opens the Ragdoll Motion Preview node graph. Switch to Modeling for the character.\n"
        "The graph is Pose Source -> Ragdoll Motion -> Apply Ragdoll; Hz Trigger drives Tick.\n"
        "Use Play on Apply, then Apply / Add Impulse on Ragdoll.\n"
        "Hit again during the fall to add force from the current pose.\n"
        "Auto Recover waits for minimum hold and stability, with a timeout exit.\n"
        "Stop / Restore restores the original pose. Playback pauses after 30 seconds.\n\n"
        "The timeline is deliberately parked at frame 42 + 0.25 and never played.\n"
        "The demo is saved with the preview stopped. Ground is z=0.\n"
        "This is an isolated ragdoll module preview; Studio integration awaits its core APIs.\n"
    )
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_distance = 4.4
                area.spaces.active.region_3d.view_location = (0, 0, 1.0)
                area.spaces.active.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
                area.spaces.active.shading.color_type = "MATERIAL"
                area.spaces.active.overlay.show_floor = False
    if bpy.context.screen is not None:
        view_areas = [area for area in bpy.context.screen.areas if area.type == "VIEW_3D"]
        if view_areas:
            area = max(view_areas, key=lambda candidate: candidate.width * candidate.height)
            area.type = "NODE_EDITOR"
            area.spaces.active.tree_type = "YUGORAG_NT_preview"
            area.spaces.active.pin = True
            area.spaces.active.node_tree = tree
            tree.nodes.active = apply
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            if region is not None:
                with bpy.context.temp_override(area=area, region=region):
                    bpy.ops.node.view_all()
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output), check_existing=False)
    assert bpy.context.scene.frame_current == 42 and abs(bpy.context.scene.frame_subframe - 0.25) < 1e-6
    assert len(armature.data.bones) == 17
    assert len(tree.nodes) == 4 and len(tree.links) == 3
    print("RAGDOLL_DEMO_OK", args.output)
    if args.render is not None:
        args.render.parent.mkdir(parents=True, exist_ok=True)
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 24
        scene.render.resolution_x, scene.render.resolution_y = 800, 800
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = str(args.render.resolve())
        bpy.ops.render.render(write_still=True)
    addon.unregister()


if __name__ == "__main__":
    main()
