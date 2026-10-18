"""Locked, independent snapshots for MA streams and Alexa playback metadata."""

import time
import logging
from copy import deepcopy
from threading import RLock
from urllib.parse import unquote, urlsplit

_lock = RLock()
_ma = None
_alexa = None
_version = 0
_players = {}
_pending_voice_requests = {}
_logger = logging.getLogger(__name__)


def begin_voice_request(request_id, player_id, old_version):
    """Remember a voice request so a late MA stream push can be correlated."""
    with _lock:
        _pending_voice_requests[player_id] = {
            "request_id": request_id,
            "old_version": old_version,
            "started": time.monotonic(),
        }


def end_voice_request(request_id, player_id):
    with _lock:
        pending = _pending_voice_requests.get(player_id)
        if pending and pending["request_id"] == request_id:
            _pending_voice_requests.pop(player_id, None)


def get_ma(player_id=None):
    with _lock:
        # _ma is also the lifetime/reset sentinel used by the application tests.
        return deepcopy(_players.get(player_id) if player_id and _ma else _ma)


def stream_player(payload):
    parts = urlsplit(payload.get("streamUrl", "")).path.split("/")
    # MA flow URLs: /flow/<server-id>/<queue-id>/<stream-id>/<filename>
    if len(parts) >= 6 and parts[1] in ("flow", "single"):
        return unquote(parts[3])
    return payload.get("player_id")


def get_alexa():
    with _lock:
        return deepcopy(_alexa)


def set_ma(payload):
    global _ma, _version
    with _lock:
        _version += 1
        _ma = deepcopy(payload)
        _ma.update(version=_version, timestamp=time.time())
        player_id = stream_player(_ma)
        if player_id:
            _players[player_id] = deepcopy(_ma)
            pending = _pending_voice_requests.get(player_id)
            if pending and _version > pending["old_version"]:
                elapsed = time.monotonic() - pending["started"]
                if elapsed <= 60:
                    _logger.info(
                        "MA stream push received request_id=%s player_id=%s "
                        "elapsed_ms=%.1f title=%r",
                        pending["request_id"], player_id, elapsed * 1000,
                        _ma.get("title"),
                    )
                _pending_voice_requests.pop(player_id, None)
        return _version


def set_alexa(payload):
    global _alexa
    with _lock:
        _alexa = deepcopy(payload)
