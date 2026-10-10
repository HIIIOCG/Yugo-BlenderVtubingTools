"""Bounded URL downloads, followed by image loading on Blender's main thread.

Node evaluation creates a frozen URL reference and performs no I/O. An explicit
consumer calls request(); get() only looks up a ready image. Workers receive
primitive jobs and produce primitive results, and never access Blender data.
Packed images survive cache resets, Undo, and file loads.
"""
from collections import OrderedDict
from array import array
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import bpy

from .constants import TREE_ID
from .image_nodes import NODE, VALUE, UrlImageRef

URL_TAG = "yugo_image_url"
KEY_TAG = "yugo_image_url_sha256"
MAX_BYTES = 4 * 1024 * 1024
MAX_PENDING = 64
MAX_RECORDS = 256
MAX_WORKERS = 2
MAX_LOADS_PER_TICK = 2
TIMEOUT = 8.0
_generation = 0
_session = None
_retired = []
_records = OrderedDict()  # Primitive URL/key/status/image-name records only.
_last_status_tick = -float("inf")


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Image consumers must run on Blender's main thread")


def _url(ref):
    if isinstance(ref, UrlImageRef):
        return str(ref.url or "").strip()
    return str(ref or "").strip() if isinstance(ref, str) else ""


def _valid_url(url):
    if not url or len(url) > 8192 or any(ord(char) < 32 for char in url):
        raise ValueError("Choose a valid HTTP or HTTPS image URL")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Only HTTP and HTTPS image URLs are supported")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Image URLs cannot contain login credentials")
    # Check malformed ports here rather than letting a worker fail later.
    parts.port
    return url


def _key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _record(url):
    key = _key(url)
    record = _records.get(key)
    if record is None:
        # Keep pending requests, while bounding remembered terminal failures.
        for old_key in tuple(_records):
            if len(_records) < MAX_RECORDS:
                break
            if _records[old_key]["state"] != "PENDING":
                _records.pop(old_key, None)
        record = {"url": url, "key": key, "state": "IDLE", "message": "Waiting for a consumer", "image": ""}
        _records[key] = record
    _records.move_to_end(key)
    return record


def get(ref):
    """Return a ready image, recovering packed cache tags without any download."""
    _main_thread()
    if isinstance(ref, bpy.types.Image):
        return ref
    url = _url(ref)
    if not url:
        return None
    key = _key(url)
    record = _records.get(key)
    image = bpy.data.images.get(record["image"]) if record and record["image"] else None
    if image is not None and image.get(KEY_TAG) == key and image.packed_file:
        return image
    if record and record["state"] in {"PENDING", "ERROR"}:
        return None
    image = next((image for image in bpy.data.images if image.get(KEY_TAG) == key and image.packed_file), None)
    if image is not None:
        record = _record(url)
        record.update(state="READY", message="Ready", image=image.name)
    return image


def status(ref):
    _main_thread()
    if get(ref) is not None:
        return "Ready"
    url = _url(ref)
    if not url:
        return "No image URL"
    record = _records.get(_key(url))
    return record["message"] if record else "Waiting for a consumer"


@dataclass(frozen=True)
class _Job:
    generation: int
    url: str
    key: str
    path_base: str


@dataclass(frozen=True)
class _Result:
    generation: int
    key: str
    path: str = ""
    error: str = ""


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, stop, deadline):
        self.stop, self.deadline = stop, deadline

    def redirect_request(self, request, response, code, message, headers, new_url):
        _valid_url(new_url)
        if self.stop.is_set() or time.monotonic() >= self.deadline:
            raise TimeoutError("Image download cancelled or timed out")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("The URL did not return a PNG, JPEG, WebP, or GIF image")


