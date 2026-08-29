import bpy

from .blendshape_mask import refresh_layer_mask
from .ifacialmocap import runtime as ifacialmocap_runtime
from .keybinds import runtime as keybinds_runtime
from .keybinds.vrm import iter_expressions as iter_keybind_expressions
from . import motion_mixer, mtoon_optimize
from .properties import SOURCE_TYPES
from .state import active_armature, active_avatar, active_layers, ensure_runtime_id
from .springbone import runtime as springbone_runtime
from .vmc import runtime as vmc_runtime
from .view_window import open_view_window


NAME_PREFIXES = {
    "NONE": "MotionLayer",
    "VMC": "VMC Receiver",
    "IFACIALMOCAP": "iFacial Mocap",
    "BASE_ACTION": "Blender Action",
    "KEYBINDS": "Keybinds",
    "SPRING_BONE": "VRM Physics",
}

LAYER_COLORS = {
    "NONE": "GRAY",
    "VMC": "ORANGE",
    "IFACIALMOCAP": "GREEN",
    "BASE_ACTION": "BLUE",
    "KEYBINDS": "PURPLE",
    "SPRING_BONE": "PINK",
}

_fast_started_armature_items = []


def fast_started_armature_items(_self, _context):
    global _fast_started_armature_items
    armatures = sorted(
        (obj for obj in bpy.data.objects if obj.type == "ARMATURE"),
        key=lambda obj: obj.name.casefold(),
    )
    _fast_started_armature_items = [
        (
            obj.name,
            obj.name,
            f"Use {obj.name} as the character armature",
            "OUTLINER_OB_ARMATURE",
            index,
        )
        for index, obj in enumerate(armatures)
    ]
    if not _fast_started_armature_items:
        _fast_started_armature_items = [
            ("NONE", "No Armatures", "No armature objects are available")
        ]
    return _fast_started_armature_items


class YUGO_OT_fast_started(bpy.types.Operator):
    bl_idname = "yugo.fast_started"
    bl_label = "Fast Started"
    bl_description = "Open the quick character setup interface"

    character: bpy.props.EnumProperty(
        name="Character",
        description="VRM character armature used by this setup",
        items=fast_started_armature_items,
    )
    face_capture: bpy.props.EnumProperty(
        name="Face Captures",
        items=(
            ("NONE", "None", "Do not create a face capture source"),
            ("IFACIALMOCAP", "iFacialMocap", "Use iFacialMocap face capture"),
            ("VMC", "VMC", "Use VMC face capture"),
        ),
        default="NONE",
    )
    body_motion: bpy.props.EnumProperty(
        name="Body Motion",
        items=(
            ("NONE", "None", "Do not create a body motion source"),
            ("VMC", "VMC", "Use VMC body motion"),
            ("ROKOKO", "Rokoko", "Use Rokoko body motion"),
            ("LEAP_MOTION", "Leap Motion", "Use Leap Motion"),
            ("MEDIAPIPE", "MediaPipe", "Use MediaPipe body motion"),
            ("STEAMVR", "SteamVR", "Use SteamVR tracking"),
            ("MOCOPI", "Mocopi", "Use Sony Mocopi body motion"),
        ),
        default="NONE",
    )
    camera: bpy.props.EnumProperty(
        name="Camera",
        items=(
            ("FOLLOW_CHARACTER", "Follow Character", "Create a character-following camera"),
            ("NONE", "None", "Do not configure a camera"),
        ),
        default="FOLLOW_CHARACTER",
    )
    fx: bpy.props.EnumProperty(
        name="FX",
        items=(
            ("AUTO", "Auto", "Automatically configure character effects"),
            ("NONE", "None", "Do not configure effects"),
        ),
        default="AUTO",
    )
    keybinds: bpy.props.EnumProperty(
        name="KeyBinds",
        items=(
            ("AUTO", "Auto", "Automatically configure character keybinds"),
            ("NONE", "None", "Do not configure keybinds"),
        ),
        default="AUTO",
    )

    def invoke(self, context, _event):
        armature = active_armature(context.scene)
        if armature is not None:
            try:
                self.character = armature.name
            except TypeError:
                pass
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, _context):
        layout = self.layout
        sections = (
            ("01 Character", "character"),
            ("02 Face Captures", "face_capture"),
            ("03 Body Motion", "body_motion"),
            ("04 Camera", "camera"),
            ("05 FX", "fx"),
            ("06 KeyBinds", "keybinds"),
        )
        for label, property_name in sections:
            box = layout.box()
            box.label(text=label)
            box.prop(self, property_name, text="")

    def execute(self, _context):
        return {"FINISHED"}


