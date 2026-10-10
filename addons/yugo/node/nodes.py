"""Custom Node Tree, sockets, and node declarations."""

from __future__ import annotations

import uuid
import re

import bpy

from .constants import (
    ARKIT_BLENDSHAPES,
    HUMANOID_BONES,
    MASK_REGIONS,
    SOCKET_BONE_MAP,
    SOCKET_EXPRESSION_MAP,
    SOCKET_EXPRESSIONS,
    SOCKET_META,
    SOCKET_MOTION,
    SOCKET_MOTION_MASK,
    SOCKET_NATIVE_EXPRESSIONS,
    SOCKET_NATIVE_POSE,
    SOCKET_SPRING_DATA,
    SOCKET_TRIGGER,
    TREE_ID,
    normalize_name,
)
from .map_values import expression_map_from_node, mix_expression_maps


_HUMANOID_ITEMS = tuple(
    (name, name, f"VRM Humanoid {name}") for name in HUMANOID_BONES
)
_BONE_MAP_CATEGORIES = (
    ("Body", ("hips", "spine", "chest", "upperChest", "neck", "head")),
    ("Head", ("leftEye", "rightEye", "jaw")),
    (
        "Legs",
        (
            "leftUpperLeg",
            "rightUpperLeg",
            "leftLowerLeg",
            "rightLowerLeg",
            "leftFoot",
            "rightFoot",
            "leftToes",
            "rightToes",
        ),
    ),
    (
        "Arms",
        (
            "leftShoulder",
            "rightShoulder",
            "leftUpperArm",
            "rightUpperArm",
            "leftLowerArm",
            "rightLowerArm",
            "leftHand",
            "rightHand",
        ),
    ),
    (
        "Left Fingers",
        (
            "leftThumbMetacarpal",
            "leftThumbProximal",
            "leftThumbDistal",
            "leftIndexProximal",
            "leftIndexIntermediate",
            "leftIndexDistal",
            "leftMiddleProximal",
            "leftMiddleIntermediate",
            "leftMiddleDistal",
            "leftRingProximal",
            "leftRingIntermediate",
            "leftRingDistal",
            "leftLittleProximal",
            "leftLittleIntermediate",
            "leftLittleDistal",
        ),
    ),
    (
        "Right Fingers",
        (
            "rightThumbMetacarpal",
            "rightThumbProximal",
            "rightThumbDistal",
            "rightIndexProximal",
            "rightIndexIntermediate",
            "rightIndexDistal",
            "rightMiddleProximal",
            "rightMiddleIntermediate",
            "rightMiddleDistal",
            "rightRingProximal",
            "rightRingIntermediate",
            "rightRingDistal",
            "rightLittleProximal",
            "rightLittleIntermediate",
            "rightLittleDistal",
        ),
    ),
)
_BONE_MAP_ORDER = tuple(
    name for _category, names in _BONE_MAP_CATEGORIES for name in names
)
_BONE_MAP_CATEGORY = {
    name: category for category, names in _BONE_MAP_CATEGORIES for name in names
}
_EXPRESSION_OVERRIDE_ITEMS = (
    ("NONE", "None", "Do not override this expression category"),
    ("BLOCK", "Block", "Block this expression category"),
    ("BLEND", "Blend", "Blend out this expression category by the active value"),
)
_MATERIAL_BIND_ITEMS = (
    ("color", "Color", "Base or lit color"),
    ("emissionColor", "Emission", "Emission color"),
    ("shadeColor", "Shade", "Shade color"),
    ("matcapColor", "Matcap", "Matcap color"),
    ("rimColor", "Rim", "Rim color"),
    ("outlineColor", "Outline", "Outline color"),
)


def ensure_node_id(node) -> str:
    if not getattr(node, "runtime_id", ""):
        node.runtime_id = uuid.uuid4().hex
    return str(node.runtime_id)


def _armature_poll(_self, candidate):
    return candidate is not None and candidate.type == "ARMATURE"


def _mesh_poll(_self, candidate):
    return candidate is not None and candidate.type == "MESH"


def _output_active_updated(node, context):
    from . import runtime

    runtime.output_active_changed(node, context)


def _vrm_armature_updated(node, _context):
    from . import vrm_cache

    ensure_node_id(node)
    if node.armature is None:
        vrm_cache.invalidate_node(node, "Select an Armature")
        return
    try:
        vrm_cache.reload_node(node)
    except Exception as error:
        vrm_cache.invalidate_node(node, str(error))


def _node_input(node, socket_type, name):
    return node.inputs.new(socket_type, name)


def _node_output(node, socket_type, name):
    return node.outputs.new(socket_type, name)


# Keep the original RNA properties authoritative for unlinked inputs, including
# saved keyframes and native drivers. Float socket drawing uses this same map.
NUMERIC_PARAMETERS = {
    "YUGUS_NODE_motion_smooth": ("Smooth", "smooth"),
    "YUGUS_NODE_motion_delay": ("Delay Seconds", "delay_seconds"),
    "YUGUS_NODE_motion_weight": ("Weight", "weight"),
    "YUGUS_NODE_motion_mix": ("Factor", "factor"),
    "YUGUS_NODE_expression_smooth": ("Smooth", "smooth"),
    "YUGUS_NODE_expression_weight": ("Weight", "weight"),
    "YUGUS_NODE_expression_mix": ("Factor", "factor"),
    "YUGUS_NODE_bone_rotation_mix": ("Factor", "factor"),
}


def ensure_numeric_parameter(node):
    """Append a parameter input without replacing existing sockets or links."""
    parameter = NUMERIC_PARAMETERS.get(node.bl_idname)
    if parameter is None:
        return None
    name, property_name = parameter
    socket = node.inputs.get(name)
    if socket is None:
        socket = _node_input(node, "YUGUS_NS_float", name)
        socket.default_value = float(getattr(node, property_name))
    return socket


# Connection sockets retain the original node properties as their manual values.
SOURCE_CONNECTION_PARAMETERS = {
    "YUGUS_NODE_vmc_source": (
        ("Address", "address", "YUGUS_NS_text"),
        ("Port", "port", "YUGUS_NS_float"),
    ),
    "YUGUS_NODE_ifacial_source": (
        ("iPhone Address", "address", "YUGUS_NS_text"),
        ("Device Port", "device_port", "YUGUS_NS_float"),
    ),
}


def source_connection_property(node, name):
    return next((prop for label, prop, _kind in
                 SOURCE_CONNECTION_PARAMETERS.get(node.bl_idname, ()) if label == name), None)


def ensure_source_connection_inputs(node):
    """Add visible connection parameters while retaining their manual properties."""
    if node.bl_idname == "YUGUS_NODE_ifacial_source":
        for name in ("Listen Address", "Listen Port"):
            obsolete = node.inputs.get(name)
            if obsolete is not None:
                node.inputs.remove(obsolete)
    for index, (name, property_name, kind) in enumerate(
            SOURCE_CONNECTION_PARAMETERS.get(node.bl_idname, ())):
        socket = node.inputs.get(name)
        if socket is None:
            socket = _node_input(node, kind, name)
            socket.default_value = getattr(node, property_name)
        current = list(node.inputs).index(socket)
        if current != index:
            node.inputs.move(current, index)


class YUGUS_PG_bone_map_entry(bpy.types.PropertyGroup):
    humanoid: bpy.props.EnumProperty(name="Humanoid", items=_HUMANOID_ITEMS)
    category: bpy.props.StringProperty(default="", options={"HIDDEN"})
    bone_name: bpy.props.StringProperty(name="Blender Bone")


