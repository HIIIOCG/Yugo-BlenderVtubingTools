"""Background network receivers. Threads only store plain Python values."""

from __future__ import annotations

import codecs
import socket
import threading
import time

from mathutils import Quaternion, Vector

from .constants import ARKIT_BLENDSHAPES, HUMANOID_BY_CASEFOLD, normalize_name
from .data import ExpressionFrame, HumanoidMotion, HumanoidTransform
from .protocols import (
    IFACIAL_START_COMMAND,
    IFACIAL_STOP_COMMAND,
    IFacialFrameBuffer,
    ProtocolError,
    decode_osc_packet,
    parse_ifacial_frame,
    parse_vmc_expression,
    parse_vmc_transform,
)


class VMCReceiver:
    def __init__(self, address, port):
        self.address = address
        self.port = int(port)
        self.socket = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.bones = {}
        self.root = None
        self.expressions = {}
        self.hips_origin = None
        self.last_packet_time = 0.0
        self.error = ""

    def start(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket = receiver
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        receiver.bind((self.address, self.port))
        receiver.settimeout(0.2)
        self.thread = threading.Thread(
            target=self._run, name=f"YuguNode-VMC-{self.port}", daemon=True
        )
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                packet, _peer = self.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stop_event.is_set():
                    self.error = str(error)
                break
            try:
                messages = decode_osc_packet(packet)
            except ProtocolError as error:
                self.error = str(error)
                continue
            with self.lock:
                for message in messages:
                    transform = parse_vmc_transform(message)
                    if transform is not None:
                        self.bones[transform.name] = transform
                        continue
                    root = parse_vmc_transform(message, root=True)
                    if root is not None:
                        self.root = root
                        continue
                    expression = parse_vmc_expression(message)
                    if expression is not None:
                        self.expressions[expression[0]] = expression[1]
                self.last_packet_time = time.monotonic()
                self.error = ""

    def stop(self):
        self.stop_event.set()
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        if (self.thread is not None and self.thread is not threading.current_thread()
                and self.thread.is_alive()):
            self.thread.join(timeout=0.5)
        self.socket = None

    def snapshot(self):
        with self.lock:
            bones = dict(self.bones)
            root = self.root
            expressions = dict(self.expressions)
        motion = HumanoidMotion()
        for incoming, transform in bones.items():
            canonical = HUMANOID_BY_CASEFOLD.get(incoming.casefold())
            if canonical is None:
                continue
            converted = _vmc_transform(transform)
            if converted is None:
                continue
            location, rotation = converted
            if canonical == "hips":
                if self.hips_origin is None:
                    self.hips_origin = location.copy()
                location = location - self.hips_origin
            else:
                location = None
            motion.bones[canonical] = HumanoidTransform(rotation, location)
        if root is not None:
            converted = _vmc_transform(root)
            if converted is not None:
                motion.root = HumanoidTransform(converted[1], converted[0])
        return motion, ExpressionFrame(
            {normalize_name(name): float(value) for name, value in expressions.items()}
        )


def _vmc_transform(transform):
    px, py, pz = transform.position
    qx, qy, qz, qw = transform.rotation
    rotation = Quaternion((qw, qx, qz, -qy))
    if rotation.magnitude < 1.0e-8:
        return None
    rotation.normalize()
    return Vector((-px, -pz, py)), rotation


class IFacialReceiver:
    def __init__(self, device_address, device_port, listen_address, listen_port):
        self.device_address = device_address
        self.device_port = int(device_port)
        self.listen_address = listen_address
        self.listen_port = int(listen_port)
        self.socket = None
        self.client = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.expressions = {}
        self.head = None
        self.last_packet_time = 0.0
        self.error = ""
        self.last_start_time = None

    def start(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket = server
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.listen_address, self.listen_port))
        server.listen(1)
        server.settimeout(0.2)
        self.thread = threading.Thread(
            target=self._run,
            name=f"YuguNode-iFacial-{self.listen_port}",
            daemon=True,
        )
        self.thread.start()
        self.request_start(force=True)

    def request_start(self, *, force=False):
        """Retry a missing phone stream without replacing a healthy listener."""
        client = self.client
        if client is not None and client.fileno() >= 0:
            return
        now = time.monotonic()
        if not force and self.last_start_time is not None and now - self.last_start_time < 1.0:
            return
        self._send(IFACIAL_START_COMMAND)
        self.last_start_time = now

    def _send(self, command):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(
                command.encode("utf-8"), (self.device_address, self.device_port)
            )

    def _run(self):
        while not self.stop_event.is_set():
            try:
                client, _address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stop_event.is_set():
                    self.error = str(error)
                break
            self.client = client
            client.settimeout(0.2)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = IFacialFrameBuffer()
            try:
                while not self.stop_event.is_set():
                    try:
                        chunk = client.recv(16384)
                    except socket.timeout:
                        continue
                    except OSError as error:
                        if not self.stop_event.is_set():
                            self.error = str(error)
                        break
                    if not chunk:
                        break
                    try:
                        frames = buffer.feed(decoder.decode(chunk))
                    except ProtocolError as error:
                        self.error = str(error)
                        break
                    for frame in frames:
                        expressions, head = parse_ifacial_frame(frame)
                        with self.lock:
                            if expressions:
                                self.expressions = expressions
                            if head is not None:
                                self.head = head
                            self.last_packet_time = time.monotonic()
                            self.error = ""
            finally:
                try:
                    client.close()
                except OSError:
                    pass
                self.client = None

    def stop(self):
        self.stop_event.set()
        try:
            self._send(IFACIAL_STOP_COMMAND)
        except OSError:
            pass
        for sock in (self.client, self.socket):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if (self.thread is not None and self.thread is not threading.current_thread()
                and self.thread.is_alive()):
            self.thread.join(timeout=0.5)

    def snapshot(self, calibration, mirror=False):
        with self.lock:
            expressions = dict(self.expressions)
            head = self.head
        motion = HumanoidMotion()
        if head is not None:
            degrees = tuple(_wrap(value - offset) for value, offset in zip(head, calibration))
            if mirror:
                pitch, yaw, roll = degrees
                degrees = (pitch, -yaw, -roll)
            rotation = _ifacial_rotation(degrees)
            motion.bones["head"] = HumanoidTransform(rotation, None)
        raw = {name: 0.0 for name in ARKIT_BLENDSHAPES}
        raw.update({str(name): float(value) for name, value in expressions.items()})
        return motion, raw


