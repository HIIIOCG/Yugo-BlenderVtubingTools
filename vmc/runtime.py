"""Shared UDP VMC receivers feeding multiple per-avatar Motion Layers."""

from __future__ import annotations

import socket
import time

import bpy
from mathutils import Quaternion, Vector

from .. import motion_mixer
from ..motion_processing import QuaternionSpring, VectorSpring
from ..state import ensure_runtime_id, layer_locator, resolve_layer_locator
from .mapping import build_vrm_humanoid_map, is_vrm_armature, mask_property_for_vmc_name
from .protocol import ProtocolError, decode_packet, parse_bone_transform, parse_root_transform


APPLY_INTERVAL = 1.0 / 60.0
CONNECTED_TIMEOUT = 2.0
MAX_DATAGRAMS_PER_TICK = 256

_receivers = {}
_layer_receivers = {}
_last_redraw_time = 0.0
_undo_in_progress = False


def _disconnect_hips_from_parent(context, armature, hips_bone_name) -> bool:
    data_bone = armature.data.bones.get(hips_bone_name)
    if data_bone is None:
        raise RuntimeError("The VRM Hips bone no longer exists")
    if data_bone.parent is None or not data_bone.use_connect:
        return False

    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    previous_selected = list(context.selected_objects)
    previous_mode = previous_active.mode if previous_active is not None else "OBJECT"
    try:
        if previous_active is not None and previous_active.mode != "OBJECT":
            with context.temp_override(object=previous_active, active_object=previous_active):
                bpy.ops.object.mode_set(mode="OBJECT")
        for selected in list(context.selected_objects):
            selected.select_set(False)
        armature.select_set(True)
        view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bone = armature.data.edit_bones.get(hips_bone_name)
        if edit_bone is None:
            raise RuntimeError("Could not edit the VRM Hips bone")
        edit_bone.use_connect = False
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        if getattr(armature, "mode", "OBJECT") != "OBJECT":
            try:
                with context.temp_override(object=armature, active_object=armature):
                    bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        for selected in list(context.selected_objects):
            selected.select_set(False)
        for selected in previous_selected:
            try:
                selected.select_set(True)
            except ReferenceError:
                pass
        if previous_active is not None:
            try:
                view_layer.objects.active = previous_active
                if previous_mode != "OBJECT":
                    with context.temp_override(object=previous_active, active_object=previous_active):
                        bpy.ops.object.mode_set(mode=previous_mode)
            except (ReferenceError, RuntimeError):
                pass
    return True


def _convert_vmc_transform(transform):
    """Convert Unity left-handed Y-up VMC values to Blender Z-up values."""
    px, py, pz = transform.position
    qx, qy, qz, qw = transform.rotation
    location = Vector((-px, -pz, py))
    rotation = Quaternion((qw, qx, qz, -qy))
    if rotation.magnitude < 1.0e-6:
        return None
    rotation.normalize()
    return location, rotation


def _redraw_view3d() -> None:
    global _last_redraw_time
    now = time.monotonic()
    if now - _last_redraw_time < 0.25:
        return
    _last_redraw_time = now
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