class YUGO_OT_setting_popup(bpy.types.Operator):
    bl_idname = "yugo.setting_popup"
    bl_label = "Setting"
    bl_description = "Open Yugo interface settings"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(context.scene, "yugo_ui_language", text="Language")
        self.layout.prop(context.scene, "yugo_ui_preset", text="Preset")

    def execute(self, _context):
        return {"FINISHED"}


class YUGO_OT_help_placeholder(bpy.types.Operator):
    bl_idname = "yugo.help_placeholder"
    bl_label = "Help"
    bl_description = "Open the Yugo Handbook (coming soon)"

    def execute(self, _context):
        return {"FINISHED"}


class YUGO_OT_view_window_settings(bpy.types.Operator):
    bl_idname = "yugo.view_window_settings"
    bl_label = "No UI Window Setting"
    bl_description = "Choose the viewport display mode for No UI Window"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "yugo_view_shading")

    def execute(self, _context):
        return {"FINISHED"}


class YUGO_OT_view_window(bpy.types.Operator):
    bl_idname = "yugo.view_window"
    bl_label = "No UI Window"
    bl_description = "Open a clean independent camera viewport"

    @classmethod
    def poll(cls, context):
        return context.window is not None and context.area is not None

    def execute(self, context):
        scene = context.scene
        try:
            open_view_window(context, scene.yugo_view_shading)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if scene.camera is None:
            self.report({"WARNING"}, "No UI Window opened without a camera")
        return {"FINISHED"}


class YUGO_OT_optimize(bpy.types.Operator):
    bl_idname = "yugo.optimize"
    bl_label = "Optimize"
    bl_description = "Optimize selected objects for Yugo"
    bl_options = {"REGISTER", "UNDO"}

    use_mtoon: bpy.props.BoolProperty(
        name="MToon",
        description="Install Yugo's custom MToon preview shader",
        default=True,
    )
    use_modifiers: bpy.props.BoolProperty(
        name="Modifier",
        description="Modifier optimization is reserved for a future version",
        default=False,
    )
    use_mesh: bpy.props.BoolProperty(
        name="Mesh",
        description="Mesh optimization is reserved for a future version",
        default=False,
    )
    selected_count: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    mtoon_count: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, _event):
        self.selected_count = mtoon_optimize.selected_object_count(context)
        self.mtoon_count = mtoon_optimize.selected_mtoon_material_count(context)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout
        summary = layout.box()
        summary.label(text=f"Selected Objects: {self.selected_count}", icon="OBJECT_DATA")
        summary.label(text=f"MToon Materials: {self.mtoon_count}", icon="MATERIAL")
        layout.separator()
        layout.prop(self, "use_mtoon")
        layout.prop(self, "use_modifiers")
        layout.prop(self, "use_mesh")

    def execute(self, context):
        if not self.use_mtoon:
            self.report({"INFO"}, "No implemented optimize option was selected")
            return {"FINISHED"}

        optimized, errors = mtoon_optimize.optimize_selected_mtoon_materials(context)
        if optimized == 0:
            self.report({"WARNING"}, errors[0] if errors else "Nothing was optimized")
            return {"CANCELLED"}
        if errors:
            self.report(
                {"WARNING"},
                f"Optimized {optimized} MToon material(s); {len(errors)} failed",
            )
            for error in errors:
                print("Yugo MToon Optimize:", error)
        else:
            self.report({"INFO"}, f"Optimized {optimized} MToon material(s)")
        return {"FINISHED"}


def active_layer(scene):
    avatar = active_avatar(scene)
    if avatar is None:
        return None
    index = avatar.motion_layer_index
    layers = avatar.motion_layers
    if 0 <= index < len(layers):
        return layers[index]
    return None


def next_layer_name(layers, source_type):
    prefix = NAME_PREFIXES[source_type]
    used_names = {layer.name for layer in layers}
    number = len(layers) + 1 if source_type == "NONE" else 1
    while f"{prefix} {number:02d}" in used_names:
        number += 1
    return f"{prefix} {number:02d}"


