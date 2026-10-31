"""Music Assistant actions that upstream does not offer: what is playing,
shuffle and repeat, and the sleep timer."""

import asyncio
import logging
import threading
import time

import aiohttp
from env_secrets import get_env_secret
from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import RepeatMode

from . import upstream_api as upstream

logger = logging.getLogger(__name__)

QUEUE_COMMANDS = ("shuffle_on", "shuffle_off", "repeat_on", "repeat_off")


# --------------------------------------------------------- shuffle / repeat

async def _queue_command(server_url, token, player_id, command):
    async with (
        aiohttp.ClientSession() as session,
        MusicAssistantClient(server_url, session, token=token) as client,
    ):
        if command == "shuffle_on":
            await client.player_queues.shuffle(player_id, True)
        elif command == "shuffle_off":
            await client.player_queues.shuffle(player_id, False)
        elif command == "repeat_on":
            await client.player_queues.repeat(player_id, RepeatMode.ALL)
        elif command == "repeat_off":
            await client.player_queues.repeat(player_id, RepeatMode.OFF)
        else:
            raise ValueError(f"Unsupported queue command: {command}")


def send_queue_command(player_id, command, timeout=6.0):
    """Shuffle or repeat the player's queue. Returns True on success."""
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url or not player_id or command not in QUEUE_COMMANDS:
        return False
    try:
        asyncio.run(asyncio.wait_for(
            _queue_command(server_url, token, player_id, command), timeout=timeout))
        return True
    except Exception as err:
        logger.warning("Queue command %s failed for player %s: %s",
                       command, player_id, type(err).__name__)
        return False


# -------------------------------------------------------------- now playing

async def _now_playing(server_url, token, player_id):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            item = getattr(queue, "current_item", None)
            if item is None:
                return None
            media = getattr(item, "media_item", None)
            title = getattr(media, "name", None) or getattr(item, "name", None)
            artists = [getattr(a, "name", "") for a in getattr(media, "artists", None) or []]
            return {"title": title or "", "artist": ", ".join(a for a in artists if a)}


def get_now_playing(player_id, timeout=4.0):
    """Return {'title', 'artist'} for the current queue item, or None."""
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url or not player_id:
        return None
    try:
        return asyncio.run(asyncio.wait_for(
            _now_playing(server_url, token, player_id), timeout=timeout))
    except Exception as err:
        logger.warning("Could not read now playing for player %s: %s",
                       player_id, type(err).__name__)
        return None


# -------------------------------------------------------------- sleep timer

# Sleep timers live in the single Gunicorn worker; a restart cancels them.
_sleep_timers = {}
_sleep_lock = threading.Lock()


def _sleep_timer_fired(device_id, player_id):
    with _sleep_lock:
        _sleep_timers.pop(device_id, None)
    logger.info("Sleep timer fired for player %s", player_id)
    # MA's alexa provider pauses by speaking "pause" into the Echo, which comes
    # back to this skill as AMAZON.PauseIntent: mark it so it is not re-sent.
    upstream.mark_ma_triggered(device_id, "pause")
    upstream.send_player_command(player_id, "pause")


def set_sleep_timer(device_id, player_id, seconds):
    """(Re)start the sleep timer for this Echo. Returns the stop time (epoch)."""
    timer = threading.Timer(seconds, _sleep_timer_fired, args=(device_id, player_id))
    timer.daemon = True
    with _sleep_lock:
        old = _sleep_timers.pop(device_id, None)
        if old:
            old.cancel()
        _sleep_timers[device_id] = timer
    timer.start()
    return time.time() + seconds


def cancel_sleep_timer(device_id):
    """Cancel this Echo's sleep timer. Returns True when one was pending."""
    with _sleep_lock:
        timer = _sleep_timers.pop(device_id, None)
    if timer:
        timer.cancel()
    return timer is not None
