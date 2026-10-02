"""Dependency-free OSC/VMC packet decoding.

Only the transport parser lives here so it can be tested without Blender.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct


DEFAULT_LISTEN_ADDRESS = "127.0.0.1"
DEFAULT_LISTEN_PORT = 39539


class ProtocolError(ValueError):
    """Raised when a UDP datagram is not a valid supported OSC packet."""


@dataclass(frozen=True, slots=True)
class OscMessage:
    address: str
    arguments: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class VmcTransform:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]


def _aligned(offset: int) -> int:
    return (offset + 3) & ~3


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(data):
        raise ProtocolError("OSC string starts outside the packet")

    end = data.find(b"\x00", offset)
    if end < 0:
        raise ProtocolError("OSC string is missing its null terminator")

    try:
        value = data[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("OSC string is not valid UTF-8") from error

    next_offset = _aligned(end + 1)
    if next_offset > len(data):
        raise ProtocolError("OSC string padding exceeds the packet")
    return value, next_offset


def _unpack(data: bytes, offset: int, fmt: str, label: str):
    size = struct.calcsize(fmt)
    end = offset + size
    if end > len(data):
        raise ProtocolError(f"OSC {label} exceeds the packet")
    return struct.unpack_from(fmt, data, offset)[0], end


def _decode_message(data: bytes) -> OscMessage:
    address, offset = _read_string(data, 0)
    if not address.startswith("/"):
        raise ProtocolError("OSC message address must start with '/'")

    type_tags, offset = _read_string(data, offset)
    if not type_tags.startswith(","):
        raise ProtocolError("OSC type tag string must start with ','")

    arguments: list[object] = []
    arrays: list[list[object]] = []

    def append(value: object) -> None:
        if arrays:
            arrays[-1].append(value)
        else:
            arguments.append(value)

    for tag in type_tags[1:]:
        if tag == "i":
            value, offset = _unpack(data, offset, ">i", "int32")
            append(value)
        elif tag == "f":
            value, offset = _unpack(data, offset, ">f", "float32")
            append(value)
        elif tag in {"s", "S"}:
            value, offset = _read_string(data, offset)
            append(value)
        elif tag == "h":
            value, offset = _unpack(data, offset, ">q", "int64")
            append(value)
        elif tag == "d":
            value, offset = _unpack(data, offset, ">d", "float64")
            append(value)
        elif tag == "t":
            value, offset = _unpack(data, offset, ">Q", "timetag")
            append(value)
        elif tag == "c":
            value, offset = _unpack(data, offset, ">I", "character")
            append(chr(value))
        elif tag == "r":
            value, offset = _unpack(data, offset, ">I", "RGBA color")
            append(value)
        elif tag == "m":
            end = offset + 4
            if end > len(data):
                raise ProtocolError("OSC MIDI value exceeds the packet")
            append(data[offset:end])
            offset = end
        elif tag == "b":
            size, offset = _unpack(data, offset, ">i", "blob size")
            if size < 0 or offset + size > len(data):
                raise ProtocolError("OSC blob has an invalid size")
            append(data[offset : offset + size])
            offset = _aligned(offset + size)
            if offset > len(data):
                raise ProtocolError("OSC blob padding exceeds the packet")
        elif tag == "T":
            append(True)
        elif tag == "F":
            append(False)
        elif tag == "N":
            append(None)
        elif tag == "I":
            append(math.inf)
        elif tag == "[":
            arrays.append([])
        elif tag == "]":
            if not arrays:
                raise ProtocolError("OSC array closes without opening")
            value = tuple(arrays.pop())
            append(value)
        else:
            raise ProtocolError(f"Unsupported OSC type tag: {tag!r}")

    if arrays:
        raise ProtocolError("OSC array is missing a closing tag")
    return OscMessage(address, tuple(arguments))


def _decode_packet(data: bytes, depth: int) -> list[OscMessage]:
    if depth > 8:
        raise ProtocolError("OSC bundle nesting is too deep")
    if not data:
        raise ProtocolError("OSC packet is empty")

    if not data.startswith(b"#bundle\x00"):
        return [_decode_message(data)]

    if len(data) < 16:
        raise ProtocolError("OSC bundle is missing its timetag")

    messages: list[OscMessage] = []
    offset = 16
    while offset < len(data):
        element_size, offset = _unpack(data, offset, ">i", "bundle element size")
        if element_size <= 0:
            raise ProtocolError("OSC bundle element size must be positive")
        end = offset + element_size
        if end > len(data):
            raise ProtocolError("OSC bundle element exceeds the packet")
        messages.extend(_decode_packet(data[offset:end], depth + 1))
        offset = end
    return messages


def decode_packet(data: bytes) -> list[OscMessage]:
    """Decode one OSC UDP datagram, including nested bundles."""

    if not isinstance(data, bytes):
        raise TypeError("OSC packet must be bytes")
    return _decode_packet(data, 0)


def _finite_floats(values) -> tuple[float, ...] | None:
    try:
        converted = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    return converted if all(math.isfinite(value) for value in converted) else None


def parse_bone_transform(message: OscMessage) -> VmcTransform | None:
    """Parse `/VMC/Ext/Bone/Pos name px py pz qx qy qz qw`."""

    if message.address != "/VMC/Ext/Bone/Pos" or len(message.arguments) < 8:
        return None
    name = message.arguments[0]
    if not isinstance(name, str) or not name:
        return None
    values = _finite_floats(message.arguments[1:8])
    if values is None:
        return None
    return VmcTransform(name, values[:3], values[3:7])

def parse_root_transform(message: OscMessage) -> VmcTransform | None:
    """Parse the VMC root transform. The standard form includes a name."""

    if message.address != "/VMC/Ext/Root/Pos":
        return None

    arguments = message.arguments
    if len(arguments) >= 8 and isinstance(arguments[0], str):
        name = arguments[0]
        values = _finite_floats(arguments[1:8])
    elif len(arguments) >= 7:
        name = "root"
        values = _finite_floats(arguments[:7])
    else:
        return None

    if values is None:
        return None
    return VmcTransform(name, values[:3], values[3:7])
