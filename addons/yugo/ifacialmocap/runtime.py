"""iFacialMocap receiver and VRM Expression preview application."""

import codecs
import math
import socket
import threading
import time

import bpy
from mathutils import Quaternion

from .. import motion_mixer, scheduler
from ..blendshape_mask import (
    build_mask_lookup,
    iter_bound_expressions,
    resolve_mask_item,
)
from ..motion_processing import QuaternionSpring, ScalarSpring
from ..predict_tracker import PredictDataTracker
from ..state import find_avatar_for_layer, layer_locator, resolve_layer_locator
from ..vrm import get_vrm_extension
from .protocol import (
    DEFAULT_LISTEN_PORT,
    START_COMMAND,
    STOP_COMMAND,
    FrameBuffer,
    ProtocolError,
    normalize_expression_name,
    parse_blendshapes,
    parse_head_rotation,
    resolve_expression,
)


APPLY_INTERVAL = scheduler.MOTION_INTERVAL
LISTEN_ADDRESS = "0.0.0.0"

_state_lock = threading.Lock()
_stop_event = threading.Event()
_receiver_thread = None
_server_socket = None
_client_socket = None

_running = False
_connected = False
_peer_address = ""
_last_error = ""
_last_packet_time = 0.0
_frame_count = 0
_latest_blendshapes = None
_latest_head_rotation = None

_device_address = ""
_device_port = 0
_target_scene = None
_target_layer = None
_target_armature = None
_target_layer_pointer = 0
_expression_lookup = {}
_driven_expressions = []
_pose_bones = {}
_calibration_rotation = (0.0, 0.0, 0.0)
_auto_calibration_pending = False
_last_redraw_time = 0.0
_last_apply_time = 0.0
_expression_springs = {}
_bone_springs = {}
_predict_tracker = PredictDataTracker()
_predict_last_frame_count = 0
_targets = {}
_undo_in_progress = False


class _TargetState:
    def __init__(self, scene, layer, armature, expression_lookup, pose_bones):
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.layer_pointer = layer.as_pointer()
        self.locator = layer_locator(scene, layer)
        self.expression_lookup = expression_lookup
        self.driven_expressions = []
        self.pose_bones = pose_bones
        self.calibration_rotation = (0.0, 0.0, 0.0)
        self.auto_calibration_pending = True
        self.expression_springs = {}
        self.bone_springs = {}
        self.predict_tracker = PredictDataTracker()
        self.predict_last_frame_count = max(_frame_count - 1, 0)

    def detach_for_undo(self):
        if self.scene is not None and self.layer is not None:
            self.locator = layer_locator(self.scene, self.layer) or self.locator
        self.scene = None
        self.layer = None
        self.armature = None
        self.expression_lookup = {}
        self.driven_expressions.clear()
        self.pose_bones = {}

    def rebind_after_undo(self):
        resolved = resolve_layer_locator(self.locator)
        if resolved is None:
            return False
        scene, _avatar, layer, armature = resolved
        if str(layer.source_type) != "IFACIALMOCAP":
            return False
        if armature is None or getattr(armature, "type", "") != "ARMATURE":
            return False
        try:
            expression_lookup = _build_expression_lookup(armature)
            pose_bones = _resolve_humanoid_pose_bones(armature)
        except (AttributeError, ReferenceError, RuntimeError):
            return False
        if "head" not in pose_bones:
            return False
        self.scene = scene
        self.layer = layer
        self.armature = armature
        self.expression_lookup = expression_lookup
        self.pose_bones = pose_bones
        self.layer_pointer = layer.as_pointer()
        self.layer.connected = is_running()
        return True


