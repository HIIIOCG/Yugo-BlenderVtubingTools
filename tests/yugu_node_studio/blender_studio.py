"""Independent registration, lifecycle, keyboard and native expression-mask tests."""
from pathlib import Path
import sys
import importlib
from types import SimpleNamespace
import bpy
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()
import yugo
assert Path(yugo.__file__).resolve() == Path(__file__).resolve().parents[2] / "addons/yugo/__init__.py"

import yugu_node_studio as addon
from yugu_node_studio import keyboard, runtime, studio, nodes, scheduler
from yugu_node_studio.data import ExpressionFrame, ExpressionMapData, ExpressionDefinition


def event(key, value="PRESS", **kwargs):
    return SimpleNamespace(type=key, value=value, ctrl=kwargs.get("ctrl", False),
                           shift=False, alt=kwargs.get("alt", False), oskey=False,
                           is_repeat=kwargs.get("repeat", False))


def advance():
    keyboard.tick(bpy.context, 1/60)


TREE_ID = nodes.YUGUS_NT_humanoid_motion.bl_idname
SCENE_FIELDS = (
    "yugo_avatars", "yugo_avatar_index", "yugo_motion_layers",
    "yugo_motion_layer_index", "yugo_vrm_armature", "yugo_composing",
    "yugus_avatars", "yugus_avatar_index", "yugus_composing",
    "yugu_node_studio_tree",
)


# Integrated Yugo owns the same saved graph IDs as independent Studio.
yugo.register()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID) is yugo.node.nodes.YUGUS_NT_humanoid_motion
assert hasattr(bpy.types.Scene, "yugo_avatars")
assert hasattr(bpy.types.Scene, "yugo_composing")
assert bpy.app.timers.is_registered(yugo.scheduler._motion_timer)
assert "keybinds" in yugo.scheduler._motion_callbacks
parent_timer = yugo.scheduler._motion_timer
parent_callbacks = dict(yugo.scheduler._motion_callbacks)
parent_fields = {name: bpy.types.Scene.bl_rna.properties[name].as_pointer()
                 for name in SCENE_FIELDS if hasattr(bpy.types.Scene, name)}
try:
    with RestrictBlend():
        addon.register()
except RuntimeError as error:
    assert "Another Yugo Node" in str(error) and "Disable" in str(error), error
else:
    raise AssertionError("Studio must refuse integrated Yugo's Node owner")
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID) is yugo.node.nodes.YUGUS_NT_humanoid_motion
assert yugo.scheduler._motion_timer is parent_timer
assert bpy.app.timers.is_registered(parent_timer)
assert yugo.scheduler._motion_callbacks == parent_callbacks
assert {name: bpy.types.Scene.bl_rna.properties[name].as_pointer()
        for name in SCENE_FIELDS if hasattr(bpy.types.Scene, name)} == parent_fields
assert yugo._registration_complete and not addon._registered
yugo.unregister()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID, None) is None
assert all(not hasattr(bpy.types.Scene, name) for name in SCENE_FIELDS)
assert not bpy.app.timers.is_registered(parent_timer)

# Studio's original feature checks run with Studio as the sole Node owner.
with RestrictBlend():
    addon.register()
addon.register()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID) is addon.nodes.YUGUS_NT_humanoid_motion
assert hasattr(bpy.types.Scene, "yugus_avatars")
assert not hasattr(bpy.types.Scene, "yugo_avatars")
studio_timer = addon.scheduler._motion_timer
studio_callbacks = dict(addon.scheduler._motion_callbacks)
studio_fields = {name: bpy.types.Scene.bl_rna.properties[name].as_pointer()
                 for name in SCENE_FIELDS if hasattr(bpy.types.Scene, name)}
try:
    with RestrictBlend():
        yugo.register()
except RuntimeError as error:
    assert "Another Yugo Node" in str(error) and "Disable" in str(error), error
else:
    raise AssertionError("Integrated Yugo must refuse Studio's Node owner")
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID) is addon.nodes.YUGUS_NT_humanoid_motion
assert addon._registered and addon.keyboard._enabled
assert addon.scheduler._motion_timer is studio_timer
assert bpy.app.timers.is_registered(studio_timer)
assert addon.scheduler._motion_callbacks == studio_callbacks
assert {name: bpy.types.Scene.bl_rna.properties[name].as_pointer()
        for name in SCENE_FIELDS if hasattr(bpy.types.Scene, name)} == studio_fields
