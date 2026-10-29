"""Search Music Assistant and start the result on the requesting Echo queue."""

import asyncio
import logging
import random
import re
import threading
import unicodedata
import time
import uuid

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
from music_assistant_models.errors import MediaNotFoundError, MusicAssistantError

logger = logging.getLogger(__name__)

_PUSH_TIMEOUT = 3.0
_REQUEST_TIMEOUT = 6.5
_ARTIST_QUEUE_LIMIT = 8
_FUTURA_STREAM_URI = "builtin://radio/https://s4.radio.co/s7e7b6c165/listen"
_JOANA_PODCAST_URI = "library://podcast/1"


class SearchPlaybackError(Exception):
    """A safe-to-report error while searching or starting playback."""


def _normal(value):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize(
        "NFKD", value.casefold()).encode("ascii", "ignore").decode()).split())


def _query_variants(query):
    """Generate search variants for acronyms, spoken spaced letters, and common phonetics."""
    variants = [query]
    no_radio = re.sub(r"^(?:(?:a\s+|o\s+)?(?:radio|rádio)\s+)+", "", query.strip(), flags=re.I).strip()
    if no_radio and _normal(no_radio) not in [_normal(v) for v in variants]:
        variants.append(no_radio)
    no_dots = re.sub(r"\.", "", query).strip()
    collapsed = re.sub(r"(?<=\b[a-z0-9])\s+(?=[a-z0-9]\b)", "", no_dots, flags=re.I)
    for alt in (no_dots, collapsed):
        if alt and _normal(alt) not in [_normal(v) for v in variants]:
            variants.append(alt)
    # Common PT phonetic collision: 'isa' (diminutive) transcribed for 'iza'
    if collapsed.casefold() == "isa":
        if "iza" not in [_normal(v) for v in variants]:
            variants.append("iza")
    return variants


def _log_stage(diag, stage, started, outcome="ok", **fields):
    """Log a safe duration for one Music Assistant request stage."""
    if diag is not None:
        diag["stage"] = stage
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    logger.info(
        "MA voice request_id=%s stage=%s duration_ms=%.1f outcome=%s%s",
        (diag or {}).get("request_id", "direct"), stage,
        (time.monotonic() - started) * 1000, outcome,
        f" {details}" if details else "",
    )


async def _search_results(client, query, media_types, library_only, diag):
    """Run one timed MA search and return its result object."""
    media_types = tuple(media_types)
    scope = "library" if library_only else "global"
    stage = f"search.{'+'.join(media_types)}.{scope}"
    started = time.monotonic()
    if diag is not None:
        diag["stage"] = stage
        diag["stage_started"] = started
    try:
        results = await client.music.search(
            search_query=query,
            media_types=[MediaType(item) for item in media_types],
            limit=20,
            library_only=library_only,
        )
    except Exception as err:
        _log_stage(diag, stage, started, "error", query=query,
                   error=type(err).__name__)
        raise
    fields = {
        "artist": "artists",
        "track": "tracks",
        "radio": "radio",
        "album": "albums",
        "playlist": "playlists",
        "podcast": "podcasts",
    }
    result_counts = {
        item: len(getattr(results, fields[item], None) or [])
        for item in media_types
    }
    _log_stage(diag, stage, started, query=query, results=result_counts)
    return results


async def _search(client, query, media_type, library_only, diag):
    """Run one timed MA search and return its matching media collection."""
    results = await _search_results(client, query, (media_type,), library_only, diag)
    key = {
        "artist": "artists",
        "track": "tracks",
        "radio": "radio",
        "album": "albums",
        "playlist": "playlists",
        "podcast": "podcasts",
    }[media_type]
    return list(getattr(results, key, None) or [])


def _best_library_item(items):
    return min(items, key=lambda item: not item.uri.startswith("library://"))


