"""Original, procedural humanoid test material (GPL-3.0-or-later).

No avatar, motion capture, or third-party character assets are required.
"""

import bpy


def make_armature(name="RagdollFixture"):
    """Build a small free-standing humanoid with parent attachment offsets."""
    data = bpy.data.armatures.new(f"{name}Data")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    specifications = [
        ("hips", None, (0, 0, 1.0), (0, 0, 1.25)),
        ("spine", "hips", (0, 0, 1.25), (0, 0, 1.45)),
        ("chest", "spine", (0, 0, 1.45), (0, 0, 1.65)),
        ("neck", "chest", (0, 0, 1.65), (0, 0, 1.76)),
        ("head", "neck", (0, 0, 1.76), (0, 0, 1.95)),
    ]
    for side, sign in (("left", 1), ("right", -1)):
        specifications.extend([
            (f"{side}_shoulder", "chest", (0, 0, 1.61), (sign * .18, 0, 1.61)),
            (f"{side}_upper_arm", f"{side}_shoulder", (sign * .18, 0, 1.61), (sign * .48, 0, 1.57)),
            (f"{side}_lower_arm", f"{side}_upper_arm", (sign * .48, 0, 1.57), (sign * .74, -.02, 1.53)),
            (f"{side}_hand", f"{side}_lower_arm", (sign * .74, -.02, 1.53), (sign * .87, -.02, 1.51)),
            (f"{side}_upper_leg", "hips", (sign * .10, 0, 1.02), (sign * .12, 0, .60)),
            (f"{side}_lower_leg", f"{side}_upper_leg", (sign * .12, 0, .60), (sign * .12, 0, .16)),
            (f"{side}_foot", f"{side}_lower_leg", (sign * .12, 0, .16), (sign * .12, -.20, .12)),
            (f"{side}_toes", f"{side}_foot", (sign * .12, -.20, .12), (sign * .12, -.30, .12)),
        ])
    for name, parent, head, tail in specifications:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        bone.roll = .17 if name.startswith("left") else -.11
        if parent:
            bone.parent = data.edit_bones[parent]
            bone.use_connect = (bone.head - bone.parent.tail).length < 1e-6
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.show_in_front = True
    bpy.context.view_layer.update()
    return obj