assert not yugo._registered_modules and not yugo._registration_complete
assert not yugo.node._registered
tree = bpy.data.node_groups.new("Studio keyboard", "YUGUS_NT_humanoid_motion")
bpy.context.scene.yugu_node_studio_tree = tree
a = tree.nodes.new("YUGUS_NODE_key_bind")
b = tree.nodes.new("YUGUS_NODE_key_bind")
assert a.key == "NONE" and not a.binary and abs(a.smooth - 0.15) < 1e-6
a.key, b.key = "A", "B"
a.binary = b.binary = True
a.use_ctrl = True
assert not keyboard.process_event(event("A"))
assert not keyboard.process_event(event("A", ctrl=True, alt=True))
assert keyboard.process_event(event("A", ctrl=True))
advance()
assert keyboard.signal(a) == 1
assert keyboard.process_event(event("A", "RELEASE"))  # Ctrl already released.
advance()
assert keyboard.signal(a) == 0
a.use_ctrl = False
a.mode = "TRIGGER"
keyboard.process_event(event("A"))
keyboard.process_event(event("A", repeat=True))
advance()
assert keyboard.signal(a) == 1
b.mode = "SOLO"
keyboard.process_event(event("B"))
advance()
assert keyboard.signal(a) == 0 and keyboard.signal(b) == 1
keyboard.process_event(event("B"))
advance()
assert keyboard.signal(a) == 1 and keyboard.signal(b) == 0
b.mode = "HOLD"
b.mask_exclusive = True
keyboard.process_event(event("B"))
advance()
assert keyboard.signal(a) == 0 and keyboard.signal(b) == 1
keyboard.dispatch(bpy.context, event("WINDOW_DEACTIVATE"))
advance()
assert keyboard.signal(a) == 1 and keyboard.signal(b) == 0
a.binary = False
keyboard.process_event(event("A"))
advance()
assert 0 < keyboard.signal(a) < 1
for _ in range(120):
    advance()
assert keyboard.signal(a) == 0
a.binary = True
a.key = "RET"
assert keyboard.dispatch(bpy.context, event("RET")) == {"PASS_THROUGH"}
advance()
assert keyboard.signal(a) == 1
keyboard.reset()
a.key, a.mode, a.mask_eyes = "A", "HOLD", True
keyboard.process_event(event("A"))
advance()
output = tree.nodes.new("YUGUS_NODE_blender_output")
action = tree.nodes.new("YUGUS_NODE_expression_action")
emap = tree.nodes.new("YUGUS_NODE_expression_map_value")
entry = emap.expressions.add()
entry.name = "happy"
tree.links.new(emap.outputs["Expression Map"], action.inputs["Expression Map"])
tree.links.new(a.outputs["Trigger"], action.inputs["Trigger"])
tree.links.new(action.outputs["Expressions"], output.inputs["Expressions"])
action.expression_key = "happy"
definition = ExpressionDefinition("blinkLeft", "blinkleft", False, (), ())
values = keyboard.mask_expressions(output, ExpressionFrame({"happy": 1, "aa": 0.5}),
                                  ExpressionMapData({"blinkleft": definition}))
assert values.values == {"happy": 1, "aa": 0.5, "blinkleft": 0}
a.mask_mouth = True
assert keyboard.mask_expressions(output, ExpressionFrame({"aa": 0.5}), None).values["aa"] == 0
entry.name = "blinkLeft"
action.expression_key = "blinkleft"
assert keyboard.mask_expressions(output, ExpressionFrame({"blinkleft": 0.8}), None).values["blinkleft"] == 0.8
unrelated = tree.nodes.new("YUGUS_NODE_blender_output")
assert keyboard.mask_expressions(unrelated, ExpressionFrame({"aa": 0.5}), None).values["aa"] == 0.5
duplicate = tree.nodes.new("YUGUS_NODE_key_bind")
duplicate.copy(a)
assert duplicate.runtime_id != a.runtime_id

