from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yugo
from yugo.vmc import runtime


yugo.register()

armature_data = bpy.data.armatures.new("YugoHiddenVmcData")
armature = bpy.data.objects.new("YugoHiddenVmc", armature_data)
bpy.context.scene.collection.objects.link(armature)
bpy.context.view_layer.objects.active = armature
armature.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
root = armature.data.edit_bones.new("Root")
root.head = (0.0, 0.0, 0.0)
root.tail = (0.0, 0.0, 1.0)
hips = armature.data.edit_bones.new("Hips")
hips.head = root.tail
hips.tail = (0.0, 0.0, 2.0)
hips.parent = root
hips.use_connect = True
bpy.ops.object.mode_set(mode="OBJECT")
armature.select_set(False)

view_layer = bpy.context.view_layer
armature.hide_set(True, view_layer=view_layer)
assert armature.hide_get(view_layer=view_layer)
assert armature.data.bones["Hips"].use_connect

assert runtime._disconnect_hips_from_parent(bpy.context, armature, "Hips")
assert not armature.data.bones["Hips"].use_connect
assert armature.hide_get(view_layer=view_layer), "Yugo changed the Outliner eye state"
assert armature.hide_viewport is False

print("YUGO_VMC_HIDDEN_ARMATURE_TEST_OK")
