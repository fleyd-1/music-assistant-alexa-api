"""Locked, independent snapshots for MA streams and Alexa playback metadata."""

import time
from copy import deepcopy
from threading import RLock
from urllib.parse import unquote, urlsplit

_lock = RLock()
_ma = None
_alexa = None
_version = 0
_players = {}


def get_ma(player_id=None):
    with _lock:
        # _ma is also the lifetime/reset sentinel used by the application tests.
        return deepcopy(_players.get(player_id) if player_id and _ma else _ma)


def stream_player(payload):
    parts = urlsplit(payload.get("streamUrl", "")).path.split("/")
    # MA flow URLs: /flow/<server-id>/<queue-id>/<stream-id>/<filename>
    if len(parts) >= 6 and parts[1] == "flow":
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
        return _version


def set_alexa(payload):
    global _alexa
    with _lock:
        _alexa = deepcopy(payload)