class YUGO_OT_avatar_add(bpy.types.Operator):
    bl_idname = "yugo.avatar_add"
    bl_label = "Add VRM Avatar"
    bl_description = "Add a VRM avatar with its own Motion Layer stack"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        avatar = scene.yugo_avatars.add()
        ensure_runtime_id(avatar)
        avatar.motion_layer_index = -1
        scene.yugo_avatar_index = len(scene.yugo_avatars) - 1
        return {"FINISHED"}


class YUGO_OT_avatar_remove(bpy.types.Operator):
    bl_idname = "yugo.avatar_remove"
    bl_label = "Remove VRM Avatar"
    bl_description = "Remove the selected avatar and its Motion Layers from Yugo"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_avatar(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        index = scene.yugo_avatar_index
        avatar = scene.yugo_avatars[index]
        for layer in tuple(avatar.motion_layers):
            if ifacialmocap_runtime.is_layer_running(layer):
                ifacialmocap_runtime.stop(layer)
            if vmc_runtime.is_layer_running(layer):
                vmc_runtime.stop(layer)
            if keybinds_runtime.is_layer_running(layer):
                keybinds_runtime.stop(layer)
            springbone_runtime.stop(layer, clear_visualization=True)
            motion_mixer.clear_layer(layer)
        scene.yugo_avatars.remove(index)
        scene.yugo_avatar_index = min(index, len(scene.yugo_avatars) - 1)
        return {"FINISHED"}


class YUGO_OT_layer_add(bpy.types.Operator):
    bl_idname = "yugo.layer_add"
    bl_label = "Add Motion Layer"
    bl_description = "Add a motion source layer"
    bl_options = {"REGISTER", "UNDO"}

    source_type: bpy.props.EnumProperty(
        name="Source Type",
        items=SOURCE_TYPES,
        default="NONE",
        options={"HIDDEN"},
    )

    def execute(self, context):
        avatar = active_avatar(context.scene)
        if avatar is None:
            return {"CANCELLED"}
        layers = avatar.motion_layers
        layer = layers.add()
        ensure_runtime_id(layer)
        layer.source_type = self.source_type
        layer.name = next_layer_name(layers[:-1], self.source_type)
        layer.color_tag = LAYER_COLORS[self.source_type]
        if self.source_type == "IFACIALMOCAP":
            layer.port = "49983"
        avatar.motion_layer_index = len(layers) - 1
        return {"FINISHED"}


class YUGO_OT_source_type_set(bpy.types.Operator):
    bl_idname = "yugo.source_type_set"
    bl_label = "Set Source Type"
    bl_description = "Choose the source used by this Motion Layer"
    bl_options = {"UNDO", "INTERNAL"}

    source_type: bpy.props.EnumProperty(
        name="Source Type",
        items=SOURCE_TYPES,
        default="NONE",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return layer is not None and not (
            ifacialmocap_runtime.is_layer_running(layer)
            or vmc_runtime.is_layer_running(layer)
            or keybinds_runtime.is_layer_running(layer)
            or springbone_runtime.is_layer_running(layer)
        )

    def execute(self, context):
        layer = active_layer(context.scene)
        if layer is None:
            return {"CANCELLED"}
        layer.source_type = self.source_type
        return {"FINISHED"}


class YUGO_OT_layer_remove(bpy.types.Operator):
    bl_idname = "yugo.layer_remove"
    bl_label = "Remove Motion Layer"
    bl_description = "Remove the selected motion layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_layer(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        avatar = active_avatar(scene)
        index = avatar.motion_layer_index
        layer = avatar.motion_layers[index]
        if ifacialmocap_runtime.is_layer_running(layer):
            ifacialmocap_runtime.stop(layer)
        if vmc_runtime.is_layer_running(layer):
            vmc_runtime.stop(layer)
        if keybinds_runtime.is_layer_running(layer):
            keybinds_runtime.stop(layer)
        springbone_runtime.stop(layer, clear_visualization=True)
        motion_mixer.clear_layer(layer)
        avatar.motion_layers.remove(index)
        avatar.motion_layer_index = min(index, len(avatar.motion_layers) - 1)
        return {"FINISHED"}


class YUGO_OT_layer_move(bpy.types.Operator):
    bl_idname = "yugo.layer_move"
    bl_label = "Move Motion Layer"
    bl_description = "Move the selected layer to change its priority"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=(
            ("UP", "Up", "Move toward higher priority"),
            ("DOWN", "Down", "Move toward lower priority"),
        )
    )

    @classmethod
    def poll(cls, context):
        return active_layer(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        avatar = active_avatar(scene)
        index = avatar.motion_layer_index
        layers = avatar.motion_layers
        target = index - 1 if self.direction == "UP" else index + 1
        if target < 0 or target >= len(layers):
            return {"CANCELLED"}
        layers.move(index, target)
        avatar.motion_layer_index = target
        return {"FINISHED"}


class YUGO_OT_connection_toggle(bpy.types.Operator):
    bl_idname = "yugo.connection_toggle"
    bl_label = "Connect"
    bl_description = "Start or stop this motion source receiver"

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return layer is not None and layer.source_type in {
            "VMC", "IFACIALMOCAP", "KEYBINDS", "SPRING_BONE"
        }

    def execute(self, context):
        layer = active_layer(context.scene)
        if layer.source_type == "SPRING_BONE":
            try:
                if springbone_runtime.is_layer_running(layer):
                    springbone_runtime.stop(layer)
                    self.report({"INFO"}, "Spring Bone physics stopped")
                else:
                    springbone_runtime.start(
                        context,
                        context.scene,
                        layer,
                        active_armature(context.scene),
                    )
                    self.report({"INFO"}, "Spring Bone physics started")
            except Exception as error:
                layer.connected = False
                layer.spring_last_error = str(error)
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            return {"FINISHED"}

        if layer.source_type == "KEYBINDS":
            if keybinds_runtime.is_layer_running(layer):
                keybinds_runtime.stop(layer)
                self.report({"INFO"}, "Keybinds listener stopped")
                return {"FINISHED"}
            try:
                owns_listener = keybinds_runtime.start(self, context, layer)
            except Exception as error:
                layer.connected = False
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            self.report({"INFO"}, "Keybinds listener started")
            return {"RUNNING_MODAL"} if owns_listener else {"FINISHED"}

        if layer.source_type == "IFACIALMOCAP":
            try:
                if ifacialmocap_runtime.is_layer_running(layer):
                    ifacialmocap_runtime.stop(layer)
                    layer.connected = False
                    self.report({"INFO"}, "iFacialMocap disconnected")
                else:
                    ifacialmocap_runtime.start(
                        context.scene,
                        layer,
                        active_armature(context.scene),
                    )
                    layer.connected = True
                    self.report({"INFO"}, "iFacialMocap receiver started")
            except Exception as error:
                layer.connected = False
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            return {"FINISHED"}

        try:
            if vmc_runtime.is_layer_running(layer):
                vmc_runtime.stop(layer)
                layer.connected = False
                self.report({"INFO"}, "VMC receiver stopped")
            else:
                vmc_runtime.start(
                    context,
                    context.scene,
                    layer,
                    active_armature(context.scene),
                )
                layer.connected = True
                self.report({"INFO"}, "VMC receiver started")
        except Exception as error:
            layer.connected = False
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}

    def modal(self, _context, event):
        if not keybinds_runtime.owns(self):
            return {"CANCELLED"}
        if event.type == "TIMER":
            keybinds_runtime.tick()
            return {"PASS_THROUGH"}
        if keybinds_runtime.process_event(event):
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, _context):
        if keybinds_runtime.owns(self):
            keybinds_runtime.stop()


