"""Search Music Assistant and start the result on the requesting Echo queue."""

import asyncio
import logging
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
_REQUEST_TIMEOUT = 5.0


class SearchPlaybackError(Exception):
    """A safe-to-report error while searching or starting playback."""


async def _search_and_play(query, media_type, player_id, old_version, server_url, token):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            results = await client.music.search(
                search_query=query,
                media_types=[MediaType(media_type)],
                limit=5,
            )
            items = getattr(results, "tracks" if media_type == "track" else "radio", [])
            item = next((candidate for candidate in items if getattr(candidate, "uri", None)), None)
            if item is None:
                raise SearchPlaybackError("not_found")

            await client.player_queues.play_media(
                queue_id=player_id,
                media=item.uri,
                option=QueueOption.REPLACE,
            )

    # The MA Alexa player provider pushes the selected queue's fresh stream URL
    # back to this add-on. Wait briefly so the Alexa response can play that URL.
    deadline = time.monotonic() + _PUSH_TIMEOUT
    while time.monotonic() < deadline:
        current = shared_store.get_ma() or {}
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

    old_version = (shared_store.get_ma() or {}).get("version", 0)
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
