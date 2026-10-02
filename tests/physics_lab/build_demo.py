"""Build a paused, inactive preview scene in this task's artifacts folder."""

from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
import yugo_physics_lab as addon
from yugo_physics_lab import demo, preview

addon.register()
assert Path(addon.__file__).resolve().is_relative_to(ROOT / "addons")
for obj in list(bpy.context.scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
demo.create_demo(bpy.context.scene)
bpy.context.scene.frame_set(42)
assert not preview.service.active()
assert not bpy.app.timers.is_registered(preview._timer)
instructions = bpy.data.texts.new("Physics Lab — Start Here")
instructions.write("Yugo Physics Lab · Stage 2\n\n"
    "Timeline stays on frame 42. Open View3D > N > Physics Lab.\n"
    "1. Start: both props fall and collide with the ground.\n"
    "2. Apply Impulse: Cyan Prop launches, spins and hits the yellow wall.\n"
    "3. Grab Left: nearest free prop follows Left Anchor with spring forces.\n"
    "   Select the anchor and move it; Release Left retains its acquired speed.\n"
    "4. Attract Home: spring attraction returns the selected prop with collisions.\n"
    "   Maintain Attraction keeps the force active after settling.\n"
    "5. Stop freezes the current pose. Reset Home restores the Start pose.\n\n"
    "This prototype uses spherical prop colliders and static box bounds.\n"
    "Move a static obstacle only after Stop, then Start to capture new geometry.\n"
    "Studio integration follows tasks #2 and #3.\n")
folder = ROOT / "artifacts"
folder.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(folder / "yugo-physics-lab-stage-2.blend"))
print("PHYSICS_LAB_DEMO_BUILT", folder / "yugo-physics-lab-stage-2.blend")
addon.unregister()