def _download(job, stop):
    """Worker-only I/O. No Blender objects or API calls are allowed here."""
    deadline = time.monotonic() + TIMEOUT
    temporary = job.path_base + ".part"
    destination = ""
    try:
        if stop.is_set():
            raise TimeoutError()
        opener = urllib.request.build_opener(_Redirects(stop, deadline))
        request = urllib.request.Request(job.url, headers={
            "User-Agent": "YugoNodeImage/1.0", "Accept": "image/png,image/jpeg,image/webp,image/gif",
        })
        with opener.open(request, timeout=TIMEOUT) as response:
            _valid_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("Image exceeds the 4 MB limit")
            chunks, size = [], 0
            while True:
                if stop.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError()
                chunk = response.read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Image exceeds the 4 MB limit")
            data = b"".join(chunks)
        destination = job.path_base + _extension(data)
        if stop.is_set():
            raise TimeoutError()
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, destination)
        if stop.is_set():
            raise TimeoutError()
        return _Result(job.generation, job.key, destination)
    except urllib.error.HTTPError as error:
        message = f"HTTP {error.code}"
    except TimeoutError:
        message = "Download timed out or was cancelled"
    except ValueError as error:
        message = str(error) if str(error).startswith(("Image exceeds", "The URL did", "Only HTTP")) else "Invalid image response"
    except Exception:
        message = "Image download failed"
    for path in (temporary, destination):
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    return _Result(job.generation, job.key, error=message)


class _Session:
    def __init__(self, generation):
        self.generation = generation
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=MAX_PENDING)
        self.results = queue.Queue()
        self.threads = []
        self.directory = Path(bpy.app.tempdir) / "yugo-images" / uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)

    def _run(self):
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                result = _download(job, self.stop)
                if self.stop.is_set():
                    _discard_path(result.path)
                else:
                    self.results.put(result)
            finally:
                self.jobs.task_done()

    def start_available(self, available):
        while len(self.threads) < MAX_WORKERS and available > 0:
            worker = threading.Thread(target=self._run, name="Yugo image download", daemon=True)
            self.threads.append(worker)
            worker.start()
            available -= 1

    def close(self):
        self.stop.set()
        while True:
            try:
                self.jobs.get_nowait()
                self.jobs.task_done()
            except queue.Empty:
                break


def _start_available():
    if _session is not None and not _session.stop.is_set():
        retired_workers = sum(worker.is_alive() for session in _retired for worker in session.threads)
        _session.start_available(MAX_WORKERS - retired_workers - len(_session.threads))


def request(ref, *, retry=False):
    """Explicitly enqueue a deduplicated download, or return a packed ready image.

    Errors remain terminal until retry=True. An offline request never contacts
    the network, and consumers can continue using their original fallback image.
    """
    global _session
    _main_thread()
    image = get(ref)
    if image is not None:
        return image
    url = _url(ref)
    if not url:
        return None
    record = _record(url)
    if record["state"] == "PENDING" or (record["state"] == "ERROR" and not retry):
        return None
    try:
        _valid_url(url)
    except ValueError as error:
        record.update(state="ERROR", message=f"Error · {error}")
        return None
    if not bpy.app.online_access:
        record.update(state="ERROR", message="Offline · Enable Online Access, then Retry")
        return None
    if sum(r["state"] == "PENDING" for r in _records.values()) >= MAX_PENDING:
        record.update(state="ERROR", message="Error · Image download queue is full; Retry later")
        return None
    if _session is None:
        try:
            _session = _Session(_generation)
        except OSError:
            record.update(state="ERROR", message="Error · Image cache directory is unavailable")
            return None
    job = _Job(_generation, url, record["key"], str(_session.directory / record["key"]))
    try:
        _session.jobs.put_nowait(job)
    except queue.Full:
        record.update(state="ERROR", message="Error · Image download queue is full; Retry later")
        return None
    record.update(state="PENDING", message="Downloading", image="")
    _start_available()
    return None


def evaluate(context, node):
    if node.bl_idname == VALUE:
        from .runtime import _output
        return _output(node, "Image", node.image if not node.mute else None)
    if node.bl_idname != NODE:
        return None
    from .runtime import _output
    value = None if node.mute else context.input(node, "URL", node.inputs["URL"].default_value)
    ref = UrlImageRef(str(value or "")) if not node.mute else None
    return _output(node, "Image", ref)