class YUGUS_PG_morph_target_bind(bpy.types.PropertyGroup):
    mesh_object: bpy.props.PointerProperty(
        name="Mesh",
        type=bpy.types.Object,
        poll=_mesh_poll,
    )
    shape_key_name: bpy.props.StringProperty(name="Shape Key")
    weight: bpy.props.FloatProperty(
        name="Weight",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class YUGUS_PG_material_color_bind(bpy.types.PropertyGroup):
    material: bpy.props.PointerProperty(name="Material", type=bpy.types.Material)
    bind_type: bpy.props.EnumProperty(
        name="Color Type",
        items=_MATERIAL_BIND_ITEMS,
        default="color",
    )
    target_value: bpy.props.FloatVectorProperty(
        name="Target Color",
        size=4,
        subtype="COLOR",
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )


class YUGUS_PG_expression_map_entry(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name")
    is_binary: bpy.props.BoolProperty(name="Is Binary", default=False)
    override_blink: bpy.props.EnumProperty(
        name="Override Blink",
        items=_EXPRESSION_OVERRIDE_ITEMS,
        default="NONE",
    )
    override_mouth: bpy.props.EnumProperty(
        name="Override Mouth",
        items=_EXPRESSION_OVERRIDE_ITEMS,
        default="NONE",
    )
    morph_targets: bpy.props.CollectionProperty(type=YUGUS_PG_morph_target_bind)
    morph_target_index: bpy.props.IntProperty(default=0)
    material_targets: bpy.props.CollectionProperty(type=YUGUS_PG_material_color_bind)
    material_target_index: bpy.props.IntProperty(default=0)


class YUGUS_PG_ifacial_blendshape(bpy.types.PropertyGroup):
    expression_name: bpy.props.StringProperty(name="Expression", default="")
    normalized_name: bpy.props.StringProperty(default="", options={"HIDDEN"})
    raw_value: bpy.props.FloatProperty(
        name="Data Raw",
        description="Raw iFacialMocap percentage received from the phone",
        default=0.0,
        options={"SKIP_SAVE"},
        precision=3,
    )
    weight: bpy.props.FloatProperty(
        name="Weight",
        description="Multiplier applied to this blendshape",
        default=1.0,
        min=-5.0,
        max=5.0,
        precision=3,
    )
    smooth: bpy.props.FloatProperty(
        name="Smooth",
        description="Spring-Damper smoothing for this blendshape",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    output_value: bpy.props.FloatProperty(
        name="Output",
        description="Normalized value emitted by the Expressions output",
        default=0.0,
        options={"SKIP_SAVE"},
        precision=3,
    )


class YUGUS_UL_bone_map_entries(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        data,
        item,
        _icon,
        _active_data,
        _active_propname,
        _index=0,
        _flt_flag=0,
    ):
        row = layout.row(align=True)
        label = f"{item.category} · {item.humanoid}" if item.category else item.humanoid
        split = row.split(factor=0.48, align=True)
        split.label(text=label)
        armature = getattr(data, "armature", None)
        if armature is not None:
            split.prop_search(item, "bone_name", armature.data, "bones", text="")
        else:
            split.prop(item, "bone_name", text="")


class YUGUS_UL_expression_map_entries(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_propname,
        _index=0,
        _flt_flag=0,
    ):
        layout.prop(
            item,
            "name",
            text="",
            emboss=False,
            icon="CHECKBOX_HLT" if item.is_binary else "SHAPEKEY_DATA",
        )


class YUGUS_UL_morph_target_binds(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_propname,
        _index=0,
        _flt_flag=0,
    ):
        object_name = item.mesh_object.name if item.mesh_object is not None else "Mesh"
        shape_name = item.shape_key_name or "Shape Key"
        layout.label(text=f"{object_name} · {shape_name}", icon="SHAPEKEY_DATA")


class YUGUS_UL_material_color_binds(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_propname,
        _index=0,
        _flt_flag=0,
    ):
        material_name = item.material.name if item.material is not None else "Material"
        layout.label(text=f"{material_name} · {item.bind_type}", icon="MATERIAL")


class YUGUS_UL_ifacial_blendshapes(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_propname,
        _index=0,
        _flt_flag=0,
    ):
        row = layout.row(align=True)
        split = row.split(factor=0.34, align=True)
        split.label(text=item.expression_name)
        values = split.row(align=True)
        raw = values.row(align=True)
        raw.enabled = False
        raw.prop(item, "raw_value", text="")
        values.prop(item, "weight", text="")
        values.prop(item, "smooth", text="", slider=True)
        output = values.row(align=True)
        output.enabled = False
        output.prop(item, "output_value", text="")


class YUGUS_NT_humanoid_motion(bpy.types.NodeTree):
    bl_idname = TREE_ID
    bl_label = "Yugo Node"
    bl_icon = "ARMATURE_DATA"

    is_workflow_definition: bpy.props.BoolProperty(
        name="Action Flow Definition",
        description="Reusable flow; runs only when an Action Flow node calls it",
        default=False,
    )
    workflow_card_index: bpy.props.IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    workflow_return_tree: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})

    runtime_enabled: bpy.props.BoolProperty(
        name="Legacy Run",
        description="Compatibility value from Yugu Node Studio 0.4 and earlier",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    target_fps: bpy.props.IntProperty(
        name="Legacy Rate",
        description="Compatibility value from Yugu Node Studio 0.4 and earlier",
        default=60,
        min=1,
        max=120,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    last_status: bpy.props.StringProperty(default="Stopped", options={"SKIP_SAVE"})
    last_error: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})


class _YUGUS_SocketBase:
    def draw(self, _context, layout, _node, text):
        layout.label(text=text or self.name)


class YUGUS_NS_humanoid_motion(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_MOTION
    bl_label = "VRM Humanoid Motion"

    def draw_color(self, _context, _node):
        return (0.20, 0.48, 0.95, 1.0)


class YUGUS_NS_humanoid_mask(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_MOTION_MASK
    bl_label = "Humanoid Mask"

    def draw_color(self, _context, _node):
        return (0.36, 0.72, 0.30, 1.0)


class YUGUS_NS_trigger(bpy.types.NodeSocket):
    bl_idname = SOCKET_TRIGGER
    bl_label = "Trigger"

    default_value: bpy.props.FloatProperty(
        name="Value", default=0.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def draw(self, _context, layout, node, text):
        if (
            not self.is_output
            and node.bl_idname in {"YUGUS_NODE_vmc_source", "YUGUS_NODE_ifacial_source"}
            and self.name in {"Connect", "Disconnect"}
        ):
            operator = layout.operator(
                "yugu_node_studio.vmc_connection" if node.bl_idname == "YUGUS_NODE_vmc_source"
                else "yugu_node_studio.source_connection",
                text=text or self.name,
                icon="PLAY" if self.name == "Connect" else "PAUSE",
            )
            operator.tree_name = node.id_data.name
            operator.node_name = node.name
            operator.action = self.name.upper()
            return
        actions = {
            "YUGUS_NODE_vrm_data": ("Reload", "yugu_node_studio.reload_vrm", "FILE_REFRESH"),
            "YUGUS_NODE_spring_simulation": ("Reload", "yugu_node_studio.flow_spring_reload", "FILE_REFRESH"),
            "YUGUS_NODE_ifacial_source": ("Calibrate", "yugu_node_studio.ifacial_calibrate", "ORIENTATION_LOCAL"),
        }
        action = actions.get(node.bl_idname)
        if not self.is_output and action is not None and self.name == action[0]:
            column = layout.column(align=True)
            column.label(text=text or self.name)
            button = column
            if self.name == "Calibrate":
                button = column.row(align=True)
                button.enabled = node.connected
            operator = button.operator(action[1], text=action[0], icon=action[2])
            operator.tree_name = node.id_data.name
            operator.node_name = node.name
            return
        if self.is_output or self.is_linked:
            layout.label(text=text or self.name)
        else:
            layout.prop(self, "default_value", text=text or self.name, slider=True)

    def draw_color(self, _context, _node):
        return (0.85, 0.12, 0.12, 1.0)


class YUGUS_NS_expressions(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_EXPRESSIONS
    bl_label = "Expression Channels"

    def draw_color(self, _context, _node):
        return (0.95, 0.65, 0.12, 1.0)


class YUGUS_NS_native_pose(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_NATIVE_POSE
    bl_label = "Blender Native Pose"

    def draw_color(self, _context, _node):
        return (0.10, 0.72, 0.88, 1.0)


class YUGUS_NS_native_expressions(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_NATIVE_EXPRESSIONS
    bl_label = "Blender Native Expressions"

    def draw_color(self, _context, _node):
        return (0.96, 0.42, 0.58, 1.0)


class YUGUS_NS_bone_map(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_BONE_MAP
    bl_label = "Humanoid Bone Map"

    def draw_color(self, _context, _node):
        return (0.18, 0.78, 0.40, 1.0)


class YUGUS_NS_expression_map(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_EXPRESSION_MAP
    bl_label = "Expression Map"

    def draw_color(self, _context, _node):
        return (0.88, 0.32, 0.72, 1.0)


class YUGUS_NS_spring_data(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_SPRING_DATA
    bl_label = "Spring Data"

    def draw_color(self, _context, _node):
        return (0.92, 0.30, 0.36, 1.0)


class YUGUS_NS_simulation_data(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = "YUGUS_NS_simulation_data"
    bl_label = "Simulation Data"

    def draw_color(self, _context, _node):
        return (0.64, 0.66, 0.76, 1.0)


class YUGUS_NS_meta(_YUGUS_SocketBase, bpy.types.NodeSocket):
    bl_idname = SOCKET_META
    bl_label = "VRM Meta"

    def draw_color(self, _context, _node):
        return (0.45, 0.45, 0.48, 1.0)


class YUGUS_NodeBase:
    runtime_id: bpy.props.StringProperty(default="", options={"HIDDEN"})
    monitor_open: bpy.props.BoolProperty(
        name="Monitor",
        description="Show the latest data evaluated by this source",
        default=False,
    )
    monitor_text: bpy.props.StringProperty(
        default="Waiting for data", options={"SKIP_SAVE", "HIDDEN"}
    )

    @classmethod
    def poll(cls, node_tree):
        from .availability import is_available
        return getattr(node_tree, "bl_idname", "") == TREE_ID and is_available(cls.bl_idname)

    def _init_base(self, width=180.0):
        ensure_node_id(self)
        self.width = width

    def copy(self, source):
        self.runtime_id = ""
        ensure_node_id(self)
        if self.bl_idname in {"YUGUS_NODE_spring_simulation", "YUGUS_NODE_physics_solver"}:
            from . import spring_trace

            spring_trace.forget(self.runtime_id)
            self.trace_recording = False
            self.trace_summary = ""

    def _draw_monitor(self, layout):
        layout.prop(
            self,
            "monitor_open",
            toggle=True,
            icon="HIDE_OFF" if self.monitor_open else "HIDE_ON",
        )
        if not self.monitor_open:
            return
        box = layout.box()
        lines = tuple(str(self.monitor_text or "Waiting for data").splitlines())
        for line in lines[:12]:
            box.label(text=line or " ")
        if len(lines) > 12:
            box.label(text=f"... {len(lines) - 12} more", icon="THREE_DOTS")

    def free(self):
        from . import network, runtime, vrm_cache

        uid = str(getattr(self, "runtime_id", "") or "")
        if uid:
            if self.bl_idname in {"YUGUS_NODE_spring_simulation", "YUGUS_NODE_physics_solver"}:
                from . import spring_trace

                spring_trace.forget(uid)
            network.stop(uid)
            runtime.clear_node_state(uid)
        if getattr(self, "bl_idname", "") == "YUGUS_NODE_vrm_data":
            vrm_cache.invalidate_node(self, "Node removed")


def _draw_collection_buttons(layout, node, collection):
    column = layout.column(align=True)
    add = column.operator("yugu_node_studio.map_item_add", text="", icon="ADD")
    add.tree_name = node.id_data.name
    add.node_name = node.name
    add.collection = collection
    remove = column.operator("yugu_node_studio.map_item_remove", text="", icon="REMOVE")
    remove.tree_name = node.id_data.name
    remove.node_name = node.name
    remove.collection = collection


class YUGUS_NODE_vrm_data(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_vrm_data"
    bl_label = "VRM Data"
    bl_icon = "OUTLINER_OB_ARMATURE"

    armature: bpy.props.PointerProperty(
        name="Character",
        type=bpy.types.Object,
        poll=_armature_poll,
        update=_vrm_armature_updated,
    )
    cache_revision: bpy.props.IntProperty(default=0, options={"HIDDEN"})
    cache_status: bpy.props.EnumProperty(
        items=(
            ("STALE", "Reload Required", "VRM data has not been loaded"),
            ("READY", "Ready", "Cached data is ready"),
            ("ERROR", "Error", "VRM data could not be loaded"),
        ),
        default="STALE",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    cache_message: bpy.props.StringProperty(
        default="Select an Armature", options={"SKIP_SAVE"}
    )

    def init(self, _context):
        self._init_base(220.0)
        _node_input(self, "YUGUS_NS_armatures", "Armature")
        _node_output(self, "YUGUS_NS_armatures", "Armatures")
        _node_output(self, "YUGUS_NS_objects", "Objects")
        _node_output(self, SOCKET_BONE_MAP, "Bone Map")
        _node_output(self, SOCKET_EXPRESSION_MAP, "Expression Map")
        _node_output(self, SOCKET_SPRING_DATA, "Spring Data")
        _node_output(self, SOCKET_META, "Meta")
        _node_input(self, SOCKET_TRIGGER, "Reload")

    def draw_buttons(self, _context, layout):
        layout.label(
            text="Ready" if self.cache_status == "READY" else "Not loaded",
            icon="CHECKMARK" if self.cache_status == "READY" else "ERROR",
        )
        layout.label(text=self.cache_message)


def ensure_bone_map_entries(node):
    """Create one fixed, category-sorted row for every Humanoid bone."""

    mappings = getattr(node, "bone_mappings", None)
    if mappings is None:
        return False
    previous = {}
    current_order = []
    for item in mappings:
        humanoid = str(getattr(item, "humanoid", "") or "")
        if humanoid not in _BONE_MAP_CATEGORY:
            continue
        current_order.append(humanoid)
        previous[humanoid] = str(getattr(item, "bone_name", "") or "")
    if tuple(current_order) == _BONE_MAP_ORDER and len(mappings) == len(
        _BONE_MAP_ORDER
    ):
        for item in mappings:
            item.category = _BONE_MAP_CATEGORY.get(item.humanoid, "")
        return False

    mappings.clear()
    for humanoid in _BONE_MAP_ORDER:
        item = mappings.add()
        item.humanoid = humanoid
        item.category = _BONE_MAP_CATEGORY[humanoid]
        item.bone_name = previous.get(humanoid, "")
    node.bone_mapping_index = min(
        max(int(getattr(node, "bone_mapping_index", 0)), 0),
        len(mappings) - 1,
    )
    return True


class YUGUS_NODE_bone_map_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_bone_map_value"
    bl_label = "Bone Map Value"
    bl_icon = "BONE_DATA"

    armature: bpy.props.PointerProperty(
        name="Armature",
        type=bpy.types.Object,
        poll=_armature_poll,
    )
    bone_mappings: bpy.props.CollectionProperty(type=YUGUS_PG_bone_map_entry)
    bone_mapping_index: bpy.props.IntProperty(default=0)

    def init(self, _context):
        self._init_base(360.0)
        _node_output(self, SOCKET_BONE_MAP, "Bone Map")
        ensure_bone_map_entries(self)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "armature", text="")
        layout.template_list(
            "YUGUS_UL_bone_map_entries",
            "",
            self,
            "bone_mappings",
            self,
            "bone_mapping_index",
            rows=12,
        )


class YUGUS_NODE_bone_map_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_bone_map_mix"
    bl_label = "Bone Map Mix"
    bl_icon = "NODETREE"

    def init(self, _context):
        self._init_base(180.0)
        _node_input(self, SOCKET_BONE_MAP, "Base")
        _node_input(self, SOCKET_BONE_MAP, "Overlay")
        _node_output(self, SOCKET_BONE_MAP, "Bone Map")


META_TEXT_FIELDS = (
    ("Name", "meta_name", "name"), ("Author", "author", "authors"),
    ("Version", "version", "version"), ("Copyright", "copyright", "copyright"),
    ("Contact", "contact", "contact"), ("Reference", "reference", "reference"),
)


def _meta_details_changed(node, _context=None):
    for socket in node.inputs:
        hidden = socket.name not in {"Name", "Author"} and not node.show_details and not socket.is_linked
        if socket.hide != hidden:
            socket.hide = hidden


class YUGUS_NODE_meta_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_meta_value"
    bl_label = "Meta Value"
    bl_icon = "INFO"
    bl_description = "Manually enter a name, author and other character metadata"
    meta_name: bpy.props.StringProperty(name="Name")
    author: bpy.props.StringProperty(name="Author")
    version: bpy.props.StringProperty(name="Version")
    copyright: bpy.props.StringProperty(name="Copyright")
    contact: bpy.props.StringProperty(name="Contact")
    reference: bpy.props.StringProperty(name="Reference")
    show_details: bpy.props.BoolProperty(name="Details", default=False, update=_meta_details_changed)

    def init(self, _context):
        self._init_base(240)
        for name, _property, _key in META_TEXT_FIELDS:
            _node_input(self, "YUGUS_NS_text", name)
        _node_output(self, SOCKET_META, "Meta")
        _meta_details_changed(self)

    def update(self):
        _meta_details_changed(self)

    def draw_buttons(self, _context, layout):
        layout.prop(self, "show_details", toggle=True)


class YUGUS_NODE_expression_map_value(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_map_value"
    bl_label = "Expression Map Value"
    bl_icon = "SHAPEKEY_DATA"

    expressions: bpy.props.CollectionProperty(type=YUGUS_PG_expression_map_entry)
    expression_index: bpy.props.IntProperty(default=0)

    def init(self, _context):
        self._init_base(310.0)
        _node_output(self, SOCKET_EXPRESSION_MAP, "Expression Map")

    def draw_buttons(self, _context, layout):
        row = layout.row()
        row.template_list(
            "YUGUS_UL_expression_map_entries",
            "",
            self,
            "expressions",
            self,
            "expression_index",
            rows=3,
        )
        _draw_collection_buttons(row, self, "EXPRESSIONS")
        if len(self.expressions) == 0:
            layout.label(text="Add an expression", icon="INFO")
            return
        index = min(max(int(self.expression_index), 0), len(self.expressions) - 1)
        expression = self.expressions[index]
        settings = layout.box()
        settings.prop(expression, "name")
        settings.prop(expression, "is_binary")
        overrides = settings.column(align=True)
        overrides.prop(expression, "override_blink")
        overrides.prop(expression, "override_mouth")

        layout.label(text="Morph Target Binds")
        row = layout.row()
        row.template_list(
            "YUGUS_UL_morph_target_binds",
            "",
            expression,
            "morph_targets",
            expression,
            "morph_target_index",
            rows=2,
        )
        _draw_collection_buttons(row, self, "MORPH_TARGETS")
        if len(expression.morph_targets) > 0:
            bind_index = min(
                max(int(expression.morph_target_index), 0),
                len(expression.morph_targets) - 1,
            )
            bind = expression.morph_targets[bind_index]
            bind_box = layout.box()
            bind_box.prop(bind, "mesh_object")
            shape_keys = getattr(
                getattr(bind.mesh_object, "data", None), "shape_keys", None
            )
            if shape_keys is not None:
                bind_box.prop_search(
                    bind, "shape_key_name", shape_keys, "key_blocks", text="Shape Key"
                )
            else:
                bind_box.prop(bind, "shape_key_name")
            bind_box.prop(bind, "weight", slider=True)

        layout.label(text="Material Color Binds")
        row = layout.row()
        row.template_list(
            "YUGUS_UL_material_color_binds",
            "",
            expression,
            "material_targets",
            expression,
            "material_target_index",
            rows=2,
        )
        _draw_collection_buttons(row, self, "MATERIAL_TARGETS")
        if len(expression.material_targets) > 0:
            bind_index = min(
                max(int(expression.material_target_index), 0),
                len(expression.material_targets) - 1,
            )
            bind = expression.material_targets[bind_index]
            bind_box = layout.box()
            bind_box.prop(bind, "material")
            bind_box.prop(bind, "bind_type")
            bind_box.prop(bind, "target_value")


class YUGUS_NODE_expression_map_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_map_mix"
    bl_label = "Expression Map Mix"
    bl_icon = "NODETREE"

    def init(self, _context):
        self._init_base(190.0)
        _node_input(self, SOCKET_EXPRESSION_MAP, "Base")
        _node_input(self, SOCKET_EXPRESSION_MAP, "Overlay")
        _node_output(self, SOCKET_EXPRESSION_MAP, "Expression Map")


class _YUGUS_LiveSourceBase(YUGUS_NodeBase):
    address: bpy.props.StringProperty(name="Address", default="127.0.0.1")
    connected: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})
    last_error: bpy.props.StringProperty(default="", options={"SKIP_SAVE", "HIDDEN"})

    def _draw_connection(self, layout):
        operator = layout.operator(
            "yugu_node_studio.source_toggle",
            text="Disconnect" if self.connected else "Connect",
            icon="PAUSE" if self.connected else "PLAY",
        )
        operator.tree_name = self.id_data.name
        operator.node_name = self.name
        self._draw_connection_error(layout)

    def _draw_connection_error(self, layout):
        if self.last_error:
            error = layout.row()
            error.alert = True
            error.label(text=self.last_error, icon="ERROR")


def ensure_ifacial_blendshape_masks(node, extra_names=()):
    """Add the standard ARKit rows without replacing saved mask settings."""

    masks = getattr(node, "blendshape_masks", None)
    if masks is None:
        return
    existing = {
        str(item.normalized_name or normalize_name(item.expression_name))
        for item in masks
    }
    for expression_name in tuple(ARKIT_BLENDSHAPES) + tuple(extra_names):
        normalized = normalize_name(expression_name)
        if not normalized or normalized in existing:
            continue
        item = masks.add()
        item.expression_name = str(expression_name)
        item.normalized_name = normalized
        existing.add(normalized)


class YUGUS_NODE_vmc_source(_YUGUS_LiveSourceBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_vmc_source"
    bl_label = "VMC Receiver"
    bl_icon = "NETWORK_DRIVE"

    port: bpy.props.IntProperty(name="Port", default=39539, min=1, max=65535)

    def init(self, _context):
        self._init_base(190.0)
        _node_output(self, SOCKET_MOTION, "Humanoid Motion")
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")
        ensure_source_connection_inputs(self)
        _node_input(self, SOCKET_TRIGGER, "Connect")
        _node_input(self, SOCKET_TRIGGER, "Disconnect")

    def draw_buttons(self, _context, layout):
        self._draw_connection_error(layout)
        self._draw_monitor(layout)


class YUGUS_NODE_ifacial_source(_YUGUS_LiveSourceBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_ifacial_source"
    bl_label = "iFacialMocap"
    bl_icon = "OUTLINER_OB_CAMERA"

    address: bpy.props.StringProperty(name="iPhone Address", default="127.0.0.1")
    device_port: bpy.props.IntProperty(name="Device Port", default=49983, min=1, max=65535)
    listen_address: bpy.props.StringProperty(
        name="Listen Address", default="0.0.0.0", options={"HIDDEN"}
    )
    listen_port: bpy.props.IntProperty(
        name="Listen Port", default=49986, min=1, max=65535, options={"HIDDEN"}
    )
    mirror: bpy.props.BoolProperty(
        name="Mirror",
        description="Mirror head yaw and roll",
        default=True,
    )
    blendshape_mask_open: bpy.props.BoolProperty(
        name="BlendShape Mask",
        description="Show per-blendshape Raw, Weight, Smooth and Output values",
        default=False,
        options={"HIDDEN"},
    )
    blendshape_masks: bpy.props.CollectionProperty(type=YUGUS_PG_ifacial_blendshape)
    blendshape_index: bpy.props.IntProperty(default=0, min=0)
    calibration: bpy.props.FloatVectorProperty(
        name="Calibration",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype="EULER",
        options={"HIDDEN"},
    )

    def init(self, _context):
        self._init_base(520.0)
        _node_output(self, SOCKET_MOTION, "Humanoid Motion")
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")
        ensure_source_connection_inputs(self)
        _node_input(self, SOCKET_TRIGGER, "Connect")
        _node_input(self, SOCKET_TRIGGER, "Disconnect")
        _node_input(self, SOCKET_TRIGGER, "Calibrate")
        ensure_ifacial_blendshape_masks(self)

    def draw_buttons(self, _context, layout):
        self._draw_connection_error(layout)
        layout.prop(self, "mirror")

        header = layout.row(align=True)
        header.prop(
            self,
            "blendshape_mask_open",
            text=f"BlendShape Mask ({len(self.blendshape_masks)})",
            icon="TRIA_DOWN" if self.blendshape_mask_open else "TRIA_RIGHT",
            emboss=False,
        )
        if not self.blendshape_mask_open:
            return

        columns = layout.row(align=True)
        split = columns.split(factor=0.34, align=True)
        split.label(text="Expression")
        values = split.row(align=True)
        values.label(text="Data Raw")
        values.label(text="Weight")
        values.label(text="Smooth")
        values.label(text="Output")
        layout.template_list(
            "YUGUS_UL_ifacial_blendshapes",
            "",
            self,
            "blendshape_masks",
            self,
            "blendshape_index",
            rows=10,
        )


class YUGUS_NODE_action_source(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_action_source"
    bl_label = "Blender Action"
    bl_icon = "ACTION"

    action: bpy.props.PointerProperty(name="Action", type=bpy.types.Action)
    speed: bpy.props.FloatProperty(name="Speed", default=1.0, min=-10.0, max=10.0)
    frame_offset: bpy.props.FloatProperty(name="Frame Offset", default=0.0)
    cyclic: bpy.props.BoolProperty(name="Cyclic", default=True)

    def init(self, _context):
        self._init_base(190.0)
        _node_input(self, SOCKET_BONE_MAP, "Source Bone Map")
        trigger = _node_input(self, SOCKET_TRIGGER, "Trigger")
        trigger.default_value = 1.0
        _node_output(self, SOCKET_MOTION, "Humanoid Motion")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "action", text="")
        row = layout.row(align=True)
        row.prop(self, "speed")
        row.prop(self, "frame_offset", text="Offset")
        layout.prop(self, "cyclic")
        self._draw_monitor(layout)


KEY_ITEMS = tuple(
    [(letter, letter, letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    + [
        (identifier, label, label)
        for identifier, label in (
            ("ZERO", "0"),
            ("ONE", "1"),
            ("TWO", "2"),
            ("THREE", "3"),
            ("FOUR", "4"),
            ("FIVE", "5"),
            ("SIX", "6"),
            ("SEVEN", "7"),
            ("EIGHT", "8"),
            ("NINE", "9"),
        )
    ]
    + [(f"F{number}", f"F{number}", f"F{number}") for number in range(1, 13)]
    + [(key, label, label) for key, label in (
        ("SPACE", "Space"), ("TAB", "Tab"), ("RET", "Enter"),
        ("BACK_SPACE", "Backspace"), ("UP_ARROW", "Up"), ("DOWN_ARROW", "Down"),
        ("LEFT_ARROW", "Left"), ("RIGHT_ARROW", "Right"),
        ("SEMI_COLON", ";"), ("COMMA", ","), ("PERIOD", "."), ("SLASH", "/"),
        ("LEFT_BRACKET", "["), ("RIGHT_BRACKET", "]"), ("MINUS", "-"), ("EQUAL", "="))]
)


class YUGUS_NODE_key_bind(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_key_bind"
    bl_label = "Key Bind"
    bl_icon = "EVENT_K"

    key: bpy.props.EnumProperty(name="Key", items=(("NONE", "None", "Unassigned"),) + KEY_ITEMS, default="NONE")
    use_shift: bpy.props.BoolProperty(name="Shift", default=False)
    use_ctrl: bpy.props.BoolProperty(name="Ctrl", default=False)
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("HOLD", "Hold", "Active while held"),
            ("TRIGGER", "Trigger", "Toggle on each key press"),
            ("SOLO", "Solo", "Toggle and suppress other Key Bind nodes in this graph"),
        ),
        default="HOLD",
    )
    value: bpy.props.FloatProperty(
        name="On Value", default=1.0, min=0.0, max=1.0, subtype="FACTOR"
    )
    binary: bpy.props.BoolProperty(name="Binary", default=False)
    smooth: bpy.props.FloatProperty(name="Smooth", default=0.15, min=0.0, max=1.0)
    mask_exclusive: bpy.props.BoolProperty(name="Exclusive", default=False)
    mask_eyes: bpy.props.BoolProperty(name="Eyes", default=False)
    mask_mouth: bpy.props.BoolProperty(name="Mouth", default=False)

    def init(self, _context):
        self._init_base(170.0)
        _node_output(self, SOCKET_TRIGGER, "Trigger")

    def draw_buttons(self, _context, layout):
        row = layout.row(align=True)
        row.prop(self, "key", text="")
        row.prop(self, "use_ctrl", toggle=True)
        row.prop(self, "use_shift", toggle=True)
        layout.prop(self, "mode")
        layout.prop(self, "value", slider=True)
        layout.prop(self, "binary")
        row = layout.row()
        row.enabled = not self.binary
        row.prop(self, "smooth", slider=True)
        row = layout.row(align=True)
        for name in ("mask_exclusive", "mask_eyes", "mask_mouth"):
            row.prop(self, name, toggle=True)
        self._draw_monitor(layout)


# Retained so older .blend files can still load. New graphs use the split
# Key Bind -> Expression Action nodes below.
class YUGUS_NODE_keybind_expression(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_keybind_expression"
    bl_label = "Keybind Expression"
    bl_icon = "EVENT_K"

    key: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default="A")
    use_shift: bpy.props.BoolProperty(name="Shift", default=False)
    use_ctrl: bpy.props.BoolProperty(name="Ctrl", default=False)
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("HOLD", "Hold", "Active while held"),
            ("TRIGGER", "Trigger", "Toggle on key press"),
        ),
        default="HOLD",
    )
    expression: bpy.props.StringProperty(name="Expression", default="happy")
    value: bpy.props.FloatProperty(name="Value", default=1.0, min=0.0, max=1.0)

    def init(self, _context):
        self._init_base(180.0)
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "expression")
        row = layout.row(align=True)
        row.prop(self, "key", text="")
        row.prop(self, "use_ctrl", toggle=True)
        row.prop(self, "use_shift", toggle=True)
        layout.prop(self, "mode")
        layout.prop(self, "value")


_EXPRESSION_ACTION_ITEM_CACHE = {}


def _expression_map_for_action_node(node):
    return _expression_map_from_input(node.inputs.get("Expression Map"), set())


def _expression_map_from_input(socket, visited):
    if socket is None or not socket.is_linked:
        return None
    links = tuple(socket.links)
    if not links:
        return None
    source = links[0].from_node
    pointer = source.as_pointer()
    if pointer in visited:
        return None
    visited = visited | {pointer}
    if source.bl_idname == "YUGUS_NODE_vrm_data":
        from . import vrm_cache

        snapshot = vrm_cache.get_snapshot(source)
        return snapshot.expression_map if snapshot is not None else None
    if source.bl_idname == "YUGUS_NODE_expression_map_value":
        return expression_map_from_node(source)
    if source.bl_idname == "YUGUS_NODE_expression_map_mix":
        return mix_expression_maps(
            _expression_map_from_input(source.inputs.get("Base"), visited),
            _expression_map_from_input(source.inputs.get("Overlay"), visited),
        )
    if source.bl_idname == "NodeReroute" and source.inputs:
        return _expression_map_from_input(source.inputs[0], visited)
    return None


def _expression_action_items(node, _context):
    expression_map = _expression_map_for_action_node(node)
    items = []
    for key, definition in getattr(expression_map, "definitions", {}).items():
        items.append((str(key), str(definition.name), f"Drive {definition.name}"))
    if not items:
        items = [
            (
                "__NONE__",
                "Connect Expression Map",
                "Connect a reloaded VRM Data Expression Map",
            )
        ]
    cache_key = str(getattr(node, "runtime_id", "") or node.name)
    _EXPRESSION_ACTION_ITEM_CACHE[cache_key] = tuple(items)
    return _EXPRESSION_ACTION_ITEM_CACHE[cache_key]


class YUGUS_NODE_expression_action(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_action"
    bl_label = "Expression Action"
    bl_icon = "SHAPEKEY_DATA"

    expression_key: bpy.props.EnumProperty(
        name="Expression", items=_expression_action_items
    )
    strength: bpy.props.FloatProperty(
        name="Strength", default=1.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(190.0)
        _node_input(self, SOCKET_TRIGGER, "Trigger")
        _node_input(self, SOCKET_EXPRESSION_MAP, "Expression Map")
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "expression_key", text="")
        layout.prop(self, "strength", slider=True)


# Yugo 0.1.29 iFacialMocap profiles, in Pitch / Yaw / Roll order.
HEAD_PROFILE_PRESETS = {
    "NORMAL": {
        "head": (0.8, 0.8, 0.6),
        "neck": (0.5, 0.5, 0.3),
        "chest": (0.6, 0.6, 0.5),
        "spine": (0.3, 0.3, 0.2),
    },
    "LIVE2D": {
        "head": (0.7, 1.0, 0.4),
        "neck": (0.6, -0.4, 0.5),
        "chest": (-0.3, 0.35, 0.2),
        "spine": (0.85, -0.5, -0.5),
    },
}
_HEAD_PROFILE_PRESET_UPDATES = set()


def _head_profile_preset_updated(node, _context):
    values = HEAD_PROFILE_PRESETS.get(node.preset)
    if values is None:
        return
    pointer = node.as_pointer()
    if pointer in _HEAD_PROFILE_PRESET_UPDATES:
        return
    _HEAD_PROFILE_PRESET_UPDATES.add(pointer)
    try:
        for name, factors in values.items():
            setattr(node, name, factors)
    finally:
        _HEAD_PROFILE_PRESET_UPDATES.discard(pointer)


def _head_profile_values_updated(node, _context):
    if node.as_pointer() in _HEAD_PROFILE_PRESET_UPDATES:
        return
    values = HEAD_PROFILE_PRESETS.get(node.preset)
    if values is not None and any(
        abs(float(value) - expected) > 1.0e-6
        for name, factors in values.items()
        for value, expected in zip(getattr(node, name), factors)
    ):
        node.preset = "CUSTOM"


class YUGUS_NODE_head_profile(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_head_profile"
    bl_label = "Head Rotation Profile"
    bl_icon = "BONE_DATA"

    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Head and body rotation multipliers from Yugo",
        items=(
            ("NORMAL", "Normal", "Yugo non-inverted rotation profile"),
            ("LIVE2D", "Live2D", "Yugo inverted rotation profile with body counter-rotation"),
            ("CUSTOM", "Custom", "Keep manually adjusted rotation multipliers"),
        ),
        default="NORMAL",
        update=_head_profile_preset_updated,
    )
    head: bpy.props.FloatVectorProperty(
        name="Head", size=3, default=HEAD_PROFILE_PRESETS["NORMAL"]["head"],
        update=_head_profile_values_updated,
    )
    neck: bpy.props.FloatVectorProperty(
        name="Neck", size=3, default=HEAD_PROFILE_PRESETS["NORMAL"]["neck"],
        update=_head_profile_values_updated,
    )
    chest: bpy.props.FloatVectorProperty(
        name="Chest", size=3, default=HEAD_PROFILE_PRESETS["NORMAL"]["chest"],
        update=_head_profile_values_updated,
    )
    spine: bpy.props.FloatVectorProperty(
        name="Spine", size=3, default=HEAD_PROFILE_PRESETS["NORMAL"]["spine"],
        update=_head_profile_values_updated,
    )

    def init(self, _context):
        self._init_base(205.0)
        _node_input(self, SOCKET_MOTION, "Motion")
        _node_output(self, SOCKET_MOTION, "Motion")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "preset")
        layout.label(text="Pitch / Yaw / Roll")
        layout.prop(self, "head")
        layout.prop(self, "neck")
        layout.prop(self, "chest")
        layout.prop(self, "spine")


class YUGUS_NODE_humanoid_mask(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_humanoid_mask"
    bl_label = "Humanoid Mask"
    bl_icon = "MOD_MASK"

    def init(self, _context):
        self._init_base(210.0)
        _node_output(self, SOCKET_MOTION_MASK, "Mask")

    def draw_buttons(self, _context, layout):
        for left, right in (
            ("root", "hips"),
            ("spine", "chest"),
            ("neck", "head"),
            ("left_leg", "right_leg"),
            ("left_shoulder", "right_shoulder"),
            ("left_arm", "right_arm"),
            ("left_hand", "right_hand"),
            ("left_finger", "right_finger"),
        ):
            row = layout.row(align=True)
            row.prop(self, left, text=left.replace("_", " ").title(), slider=True)
            row.prop(self, right, text=right.replace("_", " ").title(), slider=True)


if "__annotations__" not in YUGUS_NODE_humanoid_mask.__dict__:
    YUGUS_NODE_humanoid_mask.__annotations__ = {}
for _region in MASK_REGIONS:
    YUGUS_NODE_humanoid_mask.__annotations__[_region] = bpy.props.FloatProperty(
        name=_region.replace("_", " ").title(),
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class YUGUS_NODE_motion_smooth(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_motion_smooth"
    bl_label = "Motion Smooth"
    bl_icon = "SMOOTHCURVE"

    smooth: bpy.props.FloatProperty(
        name="Smooth", default=0.15, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(175.0)
        _node_input(self, SOCKET_MOTION, "Motion")
        _node_input(self, SOCKET_MOTION_MASK, "Mask")
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_MOTION, "Motion")


class YUGUS_NODE_mirror_motion(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_mirror_motion"
    bl_label = "Mirror Motion"
    bl_icon = "MOD_MIRROR"

    def init(self, _context):
        self._init_base(155.0)
        _node_input(self, SOCKET_MOTION, "Motion")
        _node_output(self, SOCKET_MOTION, "Motion")


class YUGUS_NODE_motion_weight(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_motion_weight"
    bl_label = "Motion Weight"
    bl_icon = "MOD_OPACITY"

    weight: bpy.props.FloatProperty(
        name="Weight", default=1.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(155.0)
        _node_input(self, SOCKET_MOTION, "Motion")
        _node_input(self, SOCKET_MOTION_MASK, "Mask")
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_MOTION, "Motion")


class YUGUS_NODE_motion_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_motion_mix"
    bl_label = "Motion Mix"
    bl_icon = "NODETREE"

    factor: bpy.props.FloatProperty(
        name="Factor", default=1.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(165.0)
        _node_input(self, SOCKET_MOTION, "Base")
        _node_input(self, SOCKET_MOTION, "Overlay")
        _node_input(self, SOCKET_MOTION_MASK, "Mask")
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_MOTION, "Motion")


class YUGUS_NODE_humanoid_retarget(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_humanoid_retarget"
    bl_label = "Humanoid Retarget"
    bl_icon = "CONSTRAINT_BONE"

    def init(self, _context):
        self._init_base(180.0)
        _node_input(self, SOCKET_MOTION, "Humanoid Motion")
        _node_input(self, SOCKET_BONE_MAP, "Bone Map")
        _node_output(self, SOCKET_NATIVE_POSE, "Native Pose")


class YUGUS_NODE_expression_mask(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_mask"
    bl_label = "Expression Mask"
    bl_icon = "MOD_MASK"

    names: bpy.props.StringProperty(
        name="Names", description="Comma-separated expression names", default="blinkLeft, blinkRight"
    )
    weight: bpy.props.FloatProperty(name="Weight", default=0.0, min=-5.0, max=5.0)

    def init(self, _context):
        self._init_base(185.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "names")
        layout.prop(self, "weight")


class YUGUS_NODE_expression_smooth(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_smooth"
    bl_label = "Expression Smooth"
    bl_icon = "SMOOTHCURVE"

    names: bpy.props.StringProperty(
        name="Names", description="Comma-separated expression names; blank means all expressions", default=""
    )
    smooth: bpy.props.FloatProperty(
        name="Smooth", default=0.15, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(185.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        ensure_expression_smooth_names(self)
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")


def ensure_expression_smooth_names(node):
    """Add the text parameter while keeping saved values and socket links."""
    socket = node.inputs.get("Names")
    if socket is None:
        socket = _node_input(node, "YUGUS_NS_text", "Names")
        socket.default_value = node.names
        index = len(node.inputs) - 1
        node.inputs.move(index, 1)
    return socket


class YUGUS_NODE_expression_weight(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_weight"
    bl_label = "Expression Weight"
    bl_icon = "MOD_OPACITY"

    weight: bpy.props.FloatProperty(name="Weight", default=1.0, min=-5.0, max=5.0)

    def init(self, _context):
        self._init_base(155.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")


class YUGUS_NODE_expression_mix(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_mix"
    bl_label = "Expression Mix"
    bl_icon = "NODETREE"

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("REPLACE", "Replace", "Blend overlay over base"),
            ("ADD", "Add", "Add overlay values"),
            ("MAX", "Maximum", "Keep the stronger value"),
        ),
        default="REPLACE",
    )
    factor: bpy.props.FloatProperty(
        name="Factor", default=1.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def init(self, _context):
        self._init_base(165.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Base")
        _node_input(self, SOCKET_EXPRESSIONS, "Overlay")
        ensure_numeric_parameter(self)
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "mode")


class YUGUS_NODE_expression_lock(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_lock"
    bl_label = "Expression Lock"
    bl_icon = "LOCKED"

    lock_eyes: bpy.props.BoolProperty(name="Lock Eyes", default=False)
    lock_mouth: bpy.props.BoolProperty(name="Lock Mouth", default=False)

    def init(self, _context):
        self._init_base(165.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        _node_output(self, SOCKET_EXPRESSIONS, "Expressions")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "lock_eyes")
        layout.prop(self, "lock_mouth")


class YUGUS_NODE_expression_bind(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_expression_bind"
    bl_label = "Expression Bind"
    bl_icon = "CONSTRAINT"

    def init(self, _context):
        self._init_base(180.0)
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        _node_input(self, SOCKET_EXPRESSION_MAP, "Expression Map")
        _node_output(self, SOCKET_NATIVE_EXPRESSIONS, "Native Expressions")


class YUGUS_NODE_spring_simulation(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_spring_simulation"
    bl_label = "Spring Simulation (Legacy)"
    bl_icon = "PHYSICS"

    spring_active: bpy.props.BoolProperty(
        name="Spring Physics Running",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    spring_last_error: bpy.props.StringProperty(
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    spring_status: bpy.props.StringProperty(
        default="Stopped",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    trace_duration: bpy.props.FloatProperty(
        name="Seconds", description="Duration of the comparison recording",
        default=10.0, min=0.1, max=120.0, unit="TIME",
    )
    trace_sample_hz: bpy.props.IntProperty(
        name="Record Hz", description="Samples per second in the comparison recording",
        default=60, min=1, max=240,
    )
    trace_recording: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    trace_summary: bpy.props.StringProperty(default="No recording", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(260.0)
        _node_input(self, SOCKET_SPRING_DATA, "Spring Data")
        _node_output(self, "YUGUS_NS_bone_rotation", "Bone Transform")
        _node_output(self, "YUGUS_NS_simulation_data", "Simulation Data")
        _node_input(self, SOCKET_TRIGGER, "Reload")

    def draw_buttons(self, _context, layout):
        layout.label(text="Runs on trigger or while recording")
        debug = layout.operator("yugu_node_studio.spring_debug_print", text="Print Spring Debug", icon="CONSOLE")
        debug.tree_name = self.id_data.name
        debug.node_name = self.name
        layout.label(
            text=self.spring_status,
            icon="CHECKMARK" if self.spring_active else "INFO",
        )
        if self.spring_last_error:
            error = layout.row()
            error.alert = True
            error.label(text=self.spring_last_error, icon="ERROR")
        from . import spring_trace

        spring_trace.draw_controls(layout, self)


class YUGUS_NODE_blender_output(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_blender_output"
    bl_label = "Character Output (Retired)"
    bl_icon = "OUTPUT"

    is_active_output: bpy.props.BoolProperty(
        name="Active",
        description="Drive this Blender character after validating the hips mapping",
        default=False,
        update=_output_active_updated,
    )
    active_validated: bpy.props.BoolProperty(
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    write_root: bpy.props.BoolProperty(name="Write Root Object", default=True)
    write_locations: bpy.props.BoolProperty(name="Write Bone Locations", default=True)
    last_summary: bpy.props.StringProperty(default="Idle", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(230.0)
        _node_input(self, SOCKET_MOTION, "Humanoid Motion")
        _node_input(self, SOCKET_BONE_MAP, "Bone Map")
        _node_input(self, SOCKET_EXPRESSIONS, "Expressions")
        _node_input(self, SOCKET_EXPRESSION_MAP, "Expression Map")

    def draw_buttons(self, _context, layout):
        layout.label(text="Retired: use Hz Trigger + Apply nodes")
        layout.label(text=self.last_summary)


class YUGUS_NODE_data_viewer(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_data_viewer"
    bl_label = "Data Viewer"
    bl_icon = "INFO"

    summary: bpy.props.StringProperty(default="No data", options={"SKIP_SAVE"})

    def init(self, _context):
        self._init_base(190.0)
        _node_input(self, SOCKET_BONE_MAP, "Bone Map")
        _node_input(self, SOCKET_EXPRESSION_MAP, "Expression Map")
        _node_input(self, SOCKET_SPRING_DATA, "Spring Data")
        _node_input(self, SOCKET_META, "Meta")
        _node_input(self, "YUGUS_NS_simulation_data", "Simulation Data")

    def draw_buttons(self, _context, layout):
        layout.label(text=self.summary)


SOCKET_CLASSES = (
    YUGUS_NS_humanoid_motion,
    YUGUS_NS_humanoid_mask,
    YUGUS_NS_trigger,
    YUGUS_NS_native_pose,
    YUGUS_NS_expressions,
    YUGUS_NS_native_expressions,
    YUGUS_NS_bone_map,
    YUGUS_NS_expression_map,
    YUGUS_NS_spring_data,
    YUGUS_NS_simulation_data,
    YUGUS_NS_meta,
)

PROPERTY_CLASSES = (
    YUGUS_PG_bone_map_entry,
    YUGUS_PG_morph_target_bind,
    YUGUS_PG_material_color_bind,
    YUGUS_PG_expression_map_entry,
    YUGUS_PG_ifacial_blendshape,
)

UI_LIST_CLASSES = (
    YUGUS_UL_bone_map_entries,
    YUGUS_UL_expression_map_entries,
    YUGUS_UL_morph_target_binds,
    YUGUS_UL_material_color_binds,
    YUGUS_UL_ifacial_blendshapes,
)

NODE_CLASSES = (
    YUGUS_NODE_vrm_data,
    YUGUS_NODE_bone_map_value,
    YUGUS_NODE_bone_map_mix,
    YUGUS_NODE_expression_map_value,
    YUGUS_NODE_expression_map_mix,
    YUGUS_NODE_meta_value,
    YUGUS_NODE_vmc_source,
    YUGUS_NODE_ifacial_source,
    YUGUS_NODE_action_source,
    YUGUS_NODE_key_bind,
    YUGUS_NODE_keybind_expression,
    YUGUS_NODE_expression_action,
    YUGUS_NODE_head_profile,
    YUGUS_NODE_humanoid_mask,
    YUGUS_NODE_motion_smooth,
    YUGUS_NODE_mirror_motion,
    YUGUS_NODE_motion_weight,
    YUGUS_NODE_motion_mix,
    YUGUS_NODE_humanoid_retarget,
    YUGUS_NODE_expression_mask,
    YUGUS_NODE_expression_smooth,
    YUGUS_NODE_expression_weight,
    YUGUS_NODE_expression_mix,
    YUGUS_NODE_expression_lock,
    YUGUS_NODE_expression_bind,
    YUGUS_NODE_spring_simulation,
    YUGUS_NODE_blender_output,
    YUGUS_NODE_data_viewer,
)

CLASSES = (
    PROPERTY_CLASSES
    + UI_LIST_CLASSES
    + (YUGUS_NT_humanoid_motion,)
    + SOCKET_CLASSES
    + NODE_CLASSES
)


NODE_MENU = (
    (
        "Inputs",
        (
            (YUGUS_NODE_vmc_source.bl_idname, "VMC Receiver"),
            (YUGUS_NODE_ifacial_source.bl_idname, "iFacialMocap"),
            (YUGUS_NODE_action_source.bl_idname, "Blender Action"),
            (YUGUS_NODE_data_viewer.bl_idname, "Data Viewer"),
            (YUGUS_NODE_vrm_data.bl_idname, "VRM Data"),
        ),
    ),
    (
        "Custom Inputs",
        (
            ("YUGUS_NODE_float_value", "Float"),
            ("YUGUS_NODE_vector_value", "Vector"),
            ("YUGUS_NODE_string_value", "String"),
            ("YUGUS_NODE_armature_value", "Armature"),
            ("YUGUS_NODE_image_value", "Image"),
            ("YUGUS_NODE_object_value", "Object"),
            ("YUGUS_NODE_objects_value", "Objects List"),
            ("YUGUS_NODE_collection_value", "Collection"),
            (YUGUS_NODE_meta_value.bl_idname, "Meta Value"),
            ("YUGUS_NODE_image_from_url", "Image From URL"),
            (YUGUS_NODE_bone_map_value.bl_idname, "Bone Map Value"),
            (YUGUS_NODE_expression_map_value.bl_idname, "Expression Map Value"),
        ),
    ),
    (
        "Math / Mixer",
        (
            ("YUGUS_NODE_math", "Math"),
            ("YUGUS_NODE_vector_math", "Vector Math"),
            ("YUGUS_NODE_string_to_value", "String to Value"),
            ("YUGUS_NODE_value_to_string", "Value to String"),
            ("YUGUS_NODE_mixer", "Mixer"),
            ("YUGUS_NODE_combine_xyz", "Combine XYZ"),
            ("YUGUS_NODE_separate_xyz", "Separate XYZ"),
            ("YUGUS_NODE_map_range", "Map Range"),
            ("YUGUS_NODE_clamp", "Clamp"),
        ),
    ),
    (
        "FlowControl",
        (
            ("YUGUS_NODE_hz_trigger", "Hz Trigger"),
            ("YUGUS_NODE_button_trigger", "Button Trigger"),
            ("YUGUS_NODE_flow_if", "If"),
            ("YUGUS_NODE_flow_gate", "Gate"),
            ("YUGUS_NODE_flow_delay", "Delay"),
            ("YUGUS_NODE_flow_wait_until", "Wait Until"),
            ("YUGUS_NODE_flow_once", "Once"),
            ("YUGUS_NODE_flow_sequence", "Flow Sequence"),
            ("YUGUS_NODE_flow_every_n", "Every N"),
            ("YUGUS_NODE_flow_repeat", "Repeat"),
        ),
    ),
    (
        "Outputs",
        (
            ("YUGUS_NODE_apply_rotation", "Bone Transforms to Armature"),
            ("YUGUS_NODE_apply_shapes", "ShapeKeysValues to Objects List"),
            ("YUGUS_NODE_apply_object_transform", "Apply Transform"),
        ),
    ),
    (
        "Motion Operation",
        (
            ("YUGUS_NODE_mixer", "Mixer"),
            (YUGUS_NODE_motion_smooth.bl_idname, "Motion Smooth"),
            (YUGUS_NODE_head_profile.bl_idname, "Head Rotation Profile"),
            (YUGUS_NODE_humanoid_mask.bl_idname, "Humanoid Mask"),
            (YUGUS_NODE_motion_weight.bl_idname, "Motion Weight"),
            ("YUGUS_NODE_motion_delay", "Motion Delay"),
            (YUGUS_NODE_mirror_motion.bl_idname, "Mirror Motion"),
            ("YUGUS_NODE_motion_to_rotation", "Motion to Bone Transform"),
        ),
    ),
    (
        "Expression Operation",
        (
            ("YUGUS_NODE_mixer", "Mixer"),
            (YUGUS_NODE_expression_weight.bl_idname, "Expression Weight"),
            (YUGUS_NODE_expression_smooth.bl_idname, "Expression Smooth"),
            (YUGUS_NODE_expression_action.bl_idname, "Expression Action"),
            (YUGUS_NODE_expression_lock.bl_idname, "Expression Lock"),
            (YUGUS_NODE_expression_mask.bl_idname, "Expression Mask"),
            ("YUGUS_NODE_expression_to_shapes", "Expressions to ShapeKeysValues"),
        ),
    ),
    (
        "Physics",
        (
            ("YUGUS_NODE_spring_to_physics", "Spring to YugoPhysics"),
            ("YUGUS_NODE_rigid_to_physics", "Rigid Body to YugoPhysics"),
            ("YUGUS_NODE_physics_solver", "YugoPhysics Solver"),
        ),
    ),
)

# Shortcuts configure the same Mixer class through Blender's Add Node settings.
# All types remain selectable on the created node, regardless of its category.
NODE_MENU_SETTINGS = {
    "Math / Mixer": {"YUGUS_NODE_mixer": {"data_type": "FLOAT"}},
    "Motion Operation": {"YUGUS_NODE_mixer": {"data_type": "HUMANOID_MOTION"}},
    "Expression Operation": {"YUGUS_NODE_mixer": {"data_type": "EXPRESSIONS"}},
}


def _ensure_input(node, socket_type, name, default_value=None):
    socket = node.inputs.get(name)
    if socket is not None and socket.bl_idname != socket_type:
        node.inputs.remove(socket)
        socket = None
    if socket is None:
        socket = _node_input(node, socket_type, name)
    if default_value is not None and hasattr(socket, "default_value"):
        socket.default_value = default_value
    return socket


def _ensure_bottom_trigger_input(node, name):
    """Add an action trigger without replacing existing socket links."""

    socket = _ensure_input(node, SOCKET_TRIGGER, name)
    index = next(index for index, item in enumerate(node.inputs) if item == socket)
    if index != len(node.inputs) - 1:
        node.inputs.move(index, len(node.inputs) - 1)
    return socket


def _ensure_reload_input(node):
    return _ensure_bottom_trigger_input(node, "Reload")


def _ensure_output(node, socket_type, name):
    socket = node.outputs.get(name)
    if socket is not None and socket.bl_idname != socket_type:
        node.outputs.remove(socket)
        socket = None
    return socket if socket is not None else _node_output(node, socket_type, name)


def _linked_source(input_socket):
    if input_socket is None or not input_socket.is_linked:
        return None
    links = tuple(input_socket.links)
    return links[0].from_socket if links else None


def _trace_native_pose(source_socket):
    visited = set()
    while source_socket is not None:
        node = source_socket.node
        pointer = node.as_pointer()
        if pointer in visited:
            break
        visited.add(pointer)
        if node.bl_idname == "YUGUS_NODE_humanoid_retarget":
            return (
                _linked_source(node.inputs.get("Humanoid Motion")),
                _linked_source(node.inputs.get("Bone Map")),
            )
        if node.bl_idname == "YUGUS_NODE_spring_simulation":
            source_socket = _linked_source(node.inputs.get("Native Pose"))
            continue
        break
    return None, None


def _trace_native_expressions(source_socket):
    if source_socket is None:
        return None, None
    node = source_socket.node
    if node.bl_idname != "YUGUS_NODE_expression_bind":
        return None, None
    return (
        _linked_source(node.inputs.get("Expressions")),
        _linked_source(node.inputs.get("Expression Map")),
    )


def _connect_if_free(tree, source, destination):
    if source is not None and destination is not None and not destination.is_linked:
        tree.links.new(source, destination)


_BONE_TRANSFORM_DEFAULT_NAMES = {
    "YUGUS_NODE_motion_to_rotation": (
        "Motion to Bone Rotation", "Motion to Bone Transform"
    ),
    "YUGUS_NODE_bone_rotation_mix": (
        "Bone Rotation Mix", "Bone Transform Mix"
    ),
    "YUGUS_NODE_apply_rotation": (
        "BoneRotations to Armature", "Bone Transforms to Armature"
    ),
}


def _migrate_bone_transform_labels(node):
    """Rename saved UI in place; identifiers and linked RNA stay intact."""
    for sockets in (node.inputs, node.outputs):
        for socket in sockets:
            if (
                socket.bl_idname == "YUGUS_NS_bone_rotation"
                and socket.name == "Bone Rotation"
            ):
                socket.name = "Bone Transform"
    names = _BONE_TRANSFORM_DEFAULT_NAMES.get(node.bl_idname)
    if names is None or node.label:
        return
    previous, current = names
    match = re.fullmatch(re.escape(previous) + r"(\.\d{3,})?", node.name)
    if match is not None:
        node.name = current + (match.group(1) or "")


def ensure_current_sockets():
    """Upgrade socket layouts stored by earlier yugu_node_studio versions."""
    from .availability import is_available
    from . import mixer
    node_groups = getattr(getattr(bpy, "data", None), "node_groups", None)
    if node_groups is None:
        # During Extension enable/reinstall Blender exposes _RestrictData.
        # Socket migration must wait until normal blend data is available.
        return False
    trees = tuple(
        tree for tree in node_groups if tree.bl_idname == TREE_ID
    )
    for tree in trees:
        current_nodes = tuple(node for node in tree.nodes if is_available(node))
        for node in current_nodes:
            _migrate_bone_transform_labels(node)
            ensure_numeric_parameter(node)
            ensure_source_connection_inputs(node)
            if node.bl_idname == "YUGUS_NODE_expression_smooth":
                ensure_expression_smooth_names(node)
            if node.bl_idname == mixer.NODE:
                mixer.refresh_layout(node)
            if node.bl_idname == "YUGUS_NODE_physics_solver":
                from . import physics
                physics.ensure_solver_sockets(node)
            if node.bl_idname in {"YUGUS_NODE_spring_simulation", "YUGUS_NODE_physics_solver"}:
                _ensure_output(node, "YUGUS_NS_simulation_data", "Simulation Data")
        for node in current_nodes:
            if node.bl_idname == "YUGUS_NODE_vrm_data":
                _ensure_input(node, "YUGUS_NS_armatures", "Armature")
                _ensure_output(node, "YUGUS_NS_armatures", "Armatures")
                _ensure_output(node, "YUGUS_NS_objects", "Objects")
                _ensure_reload_input(node)
            elif node.bl_idname == "YUGUS_NODE_apply_rotation":
                previous = node.inputs.get("Trigger")
                if previous is not None and node.inputs.get("Enter") is None:
                    previous.name = "Enter"
                _ensure_input(node, "YUGUS_NS_update_event", "Enter")
                _ensure_output(node, "YUGUS_NS_update_event", "End")
                _ensure_input(node, "YUGUS_NS_armatures", "Armatures")
            elif node.bl_idname == "YUGUS_NODE_apply_shapes":
                _ensure_input(node, "YUGUS_NS_objects", "Objects")
            elif node.bl_idname == "YUGUS_NODE_chat_list":
                _ensure_input(node, "YUGUS_NS_image", "Avatar")
            elif node.bl_idname == "YUGUS_NODE_data_viewer":
                _ensure_input(node, "YUGUS_NS_simulation_data", "Simulation Data")
        for node in current_nodes:
            if node.bl_idname != "YUGUS_NODE_blender_output":
                continue
            motion_source = _linked_source(node.inputs.get("Humanoid Motion"))
            bone_map_source = _linked_source(node.inputs.get("Bone Map"))
            expression_source = _linked_source(node.inputs.get("Expressions"))
            expression_map_source = _linked_source(node.inputs.get("Expression Map"))

            legacy_pose_source = _linked_source(node.inputs.get("Native Pose"))
            legacy_expression_source = _linked_source(
                node.inputs.get("Native Expressions")
            )
            if legacy_pose_source is not None:
                traced_motion, traced_bone_map = _trace_native_pose(
                    legacy_pose_source
                )
                motion_source = motion_source or traced_motion
                bone_map_source = bone_map_source or traced_bone_map
            if legacy_expression_source is not None:
                traced_expression, traced_expression_map = _trace_native_expressions(
                    legacy_expression_source
                )
                expression_source = expression_source or traced_expression
                expression_map_source = expression_map_source or traced_expression_map

            for name in ("Native Pose", "Native Expressions"):
                socket = node.inputs.get(name)
                if socket is not None:
                    node.inputs.remove(socket)
            motion_input = _ensure_input(node, SOCKET_MOTION, "Humanoid Motion")
            bone_map_input = _ensure_input(node, SOCKET_BONE_MAP, "Bone Map")
            expression_input = _ensure_input(node, SOCKET_EXPRESSIONS, "Expressions")
            expression_map_input = _ensure_input(
                node, SOCKET_EXPRESSION_MAP, "Expression Map"
            )
            _connect_if_free(tree, motion_source, motion_input)
            _connect_if_free(tree, bone_map_source, bone_map_input)
            _connect_if_free(tree, expression_source, expression_input)
            _connect_if_free(tree, expression_map_source, expression_map_input)

        for node in current_nodes:
            if node.bl_idname == "YUGUS_NODE_bone_map_value":
                ensure_bone_map_entries(node)
            if node.bl_idname in {"YUGUS_NODE_objects_value", "YUGUS_NODE_apply_shapes"}:
                from .targets import migrate_legacy_collection
                migrate_legacy_collection(node)
            elif node.bl_idname == "YUGUS_NODE_action_source":
                _ensure_input(node, SOCKET_TRIGGER, "Trigger", 1.0)
            elif node.bl_idname == "YUGUS_NODE_vmc_source":
                _ensure_bottom_trigger_input(node, "Connect")
                _ensure_bottom_trigger_input(node, "Disconnect")
            elif node.bl_idname == "YUGUS_NODE_ifacial_source":
                ensure_ifacial_blendshape_masks(node)
                _ensure_bottom_trigger_input(node, "Connect")
                _ensure_bottom_trigger_input(node, "Disconnect")
                _ensure_bottom_trigger_input(node, "Calibrate")
            elif node.bl_idname == "YUGUS_NODE_head_profile":
                if not node.is_property_set("preset"):
                    _head_profile_values_updated(node, None)
            elif node.bl_idname in {
                "YUGUS_NODE_motion_smooth",
                "YUGUS_NODE_motion_weight",
                "YUGUS_NODE_motion_mix",
            }:
                _ensure_input(node, SOCKET_MOTION_MASK, "Mask")
            elif node.bl_idname == "YUGUS_NODE_spring_simulation":
                _ensure_input(node, SOCKET_SPRING_DATA, "Spring Data")
                # An existing typed output may have a user-defined name. Keep
                # its RNA and links rather than adding a duplicate output.
                if not any(
                    socket.bl_idname == "YUGUS_NS_bone_rotation"
                    for socket in node.outputs
                ):
                    _ensure_output(node, "YUGUS_NS_bone_rotation", "Bone Transform")
                native_input = node.inputs.get("Native Pose")
                native_output = node.outputs.get("Native Pose")
                if native_input is not None:
                    node.inputs.remove(native_input)
                if native_output is not None:
                    node.outputs.remove(native_output)
                _ensure_reload_input(node)

        for node in current_nodes:
            if node.bl_idname != "YUGUS_NODE_humanoid_mask":
                continue
            old_input = node.inputs.get("Motion")
            old_output = node.outputs.get("Motion")
            upstream = None
            destinations = []
            if old_input is not None and old_input.is_linked:
                links = tuple(old_input.links)
                upstream = links[0].from_socket if links else None
            if old_output is not None:
                destinations = [link.to_socket for link in tuple(old_output.links)]
            if old_input is not None:
                node.inputs.remove(old_input)
            if old_output is not None:
                node.outputs.remove(old_output)
            mask_output = _ensure_output(node, SOCKET_MOTION_MASK, "Mask")

            for destination in destinations:
                if (
                    upstream is not None
                    and destination.bl_idname == SOCKET_MOTION
                    and not destination.is_linked
                ):
                    tree.links.new(upstream, destination)
                mask_input = destination.node.inputs.get("Mask")
                if (
                    mask_input is not None
                    and mask_input.bl_idname == SOCKET_MOTION_MASK
                    and not mask_input.is_linked
                ):
                    tree.links.new(mask_output, mask_input)
    from .flow import migrate_flow_ui
    migrate_flow_ui()
    return True