def _wrap(value):
    return (float(value) + 180.0) % 360.0 - 180.0


def _ifacial_rotation(degrees):
    import math

    x, y, z = (math.radians(float(value)) for value in degrees)
    unity = (
        Quaternion((0.0, 1.0, 0.0), y)
        @ Quaternion((1.0, 0.0, 0.0), x)
        @ Quaternion((0.0, 0.0, 1.0), z)
    )
    result = Quaternion((unity.w, unity.x, unity.z, -unity.y))
    result.normalize()
    return result


_vmc_receivers: dict[str, VMCReceiver] = {}
_ifacial_receivers: dict[str, IFacialReceiver] = {}


def _receiver_is_running(receiver):
    return bool(
        receiver is not None
        and not receiver.stop_event.is_set()
        and receiver.socket is not None
        and receiver.socket.fileno() >= 0
        and receiver.thread is not None
        and receiver.thread.is_alive()
    )


def start_vmc(uid, address, port):
    """Keep an unchanged healthy receiver; reconnect when settings or state differ."""
    address, port = str(address).strip(), int(port)
    receiver = _vmc_receivers.get(uid)
    if (_receiver_is_running(receiver)
            and (receiver.address, receiver.port) == (address, port)):
        return
    stop(uid)
    receiver = VMCReceiver(address, port)
    try:
        receiver.start()
    except Exception:
        receiver.stop()
        raise
    _vmc_receivers[uid] = receiver


def start_ifacial(uid, device_address, device_port, listen_address, listen_port, *, retry=False):
    """Apply current settings and retry the handshake if the phone stream is absent."""
    settings = (str(device_address).strip(), int(device_port),
                str(listen_address).strip(), int(listen_port))
    receiver = _ifacial_receivers.get(uid)
    if (_receiver_is_running(receiver)
            and (receiver.device_address, receiver.device_port,
                 receiver.listen_address, receiver.listen_port) == settings):
        receiver.request_start(force=retry)
        return
    stop(uid)
    receiver = IFacialReceiver(*settings)
    try:
        receiver.start()
    except Exception:
        receiver.stop()
        raise
    _ifacial_receivers[uid] = receiver


def stop(uid):
    receiver = _vmc_receivers.pop(uid, None)
    if receiver is None:
        receiver = _ifacial_receivers.pop(uid, None)
    if receiver is not None:
        receiver.stop()


def is_running(uid):
    return _receiver_is_running(_vmc_receivers.get(uid) or _ifacial_receivers.get(uid))


def status(uid):
    receiver = _vmc_receivers.get(uid) or _ifacial_receivers.get(uid)
    if receiver is None:
        return {"running": False, "error": "", "age": None}
    age = (
        time.monotonic() - receiver.last_packet_time
        if receiver.last_packet_time
        else None
    )
    return {"running": _receiver_is_running(receiver), "error": receiver.error, "age": age}


def source_snapshot(uid, calibration=(0.0, 0.0, 0.0), mirror=False):
    receiver = _vmc_receivers.get(uid)
    if receiver is not None:
        return receiver.snapshot()
    receiver = _ifacial_receivers.get(uid)
    if receiver is not None:
        motion, raw = receiver.snapshot(calibration, mirror)
        return motion, ExpressionFrame(
            {
                normalize_name(name): float(value) / 100.0
                for name, value in raw.items()
            }
        )
    return HumanoidMotion(), ExpressionFrame()


def ifacial_source_snapshot(uid, calibration=(0.0, 0.0, 0.0), mirror=False):
    receiver = _ifacial_receivers.get(uid)
    if receiver is None:
        return HumanoidMotion(), {}
    return receiver.snapshot(calibration, mirror)


def ifacial_head(uid):
    receiver = _ifacial_receivers.get(uid)
    if receiver is None:
        return None
    with receiver.lock:
        return tuple(receiver.head) if receiver.head is not None else None


def stop_all():
    for uid in tuple(_vmc_receivers):
        stop(uid)
    for uid in tuple(_ifacial_receivers):
        stop(uid)
