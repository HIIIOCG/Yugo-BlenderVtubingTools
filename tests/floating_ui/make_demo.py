"""Build an original editable Floating UI example and a visual QA render.

Use a task-isolated Blender profile:
  blender --background --factory-startup --python tests/floating_ui/make_demo.py

Outputs remain local under artifacts/floating_ui. The demo is deliberately not
shipped as the user's preset.blend; its generated Collection shows the contract
and can be edited in Blender. Any installed font is referenced, never packed.
"""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture import artifacts_dir, cjk_font, image_file, make_template, material, rectangle, text
from yugo_floating_ui import preview


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    output = artifacts_dir()
    scene = bpy.context.scene
    avatars = []
    for i, color in enumerate(((0.12, 0.62, 0.70), (0.70, 0.28, 0.51),
                               (0.32, 0.48, 0.81), (0.67, 0.46, 0.18))):
        avatars.append(image_file(output / f"demo_avatar_{i}.png", color, portrait=True))
    image = bpy.data.images.load(avatars[0])
    # Font remains external; generated artifacts are not submitted or uploaded.
    font = cjk_font()
    template, _ = make_template(image=image, nested=True, font=font)
    template.use_fake_user = True
    bpy.data.libraries.write(str(output / "floating_ui_example_preset.blend"), {template}, fake_user=True)
    # Keep the editable original in the scene, hidden until the user edits it.
    scene.collection.children.link(template)
    template.hide_viewport = True
    template.hide_render = True
    anchor = bpy.data.objects.new("Chat placement / edit me", None)
    scene.collection.objects.link(anchor)
    anchor.location = (3.1, -2.46, 0)
    preview.register()
    graph = bpy.data.node_groups.new("Floating UI / try Send Once", preview.TREE_ID)
    graph.use_fake_user = True
    source = graph.nodes.new(preview.SOURCE_ID)
    display = graph.nodes.new(preview.DISPLAY_ID)
    source.location = (-380, 0)
    display.location = (0, 0)
    display.template = template
    display.anchor = anchor
    display.max_messages = 4
    display.spacing = 1.39
    display.move_duration = 0.28
    graph.links.new(source.outputs[0], display.inputs[0])
    service = preview.get_service(display, scene)
    messages = (
        ("Mika", "今天的場景好漂亮！"),
        ("Ari", "Hello from the live chat!"),
        ("小雨", "這個氣泡可以直接在 Blender 編輯"),
        ("Yugo", "每則留言都有自己的頭像與文字"),
    )
    for i, (author, body) in enumerate(messages):
        service.push({"id": f"demo-{i}", "text": body, "author": author,
                      "avatar_path": avatars[i]}, now=float(i))
        service.update(now=float(i) + 0.5)
    service.update(now=10)
    source.author, source.text, source.avatar_path = messages[-1][0], messages[-1][1], avatars[-1]

    board = bpy.data.collections.new("Demo presentation")
    scene.collection.children.link(board)
    background = material("Presentation / midnight", (0.013, 0.024, 0.045))
    grid = material("Presentation / grid", (0.022, 0.040, 0.066))
    accent = material("Presentation / teal", (0.22, 0.81, 0.86))
    white = material("Presentation / white", (0.86, 0.92, 0.99))
    muted = material("Presentation / gray", (0.29, 0.44, 0.59))
    rectangle(board, "Backdrop", 22, 15, (0, 0, -0.5), background)
    for x in range(-10, 11):
        rectangle(board, f"Grid column {x}", 0.008, 15, (x, 0, -0.48), grid)
    for y in range(-7, 8):
        rectangle(board, f"Grid row {y}", 22, 0.008, (0, y, -0.48), grid)
    rectangle(board, "Intro accent", 0.06, 0.78, (-6.56, 2.04, 0), accent, radius=0.025)
    text(board, "Product", "YUGO STUDIO", (-6.32, 3.06, 0), 0.17, accent)
    text(board, "Title", "Floating\nUI", (-6.32, 2.44, 0), 0.83, white)
    text(board, "Subtitle", "Your Blender collection.\nYour live chat style.",
         (-6.32, 0.28, 0), 0.23, muted)
    rows = (("01", "EDIT THE TEMPLATE"), ("02", "SEND A MESSAGE"), ("03", "WATCH IT STACK"))
    for i, (number, label) in enumerate(rows):
        y = -1.14 - i * 0.65
        text(board, f"Step {i}", number, (-6.32, y, 0), 0.16, accent)
        text(board, f"Step label {i}", label, (-5.71, y, 0), 0.16, white)
    text(board, "Template note", "Native Text  /  Avatar material  /  Mesh panel",
         (-6.32, -3.64, 0), 0.12, muted)
    text(board, "Live heading", "LIVE CHAT", (0.43, 3.05, 0), 0.17, accent)
    text(board, "Live note", "4 MESSAGES", (4.65, 3.05, 0), 0.12, muted)
    text(board, "Latest note", "NEWEST MESSAGE", (0.43, -3.40, 0), 0.12, muted)

    camera_data = bpy.data.cameras.new("Demo Camera")
    camera = bpy.data.objects.new("Demo Camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (-0.3, -0.1, 20)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 14.5
    scene.camera = camera
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(output / "floating_ui_demo.png")
    scene.world = bpy.data.worlds.new("Demo World")
    scene.world.color = (0, 0, 0)
    scene.view_settings.view_transform = "Standard"
    # Pack only the original generated PNGs; no fonts or received material.
    for owned_image in bpy.data.images:
        if owned_image.filepath and Path(bpy.path.abspath(owned_image.filepath)).parent == output:
            owned_image.pack()
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "floating_ui_demo.blend"))
    bpy.ops.render.render(write_still=True)
    print("FLOATING_UI_DEMO", output / "floating_ui_demo.blend")
    print("FLOATING_UI_RENDER", output / "floating_ui_demo.png")


if __name__ == "__main__":
    main()