def _activate_target(target):
    global _target_scene, _target_layer, _target_armature, _target_layer_pointer
    global _expression_lookup, _driven_expressions, _pose_bones
    global _calibration_rotation, _auto_calibration_pending
    global _expression_springs, _bone_springs, _predict_tracker
    global _predict_last_frame_count

    _target_scene = target.scene
    _target_layer = target.layer
    _target_armature = target.armature
    _target_layer_pointer = target.layer_pointer
    _expression_lookup = target.expression_lookup
    _driven_expressions = target.driven_expressions
    _pose_bones = target.pose_bones
    _calibration_rotation = target.calibration_rotation
    _auto_calibration_pending = target.auto_calibration_pending
    _expression_springs = target.expression_springs
    _bone_springs = target.bone_springs
    _predict_tracker = target.predict_tracker
    _predict_last_frame_count = target.predict_last_frame_count


def _save_target(target):
    target.calibration_rotation = _calibration_rotation
    target.auto_calibration_pending = _auto_calibration_pending
    target.predict_last_frame_count = _predict_last_frame_count


def _clear_active_target():
    global _target_scene, _target_layer, _target_armature, _target_layer_pointer
    global _expression_lookup, _driven_expressions, _pose_bones
    global _calibration_rotation, _auto_calibration_pending
    global _expression_springs, _bone_springs, _predict_tracker
    global _predict_last_frame_count

    _target_scene = None
    _target_layer = None
    _target_armature = None
    _target_layer_pointer = 0
    _expression_lookup = {}
    _driven_expressions = []
    _pose_bones = {}
    _calibration_rotation = (0.0, 0.0, 0.0)
    _auto_calibration_pending = False
    _expression_springs = {}
    _bone_springs = {}
    _predict_tracker = PredictDataTracker()
    _predict_last_frame_count = 0


def _set_error(message):
    global _last_error
    with _state_lock:
        _last_error = message


def _send_device_command(address, port, command):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as command_socket:
        command_socket.sendto(command.encode("utf-8"), (address, port))


def _receiver_loop():
    global _running, _connected, _peer_address, _last_error
    global _last_packet_time, _frame_count, _latest_blendshapes
    global _latest_head_rotation
    global _server_socket, _client_socket

    try:
        while not _stop_event.is_set():
            with _state_lock:
                server_socket = _server_socket
            if server_socket is None:
                break
            try:
                connection, address = server_socket.accept()
            except socket.timeout:
                continue
            except OSError as error:
                if not _stop_event.is_set():
                    _set_error(f"TCP accept failed: {error}")
                break

            connection.settimeout(0.2)
            with _state_lock:
                _client_socket = connection
                _connected = True
                _peer_address = f"{address[0]}:{address[1]}"
                _last_error = ""

            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            frame_buffer = FrameBuffer()
            try:
                while not _stop_event.is_set():
                    try:
                        data = connection.recv(16384)
                    except socket.timeout:
                        continue
                    except OSError as error:
                        if not _stop_event.is_set():
                            _set_error(f"TCP receive failed: {error}")
                        break
                    if not data:
                        break
                    try:
                        frames = frame_buffer.feed(decoder.decode(data))
                    except ProtocolError as error:
                        _set_error(str(error))
                        break
                    for frame in frames:
                        blendshapes = parse_blendshapes(frame)
                        head_rotation = parse_head_rotation(frame)
                        if not blendshapes and head_rotation is None:
                            continue
                        with _state_lock:
                            _latest_blendshapes = blendshapes
                            _latest_head_rotation = head_rotation
                            _frame_count += 1
                            _last_packet_time = time.monotonic()
            finally:
                try:
                    connection.close()
                except OSError:
                    pass
                with _state_lock:
                    if _client_socket is connection:
                        _client_socket = None
                    _connected = False
                    _peer_address = ""
    finally:
        with _state_lock:
            server_socket = _server_socket
            _server_socket = None
            _client_socket = None
            _connected = False
            _peer_address = ""
            _running = False
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass


def _redraw_view3d():
    global _last_redraw_time
    now = time.monotonic()
    if now - _last_redraw_time < scheduler.SYSTEM_INTERVAL:
        return
    _last_redraw_time = now
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _layer_influence():
    layer = _target_layer
    scene = _target_scene
    try:
        if layer is None or not layer.enabled:
            return 0.0
        avatar = find_avatar_for_layer(scene, layer) if scene is not None else None
        if avatar is not None and any(item.solo for item in avatar.motion_layers):
            if not layer.solo:
                return 0.0
        return float(layer.weight)
    except ReferenceError:
        return 0.0


