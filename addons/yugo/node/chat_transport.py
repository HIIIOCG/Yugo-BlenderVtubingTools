"""YouTube REST chat receiver with independent, pull-only history cursors.

This module deliberately has no Blender imports, callbacks, or application timers.
The worker only fetches JSON and appends plain data. A graph consumer owns its
cursor and explicitly calls ``read``; reading never consumes another consumer's
messages. YouTube's polling interval is independent of graph evaluation rate.

API references:
https://developers.google.com/youtube/v3/live/docs/liveChatMessages/list
https://developers.google.com/youtube/v3/live/docs/liveChatMessages
https://developers.google.com/youtube/v3/docs/videos#liveStreamingDetails.activeLiveChatId
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
import json
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


API_BASE = "https://www.googleapis.com/youtube/v3/"
EVENT_TYPES = {
    "textMessageEvent": "MESSAGE",
    "superChatEvent": "SUPER_CHAT",
    "superStickerEvent": "SUPER_STICKER",
    "newSponsorEvent": "NEW_MEMBER",
    "memberMilestoneChatEvent": "MEMBER_MILESTONE",
    "membershipGiftingEvent": "MEMBERSHIP_GIFT",
    "giftMembershipReceivedEvent": "GIFT_RECEIVED",
    "giftEvent": "GIFT",
    "chatEndedEvent": "CHAT_ENDED",
    "messageDeletedEvent": "MESSAGE_DELETED",
    "userBannedEvent": "USER_BANNED",
    "tombstone": "DELETED",
    "pollEvent": "POLL",
}
_DETAIL_NAMES = {
    "textMessageEvent": "textMessageDetails",
    "superChatEvent": "superChatDetails",
    "superStickerEvent": "superStickerDetails",
    "newSponsorEvent": "newSponsorDetails",
    "memberMilestoneChatEvent": "memberMilestoneChatDetails",
    "membershipGiftingEvent": "membershipGiftingDetails",
    "giftMembershipReceivedEvent": "giftMembershipReceivedDetails",
    "giftEvent": "giftEventDetails",
    "messageDeletedEvent": "messageDeletedDetails",
    "userBannedEvent": "userBannedDetails",
    "pollEvent": "pollDetails",
}
_QUOTA_ERRORS = {"quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg", "RESOURCE_EXHAUSTED"}
_ENDED_ERRORS = {"liveChatEnded"}
_TERMINAL_ERRORS = {
    "liveChatDisabled", "liveChatNotFound", "videoNotFound", "noActiveLiveChat",
    "forbidden", "accessNotConfigured", "keyInvalid", "authError",
    "ipRefererBlocked", "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED",
    "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED",
}
_TERMINAL_STATES = {"ENDED", "QUOTA_EXCEEDED", "ERROR", "STOPPED"}


def parse_video_id(value):
    """Accept an 11-character video ID or a YouTube watch/live/share URL."""
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Enter a YouTube live video URL or video ID")
    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"} and parts:
        candidate = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path.rstrip("/") == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in {"live", "embed", "shorts"}:
            candidate = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("Enter a YouTube live video URL or video ID")
    return candidate


def _text(value):
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", ""))


def _integer(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default


def normalize_message(item):
    """Convert an official resource to a JSON-safe platform-neutral message."""
    if not isinstance(item, dict) or not isinstance(item.get("snippet"), dict):
        return None
    snippet = item["snippet"]
    author = item.get("authorDetails") or {}
    author = author if isinstance(author, dict) else {}
    message_id = _text(item.get("id"))
    raw_type = _text(snippet.get("type"))
    if not message_id or not raw_type:
        return None
    details = snippet.get(_DETAIL_NAMES.get(raw_type, "")) or {}
    details = details if isinstance(details, dict) else {}
    # JSON copying enforces the same data-only boundary as the event runtime.
    try:
        details = json.loads(json.dumps(details, ensure_ascii=False))
    except (ValueError, TypeError):
        details = {}
    display = _text(snippet.get("displayMessage"))
    if raw_type == "textMessageEvent":
        text = _text(details.get("messageText")) or display
    elif raw_type in {"superChatEvent", "memberMilestoneChatEvent"}:
        text = _text(details.get("userComment")) or display
    elif raw_type == "superStickerEvent":
        metadata = details.get("superStickerMetadata") or {}
        text = _text(metadata.get("altText")) if isinstance(metadata, dict) else ""
        text = text or display
    else:
        text = display
    published = _text(snippet.get("publishedAt"))
    return {
        "platform": "youtube", "type": EVENT_TYPES.get(raw_type, "OTHER"),
        "raw_type": raw_type, "id": message_id, "message_id": message_id,
        "event_id": message_id, "text": text, "display_text": display,
        "author": _text(author.get("displayName")),
        "author_id": _text(author.get("channelId")) or _text(snippet.get("authorChannelId")),
        "author_url": _text(author.get("channelUrl")),
        "avatar_url": _text(author.get("profileImageUrl")),
        "timestamp": published, "published_at": published,
        "live_chat_id": _text(snippet.get("liveChatId")),
        "is_owner": bool(author.get("isChatOwner", False)),
        "is_moderator": bool(author.get("isChatModerator", False)),
        "is_member": bool(author.get("isChatSponsor", False)),
        "is_verified": bool(author.get("isVerified", False)),
        "amount_micros": _integer(details.get("amountMicros")),
        "amount": _integer(details.get("amountMicros")) / 1_000_000.0,
        "currency": _text(details.get("currency")),
        "amount_display": _text(details.get("amountDisplayString")),
        "tier": _integer(details.get("tier")),
        "member_months": _integer(details.get("memberMonth")),
        "member_level": _text(details.get("memberLevelName")) or _text(details.get("giftMembershipsLevelName")),
        "gift_count": _integer(details.get("giftMembershipsCount")),
        "gifter_id": _text(details.get("gifterChannelId")),
        "gifting_message_id": _text(details.get("associatedMembershipGiftingMessageId")),
        "is_upgrade": bool(details.get("isUpgrade", False)),
        "details": details,
    }


@dataclass(frozen=True)
class ChatBatch:
    messages: tuple = ()
    cursor: int = 0
    overrun: int = 0


class YouTubeAPIError(Exception):
    """API error carries only an error code, never a credential-bearing URL."""
    def __init__(self, reason="httpError", status=0):
        self.reason = str(reason)
        self.status = _integer(status)
        super().__init__("YouTube request failed")


class YouTubeChatTransport:
    """Fetch chat on a daemon thread; consumers explicitly pull saved messages.

    ``request_json(endpoint, params)`` can be injected for deterministic tests.
    ``poll_once`` returns the mandatory delay before the next request. The normal
    worker enforces that delay with an interruptible event wait. Calling it
    directly is intended only for tests, not graph updates.
    """
    def __init__(self, video, api_key, *, include_history=False, max_messages=2048,
                 dedup_limit=50000, max_results=500, request_json=None,
                 request_timeout=5.0, max_response_bytes=8 * 1024 * 1024,
                 retry_initial=2.0, retry_max=60.0):
        self.video_id = parse_video_id(video)
        self._api_key = str(api_key or "").strip()
        self.include_history = bool(include_history)
        self.max_messages = max(1, int(max_messages))
        self.dedup_limit = max(self.max_messages, int(dedup_limit))
        self.max_results = min(2000, max(200, int(max_results)))
        self.request_timeout = max(0.1, float(request_timeout))
        self.max_response_bytes = max(1024, int(max_response_bytes))
        self.retry_initial = max(0.01, float(retry_initial))
        self.retry_max = max(self.retry_initial, float(retry_max))
        self._request = request_json or self._http_json
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._history = deque(maxlen=self.max_messages)
        self._seen = OrderedDict()
        self._sequence = 0
        self._first_page = True
        self._live_chat_id = ""
        self._page_token = ""
        self._poll_interval = 5.0
        self._state = "DISCONNECTED"
        self._error = ""
        self._error_reason = ""
        self._retry_count = 0
        self._received = 0
        self._skipped_history = 0
        self._duplicates = 0
        self._evicted = 0
        self._last_received = 0.0
        self._offline_at = ""
        self._token_recovery = False

    def status(self):
        with self._lock:
            return {
                "state": self._state, "connected": self._state == "CONNECTED",
                "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
                "error": self._error, "error_reason": self._error_reason,
                "video_id": self.video_id, "live_chat_id": self._live_chat_id,
                "polling_interval": self._poll_interval, "retry_count": self._retry_count,
                "buffered": len(self._history), "received": self._received,
                "skipped_history": self._skipped_history, "duplicates": self._duplicates,
                "evicted": self._evicted, "cursor": self._sequence,
                "last_received": self._last_received, "offline_at": self._offline_at,
            }

    def latest_cursor(self):
        with self._lock:
            return self._sequence

    def read(self, cursor=0, limit=100):
        """Copy ordered messages after cursor without altering shared history.

        Cursor zero reads from the start of retained history. Use latest_cursor
        to subscribe starting now. If messages were evicted, overrun gives the
        exact gap and the returned cursor skips that gap before reading a batch.
        """
        limit = min(self.max_messages, max(0, _integer(limit)))
        with self._lock:
            cursor = min(self._sequence, max(0, _integer(cursor)))
            if cursor == self._sequence:
                return ChatBatch((), cursor, 0)
            oldest = self._history[0]["sequence"] if self._history else self._sequence + 1
            overrun = max(0, oldest - 1 - cursor)
            cursor += overrun
            messages = []
            if limit:
                for message in self._history:
                    if message["sequence"] > cursor:
                        messages.append(deepcopy(message))
                        if len(messages) >= limit:
                            break
            if messages:
                cursor = messages[-1]["sequence"]
            return ChatBatch(tuple(messages), cursor, overrun)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return not self._stop.is_set()
            if not self._api_key:
                self._state, self._error_reason = "ERROR", "missingApiKey"
                self._error = "Add a YouTube API key in Yugo Node preferences"
                return False
            self._stop.clear()
            self._state, self._error, self._error_reason = "CONNECTING", "", ""
            self._retry_count = 0
            self._thread = threading.Thread(target=self._run, name="YugoNode-YouTubeChat", daemon=True)
            self._thread.start()
            return True

    def stop(self, join_timeout=0.25):
        """Interrupt waits immediately; bound joining a pending HTTP request.

        An in-flight request may finish within request_timeout, but its result
        is discarded after stopping. The daemon never accesses Blender data.
        """
        self._stop.set()
        with self._lock:
            self._state = "STOPPED"
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(join_timeout)))
        return not bool(thread and thread.is_alive())

    def _http_json(self, endpoint, params):
        url = API_BASE + endpoint + "?" + urlencode(params)
        request = Request(url, headers={"User-Agent": "YugoNode/0.7", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            reason = "httpError"
            try:
                body = json.loads(error.read(min(self.max_response_bytes, 65536)))
                reasons = body.get("error", {}).get("errors", [])
                if reasons and isinstance(reasons[0], dict):
                    reason = reasons[0].get("reason", reason)
                else:
                    reason = body.get("error", {}).get("status", reason)
            except (ValueError, TypeError, AttributeError):
                pass
            raise YouTubeAPIError(self._safe_reason(reason), error.code) from None
        except Exception:
            # URLError / SSL / DNS exceptions can embed the complete API URL.
            raise YouTubeAPIError("networkError") from None
        if len(raw) > self.max_response_bytes:
            raise YouTubeAPIError("responseTooLarge")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise YouTubeAPIError("invalidResponse") from None
        if not isinstance(value, dict):
            raise YouTubeAPIError("invalidResponse")
        return value

    def _safe_reason(self, reason):
        value = str(reason or "requestError")
        if self._api_key:
            value = value.replace(self._api_key, "REDACTED")
        return value if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) else "requestError"

    def _fetch(self, endpoint, params):
        value = self._request(endpoint, {**params, "key": self._api_key})
        if not isinstance(value, dict):
            raise YouTubeAPIError("invalidResponse")
        if "error" in value:
            error = value["error"] if isinstance(value["error"], dict) else {}
            reasons = error.get("errors") or []
            reason = reasons[0].get("reason", "requestError") if reasons and isinstance(reasons[0], dict) else error.get("status", "requestError")
            raise YouTubeAPIError(self._safe_reason(reason), error.get("code", 0))
        return value

    def poll_once(self):
        """Resolve the room and fetch a page; return the server's next delay."""
        if self._stop.is_set():
            return self._poll_interval
        if not self._live_chat_id:
            video = self._fetch("videos", {"part": "liveStreamingDetails", "id": self.video_id})
            if self._stop.is_set():
                return self._poll_interval
            items = video.get("items") or []
            if not items:
                raise YouTubeAPIError("videoNotFound", 404)
            details = items[0].get("liveStreamingDetails") or {}
            chat_id = _text(details.get("activeLiveChatId"))
            if not chat_id:
                raise YouTubeAPIError("liveChatEnded" if details.get("actualEndTime") else "noActiveLiveChat")
            with self._lock:
                self._live_chat_id = chat_id
        params = {"part": "id,snippet,authorDetails", "liveChatId": self._live_chat_id,
                  "maxResults": self.max_results}
        if self._page_token:
            params["pageToken"] = self._page_token
        response = self._fetch("liveChat/messages", params)
        if not self._stop.is_set():
            self.ingest_response(response)
        return self._poll_interval

    def ingest_response(self, response):
        """Ingest an official page without network; useful for replay/tests."""
        if not isinstance(response, dict) or not isinstance(response.get("items", []), list):
            raise YouTubeAPIError("invalidResponse")
        with self._lock:
            if self._stop.is_set():
                return
            skip = self._first_page and not self.include_history
            self._first_page = False
            # Missing or malformed intervals use a conservative 5-second delay.
            interval_ms = _integer(response.get("pollingIntervalMillis"), 5000)
            self._poll_interval = max(0.1, interval_ms / 1000.0) if interval_ms > 0 else 5.0
            self._page_token = _text(response.get("nextPageToken"))
            self._offline_at = _text(response.get("offlineAt"))
            ended = bool(self._offline_at)
            for item in response.get("items", []):
                message = normalize_message(item)
                if message is None:
                    continue
                ended = ended or message["type"] == "CHAT_ENDED"
                message_id = message["message_id"]
                if message_id in self._seen:
                    self._duplicates += 1
                    self._seen.move_to_end(message_id)
                    continue
                self._seen[message_id] = None
                if len(self._seen) > self.dedup_limit:
                    self._seen.popitem(last=False)
                if skip:
                    self._skipped_history += 1
                    continue
                self._sequence += 1
                message["sequence"] = self._sequence
                if len(self._history) == self.max_messages:
                    self._evicted += 1
                self._history.append(message)
                self._received += 1
                self._last_received = time.monotonic()
            self._state = "ENDED" if ended else "CONNECTED"
            self._error = ""
            self._error_reason = ""
            self._retry_count = 0
            self._token_recovery = False

    def _record_error(self, error):
        reason = self._safe_reason(error.reason if isinstance(error, YouTubeAPIError) else "networkError")
        status = error.status if isinstance(error, YouTubeAPIError) else 0
        with self._lock:
            if self._stop.is_set():
                return False
            self._error_reason = reason
            self._error = f"YouTube: {reason}" + (f" (HTTP {status})" if status else "")
            if reason in _QUOTA_ERRORS:
                self._state = "QUOTA_EXCEEDED"
            elif reason in _ENDED_ERRORS:
                self._state = "ENDED"
            elif reason in _TERMINAL_ERRORS or (status in {400, 401, 403, 404} and reason not in {"rateLimitExceeded", "userRateLimitExceeded", "invalidPageToken"}):
                self._state = "ERROR"
            elif reason == "invalidPageToken":
                if self._token_recovery:
                    self._state = "ERROR"
                else:
                    self._page_token = ""
                    self._token_recovery = True
                    self._state = "RECONNECTING"
            else:
                self._state = "RECONNECTING"
            self._retry_count += 1
            return self._state not in _TERMINAL_STATES

    def _run(self):
        while not self._stop.is_set():
            try:
                delay = self.poll_once()
                with self._lock:
                    terminal = self._state in _TERMINAL_STATES
                if terminal:
                    break
            except Exception as error:
                if self._stop.is_set() or not self._record_error(error):
                    break
                with self._lock:
                    count = self._retry_count
                    interval = self._poll_interval
                delay = max(interval, min(self.retry_max, self.retry_initial * (2 ** min(count - 1, 20))))
            if self._stop.wait(delay):
                break