from blender_composing import LayoutRecorder
for panel in (studio.YUGUS_PT_node_materials, studio.YUGUS_PT_node_composer,
              studio.YUGUS_PT_node_mainfx):
    panel.draw(SimpleNamespace(layout=LayoutRecorder()), bpy.context)
a.draw_buttons(bpy.context, LayoutRecorder())
# Character collection controls are independent of the old Yugo avatar list.
rig = bpy.data.objects.new("Studio Composer Rig", bpy.data.armatures.new("Studio Composer Rig"))
bpy.context.scene.collection.objects.link(rig)
body = bpy.data.objects.new("Studio Composer Body", bpy.data.meshes.new("Studio Composer Body"))
bpy.context.scene.collection.objects.link(body)
body.modifiers.new("Armature", "ARMATURE").object = rig
bone_map = tree.nodes.new("YUGUS_NODE_bone_map_value")
bone_map.armature = rig
assert bpy.ops.yugu_node_studio.composer_character(action="GRAPH") == {"FINISHED"}
assert len(bpy.context.scene.yugus_avatars) == 1
assert bpy.ops.yugu_node_studio.composer_character(action="GRAPH") == {"FINISHED"}
assert len(bpy.context.scene.yugus_avatars) == 1
bpy.context.scene.yugus_avatars[0].composing_enabled = True
assert bpy.context.scene.yugus_composing.initialized
assert bpy.ops.yugu_node_studio.composer_reload() == {"FINISHED"}
assert bpy.ops.yugu_node_studio.composer_character(action="REMOVE") == {"FINISHED"}
assert not bpy.context.scene.yugus_avatars
addon._undo_pre(None)
assert scheduler.is_suspended() and not runtime._key_state
addon._undo_post(None)
assert not scheduler.is_suspended()
with RestrictBlend():
    addon.unregister()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID, None) is None
assert all(not hasattr(bpy.types.Scene, name) for name in SCENE_FIELDS)
assert not bpy.app.timers.is_registered(studio_timer)
assert not keyboard._windows and not scheduler._motion_callbacks
with RestrictBlend():
    addon.register()
    addon.unregister()
# Reload while enabled, as Blender does during a script/add-on refresh.
addon.register()
old_keyboard = addon.keyboard
old_runtime = addon.runtime
old_timer = addon.scheduler._motion_timer
with RestrictBlend():
    importlib.reload(addon)
    addon.register()
assert addon.keyboard is not old_keyboard
assert not old_keyboard._enabled
assert not bpy.app.timers.is_registered(old_timer)
assert not bpy.app.timers.is_registered(old_runtime._timer)
assert sum(handler is addon._load_pre for handler in bpy.app.handlers.load_pre) == 1
with RestrictBlend():
    addon.unregister()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID, None) is None
assert all(not hasattr(bpy.types.Scene, name) for name in SCENE_FIELDS)
addon.register()
addon.unregister()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID, None) is None
assert all(not hasattr(bpy.types.Scene, name) for name in SCENE_FIELDS)
# Disabling Studio releases the shared graph IDs so integrated Yugo can return.
with RestrictBlend():
    yugo.register()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID) is yugo.node.nodes.YUGUS_NT_humanoid_motion
assert yugo._registration_complete and yugo.node._registered
assert hasattr(bpy.types.Scene, "yugo_avatars")
assert hasattr(bpy.types.Scene, "yugo_motion_layers")
assert hasattr(bpy.types.Scene, "yugo_vrm_armature")
assert hasattr(bpy.types.Scene, "yugo_composing")
assert hasattr(bpy.types.Scene, "yugu_node_studio_tree")
assert not hasattr(bpy.types.Scene, "yugus_avatars")
assert not hasattr(bpy.types.Scene, "yugus_composing")
assert bpy.app.timers.is_registered(yugo.scheduler._motion_timer)
assert "keybinds" in yugo.scheduler._motion_callbacks
yugo.unregister()
assert bpy.types.NodeTree.bl_rna_get_subclass_py(TREE_ID, None) is None
assert all(not hasattr(bpy.types.Scene, name) for name in SCENE_FIELDS)
print("YUGU_STUDIO_KEYBOARD_LIFECYCLE_OK")