def _smooth_data_enabled():
    layer = _target_layer
    try:
        return layer is not None and layer.ifacial_data_mode == "SMOOTH"
    except ReferenceError:
        return False


def _apply_blendshapes(
    blendshapes, delta_time=0.0, raw_blendshapes=None
):
    influence = _layer_influence()
    layer = _target_layer
    try:
        has_mask = layer is not None and bool(layer.blendshape_masks)
        mask_lookup = build_mask_lookup(layer) if has_mask else {}
    except ReferenceError:
        has_mask = False
        mask_lookup = {}

    if not has_mask:
        for channel_name, percentage in blendshapes.items():
            expression = resolve_expression(channel_name, _expression_lookup)
            if expression is None:
                continue
            value = min(
                max((float(percentage) / 100.0) * influence, 0.0), 1.0
            )
            try:
                expression.preview = value
            except (AttributeError, ReferenceError, TypeError):
                continue
            if expression not in _driven_expressions:
                _driven_expressions.append(expression)
        return

    received = {}
    for channel_name, percentage in blendshapes.items():
        expression = resolve_expression(channel_name, _expression_lookup)
        mask_item = resolve_mask_item(channel_name, mask_lookup)
        if expression is None:
            continue
        if mask_item is None:
            try:
                expression.preview = 0.0
            except (AttributeError, ReferenceError, TypeError):
                pass
            continue
        received[mask_item.normalized_name] = (float(percentage), expression)

    raw_received = {}
    raw_source = blendshapes if raw_blendshapes is None else raw_blendshapes
    for channel_name, percentage in raw_source.items():
        mask_item = resolve_mask_item(channel_name, mask_lookup)
        if mask_item is not None:
            raw_received[mask_item.normalized_name] = float(percentage)

    for mask_item in layer.blendshape_masks:
        raw_value, expression = received.get(
            mask_item.normalized_name,
            (0.0, _expression_lookup.get(mask_item.normalized_name)),
        )
        mask_item.raw_value = raw_received.get(
            mask_item.normalized_name, 0.0
        )
        spring = _expression_springs.setdefault(
            mask_item.normalized_name, ScalarSpring()
        )
        smooth = float(mask_item.smooth) if _smooth_data_enabled() else 0.0
        virtual_value = spring.step(
            (raw_value / 100.0) * float(mask_item.weight),
            smooth,
            delta_time,
        )
        value = virtual_value * influence
        if expression is None:
            mask_item.output_value = value
            continue
        try:
            expression.preview = value
            mask_item.output_value = float(expression.preview)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            mask_item.output_value = value
            continue
        if expression not in _driven_expressions:
            _driven_expressions.append(expression)


def _ifacial_rotation_to_source_quaternion(rotation_degrees):
    rotation_x, rotation_y, rotation_z = (
        math.radians(float(value)) for value in rotation_degrees
    )
    result = (
        Quaternion((0.0, 1.0, 0.0), rotation_y)
        @ Quaternion((1.0, 0.0, 0.0), rotation_x)
        @ Quaternion((0.0, 0.0, 1.0), rotation_z)
    )
    result.normalize()
    return result


def _source_quaternion_to_ifacial_rotation(rotation):
    euler = rotation.to_euler("ZXY")
    return tuple(math.degrees(value) for value in euler)


def _ifacial_rotation_to_blender_quaternion(rotation_degrees):
    """Convert Unity-style Euler XYZ degrees to Blender coordinates."""

    unity_rotation = _ifacial_rotation_to_source_quaternion(rotation_degrees)
    w, x, y, z = unity_rotation
    blender_rotation = Quaternion((w, x, z, -y))
    blender_rotation.normalize()
    return blender_rotation


