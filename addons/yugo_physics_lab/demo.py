"""Generated, shareable fixture: no private avatar or external asset required."""

import math

import bmesh
import bpy


def _material(name, color):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1)
    return material


def _box(collection, name, center, half_extents, material):
    mesh = bpy.data.meshes.new(name)
    vertices = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    mesh.from_pydata(vertices, [], [(0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4),
                                   (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = center
    obj.scale = half_extents
    obj.data.materials.append(material)
    return obj


def _sphere(collection, name, center, radius, material):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=radius)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = center
    obj["yugo_physics_radius"] = radius
    obj["yugo_physics_mass"] = 1.0
    obj.data.materials.append(material)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    return obj


def create_demo(scene):
    """Add a fresh lab without removing or overwriting existing scene data."""
    lab = bpy.data.collections.new("Yugo Physics Lab Demo")
    scene.collection.children.link(lab)
    bodies = bpy.data.collections.new("Physics Lab Props")
    colliders = bpy.data.collections.new("Physics Lab Obstacles")
    lab.children.link(bodies)
    lab.children.link(colliders)
    cyan = _material("Physics Lab Cyan", (.06, .65, .85))
    coral = _material("Physics Lab Coral", (.95, .25, .16))
    gray = _material("Physics Lab Stage", (.12, .17, .24))
    wall_mat = _material("Physics Lab Wall", (.9, .64, .1))
    _box(lab, "Physics Lab Ground Visual", (0, 0, -.075), (4, 3, .075), gray)
    wall = _box(colliders, "Physics Lab Blocking Wall", (1.25, 0, 1.05), (.12, 1.25, 1.05), wall_mat)
    wall.rotation_euler.z = math.radians(12)
    left = _sphere(bodies, "Physics Lab Cyan Prop", (-1, .55, .85), .28, cyan)
    right = _sphere(bodies, "Physics Lab Coral Prop", (-1, -.55, .85), .28, coral)
    anchors = []
    for label, prop in (("Left", left), ("Right", right)):
        anchor = bpy.data.objects.new(f"Physics Lab {label} Anchor", None)
        lab.objects.link(anchor)
        anchor.location = prop.location
        anchor.empty_display_type = "SPHERE"
        anchor.empty_display_size = .42
        anchor.show_in_front = True
        anchors.append(anchor)
    settings = scene.yugo_physics_lab
    settings.bodies = bodies
    settings.colliders = colliders
    settings.left_anchor, settings.right_anchor = anchors
    settings.target = left
    settings.radius = .28
    settings.use_floor = True
    settings.floor_height = 0
    settings.impulse = (3, 0, 5)
    # Show the experiment immediately without assigning the scene's camera.
    if bpy.context.view_layer:
        bpy.context.view_layer.update()
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        left.select_set(True)
        bpy.context.view_layer.objects.active = left
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.overlay.show_floor = False
                region = area.spaces.active.region_3d
                if region:
                    region.view_location = (0, 0, .8)
                    region.view_distance = 8
    return lab