def _exact_title_artist_match(items, query):
    """Match a title followed by one complete artist name, with no fuzzy fallback."""
    normalized_query = _normal(query)
    matches = []
    for item in items:
        title = getattr(item, "name", "")
        if not title or not getattr(item, "uri", None):
            continue
        for artist in getattr(item, "artists", []) or []:
            artist_name = getattr(artist, "name", "")
            if artist_name and normalized_query == _normal(f"{title} {artist_name}"):
                matches.append(item)
                break
    return matches


def _log_selection(diag, item, media_type):
    if diag is not None:
        diag["selected_media_type"] = media_type.removesuffix("_fallback")
    logger.info(
        "MA voice request_id=%s selected_type=%s name=%r source=%s",
        (diag or {}).get("request_id", "direct"), media_type,
        getattr(item, "name", ""),
        (getattr(item, "uri", "").split(":", 1)[0] or "unknown"),
    )


async def _artist_track_uris(client, artist, diag):
    """Resolve a bounded list of tracks before queuing, avoiding MA's full-artist expansion."""
    uri = getattr(artist, "uri", "")
    provider, separator, resource = uri.partition("://")
    path = resource.split("?", 1)[0].strip("/")
    parts = path.split("/", 1)
    if not separator or not provider or len(parts) != 2 or parts[0] != "artist":
        raise SearchPlaybackError("not_found")

    item_id = parts[1]
    library_only = provider == "library"
    stage = "artist_tracks.library" if library_only else "artist_tracks.provider"
    started = time.monotonic()
    if diag is not None:
        diag["stage"] = stage
        diag["stage_started"] = started
    try:
        tracks = await client.music.get_artist_tracks(
            item_id=item_id,
            provider_instance_id_or_domain=provider,
            in_library_only=library_only,
        )
    except Exception as err:
        _log_stage(diag, stage, started, "error", provider=provider,
                   error=type(err).__name__)
        raise

    track_uris = [track.uri for track in tracks if getattr(track, "uri", None)]
    selected = track_uris[:_ARTIST_QUEUE_LIMIT]
    _log_stage(diag, stage, started, provider=provider,
               returned=len(track_uris), queued=len(selected))
    if not selected:
        raise SearchPlaybackError("not_found")
    return selected