def _resolve_humanoid_pose_bones(armature):
    extension = get_vrm_extension(armature)
    if extension is None:
        return {}

    version = str(getattr(extension, "spec_version", ""))
    bone_names = {}
    if version == "1.0":
        humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
        human_bones = getattr(humanoid, "human_bones", None)
        for humanoid_name in ("head", "neck", "chest", "spine"):
            human_bone = getattr(human_bones, humanoid_name, None)
            bone_name = str(
                getattr(getattr(human_bone, "node", None), "bone_name", "") or ""
            )
            if bone_name:
                bone_names[humanoid_name] = bone_name
    elif version == "0.0":
        humanoid = getattr(getattr(extension, "vrm0", None), "humanoid", None)
        for human_bone in getattr(humanoid, "human_bones", ()):
            humanoid_name = str(getattr(human_bone, "bone", ""))
            if humanoid_name not in {"head", "neck", "chest", "spine"}:
                continue
            bone_name = str(
                getattr(getattr(human_bone, "node", None), "bone_name", "") or ""
            )
            if bone_name:
                bone_names[humanoid_name] = bone_name

    pose = getattr(armature, "pose", None)
    pose_bones = getattr(pose, "bones", {})
    return {
        humanoid_name: pose_bone
        for humanoid_name, bone_name in bone_names.items()
        if (pose_bone := pose_bones.get(bone_name)) is not None
    }


def _wrap_degrees(value):
    return (float(value) + 180.0) % 360.0 - 180.0


def _calibrated_rotation(rotation_degrees):
    rotation = tuple(
        _wrap_degrees(value - offset)
        for value, offset in zip(rotation_degrees, _calibration_rotation)
    )
    layer = _target_layer
    try:
        mirror = bool(layer and layer.ifacial_mirror)
    except ReferenceError:
        mirror = False
    if mirror:
        pitch, yaw, roll = rotation
        return (pitch, -yaw, -roll)
    return rotation


def _body_rotation_profile(rotation_degrees):
    pitch, yaw, roll = _calibrated_rotation(rotation_degrees)
    layer = _target_layer
    try:
        inverted = bool(layer and layer.ifacial_invert_body_rotation)
        profile = (
            layer.ifacial_inverted_profile
            if inverted
            else layer.ifacial_normal_profile
        ) if layer is not None else None
    except (AttributeError, ReferenceError):
        inverted = True
        profile = None

    if inverted:
        defaults = (
            (0.7, 1.0, 0.4),
            (0.6, -0.4, 0.5),
            (-0.3, 0.35, 0.2),
            (0.85, -0.5, -0.5),
        )
    else:
        defaults = (
            (0.8, 0.8, 0.6),
            (0.5, 0.5, 0.3),
            (0.6, 0.6, 0.5),
            (0.3, 0.3, 0.2),
        )

    def factors(prefix, fallback):
        if profile is None:
            return fallback
        return tuple(
            float(getattr(profile, f"{prefix}_{axis}"))
            for axis in ("pitch", "yaw", "roll")
        )

    head_factors = factors("head", defaults[0])
    neck_factors = factors("neck", defaults[1])
    chest_factors = factors("chest", defaults[2])
    spine_factors = factors("spine", defaults[3])
    source_rotation = (pitch, yaw, roll)
    head = tuple(value * factor for value, factor in zip(
        source_rotation, head_factors
    ))
    neck = tuple(value * factor for value, factor in zip(
        source_rotation, neck_factors
    ))
    chest = tuple(value * factor for value, factor in zip(
        source_rotation, chest_factors
    ))
    spine = tuple(value * factor for value, factor in zip(
        source_rotation, spine_factors
    ))
    return {"head": head, "neck": neck, "chest": chest, "spine": spine}


def _apply_pose_bone_rotation(region_name, rotation_degrees, delta_time=0.0):
    pose_bone = _pose_bones.get(region_name)
    if pose_bone is None:
        return None

    canonical_rotation = _ifacial_rotation_to_blender_quaternion(rotation_degrees)
    rest_rotation = pose_bone.bone.matrix_local.to_3x3().normalized().to_quaternion()
    local_rotation = rest_rotation.inverted() @ canonical_rotation @ rest_rotation
    local_rotation.normalize()

    layer = _target_layer
    try:
        mask = getattr(layer, region_name, None) if layer is not None else None
        smooth = (
            float(mask.smooth)
            if mask is not None and _smooth_data_enabled()
            else 0.0
        )
    except ReferenceError:
        smooth = 0.0
    spring = _bone_springs.setdefault(region_name, QuaternionSpring())
    virtual_rotation = spring.step(local_rotation, smooth, delta_time)
    return pose_bone.name, {
        "region": region_name,
        "rotation": virtual_rotation,
        "location": None,
    }


