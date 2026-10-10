"""Create an explicit, paused Node starter without altering existing graphs."""

import bpy

from .node.constants import TREE_ID


FACE_SOURCES = frozenset({"NONE", "VMC", "IFACIALMOCAP"})
BODY_SOURCES = frozenset({"NONE", "VMC"})


def _has_face_targets(snapshot):
    for definition in snapshot.expression_map.definitions.values():
        for target in definition.morph_targets:
            if target.object_name not in snapshot.objects or not target.weight:
                continue
            obj = bpy.data.objects.get(target.object_name)
            keys = getattr(getattr(getattr(obj, "data", None), "shape_keys", None), "key_blocks", None)
            if keys is not None and target.shape_key_name in keys:
                return True
    return False


def starter_clock(tree):
    """Only expose the clock that onboarding created, never unrelated clocks."""
    if tree is None or tree.bl_idname != TREE_ID:
        return None
    identifier = tree.get("yugo_onboarding_clock", "")
    return next((node for node in tree.nodes
                 if node.bl_idname == "YUGUS_NODE_hz_trigger"
                 and identifier and node.runtime_id == identifier), None)


def context_tree(context):
    """A pinned editor owns its displayed graph; 3D controls use Scene selection."""
    space = getattr(context, "space_data", None)
    area = getattr(context, "area", None)
    if getattr(space, "type", "") == "NODE_EDITOR" or getattr(area, "type", "") == "NODE_EDITOR":
        tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
    else:
        tree = getattr(getattr(context, "scene", None), "yugu_node_studio_tree", None)
    return tree if tree is not None and tree.bl_idname == TREE_ID else None


