"""Search Music Assistant and start the result on the requesting Echo queue."""

import asyncio
import logging
import re
import unicodedata
import time

import aiohttp
import shared_store
from env_secrets import get_env_secret
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import (
    CannotConnect,
    ConnectionFailed,
    InvalidServerVersion,
)
from music_assistant_models.enums import MediaType, QueueOption
from music_assistant_models.errors import MusicAssistantError

logger = logging.getLogger(__name__)

_PUSH_TIMEOUT = 3.0
_REQUEST_TIMEOUT = 6.5
_FUTURA_STREAM_URI = "builtin://radio/https://s4.radio.co/s7e7b6c165/listen"


class SearchPlaybackError(Exception):
    """A safe-to-report error while searching or starting playback."""


def _normal(value):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize(
        "NFKD", value.casefold()).encode("ascii", "ignore").decode()).split())


async def find_media(client, query, media_type):
    """Prefer the library; split title/artist only when metadata verifies both."""
    queries = [query]
    if media_type == "radio":
        # Alexa often includes "radio" in the slot value, while MA's saved
        # station search indexes the station name itself. Try the concise name
        # first so a library hit does not depend on a slower provider search.
        # Alexa routines can prepend "rádio" while the station slot also
        # contains it (for example, "tocar a rádio rádio RFM").
        concise = re.sub(r"^(?:(?:radio|rádio)\s+)+", "", query.strip(), flags=re.I)
        concise = re.sub(r"\s+(?:radio|rádio)\s+de\s+autor$", "", concise,
                         flags=re.I).strip()
        if concise and _normal(concise) != _normal(query):
            queries.insert(0, concise)
        # Check all saved stations before broad provider searches. This keeps
        # familiar station names such as "RFM" and "Futura" quick and stable.
        for search in queries:
            results = await client.music.search(search_query=search,
                media_types=[MediaType.RADIO], limit=20, library_only=True)
            items = [x for x in getattr(results, "radio", [])
                     if getattr(x, "uri", None)]
            if items:
                return min(items, key=lambda x: not x.uri.startswith("library://"))
        for search in queries:
            results = await client.music.search(search_query=search,
                media_types=[MediaType.RADIO], limit=20)
            items = [x for x in getattr(results, "radio", [])
                     if getattr(x, "uri", None)]
            if items:
                return min(items, key=lambda x: not x.uri.startswith("library://"))
        raise SearchPlaybackError("not_found")

    title, artist = query, None
    if media_type == "track":
        parts = re.split(r"\s+(?:dos|das|do|da|de|by)\s+", query, flags=re.I)
        if len(parts) > 1:
            title, artist = " de ".join(parts[:-1]), parts[-1]
            queries.insert(0, title)
    for search in queries:
        results = await client.music.search(search_query=search,
            media_types=[MediaType(media_type)], limit=20, library_only=True)
        items = getattr(results, "tracks" if media_type == "track" else "radio", [])
        if not items:
            results = await client.music.search(search_query=search,
                media_types=[MediaType(media_type)], limit=20)
            items = getattr(results, "tracks" if media_type == "track" else "radio", [])
        items = [x for x in items if getattr(x, "uri", None)]
        if artist:
            items = [x for x in items if _normal(getattr(x, "name", "")) == _normal(title)
                     and any(_normal(a.name) == _normal(artist)
                             for a in getattr(x, "artists", []))]
        if items:
            return min(items, key=lambda x: not x.uri.startswith("library://"))
    # MA does not interpret a concatenated title + artist as a structured query.
    # Try a bounded set of title prefixes, accepting only an exact metadata match.
    if media_type == "track" and not artist:
        words = query.split()
        for count in range(1, min(4, len(words))):
            title, artist = " ".join(words[:-count]), " ".join(words[-count:])
            results = await client.music.search(search_query=title,
                media_types=[MediaType.TRACK], limit=20)
            for item in results.tracks:
                if (_normal(item.name) == _normal(title) and
                    any(_normal(a.name) == _normal(artist) for a in item.artists)):
                    return item
    raise SearchPlaybackError("not_found")


async def _search_and_play(query, media_type, player_id, old_version, server_url, token):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            item = await find_media(client, query, media_type)

            # The saved FUTURA entry still points at a dead mediatiquestream
            # hostname. The station's own Radio.co stream is live; route this
            # exact saved station through MA's built-in URL provider until its
            # library entry can be replaced safely.
            media_uri = item.uri
            if (media_type == "radio" and
                    _normal(getattr(item, "name", "")) == "futura radio de autor"):
                media_uri = _FUTURA_STREAM_URI

            await client.player_queues.play_media(
                queue_id=player_id,
                media=media_uri,
                option=QueueOption.REPLACE,
            )

    # The MA Alexa player provider pushes the selected queue's fresh stream URL
    # back to this add-on. Wait briefly so the Alexa response can play that URL.
    deadline = time.monotonic() + _PUSH_TIMEOUT
    while time.monotonic() < deadline:
        current = shared_store.get_ma(player_id) or {}
        if current.get("version", 0) > old_version and current.get("streamUrl"):
            return current
        await asyncio.sleep(0.1)
    raise SearchPlaybackError("stream_timeout")


def play_search_result(query, media_type, player_id):
    """Search/play a requested MA item and return its pushed stream metadata."""
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url:
        raise SearchPlaybackError("missing_api")

    old_version = (shared_store.get_ma(player_id) or {}).get("version", 0)
    try:
        return asyncio.run(
            asyncio.wait_for(
                _search_and_play(
                    query.strip(), media_type, player_id, old_version, server_url, token
                ),
                timeout=_REQUEST_TIMEOUT,
            )
        )
    except SearchPlaybackError:
        raise
    except (CannotConnect, ConnectionFailed, InvalidServerVersion, MusicAssistantError):
        logger.exception("Music Assistant search/play failed for media type %s", media_type)
        raise SearchPlaybackError("api_error") from None
    except asyncio.TimeoutError:
        raise SearchPlaybackError("timeout") from None
    except Exception:
        logger.exception("Unexpected Music Assistant voice-search failure")
        raise SearchPlaybackError("api_error") from None