def _apply_head_rotation(rotation_degrees, delta_time=0.0):
    bones = {}
    for region_name, region_rotation in _body_rotation_profile(rotation_degrees).items():
        contribution = _apply_pose_bone_rotation(
            region_name, region_rotation, delta_time
        )
        if contribution is not None:
            bone_name, value = contribution
            bones[bone_name] = value
    if _target_layer is not None and _target_armature is not None:
        motion_mixer.submit_layer_pose(
            _target_layer, _target_armature, bones=bones
        )


def _apply_active_target(blendshapes, head_rotation, frame_count, now, delta_time):
    global _calibration_rotation, _auto_calibration_pending
    global _predict_last_frame_count

    if frame_count != _predict_last_frame_count:
        frame_delta = max(frame_count - _predict_last_frame_count, 1)
        source_rotation = (
            _ifacial_rotation_to_source_quaternion(head_rotation)
            if head_rotation is not None
            else None
        )
        _predict_tracker.observe(
            blendshapes or {},
            source_rotation,
            repeated_count=frame_delta,
            sample_time=now,
        )
        _predict_last_frame_count = frame_count
        if _auto_calibration_pending and head_rotation is not None:
            _calibration_rotation = tuple(float(value) for value in head_rotation)
            _predict_tracker.set_rest_head(source_rotation)
            _auto_calibration_pending = False

    predicted_blendshapes, predicted_source_rotation = _predict_tracker.evaluate(
        delta_time
    )
    try:
        data_mode = _target_layer.ifacial_data_mode if _target_layer is not None else "RAW"
    except ReferenceError:
        data_mode = "RAW"
    if data_mode == "RAW":
        output_blendshapes = blendshapes
        output_head_rotation = head_rotation
    else:
        output_blendshapes = predicted_blendshapes
        output_head_rotation = (
            _source_quaternion_to_ifacial_rotation(predicted_source_rotation)
            if predicted_source_rotation is not None
            else None
        )
    if output_blendshapes is not None:
        _apply_blendshapes(output_blendshapes, delta_time, raw_blendshapes=blendshapes)
    if output_head_rotation is not None:
        _apply_head_rotation(output_head_rotation, delta_time)


def _apply_timer():
    global _last_apply_time

    if _undo_in_progress:
        return APPLY_INTERVAL

    with _state_lock:
        running = _running
        blendshapes = _latest_blendshapes
        head_rotation = _latest_head_rotation
        frame_count = _frame_count
    if not running:
        for target in tuple(_targets.values()):
            _activate_target(target)
            try:
                target.layer.connected = False
            except ReferenceError:
                pass
            _reset_driven_expressions()
            _reset_body_rotation()
        _clear_active_target()
        _redraw_view3d()
        return None

    now = time.monotonic()
    delta_time = min(max(now - _last_apply_time, 0.0), 0.1) if _last_apply_time else APPLY_INTERVAL
    _last_apply_time = now
    for layer_pointer, target in tuple(_targets.items()):
        try:
            _activate_target(target)
            _apply_active_target(blendshapes, head_rotation, frame_count, now, delta_time)
            target.layer.connected = True
            _save_target(target)
        except (ReferenceError, RuntimeError, ValueError):
            try:
                target.layer.connected = False
                _reset_driven_expressions()
                _reset_body_rotation()
            except (ReferenceError, RuntimeError):
                pass
            _targets.pop(layer_pointer, None)
    _clear_active_target()
    _redraw_view3d()
    return APPLY_INTERVAL if _targets else None