async def find_media(client, query, media_type, diag=None):
    """Prefer exact library matches; broaden the search only when needed."""
    if media_type == "artist":
        variants = _query_variants(query)
        for library_only in (True, False):
            for variant in variants:
                items = await _search(client, variant, "artist", library_only, diag)
                exact = [item for item in items if getattr(item, "uri", None)
                         and any(_normal(getattr(item, "name", "")) == _normal(v) for v in variants)]
                if exact:
                    selected = _best_library_item(exact)
                    _log_selection(diag, selected, "artist")
                    return selected
        raise SearchPlaybackError("not_found")

    if media_type == "radio":
        # Alexa can repeat the word "radio" in the station slot.
        concise = re.sub(r"^(?:(?:radio|rádio)\s+)+", "", query.strip(), flags=re.I)
        concise = re.sub(r"\s+(?:radio|rádio)\s+de\s+autor$", "", concise,
                         flags=re.I).strip()
        searches = [query]
        if concise and _normal(concise) != _normal(query):
            searches.insert(0, concise)
        no_freq = re.sub(r"\s+(?:\d+(?:[\.,]\w+)?|\d+)?\s*(?:fm|am)$", "", concise, flags=re.I).strip()
        no_freq = re.sub(r"\s+\d+(?:[\.,]\w+)?$", "", no_freq, flags=re.I).strip()
        if no_freq and _normal(no_freq) not in [_normal(s) for s in searches]:
            searches.append(no_freq)
        for library_only in (True, False):
            for search in searches:
                items = await _search(client, search, "radio", library_only, diag)
                items = [item for item in items if getattr(item, "uri", None)]
                if items:
                    selected = _best_library_item(items)
                    _log_selection(diag, selected, "radio")
                    return selected
        raise SearchPlaybackError("not_found")

    if media_type == "album":
        concise = re.sub(
            r"^(?:(?:o\s+)?(?:álbum|album|disco)\s+(?:de\s+|do\s+|da\s+|dos\s+|das\s+)?)+",
            "", query.strip(), flags=re.I
        ).strip()
        searches = [query]
        if concise and _normal(concise) != _normal(query):
            searches.insert(0, concise)
        for library_only in (True, False):
            for search in searches:
                items = await _search(client, search, "album", library_only, diag)
                exact = [item for item in items if getattr(item, "uri", None)
                         and (_normal(getattr(item, "name", "")) == _normal(search)
                              or _normal(getattr(item, "name", "")) == _normal(query))]
                if exact:
                    selected = _best_library_item(exact)
                    _log_selection(diag, selected, "album")
                    return selected
                if library_only:
                    sub = [item for item in items if getattr(item, "uri", None)
                           and (_normal(search) in _normal(getattr(item, "name", ""))
                                or _normal(getattr(item, "name", "")) in _normal(search))]
                    if sub:
                        selected = _best_library_item(sub)
                        _log_selection(diag, selected, "album")
                        return selected
        raise SearchPlaybackError("not_found")

    if media_type == "playlist":
        concise = re.sub(
            r"^(?:(?:a\s+)?(?:playlist|lista(?:\s+de\s+reprodução)?)\s+(?:de\s+|do\s+|da\s+|dos\s+|das\s+)?)+",
            "", query.strip(), flags=re.I
        ).strip()
        searches = [query]
        if concise and _normal(concise) != _normal(query):
            searches.insert(0, concise)
        for library_only in (True, False):
            for search in searches:
                items = await _search(client, search, "playlist", library_only, diag)
                exact = [item for item in items if getattr(item, "uri", None)
                         and (_normal(getattr(item, "name", "")) == _normal(search)
                              or _normal(getattr(item, "name", "")) == _normal(query))]
                if exact:
                    selected = _best_library_item(exact)
                    _log_selection(diag, selected, "playlist")
                    return selected
                if library_only:
                    sub = [item for item in items if getattr(item, "uri", None)
                           and (_normal(search) in _normal(getattr(item, "name", ""))
                                or _normal(getattr(item, "name", "")) in _normal(search))]
                    if sub:
                        selected = _best_library_item(sub)
                        _log_selection(diag, selected, "playlist")
                        return selected
        raise SearchPlaybackError("not_found")

    if media_type == "podcast":
        concise = re.sub(
            r"^(?:(?:o\s+)?(?:podcast)\s+(?:de\s+|do\s+|da\s+|dos\s+|das\s+)?)+",
            "", query.strip(), flags=re.I
        ).strip()
        searches = [query]
        if concise and _normal(concise) != _normal(query):
            searches.insert(0, concise)
        for library_only in (True, False):
            for search in searches:
                items = await _search(client, search, "podcast", library_only, diag)
                exact = [item for item in items if getattr(item, "uri", None)
                         and (_normal(getattr(item, "name", "")) == _normal(search)
                              or _normal(getattr(item, "name", "")) == _normal(query))]
                if exact:
                    selected = _best_library_item(exact)
                    _log_selection(diag, selected, "podcast")
                    return selected
                sub = [item for item in items if getattr(item, "uri", None)
                       and (_normal(search) in _normal(getattr(item, "name", ""))
                            or _normal(getattr(item, "name", "")) in _normal(search))]
                if sub:
                    selected = _best_library_item(sub)
                    _log_selection(diag, selected, "podcast")
                    return selected
        raise SearchPlaybackError("not_found")

    if media_type != "track":
        raise SearchPlaybackError("not_found")

    variants = _query_variants(query)

    # Search both library tracks and artists in one request. If Alexa routes a
    # bare artist name to PlayTrackIntent, this avoids an unnecessary global
    # provider search before the exact local artist fallback.
    for variant in variants:
        local_results = await _search_results(
            client, variant, ("track", "artist", "radio"), True, diag
        )
        exact_tracks = [item for item in (getattr(local_results, "tracks", None) or [])
                        if getattr(item, "uri", None)
                        and any(_normal(getattr(item, "name", "")) == _normal(v) for v in variants)]
        if exact_tracks:
            selected = _best_library_item(exact_tracks)
            _log_selection(diag, selected, "track")
            return selected

        # Check if query matches a local radio station before falling back to artist or slow global search
        local_radios = [item for item in (getattr(local_results, "radio", None) or [])
                        if getattr(item, "uri", None)]
        if local_radios:
            for v in variants:
                norm_v = _normal(v)
                matched_radio = [
                    r for r in local_radios
                    if norm_v == _normal(getattr(r, "name", ""))
                    or _normal(getattr(r, "name", "")).startswith(norm_v)
                    or norm_v in _normal(getattr(r, "name", ""))
                ]
                if matched_radio:
                    selected = _best_library_item(matched_radio)
                    _log_selection(diag, selected, "radio")
                    return selected

        parts = re.split(r"\s+(?:dos|das|do|da|de|by)\s+", variant, flags=re.I)
        if len(parts) == 1:
            combined_local = _exact_title_artist_match(
                getattr(local_results, "tracks", None) or [], variant
            )
            if combined_local:
                selected = _best_library_item(combined_local)
                _log_selection(diag, selected, "track")
                return selected
            exact_local_artists = [item for item in (getattr(local_results, "artists", None) or [])
                                   if getattr(item, "uri", None)
                                   and any(_normal(getattr(item, "name", "")) == _normal(v) for v in variants)]
            if exact_local_artists:
                selected = _best_library_item(exact_local_artists)
                _log_selection(diag, selected, "artist_fallback")
                return selected

    # MA global search can be fuzzy, so only an exact title can be selected.
    for variant in variants:
        items = await _search(client, variant, "track", False, diag)
        exact = [item for item in items if getattr(item, "uri", None)
                 and any(_normal(getattr(item, "name", "")) == _normal(v) for v in variants)]
        if exact:
            selected = _best_library_item(exact)
            _log_selection(diag, selected, "track")
            return selected
        parts = re.split(r"\s+(?:dos|das|do|da|de|by)\s+", variant, flags=re.I)
        if len(parts) == 1:
            combined_global = _exact_title_artist_match(items, variant)
            if combined_global:
                selected = _best_library_item(combined_global)
                _log_selection(diag, selected, "track")
                return selected

    # A title such as "Sonho de Elsa" must survive the parser unchanged when
    # MA has an exact result. For title + artist phrases, verify both fields.
    parts = re.split(r"\s+(?:dos|das|do|da|de|by)\s+", query, flags=re.I)
    if len(parts) > 1:
        title, artist = " ".join(parts[:-1]), parts[-1]
        for library_only in (True, False):
            items = await _search(client, title, "track", library_only, diag)
            exact = [item for item in items if getattr(item, "uri", None)
                     and _normal(getattr(item, "name", "")) == _normal(title)
                     and any(_normal(getattr(a, "name", "")) == _normal(artist)
                             for a in getattr(item, "artists", []))]
            if exact:
                selected = _best_library_item(exact)
                _log_selection(diag, selected, "track")
                return selected
        raise SearchPlaybackError("not_found")

    # A bare artist may not be in the local library; broaden only after the
    # exact local track/artist check and exact global track check have failed.
    for variant in variants:
        items = await _search(client, variant, "artist", False, diag)
        exact = [item for item in items if getattr(item, "uri", None)
                 and any(_normal(getattr(item, "name", "")) == _normal(v) for v in variants)]
        if exact:
            selected = _best_library_item(exact)
            _log_selection(diag, selected, "artist_fallback")
            return selected
    raise SearchPlaybackError("not_found")