def active_keybind(layer):
    index = layer.keybind_active_index
    if 0 <= index < len(layer.keybinds):
        return layer.keybinds[index]
    return None


class YUGO_OT_keybind_add(bpy.types.Operator):
    bl_idname = "yugo.keybind_add"
    bl_label = "Add Keybind"
    bl_description = "Add an empty keybind to this Motion Layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return layer is not None and layer.source_type == "KEYBINDS"

    def execute(self, context):
        layer = active_layer(context.scene)
        item = layer.keybinds.add()
        item.key = "NONE"
        records = iter_keybind_expressions(active_armature(context.scene))
        if records:
            item.expression_id = records[0].identifier
        layer.keybind_active_index = len(layer.keybinds) - 1
        return {"FINISHED"}


class YUGO_OT_keybind_remove(bpy.types.Operator):
    bl_idname = "yugo.keybind_remove"
    bl_label = "Remove Keybind"
    bl_description = "Remove the selected keybind"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return layer is not None and active_keybind(layer) is not None

    def execute(self, context):
        layer = active_layer(context.scene)
        index = layer.keybind_active_index
        layer.keybinds.remove(index)
        layer.keybind_active_index = min(index, len(layer.keybinds) - 1)
        return {"FINISHED"}


class YUGO_OT_keybind_move(bpy.types.Operator):
    bl_idname = "yugo.keybind_move"
    bl_label = "Move Keybind"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=(("UP", "Up", "Move up"), ("DOWN", "Down", "Move down"))
    )

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return layer is not None and active_keybind(layer) is not None

    def execute(self, context):
        layer = active_layer(context.scene)
        index = layer.keybind_active_index
        target = index - 1 if self.direction == "UP" else index + 1
        if not 0 <= target < len(layer.keybinds):
            return {"CANCELLED"}
        layer.keybinds.move(index, target)
        layer.keybind_active_index = target
        return {"FINISHED"}