def _reset_driven_expressions():
    for expression in tuple(_driven_expressions):
        try:
            expression.preview = 0.0
        except (AttributeError, ReferenceError, TypeError):
            pass
    _driven_expressions.clear()
    _expression_springs.clear()
    try:
        if _target_layer is not None:
            for item in _target_layer.blendshape_masks:
                item.raw_value = 0.0
                item.output_value = 0.0
    except ReferenceError:
        pass


def _reset_body_rotation():
    if _target_layer is not None:
        motion_mixer.clear_layer(_target_layer)
    if _target_scene is not None and _target_armature is not None:
        motion_mixer.apply_scene(_target_scene, _target_armature)
    _bone_springs.clear()


def _build_expression_lookup(armature):
    extension = get_vrm_extension(armature)
    if extension is None:
        raise RuntimeError("The selected armature has no VRM Add-on data")
    version = str(getattr(extension, "spec_version", ""))
    if version not in {"0.0", "1.0"}:
        raise RuntimeError("Unsupported VRM version")

    expressions = iter_bound_expressions(armature)

    lookup = {}
    duplicates = set()
    for name, expression in expressions:
        normalized = normalize_expression_name(name)
        if not normalized:
            continue
        if normalized in lookup and lookup[normalized] is not expression:
            duplicates.add(normalized)
        else:
            lookup[normalized] = expression
    for normalized in duplicates:
        lookup.pop(normalized, None)
    return lookup


def start(scene, layer, armature):
    global _receiver_thread, _server_socket, _running, _connected
    global _peer_address, _last_error, _last_packet_time, _frame_count
    global _latest_blendshapes, _latest_head_rotation, _device_address, _device_port
    global _last_redraw_time, _last_apply_time

    if is_layer_running(layer):
        raise RuntimeError("This iFacialMocap layer is already connected")
    if armature is None or getattr(armature, "type", "") != "ARMATURE":
        raise RuntimeError("Select a VRM Armature first")

    device_address = str(layer.address).strip()
    if not device_address:
        raise RuntimeError("iPhone Address cannot be empty")
    try:
        device_port = int(str(layer.port).strip())
    except ValueError as error:
        raise RuntimeError("Port must be a number") from error
    if not 1 <= device_port <= 65535:
        raise RuntimeError("Port must be between 1 and 65535")

    expression_lookup = _build_expression_lookup(armature)
    pose_bones = _resolve_humanoid_pose_bones(armature)
    if "head" not in pose_bones:
        raise RuntimeError("The selected VRM has no assigned Head bone")
    if is_running():
        if (_device_address, _device_port) != (device_address, device_port):
            raise RuntimeError(
                "All active iFacialMocap avatar layers must use the same iPhone Address and Port"
            )
    else:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((LISTEN_ADDRESS, DEFAULT_LISTEN_PORT))
            server_socket.listen(1)
            server_socket.settimeout(0.2)
        except Exception:
            server_socket.close()
            raise

        _stop_event.clear()
        with _state_lock:
            _server_socket = server_socket
            _running = True
            _connected = False
            _peer_address = ""
            _last_error = ""
            _last_packet_time = 0.0
            _frame_count = 0
            _latest_blendshapes = None
            _latest_head_rotation = None
        _device_address = device_address
        _device_port = device_port
        _last_redraw_time = 0.0
        _last_apply_time = 0.0
        _receiver_thread = threading.Thread(
            target=_receiver_loop,
            name="Yugo-iFacialMocap-TCP",
            daemon=True,
        )
        _receiver_thread.start()
        try:
            _send_device_command(device_address, device_port, START_COMMAND)
        except Exception:
            stop(send_stop_command=False, reset_expressions=False)
            raise

    target = _TargetState(scene, layer, armature, expression_lookup, pose_bones)
    _targets[target.layer_pointer] = target
    layer.connected = True