async def _search_and_play(query, media_type, player_id, old_version,
                           server_url, token, diag):
    async with aiohttp.ClientSession() as session:
        connection_started = time.monotonic()
        diag["stage"] = "connect"
        diag["stage_started"] = connection_started
        connected = False
        try:
            async with MusicAssistantClient(server_url, session, token=token) as client:
                connected = True
                _log_stage(diag, "connect", connection_started)
                item = await find_media(client, query, media_type, diag)

                # The saved FUTURA entry still points at a dead mediatiquestream
                # hostname. The station's own Radio.co stream is live; route this
                # exact saved station through MA's built-in URL provider until its
                # library entry can be replaced safely.
                media_uri = item.uri
                if diag.get("selected_media_type") == "artist":
                    # Passing an artist URI makes MA resolve its entire discography
                    # before starting. A bounded track list keeps the first stream
                    # inside Alexa's response deadline and still queues several songs.
                    media_uri = await _artist_track_uris(client, item, diag)
                if (media_type == "radio" and
                        _normal(getattr(item, "name", "")) == "futura radio de autor"):
                    media_uri = _FUTURA_STREAM_URI

                play_started = time.monotonic()
                diag["stage"] = "play_media"
                diag["stage_started"] = play_started
                try:
                    play_kwargs = {
                        "queue_id": player_id,
                        "media": media_uri,
                        "option": QueueOption.REPLACE,
                    }
                    if diag.get("selected_media_type") == "podcast" or media_type == "podcast":
                        play_kwargs["start_item"] = "latest"

                    await client.player_queues.play_media(**play_kwargs)
                except Exception as err:
                    _log_stage(diag, "play_media", play_started, "error",
                               player_id=player_id, error=type(err).__name__)
                    raise
                _log_stage(diag, "play_media", play_started, player_id=player_id)
        except Exception as err:
            if not connected:
                _log_stage(diag, "connect", connection_started, "error",
                           error=type(err).__name__)
            raise

    # The MA Alexa player provider pushes the selected queue's fresh stream URL
    # back to this add-on. Wait briefly so the Alexa response can play that URL.
    deadline = time.monotonic() + _PUSH_TIMEOUT
    push_started = time.monotonic()
    diag["stage"] = "stream_push_wait"
    diag["stage_started"] = push_started
    while time.monotonic() < deadline:
        current = shared_store.get_ma(player_id) or {}
        if current.get("version", 0) > old_version and current.get("streamUrl"):
            _log_stage(diag, "stream_push_wait", push_started,
                       player_id=player_id, outcome="received")
            return current
        await asyncio.sleep(0.1)
    _log_stage(diag, "stream_push_wait", push_started, "timeout",
               player_id=player_id)
    raise SearchPlaybackError("stream_timeout")


