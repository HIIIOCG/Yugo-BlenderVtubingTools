"""Dependency-free VMC/OSC and iFacialMocap protocol parsing."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct

from .constants import ARKIT_BLENDSHAPES, normalize_name


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OscMessage:
    address: str
    arguments: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class VmcTransform:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]


def _aligned(offset):
    return (offset + 3) & ~3


def _read_string(data, offset):
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ProtocolError("OSC string is missing its null terminator")
    try:
        value = data[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("OSC string is not UTF-8") from error
    return value, _aligned(end + 1)


def _unpack(data, offset, fmt):
    size = struct.calcsize(fmt)
    if offset + size > len(data):
        raise ProtocolError("OSC argument exceeds packet")
    return struct.unpack_from(fmt, data, offset)[0], offset + size


def _decode_message(data):
    address, offset = _read_string(data, 0)
    tags, offset = _read_string(data, offset)
    if not address.startswith("/") or not tags.startswith(","):
        raise ProtocolError("Invalid OSC message")
    values = []
    for tag in tags[1:]:
        if tag == "f":
            value, offset = _unpack(data, offset, ">f")
        elif tag == "i":
            value, offset = _unpack(data, offset, ">i")
        elif tag in {"s", "S"}:
            value, offset = _read_string(data, offset)
        elif tag == "T":
            value = True
        elif tag == "F":
            value = False
        else:
            raise ProtocolError(f"Unsupported OSC type tag {tag!r}")
        values.append(value)
    return OscMessage(address, tuple(values))


def _decode_packet(data, depth=0):
    if depth > 8:
        raise ProtocolError("OSC bundle nesting is too deep")
    if not data.startswith(b"#bundle\x00"):
        return [_decode_message(data)]
    if len(data) < 16:
        raise ProtocolError("Invalid OSC bundle")
    offset = 16
    messages = []
    while offset < len(data):
        size, offset = _unpack(data, offset, ">i")
        end = offset + size
        if size <= 0 or end > len(data):
            raise ProtocolError("Invalid OSC bundle element")
        messages.extend(_decode_packet(data[offset:end], depth + 1))
        offset = end
    return messages


def decode_osc_packet(data):
    if not isinstance(data, bytes) or not data:
        raise ProtocolError("OSC packet must be non-empty bytes")
    return _decode_packet(data)


def _finite(values):
    try:
        values = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def parse_vmc_transform(message, root=False):
    expected = "/VMC/Ext/Root/Pos" if root else "/VMC/Ext/Bone/Pos"
    if message.address != expected:
        return None
    arguments = message.arguments
    if len(arguments) >= 8 and isinstance(arguments[0], str):
        name = arguments[0]
        values = _finite(arguments[1:8])
    elif root and len(arguments) >= 7:
        name = "root"
        values = _finite(arguments[:7])
    else:
        return None
    if values is None:
        return None
    return VmcTransform(name, values[:3], values[3:7])


def parse_vmc_expression(message):
    if message.address != "/VMC/Ext/Blend/Val" or len(message.arguments) < 2:
        return None
    name = message.arguments[0]
    values = _finite(message.arguments[1:2])
    if not isinstance(name, str) or not name or values is None:
        return None
    return name, values[0]


IFACIAL_FRAME_DELIMITER = "___iFacialMocap"
IFACIAL_START_COMMAND = (
    "iFacialMocap_UDPTCP_sahuasouryya9218sauhuiayeta91555dy3719"
    "|sendDataVersion=v2"
)
IFACIAL_STOP_COMMAND = "iFacialMocap_UDPTCPSTOP_sahuasouryya9218sauhuiayeta91555dy3719"


def _ifacial_aliases(canonical_name):
    aliases = [canonical_name]
    if canonical_name.endswith("Left"):
        stem = canonical_name[:-4]
        aliases.extend((f"{stem}_L", f"{stem}_Left"))
    elif canonical_name.endswith("Right"):
        stem = canonical_name[:-5]
        aliases.extend((f"{stem}_R", f"{stem}_Right"))
    elif canonical_name.endswith("_L"):
        stem = canonical_name[:-2]
        aliases.extend((f"{stem}Left", f"{stem}_Left"))
    elif canonical_name.endswith("_R"):
        stem = canonical_name[:-2]
        aliases.extend((f"{stem}Right", f"{stem}_Right"))
    return tuple(aliases)


_IFACIAL_CANONICAL_BY_ALIAS = {
    normalize_name(alias): canonical
    for canonical in ARKIT_BLENDSHAPES
    for alias in _ifacial_aliases(canonical)
}


def canonical_ifacial_name(channel_name):
    """Return one stable ARKit name for iFacial ``_L``/``_R`` aliases."""

    channel_name = str(channel_name).strip()
    return _IFACIAL_CANONICAL_BY_ALIAS.get(normalize_name(channel_name), channel_name)


def ifacial_expression_aliases(channel_name):
    """Return canonical and iFacial spellings for an expression channel."""

    canonical = canonical_ifacial_name(channel_name)
    aliases = list(_ifacial_aliases(canonical))
    original = str(channel_name).strip()
    if original and original not in aliases:
        aliases.append(original)
    return tuple(aliases)


class IFacialFrameBuffer:
    def __init__(self, max_size=1024 * 1024):
        self.buffer = ""
        self.max_size = max_size

    def feed(self, chunk):
        self.buffer += chunk
        frames = []
        while True:
            index = self.buffer.find(IFACIAL_FRAME_DELIMITER)
            if index < 0:
                break
            frame = self.buffer[:index].strip("\x00\r\n ")
            self.buffer = self.buffer[index + len(IFACIAL_FRAME_DELIMITER) :]
            if frame:
                frames.append(frame)
        if len(self.buffer) > self.max_size:
            self.buffer = ""
            raise ProtocolError("iFacialMocap frame buffer exceeded limit")
        return frames


def parse_ifacial_frame(frame):
    expressions = {}
    head = None
    for field in frame.split("|"):
        field = field.strip()
        if field.startswith("=head#"):
            values = _finite(field.partition("#")[2].split(",")[:3])
            if values is not None and len(values) == 3:
                head = values
            continue
        if not field or "#" in field or field.startswith("="):
            continue
        if "&" in field:
            name, value = field.split("&", 1)
        elif "-" in field:
            name, value = field.rsplit("-", 1)
        else:
            continue
        values = _finite((value,))
        if name.strip() and values is not None:
            expressions[canonical_ifacial_name(name)] = values[0]
    return expressions, head