def stop(layer=None, send_stop_command=True, reset_expressions=True):
    global _receiver_thread, _server_socket, _client_socket, _running
    global _connected, _peer_address, _latest_blendshapes, _latest_head_rotation
    global _last_apply_time

    if layer is not None:
        try:
            layer_pointer = layer.as_pointer()
        except (AttributeError, ReferenceError):
            return
        target = _targets.pop(layer_pointer, None)
        if target is None:
            return
        _activate_target(target)
        if reset_expressions:
            _reset_driven_expressions()
            _reset_body_rotation()
        try:
            target.layer.connected = False
        except ReferenceError:
            pass
        _clear_active_target()
        if _targets:
            _redraw_view3d()
            return

    with _state_lock:
        was_running = _running
        server_socket = _server_socket
        client_socket = _client_socket
        receiver_thread = _receiver_thread

    if send_stop_command and was_running and _device_address:
        try:
            _send_device_command(_device_address, _device_port, STOP_COMMAND)
        except OSError as error:
            _set_error(f"Could not send stop command: {error}")

    _stop_event.set()
    for active_socket in (client_socket, server_socket):
        if active_socket is None:
            continue
        try:
            active_socket.close()
        except OSError:
            pass
    if receiver_thread is not None and receiver_thread is not threading.current_thread():
        receiver_thread.join(timeout=1.0)

    if reset_expressions:
        for target in tuple(_targets.values()):
            _activate_target(target)
            _reset_driven_expressions()
            _reset_body_rotation()
            try:
                target.layer.connected = False
            except ReferenceError:
                pass
    with _state_lock:
        _receiver_thread = None
        _server_socket = None
        _client_socket = None
        _running = False
        _connected = False
        _peer_address = ""
        _latest_blendshapes = None
        _latest_head_rotation = None
    _targets.clear()
    _clear_active_target()
    _last_apply_time = 0.0


def is_running():
    with _state_lock:
        return _running


def is_layer_running(layer):
    if layer is None or not is_running():
        return False
    try:
        return layer.as_pointer() in _targets
    except ReferenceError:
        return False


def calibrate(layer):
    global _calibration_rotation, _auto_calibration_pending

    if not is_layer_running(layer):
        raise RuntimeError("Connect this iFacialMocap layer before calibration")
    with _state_lock:
        rotation = _latest_head_rotation
    if rotation is None:
        raise RuntimeError("No iFacialMocap head rotation has been received yet")

    target = _targets[layer.as_pointer()]
    _activate_target(target)
    _calibration_rotation = tuple(float(value) for value in rotation)
    _auto_calibration_pending = False
    _predict_tracker.set_rest_head(_ifacial_rotation_to_source_quaternion(rotation))
    _bone_springs.clear()
    _apply_head_rotation(rotation)
    _save_target(target)
    _clear_active_target()


def status(_layer=None):
    with _state_lock:
        packet_age = time.monotonic() - _last_packet_time if _last_packet_time else None
        return {
            "running": _running,
            "connected": _connected,
            "peer_address": _peer_address,
            "last_error": _last_error,
            "frame_count": _frame_count,
            "packet_age": packet_age,
        }


def prepare_undo():
    global _undo_in_progress
    _undo_in_progress = True
    _clear_active_target()
    for target in tuple(_targets.values()):
        try:
            target.detach_for_undo()
        except (AttributeError, ReferenceError, RuntimeError):
            target.scene = None
            target.layer = None
            target.armature = None
            target.expression_lookup = {}
            target.pose_bones = {}


def finish_undo():
    global _undo_in_progress
    try:
        rebound_targets = {}
        for old_pointer, target in tuple(_targets.items()):
            try:
                target.rebind_after_undo()
            except (AttributeError, ReferenceError, RuntimeError):
                target.scene = None
                target.layer = None
                target.armature = None
                target.expression_lookup = {}
                target.pose_bones = {}
            key = target.layer_pointer if target.layer is not None else old_pointer
            rebound_targets[key] = target
        _targets.clear()
        _targets.update(rebound_targets)
        _clear_active_target()
    finally:
        _undo_in_progress = False


def _motion_update(_context, _delta_time):
    _apply_timer()


def register():
    scheduler.add_motion("ifacialmocap", _motion_update, priority=200)


def unregister():
    scheduler.remove_motion("ifacialmocap")
    stop()