def play_search_result(query, media_type, player_id, request_id=None):
    """Search/play a requested MA item and return its pushed stream metadata."""
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url:
        raise SearchPlaybackError("missing_api")

    old_version = (shared_store.get_ma(player_id) or {}).get("version", 0)
    request_id = request_id or uuid.uuid4().hex[:10]
    diag = {"request_id": request_id, "stage": "start"}
    started = time.monotonic()
    shared_store.begin_voice_request(request_id, player_id, old_version)
    logger.info("MA voice request_id=%s intent_type=%s query=%r player_id=%s",
                request_id, media_type, query.strip(), player_id)
    keep_pending_for_late_push = False
    try:
        return asyncio.run(
            asyncio.wait_for(
                _search_and_play(
                    query.strip(), media_type, player_id, old_version,
                    server_url, token, diag
                ),
                timeout=_REQUEST_TIMEOUT,
            )
        )
    except SearchPlaybackError as err:
        keep_pending_for_late_push = str(err) == "stream_timeout"
        logger.warning("MA voice request_id=%s failed=%s stage=%s duration_ms=%.1f "
                       "stage_duration_ms=%.1f",
                       request_id, str(err), diag.get("stage"),
                       (time.monotonic() - started) * 1000,
                       (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise
    except (CannotConnect, ConnectionFailed, InvalidServerVersion, MusicAssistantError) as err:
        logger.error("MA voice request_id=%s failed=api_error stage=%s "
                     "error_type=%s duration_ms=%.1f stage_duration_ms=%.1f",
                     request_id, diag.get("stage"), type(err).__name__,
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise SearchPlaybackError("api_error") from None
    except asyncio.TimeoutError:
        keep_pending_for_late_push = True
        logger.error("MA voice request_id=%s failed=request_timeout stage=%s "
                     "duration_ms=%.1f stage_duration_ms=%.1f limit_s=%.1f",
                     request_id, diag.get("stage"),
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000,
                     _REQUEST_TIMEOUT)
        raise SearchPlaybackError("timeout") from None
    except Exception as err:
        logger.error("MA voice request_id=%s failed=api_error stage=%s "
                     "error_type=%s duration_ms=%.1f stage_duration_ms=%.1f",
                     request_id, diag.get("stage"), type(err).__name__,
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise SearchPlaybackError("api_error") from None
    finally:
        if not keep_pending_for_late_push:
            shared_store.end_voice_request(request_id, player_id)


SHORTCUTS = ("favorites", "last_radio", "recent")
_FAVORITES_LIMIT = 50

_SHORTCUT_PATTERNS = (
    ("favorites", re.compile(r"\b(?:meus|minhas) (?:musicas )?favorit[oa]s\b|\bmusicas favoritas\b")),
    ("last_radio", re.compile(r"\bultima radio\b|\bradio (?:que )?(?:estava a ouvir|eu estava ouvindo|de ontem)\b")),
    ("recent", re.compile(r"\b(?:o que|aquilo que) (?:eu )?estava (?:a ouvir|ouvindo)\b|\bultima coisa que ouvi\b")),
)


def shortcut_for_query(query):
    """Map a misrouted spoken query (e.g. 'os meus favoritos') to a shortcut."""
    normal = _normal(query or "")
    for kind, pattern in _SHORTCUT_PATTERNS:
        if pattern.search(normal):
            return kind
    return None


async def _shortcut_media(client, kind, player_id, diag):
    """Resolve a voice shortcut to something MA can play on this queue."""
    started = time.monotonic()
    diag["stage"] = f"shortcut.{kind}"
    diag["stage_started"] = started
    if kind == "favorites":
        tracks = await client.music.get_library_tracks(favorite=True, limit=500)
        media = [item.uri for item in tracks or [] if getattr(item, "uri", None)]
        random.shuffle(media)
        media = media[:_FAVORITES_LIMIT]
    else:
        media_types = [MediaType.RADIO] if kind == "last_radio" else None
        # Prefer what played on this Echo; fall back to the most recent overall.
        item = None
        for queue_id in (player_id, None):
            items = await client.music.recently_played(
                limit=10, media_types=media_types, queue_id=queue_id,
                fully_played_only=False)
            item = next((i for i in items or [] if getattr(i, "uri", None)), None)
            if item:
                break
        media = item.uri if item else None
    _log_stage(diag, f"shortcut.{kind}", started, found=bool(media))
    if not media:
        raise SearchPlaybackError("not_found")
    return media


_FIRST_PLAYABLE_ATTEMPTS = 8


async def _play_first_playable(client, player_id, uris):
    """Start the first track MA can resolve and return the ones left to queue.

    MA rejects a whole list when one favourite is no longer available at its
    provider, so start a single track first and queue the others afterwards.
    """
    for index, uri in enumerate(uris[:_FIRST_PLAYABLE_ATTEMPTS]):
        try:
            await client.player_queues.play_media(
                queue_id=player_id, media=uri, option=QueueOption.REPLACE)
        except MediaNotFoundError:
            logger.info("Skipping unavailable favourite %s", uri)
            continue
        return uris[index + 1:]
    raise SearchPlaybackError("not_found")


async def _enqueue_each(server_url, token, player_id, uris):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            for uri in uris:
                try:
                    await client.player_queues.play_media(
                        queue_id=player_id, media=uri, option=QueueOption.ADD)
                except MediaNotFoundError:
                    logger.info("Skipping unavailable favourite %s", uri)


def _enqueue_in_background(server_url, token, player_id, uris):
    """Queue the remaining tracks after Alexa already has its response."""
    if not uris:
        return

    def run():
        try:
            asyncio.run(_enqueue_each(server_url, token, player_id, uris))
        except Exception as err:
            logger.warning("Could not queue remaining favourites for %s: %s",
                           player_id, type(err).__name__)

    threading.Thread(target=run, name="enqueue-favourites", daemon=True).start()


async def _play_shortcut(kind, player_id, old_version, server_url, token, diag):
    async with aiohttp.ClientSession() as session:
        connection_started = time.monotonic()
        diag["stage"] = "connect"
        diag["stage_started"] = connection_started
        async with MusicAssistantClient(server_url, session, token=token) as client:
            _log_stage(diag, "connect", connection_started)
            media = await _shortcut_media(client, kind, player_id, diag)
            play_started = time.monotonic()
            diag["stage"] = "play_media"
            diag["stage_started"] = play_started
            if isinstance(media, list):
                rest = await _play_first_playable(client, player_id, media)
                _enqueue_in_background(server_url, token, player_id, rest)
            else:
                await client.player_queues.play_media(
                    queue_id=player_id, media=media, option=QueueOption.REPLACE)
            _log_stage(diag, "play_media", play_started, player_id=player_id)

    deadline = time.monotonic() + _PUSH_TIMEOUT
    push_started = time.monotonic()
    diag["stage"] = "stream_push_wait"
    diag["stage_started"] = push_started
    while time.monotonic() < deadline:
        current = shared_store.get_ma(player_id) or {}
        if current.get("version", 0) > old_version and current.get("streamUrl"):
            _log_stage(diag, "stream_push_wait", push_started,
                       player_id=player_id, outcome="received")
            return current
        await asyncio.sleep(0.1)
    _log_stage(diag, "stream_push_wait", push_started, "timeout",
               player_id=player_id)
    raise SearchPlaybackError("stream_timeout")


def play_shortcut(kind, player_id, request_id=None):
    """Play favourites / the last radio / the last item on this Echo's queue."""
    if kind not in SHORTCUTS:
        raise ValueError(f"Unknown shortcut: {kind}")
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url:
        raise SearchPlaybackError("missing_api")

    old_version = (shared_store.get_ma(player_id) or {}).get("version", 0)
    request_id = request_id or uuid.uuid4().hex[:10]
    diag = {"request_id": request_id, "stage": "start"}
    started = time.monotonic()
    shared_store.begin_voice_request(request_id, player_id, old_version)
    logger.info("MA voice request_id=%s intent_type=shortcut.%s player_id=%s",
                request_id, kind, player_id)
    keep_pending_for_late_push = False
    try:
        return asyncio.run(asyncio.wait_for(
            _play_shortcut(kind, player_id, old_version, server_url, token, diag),
            timeout=_REQUEST_TIMEOUT,
        ))
    except SearchPlaybackError as err:
        keep_pending_for_late_push = str(err) == "stream_timeout"
        logger.warning("MA voice request_id=%s failed=%s stage=%s duration_ms=%.1f",
                       request_id, str(err), diag.get("stage"),
                       (time.monotonic() - started) * 1000)
        raise
    except asyncio.TimeoutError:
        keep_pending_for_late_push = True
        logger.error("MA voice request_id=%s failed=request_timeout stage=%s limit_s=%.1f",
                     request_id, diag.get("stage"), _REQUEST_TIMEOUT)
        raise SearchPlaybackError("timeout") from None
    except Exception as err:
        logger.error("MA voice request_id=%s failed=api_error stage=%s error_type=%s",
                     request_id, diag.get("stage"), type(err).__name__)
        raise SearchPlaybackError("api_error") from None
    finally:
        if not keep_pending_for_late_push:
            shared_store.end_voice_request(request_id, player_id)


async def _play_latest_joana(player_id, old_version, server_url, token, diag=None):
    async with aiohttp.ClientSession() as session:
        connection_started = time.monotonic()
        if diag is not None:
            diag["stage"] = "connect"
            diag["stage_started"] = connection_started
        async with MusicAssistantClient(server_url, session, token=token) as client:
            _log_stage(diag, "connect", connection_started)
            podcast = await client.music.get_item_by_uri(_JOANA_PODCAST_URI)
            if getattr(podcast, "media_type", None) != MediaType.PODCAST:
                raise SearchPlaybackError("not_found")
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None:
                raise SearchPlaybackError("player_unavailable")

            play_started = time.monotonic()
            if diag is not None:
                diag["stage"] = "play_media"
                diag["stage_started"] = play_started
            try:
                await client.player_queues.play_media(
                    queue_id=queue.queue_id,
                    media=_JOANA_PODCAST_URI,
                    option=QueueOption.REPLACE,
                    start_item="latest",
                )
            except Exception as err:
                _log_stage(diag, "play_media", play_started, "error",
                           player_id=player_id, error=type(err).__name__)
                raise
            _log_stage(diag, "play_media", play_started, player_id=player_id)

    deadline = time.monotonic() + _PUSH_TIMEOUT
    push_started = time.monotonic()
    if diag is not None:
        diag["stage"] = "stream_push_wait"
        diag["stage_started"] = push_started
    while time.monotonic() < deadline:
        current = shared_store.get_ma(player_id) or {}
        if current.get("version", 0) > old_version and current.get("streamUrl"):
            _log_stage(diag, "stream_push_wait", push_started,
                       player_id=player_id, outcome="received")
            return current
        await asyncio.sleep(0.1)
    _log_stage(diag, "stream_push_wait", push_started, "timeout",
               player_id=player_id)
    raise SearchPlaybackError("stream_timeout")


def play_latest_joana(player_id, request_id=None):
    """Play the newest library episode on the requesting Echo's MA queue."""
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url or not token:
        raise SearchPlaybackError("missing_api")

    old_version = (shared_store.get_ma(player_id) or {}).get("version", 0)
    request_id = request_id or uuid.uuid4().hex[:10]
    diag = {"request_id": request_id, "stage": "start"}
    started = time.monotonic()
    shared_store.begin_voice_request(request_id, player_id, old_version)
    logger.info("MA voice request_id=%s intent_type=joana player_id=%s",
                request_id, player_id)
    keep_pending_for_late_push = False
    try:
        return asyncio.run(asyncio.wait_for(
            _play_latest_joana(player_id, old_version, server_url, token, diag),
            timeout=_REQUEST_TIMEOUT,
        ))
    except SearchPlaybackError as err:
        keep_pending_for_late_push = str(err) == "stream_timeout"
        logger.warning("MA voice request_id=%s failed=%s stage=%s duration_ms=%.1f "
                       "stage_duration_ms=%.1f",
                       request_id, str(err), diag.get("stage"),
                       (time.monotonic() - started) * 1000,
                       (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise
    except (CannotConnect, ConnectionFailed, InvalidServerVersion, MusicAssistantError) as err:
        logger.error("MA voice request_id=%s failed=api_error stage=%s "
                     "error_type=%s duration_ms=%.1f stage_duration_ms=%.1f",
                     request_id, diag.get("stage"), type(err).__name__,
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise SearchPlaybackError("api_error") from None
    except asyncio.TimeoutError:
        keep_pending_for_late_push = True
        logger.error("MA voice request_id=%s failed=request_timeout stage=%s "
                     "duration_ms=%.1f stage_duration_ms=%.1f limit_s=%.1f",
                     request_id, diag.get("stage"),
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000,
                     _REQUEST_TIMEOUT)
        raise SearchPlaybackError("timeout") from None
    except Exception as err:
        logger.error("MA voice request_id=%s failed=api_error stage=%s "
                     "error_type=%s duration_ms=%.1f stage_duration_ms=%.1f",
                     request_id, diag.get("stage"), type(err).__name__,
                     (time.monotonic() - started) * 1000,
                     (time.monotonic() - diag.get("stage_started", started)) * 1000)
        raise SearchPlaybackError("api_error") from None
    finally:
        if not keep_pending_for_late_push:
            shared_store.end_voice_request(request_id, player_id)