def starter_warnings(tree):
    """Read refreshed cache state for the UI; Panel.draw must not write RNA."""
    if tree is None or not tree.get("yugo_fast_started"):
        return ()
    from .node import vrm_cache
    data = next((node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_vrm_data"), None)
    snapshot = vrm_cache.get_snapshot(data) if data is not None else None
    if snapshot is None:
        return ("Character data is not loaded. Click Trigger on Reload character.",)
    face_capture = tree.get("yugo_onboarding_face_capture", "NONE")
    warnings = []
    if face_capture != "NONE" and not _has_face_targets(snapshot):
        warnings.append("No mapped facial shapes. Add VRM expression binds, then reload character.")
    if face_capture == "IFACIALMOCAP" and "head" not in snapshot.bone_map.by_humanoid:
        warnings.append("No mapped Head bone. iFacialMocap head rotation is unavailable.")
    return tuple(warnings)


def create_starter(context, character_name, face_capture="NONE", body_motion="VMC"):
    """Validate first, then publish one new graph after its setup succeeds."""
    scene = context.scene
    armature = scene.objects.get(character_name)
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("Select a character armature in the current scene")
    if face_capture not in FACE_SOURCES or body_motion not in BODY_SOURCES:
        raise ValueError("Fast Started supports VMC body motion and VMC or iFacialMocap face capture")
    if face_capture == body_motion == "NONE":
        raise ValueError("Choose at least one face or body source")

    from .node import mixer, vrm_cache

    # READY means the cache was read, including an empty native-armature cache.
    # Verify that at least the requested output route has an actual target.
    snapshot = vrm_cache.build_snapshot(armature)
    has_bones = bool(snapshot.bone_map.by_humanoid)
    has_head = "head" in snapshot.bone_map.by_humanoid
    has_face = _has_face_targets(snapshot)
    if body_motion == "VMC" and not has_bones:
        raise ValueError("No humanoid bones are mapped. Configure the character's VRM humanoid mapping first")
    if body_motion == "NONE" and not has_face and not (face_capture == "IFACIALMOCAP" and has_head):
        raise ValueError("No usable face targets. Map VRM facial shape keys or use iFacialMocap with a mapped Head bone")
    warnings = []
    if face_capture != "NONE" and not has_face:
        warnings.append("No mapped facial shapes. Add VRM expression binds, then reload character.")
    if face_capture == "IFACIALMOCAP" and not has_head:
        warnings.append("No mapped Head bone. iFacialMocap head rotation is unavailable.")

    tree = bpy.data.node_groups.new(f"{armature.name} - Fast Started", TREE_ID)
    data = None
    try:
        def node(kind, location, label=""):
            item = tree.nodes.new(kind)
            item.location = location
            if label:
                item.label = label
            return item

        def link(source, output, target, input_name):
            tree.links.new(source.outputs[output], target.inputs[input_name])

        data = node("YUGUS_NODE_vrm_data", (-850, 700))
        # The existing property callback loads the native VRM snapshot once.
        data.armature = armature
        if data.cache_status != "READY":
            raise ValueError(f"Character data could not be loaded: {data.cache_message}")

        sources = []
        vmc = None
        if "VMC" in {face_capture, body_motion}:
            vmc = node("YUGUS_NODE_vmc_source", (-850, 370))
            vmc.label = "VMC body + face" if face_capture == body_motion == "VMC" else "VMC Receiver"
            sources.append(vmc)
        ifacial = None
        if face_capture == "IFACIALMOCAP":
            ifacial = node("YUGUS_NODE_ifacial_source", (-850, 0))
            sources.append(ifacial)

        motion = vmc if body_motion == "VMC" else None
        if ifacial is not None and has_head:
            if motion is None:
                motion = ifacial
            else:
                combined = node(mixer.NODE, (-240, 330), "VMC body + iFacialMocap head")
                combined.data_type = "HUMANOID_MOTION"
                link(motion, "Humanoid Motion", combined, mixer.input_names(combined)[0])
                link(ifacial, "Humanoid Motion", combined, mixer.input_names(combined)[1])
                motion = combined

        clock = node("YUGUS_NODE_hz_trigger", (-240, -340), "Output clock - Start / Pause Outputs in N > Yugo")
        # Hz Trigger has no Enabled switch; Blender's node mute is its pause.
        clock.mute = True
        if motion is not None:
            converted = node("YUGUS_NODE_motion_to_rotation", (90, 370))
            output = node("YUGUS_NODE_apply_rotation", (470, 370))
            motion_output = mixer.output_name(motion) if motion.bl_idname == mixer.NODE else "Humanoid Motion"
            link(motion, motion_output, converted, "Motion")
            link(data, "Bone Map", converted, "Bone Map")
            link(converted, "Bone Transform", output, "Bone Transform")
            link(data, "Armatures", output, "Armatures")
            link(clock, "Trigger", output, "Enter")

        face = vmc if face_capture == "VMC" else ifacial
        if face is not None:
            converted = node("YUGUS_NODE_expression_to_shapes", (90, -30))
            output = node("YUGUS_NODE_apply_shapes", (470, -30))
            link(face, "Expressions", converted, "Expressions")
            link(data, "Expression Map", converted, "Expression Map")
            link(converted, "Shape Keys Values", output, "Shape Keys Values")
            link(data, "Objects", output, "Objects")
            link(clock, "Trigger", output, "Enter")

        connect = node("YUGUS_NODE_button_trigger", (-850, -580), "Connect receivers")
        disconnect = node("YUGUS_NODE_button_trigger", (-560, -580), "Disconnect receivers")
        for source in sources:
            link(connect, "Trigger", source, "Connect")
            link(disconnect, "Trigger", source, "Disconnect")
        reload_character = node("YUGUS_NODE_button_trigger", (-240, -580), "Reload character")
        link(reload_character, "Trigger", data, "Reload")

        instructions = (
            "1. Set receiver address / port; click Trigger on Connect receivers.",
            "2. Click Start Outputs in N > Yugo. Pause Outputs stops character output.",
            "3. After editing VRM mappings, click Trigger on Reload character.",
        )
        for index, text in enumerate(instructions):
            frame = node("NodeFrame", (-850, 1100 - index * 90), text)
            frame.shrink = False
            frame.width = 1200
            frame.height = 60
            frame.label_size = 20

        tree.use_fake_user = True
        tree["yugo_fast_started"] = True
        tree["yugo_onboarding_clock"] = clock.runtime_id
        tree["yugo_onboarding_face_capture"] = face_capture
        if warnings:
            tree["yugo_onboarding_warning"] = "\n".join(warnings)
        tree.last_status = "Starter ready: receivers disconnected; Hz Trigger paused"
        for item in tree.nodes:
            item.select = False
        clock.select = True
        tree.nodes.active = clock
        scene.yugu_node_studio_tree = tree
        return tree
    except Exception:
        # Only this unpublished graph and its own snapshot are retired.
        if data is not None:
            vrm_cache.invalidate_node(data)
        bpy.data.node_groups.remove(tree)
        raise