class YUGO_OT_layer_solo_toggle(bpy.types.Operator):
    bl_idname = "yugo.layer_solo_toggle"
    bl_label = "Solo Motion Layer"
    bl_description = "Use only this layer; click again to restore all enabled layers"
    bl_options = {"UNDO"}

    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        layers = active_layers(scene)
        if self.index < 0 or self.index >= len(layers):
            return {"CANCELLED"}

        enable_solo = not layers[self.index].solo
        for index, layer in enumerate(layers):
            layer.solo = enable_solo and index == self.index
        avatar = active_avatar(scene)
        avatar.motion_layer_index = self.index
        return {"FINISHED"}


class YUGO_OT_ifacial_calibrate(bpy.types.Operator):
    bl_idname = "yugo.ifacial_calibrate"
    bl_label = "Calibration"
    bl_description = "Use the current tracked head angle as the new forward direction"

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return (
            layer is not None
            and layer.source_type == "IFACIALMOCAP"
            and ifacialmocap_runtime.is_layer_running(layer)
        )

    def execute(self, context):
        layer = active_layer(context.scene)
        try:
            ifacialmocap_runtime.calibrate(layer)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "iFacialMocap head rotation calibrated")
        return {"FINISHED"}


class YUGO_OT_springbone_reset(bpy.types.Operator):
    bl_idname = "yugo.springbone_reset"
    bl_label = "Reset Physics"
    bl_description = "Reset this layer's Spring Bone pose and simulation state"

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene)
        return (
            layer is not None
            and layer.source_type == "SPRING_BONE"
            and springbone_runtime.is_layer_running(layer)
        )

    def execute(self, context):
        layer = active_layer(context.scene)
        try:
            springbone_runtime.reset(layer)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Spring Bone physics reset")
        return {"FINISHED"}


class YUGO_OT_blendshape_mask_refresh(bpy.types.Operator):
    bl_idname = "yugo.blendshape_mask_refresh"
    bl_label = "Reload from VRM"
    bl_description = (
        "Reload all configured ARKit and other expression binds from the selected VRM avatar"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            active_layer(context.scene) is not None
            and active_armature(context.scene) is not None
        )

    def execute(self, context):
        layer = active_layer(context.scene)
        count = refresh_layer_mask(layer, active_armature(context.scene))
        if count:
            self.report({"INFO"}, f"Loaded {count} VRM expressions")
        else:
            self.report({"WARNING"}, "No bound VRM expressions were found")
        return {"FINISHED"}


CLASSES = (
    YUGO_OT_fast_started,
    YUGO_OT_setting_popup,
    YUGO_OT_help_placeholder,
    YUGO_OT_view_window_settings,
    YUGO_OT_view_window,
    YUGO_OT_optimize,
    YUGO_OT_avatar_add,
    YUGO_OT_avatar_remove,
    YUGO_OT_layer_add,
    YUGO_OT_source_type_set,
    YUGO_OT_layer_remove,
    YUGO_OT_layer_move,
    YUGO_OT_connection_toggle,
    YUGO_OT_keybind_add,
    YUGO_OT_keybind_remove,
    YUGO_OT_keybind_move,
    YUGO_OT_layer_solo_toggle,
    YUGO_OT_ifacial_calibrate,
    YUGO_OT_springbone_reset,
    YUGO_OT_blendshape_mask_refresh,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
