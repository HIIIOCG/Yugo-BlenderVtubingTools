"""Pure-Python helpers for the iFacialMocap v2 text protocol."""

import math


FRAME_DELIMITER = "___iFacialMocap"
DEFAULT_DEVICE_PORT = 49983
DEFAULT_LISTEN_PORT = 49986

START_COMMAND = (
    "iFacialMocap_UDPTCP_sahuasouryya9218sauhuiayeta91555dy3719"
    "|sendDataVersion=v2"
)
STOP_COMMAND = "iFacialMocap_UDPTCPSTOP_sahuasouryya9218sauhuiayeta91555dy3719"


class ProtocolError(ValueError):
    pass


class FrameBuffer:
    def __init__(self, max_buffer_size=1024 * 1024):
        self._buffer = ""
        self._max_buffer_size = max_buffer_size

    def feed(self, chunk):
        if not chunk:
            return []

        self._buffer += chunk
        frames = []
        while True:
            delimiter_index = self._buffer.find(FRAME_DELIMITER)
            if delimiter_index < 0:
                break
            frame = self._buffer[:delimiter_index].strip("\x00\r\n ")
            self._buffer = self._buffer[delimiter_index + len(FRAME_DELIMITER) :]
            if frame:
                frames.append(frame)

        if len(self._buffer) > self._max_buffer_size:
            self._buffer = ""
            raise ProtocolError("TCP data exceeded the frame buffer limit")
        return frames


def parse_blendshapes(frame):
    blendshapes = {}
    for field in frame.split("|"):
        field = field.strip()
        if not field or "#" in field or field.startswith("="):
            continue
        if "&" in field:
            name, value_text = field.split("&", 1)
        elif "-" in field:
            name, value_text = field.rsplit("-", 1)
        else:
            continue
        name = name.strip()
        value_text = value_text.strip()
        if not name or not value_text:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        if math.isfinite(value):
            blendshapes[name] = value
    return blendshapes


def parse_head_rotation(frame):
    """Return iFacialMocap head Euler XYZ degrees, or None."""

    for field in frame.split("|"):
        field = field.strip()
        if not field.startswith("=head#"):
            continue
        values = field.partition("#")[2].split(",")
        if len(values) < 3:
            return None
        try:
            rotation = tuple(float(value.strip()) for value in values[:3])
        except ValueError:
            return None
        if all(math.isfinite(value) for value in rotation):
            return rotation
        return None
    return None


def normalize_expression_name(name):
    return "".join(character for character in str(name).casefold() if character.isalnum())


def expression_aliases(channel_name):
    aliases = [channel_name]
    if channel_name.endswith("_L"):
        stem = channel_name[:-2]
        aliases.extend((f"{stem}Left", f"{stem}_Left"))
    elif channel_name.endswith("_R"):
        stem = channel_name[:-2]
        aliases.extend((f"{stem}Right", f"{stem}_Right"))
    return tuple(aliases)


def resolve_expression(channel_name, expression_lookup):
    for alias in expression_aliases(channel_name):
        expression = expression_lookup.get(normalize_expression_name(alias))
        if expression is not None:
            return expression
    return None