def _sample(node, scene=None, now=None):
    from .flow import PullContext
    context = PullContext(node.id_data, scene or bpy.context.scene, time.monotonic() if now is None else now, 0)
    return evaluate(context, node)["Image"]


def retry_node(node, scene=None):
    ref = _sample(node, scene)
    request(ref, retry=True)
    node.image_status = status(ref)


def _discard_path(path):
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def _load_ready(result):
    record = _records.get(result.key)
    if result.generation != _generation or record is None or record["state"] != "PENDING":
        _discard_path(result.path)
        return
    if result.error:
        record.update(state="ERROR", message=f"Error · {result.error}")
        return
    image = None
    try:
        image = bpy.data.images.load(result.path, check_existing=False)
        # Force decoding before publishing it; a damaged file must not replace
        # any consumer's fallback or leave an unused broken datablock behind.
        if min(image.size) <= 0:
            raise ValueError("Invalid image dimensions")
        if image.source != "FILE":
            # Blender decodes GIF through its movie reader. Freeze its first
            # frame as a native generated image, which pack() encodes as PNG.
            # Keeping raw GIF data as a FILE image cannot be decoded on reload.
            width, height = image.size
            pixels = array("f", [0.0]) * (width * height * image.channels)
            image.pixels.foreach_get(pixels)
            frozen = bpy.data.images.new("URL image first frame", width, height, alpha=True)
            try:
                frozen.pixels.foreach_set(pixels)
            except Exception:
                bpy.data.images.remove(frozen)
                raise
            bpy.data.images.remove(image)
            image = frozen
        image.pack()
        if image.packed_file is None:
            raise ValueError("Image packing failed")
        image.name = "URL Image " + result.key[:12]
        image[KEY_TAG], image[URL_TAG] = result.key, record["url"]
        record.update(state="READY", message="Ready", image=image.name)
    except Exception:
        if image is not None and image.users == 0:
            bpy.data.images.remove(image)
        record.update(state="ERROR", message="Error · The image could not be loaded or packed")
    finally:
        _discard_path(result.path)


def tick(_context_unused=None, delta_time=1 / 60, now=None):
    global _last_status_tick, _generation, _session
    _main_thread()
    from . import scheduler
    if scheduler.is_suspended():
        return
    if _session is not None and not bpy.app.online_access:
        _generation += 1
        _session.close()
        _retired.append(_session)
        _session = None
        for record in _records.values():
            if record["state"] == "PENDING":
                record.update(state="ERROR", message="Offline · Enable Online Access, then Retry")
    sessions = tuple(_retired) + ((_session,) if _session is not None else ())
    for session in sessions:
        for _ in range(MAX_PENDING if session in _retired else MAX_LOADS_PER_TICK):
            try:
                result = session.results.get_nowait()
            except queue.Empty:
                break
            _load_ready(result)
        if session in _retired and not any(worker.is_alive() for worker in session.threads):
            _retired.remove(session)
            try:
                session.directory.rmdir()
            except OSError:
                pass
    _start_available()
    now = time.monotonic() if now is None else float(now)
    if now - _last_status_tick < .5:
        return
    _last_status_tick = now
    # Status observation does not request anything or restart failed downloads.
    for tree in tuple(bpy.data.node_groups):
        if tree.bl_idname != TREE_ID:
            continue
        for node in tuple(tree.nodes):
            if node.bl_idname != NODE:
                continue
            try:
                message = "Muted" if node.mute else status(_sample(node, now=now))
            except Exception:
                message = "Waiting for a consumer"
            if node.image_status != message:
                node.image_status = message


def clear():
    """Cancel jobs and invalidate late results; retain all packed scene images."""
    global _generation, _session, _last_status_tick
    _main_thread()
    _generation += 1
    if _session is not None:
        _session.close()
        while True:
            try:
                _discard_path(_session.results.get_nowait().path)
            except queue.Empty:
                break
        _retired.append(_session)
        _session = None
    _records.clear()
    _last_status_tick = -float("inf")


def register():
    from . import scheduler
    clear()
    scheduler.add_motion("url_images", tick, priority=105)


def unregister():
    from . import scheduler
    scheduler.remove_motion("url_images")
    clear()