class _Target:
    def __init__(self, scene, layer, armature, bone_map):
        self.scene = scene
        self.layer = layer
        self.layer_key = ensure_runtime_id(layer)
        self.layer_pointer = layer.as_pointer()
        self.locator = layer_locator(scene, layer)
        self.armature = armature
        self.bone_map = bone_map
        self.hips_sender_rest = None
        self.root_location_spring = VectorSpring()
        self.root_rotation_spring = QuaternionSpring()
        self.bone_rotation_springs = {}
        self.bone_location_springs = {}
        self.applied_bone_count = 0

    def resolve_live(self):
        """Reacquire CollectionProperty items instead of touching cached RNA."""

        resolved = resolve_layer_locator(self.locator)
        if resolved is None:
            self.scene = None
            self.layer = None
            self.armature = None
            return False
        scene, _avatar, layer, armature = resolved
        if str(layer.source_type) != "VMC":
            return False
        if armature is None or getattr(armature, "type", "") != "ARMATURE":
            self.scene = None
            self.layer = None
            self.armature = None
            return False
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.layer_key = ensure_runtime_id(layer)
        self.layer_pointer = layer.as_pointer()
        return True

    def detach_for_undo(self):
        if self.scene is not None and self.layer is not None:
            self.locator = layer_locator(self.scene, self.layer) or self.locator
        self.scene = None
        self.layer = None
        self.armature = None

    def rebind_after_undo(self):
        if not self.resolve_live():
            return False
        bone_map = build_vrm_humanoid_map(self.armature)
        if not bone_map:
            return False
        self.bone_map = bone_map
        self.layer.connected = True
        return True

    def smooth(self, property_name):
        try:
            mask = getattr(self.layer, property_name, None)
            if mask is not None and self.layer.vmc_data_mode == "SMOOTH":
                return float(mask.smooth)
        except (AttributeError, ReferenceError):
            pass
        return 0.0

    def root_pose(self, transform, delta_time):
        converted = _convert_vmc_transform(transform)
        if converted is None:
            return None
        location, rotation = converted
        smooth = self.smooth("root")
        return {
            "region": "root",
            "location": self.root_location_spring.step(location, smooth, delta_time),
            "rotation": self.root_rotation_spring.step(rotation, smooth, delta_time),
        }

    def bone_pose(self, transforms, delta_time):
        bones = {}
        applied = 0
        for incoming_name, transform in transforms.items():
            key = incoming_name.casefold()
            target_name = self.bone_map.get(key)
            property_name = mask_property_for_vmc_name(incoming_name)
            if not target_name or property_name is None:
                continue
            pose_bone = self.armature.pose.bones.get(target_name)
            if pose_bone is None:
                continue
            converted = _convert_vmc_transform(transform)
            if converted is None:
                continue
            vmc_location, vmc_rotation = converted
            rest_rotation = pose_bone.bone.matrix_local.to_3x3().normalized().to_quaternion()
            local_rotation = rest_rotation.inverted() @ vmc_rotation @ rest_rotation
            local_rotation.normalize()
            smooth = self.smooth(property_name)
            rotation_spring = self.bone_rotation_springs.setdefault(key, QuaternionSpring())
            virtual_location = None
            if key == "hips":
                if self.hips_sender_rest is None:
                    self.hips_sender_rest = vmc_location.copy()
                rest_matrix = pose_bone.bone.matrix_local.to_3x3().normalized()
                hips_location = rest_matrix.inverted() @ (vmc_location - self.hips_sender_rest)
                location_spring = self.bone_location_springs.setdefault("hips", VectorSpring())
                virtual_location = location_spring.step(hips_location, smooth, delta_time)
            bones[target_name] = {
                "region": property_name,
                "rotation": rotation_spring.step(local_rotation, smooth, delta_time),
                "location": virtual_location,
            }
            applied += 1
        self.applied_bone_count = applied
        return bones

    def submit(self, root_buffer, bone_buffer, delta_time):
        if not self.resolve_live():
            raise RuntimeError("The VMC Motion Layer no longer exists")
        root = self.root_pose(root_buffer, delta_time) if root_buffer is not None else None
        bones = self.bone_pose(bone_buffer, delta_time) if bone_buffer else {}
        motion_mixer.submit_layer_pose(self.layer, self.armature, bones=bones, root=root)
        self.layer.connected = True

    def clear(self):
        if not self.resolve_live():
            return
        try:
            self.layer.connected = False
            motion_mixer.clear_layer(self.layer)
            motion_mixer.apply_scene(self.scene, self.armature)
        except (ReferenceError, RuntimeError):
            pass


