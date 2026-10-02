"""Procedural, redistributable test material; never reads a user's preset.

Run only inside a task-isolated Blender process. The generated meshes and PNGs
are original test material. Installed fonts remain external and are never packed.
"""
from pathlib import Path
import math
import os

import bpy
from mathutils import Matrix


ROLE = "floating_ui_role"


def artifacts_dir():
    root = Path(__file__).resolve().parents[2]
    path = Path(os.environ.get("FLOATING_UI_ARTIFACTS", root / "artifacts" / "floating_ui"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_file(path, color, *, portrait=False):
    """Write a tiny original avatar PNG without an imaging dependency."""
    image = bpy.data.images.new("FixtureAvatar", width=64, height=64, alpha=True)
    pixels = []
    for y in range(64):
        for x in range(64):
            u, v = (x - 31.5) / 32, (y - 31.5) / 32
            shade = 0.85 + 0.15 * (v + 1) / 2
            rgb = tuple(c * shade for c in color)
            if portrait:
                head = u * u + ((v - 0.08) / 1.1) ** 2 < 0.29
                shoulder = (u / 0.7) ** 2 + ((v + 0.9) / 0.55) ** 2 < 1
                eyes = abs(v - 0.15) < 0.045 and abs(abs(u) - 0.18) < 0.04
                if head or shoulder:
                    rgb = (0.90, 0.95, 0.99)
                if eyes:
                    rgb = (0.10, 0.16, 0.28)
            pixels.extend((*rgb, 1.0))
    image.pixels = pixels
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return str(path)


def material(name, color, image=None):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeEmission")
    shader.inputs["Color"].default_value = (*color, 1.0)
    shader.inputs["Strength"].default_value = 1.0
    mat.node_tree.links.new(shader.outputs[0], output.inputs["Surface"])
    if image is not None:
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = "Avatar"
        texture.label = "Replace avatar image"
        texture.image = image
        mat.node_tree.links.new(texture.outputs["Color"], shader.inputs["Color"])
    return mat


def rectangle(collection, name, width, height, location, mat, parent=None, radius=0):
    """Rounded panel in the XY plane with UVs suitable for an avatar image."""
    if radius:
        vertices = []
        for cx, cy, start in ((width / 2 - radius, height / 2 - radius, 0),
                              (-width / 2 + radius, height / 2 - radius, 90),
                              (-width / 2 + radius, -height / 2 + radius, 180),
                              (width / 2 - radius, -height / 2 + radius, 270)):
            for i in range(9):
                angle = math.radians(start + i * 90 / 8)
                vertices.append((cx + radius * math.cos(angle),
                                 cy + radius * math.sin(angle), 0))
    else:
        vertices = [(-width / 2, -height / 2, 0), (width / 2, -height / 2, 0),
                    (width / 2, height / 2, 0), (-width / 2, height / 2, 0)]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [list(range(len(vertices)))])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for li in polygon.loop_indices:
            x, y, _ = vertices[mesh.loops[li].vertex_index]
            uv.data[li].uv = ((x + width / 2) / width, (y + height / 2) / height)
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    return obj


def text(collection, name, body, location, size, mat, parent=None, role=None, font=None):
    curve = bpy.data.curves.new(name + "Text", "FONT")
    curve.body = body
    curve.size = size
    curve.space_line = 1.15
    curve.align_x = "LEFT"
    curve.align_y = "TOP_BASELINE"
    if font:
        curve.font = font
    curve.materials.append(mat)
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    if role:
        obj[ROLE] = role
    return obj


def cjk_font():
    """An optional external local font; not needed for the assertions."""
    for path in (Path("C:/Windows/Fonts/msjh.ttc"),
                 Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")):
        if path.is_file():
            try:
                return bpy.data.fonts.load(str(path), check_existing=True)
            except RuntimeError:
                pass
    return None


def make_template(name="Floating UI", *, image=None, nested=True, transformed=False, font=None):
    """Make an unlinked editable collection containing one message card."""
    collection = bpy.data.collections.new(name)
    content = collection
    if nested:
        content = bpy.data.collections.new(name + " Content")
        collection.children.link(content)
    origin = bpy.data.objects.new("Card Layout", None)
    collection.objects.link(origin)
    if transformed:
        origin.matrix_world = (Matrix.Translation((0.3, -0.2, 0.1))
                               @ Matrix.Rotation(0.16, 4, "Z"))
        origin.scale = (0.9, 1.1, 1.0)
    navy = material("Card / navy", (0.038, 0.060, 0.108))
    border = material("Card / border", (0.14, 0.23, 0.35))
    cyan = material("Card / accent", (0.22, 0.81, 0.86))
    white = material("Text / ivory", (0.86, 0.91, 0.98))
    muted = material("Text / muted", (0.36, 0.63, 0.73))
    avatar_mat = material("Icon / replaceable", (0.28, 0.64, 0.78), image=image)
    if image is None:
        # The empty, named texture is the data replacement point in a real preset.
        avatar_mat.node_tree.nodes.new("ShaderNodeTexImage").name = "Avatar"
    rectangle(content, "Backplate Border", 5.44, 1.22, (0, 0, -0.025), border, origin, 0.13)
    panel = rectangle(content, "Backplate", 5.40, 1.18, (0, 0, 0), navy, origin, 0.12)
    panel[ROLE] = "panel"
    rectangle(content, "Accent", 0.055, 0.79, (-2.56, 0, 0.006), cyan, origin, 0.018)
    icon = rectangle(content, "Viewer Icon", 0.68, 0.68, (-2.03, 0.02, 0.014), avatar_mat, origin, 0.07)
    icon[ROLE] = "icon"
    author = text(content, "Viewer Name", "VIEWER", (-1.49, 0.27, 0.025), 0.18,
                  cyan, origin, "author", font)
    message = text(content, "Message", "Your message appears here", (-1.49, -0.07, 0.025),
                   0.22, white, origin, "message", font)
    rectangle(content, "Status Dot", 0.055, 0.055, (2.4, 0.33, 0.012), cyan, origin, 0.023)
    return collection, {"root": origin, "message": message, "author": author,
                        "icon": icon, "panel": panel}


def counts():
    return {name: len(getattr(bpy.data, name)) for name in
            ("objects", "collections", "curves", "meshes", "materials", "images")}