class _Receiver:
    def __init__(self, address, port):
        self.address = address
        self.port = port
        self.key = (address, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.bind_address = address
        try:
            self.socket.bind(self.key)
        except OSError as error:
            unavailable_address = getattr(error, "winerror", None) == 10049 or getattr(error, "errno", None) in {99, 10049}
            if not unavailable_address or address == "0.0.0.0":
                self.socket.close()
                raise
            self.bind_address = "0.0.0.0"
            self.socket.bind((self.bind_address, port))
        self.targets = {}
        self.bone_buffer = {}
        self.root_buffer = None
        self.last_packet_time = 0.0
        self.last_apply_time = 0.0
        self.peer_address = ""
        self.last_error = ""
        self.packet_count = 0
        self.message_count = 0
        self.protocol_error_count = 0
        self.received_bones = set()
        self.timer = self._timer
        bpy.app.timers.register(self.timer, first_interval=0.0, persistent=True)

    def add_target(self, target):
        self.targets[target.layer_key] = target

    def remove_target(self, layer_key, clear=True):
        target = self.targets.pop(layer_key, None)
        if target is not None and clear:
            target.clear()

    def poll(self):
        for _index in range(MAX_DATAGRAMS_PER_TICK):
            try:
                data, address = self.socket.recvfrom(65535)
            except BlockingIOError:
                break
            except OSError as error:
                self.last_error = f"UDP receive failed: {error}"
                break
            try:
                messages = decode_packet(data)
            except ProtocolError as error:
                self.protocol_error_count += 1
                self.last_error = str(error)
                continue
            self.packet_count += 1
            self.message_count += len(messages)
            self.last_packet_time = time.monotonic()
            self.peer_address = f"{address[0]}:{address[1]}"
            for message in messages:
                bone_transform = parse_bone_transform(message)
                if bone_transform is not None:
                    key = bone_transform.name.casefold()
                    self.bone_buffer[key] = bone_transform
                    self.received_bones.add(key)
                    continue
                root_transform = parse_root_transform(message)
                if root_transform is not None:
                    self.root_buffer = root_transform

    def _timer(self):
        if self.socket is None:
            return None
        if not bpy.app.online_access:
            stop()
            return None
        self.poll()
        if _undo_in_progress:
            return APPLY_INTERVAL
        now = time.monotonic()
        delta_time = min(max(now - self.last_apply_time, 0.0), 0.1) if self.last_apply_time else APPLY_INTERVAL
        self.last_apply_time = now
        for layer_key, target in tuple(self.targets.items()):
            try:
                target.submit(self.root_buffer, dict(self.bone_buffer), delta_time)
                if target.layer_key != layer_key:
                    self.targets.pop(layer_key, None)
                    self.targets[target.layer_key] = target
                    _layer_receivers.pop(layer_key, None)
                    _layer_receivers[target.layer_key] = self
            except (ReferenceError, RuntimeError, ValueError) as error:
                self.last_error = f"Could not apply VMC pose: {error}"
                # A failed submit can mean Blender has already freed the
                # cached Layer RNA. Keep the UDP receiver and locator alive so
                # a later Undo/Redo state can rebind it, and never call clear().
        _redraw_view3d()
        return APPLY_INTERVAL if self.targets else None

    def close(self):
        receiver = self.socket
        self.socket = None
        if receiver is not None:
            try:
                receiver.close()
            except OSError:
                pass
        try:
            if bpy.app.timers.is_registered(self.timer):
                bpy.app.timers.unregister(self.timer)
        except ValueError:
            pass
        for target in tuple(self.targets.values()):
            target.clear()
        self.targets.clear()


def _endpoint(layer):
    listen_address = str(layer.address).strip()
    if not listen_address:
        raise RuntimeError("Address cannot be empty")
    try:
        listen_port = int(str(layer.port).strip())
    except ValueError as error:
        raise RuntimeError("Port must be a number") from error
    if not 1 <= listen_port <= 65535:
        raise RuntimeError("Port must be between 1 and 65535")
    return listen_address, listen_port


def start(context, scene, layer, armature) -> None:
    if is_layer_running(layer):
        raise RuntimeError("This VMC layer is already connected")
    if not bpy.app.online_access:
        raise RuntimeError(
            "Online access is disabled. Enable Allow Online Access in Blender "
            "Preferences before starting a VMC receiver"
        )
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        raise RuntimeError("Select a VRM Armature first")
    if not is_vrm_armature(armature):
        raise RuntimeError("The selected armature has no VRM Add-on data")
    bone_map = build_vrm_humanoid_map(armature)
    if not bone_map:
        raise RuntimeError("No VRM humanoid bone assignments were found")
    hips_bone_name = bone_map.get("hips")
    if not hips_bone_name:
        raise RuntimeError("The selected VRM has no assigned Hips bone")

    endpoint = _endpoint(layer)
    receiver = _receivers.get(endpoint)
    if receiver is None:
        receiver = _Receiver(*endpoint)
        _receivers[endpoint] = receiver
    try:
        _disconnect_hips_from_parent(context, armature, hips_bone_name)
        target = _Target(scene, layer, armature, bone_map)
        receiver.add_target(target)
        _layer_receivers[target.layer_key] = receiver
        layer.connected = True
    except Exception:
        if not receiver.targets:
            receiver.close()
            _receivers.pop(endpoint, None)
        raise


def stop(layer=None) -> None:
    if layer is None:
        for receiver in tuple(_receivers.values()):
            receiver.close()
        _receivers.clear()
        _layer_receivers.clear()
        _redraw_view3d()
        return
    layer_key = ensure_runtime_id(layer)
    if not layer_key:
        return
    receiver = _layer_receivers.pop(layer_key, None)
    if receiver is None:
        return
    receiver.remove_target(layer_key)
    if not receiver.targets:
        receiver.close()
        _receivers.pop(receiver.key, None)
    _redraw_view3d()


def is_running() -> bool:
    return bool(_layer_receivers)


def is_layer_running(layer) -> bool:
    if layer is None:
        return False
    layer_key = ensure_runtime_id(layer)
    return bool(layer_key and layer_key in _layer_receivers)


def status(layer=None) -> dict[str, object]:
    receiver = None
    target = None
    if layer is not None:
        layer_key = ensure_runtime_id(layer)
        receiver = _layer_receivers.get(layer_key)
        target = receiver.targets.get(layer_key) if receiver else None
    elif _receivers:
        receiver = next(iter(_receivers.values()))
    packet_age = time.monotonic() - receiver.last_packet_time if receiver is not None and receiver.last_packet_time else None
    return {
        "running": receiver is not None,
        "connected": bool(receiver is not None and packet_age is not None and packet_age <= CONNECTED_TIMEOUT),
        "peer_address": receiver.peer_address if receiver else "",
        "last_error": receiver.last_error if receiver else "",
        "packet_count": receiver.packet_count if receiver else 0,
        "message_count": receiver.message_count if receiver else 0,
        "protocol_error_count": receiver.protocol_error_count if receiver else 0,
        "mapped_bone_count": len(target.bone_map) if target else 0,
        "received_bone_count": len(receiver.received_bones) if receiver else 0,
        "applied_bone_count": target.applied_bone_count if target else 0,
        "packet_age": packet_age,
    }


def prepare_undo():
    global _undo_in_progress
    _undo_in_progress = True
    for receiver in tuple(_receivers.values()):
        for target in tuple(receiver.targets.values()):
            try:
                target.detach_for_undo()
            except (AttributeError, ReferenceError, RuntimeError):
                target.scene = None
                target.layer = None
                target.armature = None


def finish_undo():
    global _undo_in_progress
    try:
        _layer_receivers.clear()
        for receiver in tuple(_receivers.values()):
            rebound_targets = {}
            for old_key, target in tuple(receiver.targets.items()):
                try:
                    target.rebind_after_undo()
                except (AttributeError, ReferenceError, RuntimeError):
                    target.scene = None
                    target.layer = None
                    target.armature = None
                key = target.layer_key if target.layer is not None else old_key
                rebound_targets[key] = target
                if target.layer is not None:
                    _layer_receivers[key] = receiver
            receiver.targets = rebound_targets
    finally:
        _undo_in_progress = False


def register():
    pass


def unregister():
    stop()
