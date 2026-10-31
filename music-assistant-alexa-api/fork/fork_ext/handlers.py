"""Portuguese (pt-BR) voice handlers layered on top of the upstream skill."""

import logging
import re
import time

from ask_sdk_core.dispatch_components import (
    AbstractRequestInterceptor,
    AbstractExceptionHandler,
    AbstractRequestHandler,
)
from ask_sdk_core.utils import is_intent_name, is_request_type

from . import ma_extras, messages, voice_search
from . import upstream_api as upstream

logger = logging.getLogger(__name__)

# A stream pushed by Music Assistant this recently means the user just started
# music from the Music Assistant app and expects the Echo to follow.
FRESH_PUSH_SECONDS = 15


def _speak_and_ask(handler_input, speech, reprompt=messages.ASK_WHAT_TO_PLAY):
    handler_input.response_builder.speak(speech).ask(reprompt)
    return handler_input.response_builder.response


def _speak_and_end(handler_input, speech):
    handler_input.response_builder.speak(speech).set_should_end_session(True)
    return handler_input.response_builder.response


def _request_id(handler_input):
    return getattr(handler_input.request_envelope.request, "request_id", None) or "unknown"


def _audio_capable(handler_input):
    """False on devices that cannot play audio; upstream refuses those first."""
    return upstream.device_supports_audio(handler_input)


def _slot_query(intent):
    for slot in (getattr(intent, "slots", None) or {}).values():
        if getattr(slot, "value", None):
            return slot.value.strip()
    return ""


def _play_pushed_stream(handler_input, player_id, fallback_title):
    """Pin the stream Music Assistant just pushed and play it on this Echo."""
    upstream.pin_stream(player_id)
    url, _audio = upstream.stream_url(handler_input.request_envelope.request)
    if not url:
        raise voice_search.SearchPlaybackError("stream_timeout")
    title = upstream.current_info().get("primaryText") or fallback_title
    return upstream.play(handler_input, url, messages.PLAYING.format(title))


# ------------------------------------------------------------ launch / open

class LaunchHandler(AbstractRequestHandler):
    """Opening the skill asks what to play, unless music was just started from
    Music Assistant itself (or the user asked to play the current stream).
    """

    def can_handle(self, handler_input):
        return (is_request_type("LaunchRequest")(handler_input)
                or is_intent_name("PlayAudio")(handler_input))

    def handle(self, handler_input):
        request = handler_input.request_envelope.request
        url, _audio = upstream.stream_url(request)
        snapshot = upstream.latest_snapshot(upstream.player_for(handler_input)) or {}
        fresh_push = bool(url) and time.time() - snapshot.get("timestamp", 0) < FRESH_PUSH_SECONDS
        explicit_play = is_intent_name("PlayAudio")(handler_input)

        if not (url and (explicit_play or fresh_push)):
            logger.info("Conversational mode: asking what to play "
                        "(play_audio=%s, fresh_push=%s)", explicit_play, fresh_push)
            return _speak_and_ask(handler_input, messages.ASK_WHAT_TO_PLAY)

        response = upstream.default_launch_handler().handle(handler_input)
        if not getattr(response, "directives", None):
            # The stream could not be started (stale URL): keep the dialogue open.
            logger.info("Stream not playable; asking what to play instead")
            handler_input.response_builder.speak(messages.ASK_WHAT_TO_PLAY).ask(
                messages.ASK_WHAT_TO_PLAY)
            return handler_input.response_builder.response
        return response


class HelpHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        handler_input.response_builder.speak(messages.HELP).ask(messages.ASK_WHAT_TO_PLAY)
        return handler_input.response_builder.response


class FallbackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        return _speak_and_ask(handler_input, messages.NOT_UNDERSTOOD)


class ErrorHandler(AbstractExceptionHandler):
    """Any failure keeps the dialogue open instead of ending the session."""

    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)
        return _speak_and_ask(handler_input, messages.ERROR_ASK_AGAIN)


# ------------------------------------------------------------ search / play

class LatestJoanaHandler(AbstractRequestHandler):
    """Play the newest Extremamente Desagradável episode on this Echo."""

    def can_handle(self, handler_input):
        return _audio_capable(handler_input) and is_intent_name("LatestJoanaIntent")(handler_input)

    def handle(self, handler_input):
        request_id = _request_id(handler_input)
        started = time.monotonic()
        player_id = upstream.player_for(handler_input)
        if not player_id:
            logger.warning("Music Assistant intent=LatestJoanaIntent has no player mapping")
            return _speak_and_end(handler_input, messages.NOT_MAPPED)

        logger.info("Alexa request_id=%s intent=LatestJoanaIntent player_id=%s",
                    request_id, player_id)
        try:
            voice_search.play_latest_joana(player_id)
            response = _play_pushed_stream(
                handler_input, player_id, "o último Extremamente Desagradável")
        except voice_search.SearchPlaybackError as err:
            return _speak_and_end(handler_input, messages.JOANA_ERRORS.get(
                str(err), messages.JOANA_ERRORS["api_error"]))
        logger.info("Alexa request_id=%s stage=response_ready total_ms=%.1f",
                    request_id, (time.monotonic() - started) * 1000)
        return response


class SpecificMediaHandler(AbstractRequestHandler):
    """Search for a requested track, artist, radio, album, playlist or podcast."""

    INTENT_TYPES = {
        "PlayTrackIntent": "track",
        "PlayArtistIntent": "artist",
        "PlayRadioIntent": "radio",
        "PlayAlbumIntent": "album",
        "PlayPlaylistIntent": "playlist",
        "PlayPodcastIntent": "podcast",
    }

    def can_handle(self, handler_input):
        intent = getattr(handler_input.request_envelope.request, "intent", None)
        return bool(intent and intent.name in self.INTENT_TYPES) and _audio_capable(handler_input)

    def handle(self, handler_input):
        request = handler_input.request_envelope.request
        request_id = _request_id(handler_input)
        started = time.monotonic()
        intent_name = request.intent.name
        media_type = self.INTENT_TYPES[intent_name]
        logger.info("Alexa request received request_id=%s intent=%s", request_id, intent_name)

        query = _slot_query(request.intent)
        if not query:
            return _speak_and_ask(handler_input, messages.ASK_WHAT_TO_PLAY)

        # "toca {query}" can swallow "toca os meus favoritos" and similar.
        shortcut = voice_search.shortcut_for_query(query)
        if shortcut:
            logger.info("Alexa request_id=%s query=%r routed to shortcut=%s",
                        request_id, query, shortcut)
            return play_shortcut_response(handler_input, shortcut)

        player_id = upstream.player_for(handler_input)
        if not player_id:
            logger.warning("Music Assistant intent=%s query=%r has no player mapping",
                           intent_name, query)
            return _speak_and_end(handler_input, messages.NOT_MAPPED)

        logger.info("Alexa request_id=%s intent=%s recognized_query=%r player_id=%s",
                    request_id, intent_name, query, player_id)
        try:
            voice_search.play_search_result(query, media_type, player_id, request_id=request_id)
            response = _play_pushed_stream(handler_input, player_id, query)
        except voice_search.SearchPlaybackError as err:
            error = messages.SEARCH_ERRORS.get(str(err), messages.SEARCH_ERRORS["api_error"])
            return _speak_and_ask(handler_input, f"{error} {messages.ASK_WHAT_TO_PLAY}")
        logger.info("Alexa request_id=%s stage=response_ready total_ms=%.1f",
                    request_id, (time.monotonic() - started) * 1000)
        return response


def play_shortcut_response(handler_input, kind):
    """Start a favourites / last radio / recent shortcut on this Echo."""
    request_id = _request_id(handler_input)
    player_id = upstream.player_for(handler_input)
    if not player_id:
        return _speak_and_end(handler_input, messages.NOT_MAPPED)

    logger.info("Alexa request_id=%s shortcut=%s player_id=%s", request_id, kind, player_id)
    try:
        voice_search.play_shortcut(kind, player_id, request_id=request_id)
        return _play_pushed_stream(handler_input, player_id, messages.SHORTCUT_TITLES[kind])
    except voice_search.SearchPlaybackError as err:
        errors = dict(messages.SHORTCUT_COMMON_ERRORS, not_found=messages.SHORTCUT_ERRORS[kind])
        error = errors.get(str(err), errors["api_error"])
        return _speak_and_ask(handler_input, f"{error} {messages.ASK_WHAT_TO_PLAY}")


class ShortcutHandler(AbstractRequestHandler):
    """Favourites, last radio and 'what I was listening to'."""

    INTENT_KINDS = {
        "PlayFavoritesIntent": "favorites",
        "PlayLastRadioIntent": "last_radio",
        "PlayRecentIntent": "recent",
    }

    def can_handle(self, handler_input):
        intent = getattr(handler_input.request_envelope.request, "intent", None)
        return bool(intent and intent.name in self.INTENT_KINDS) and _audio_capable(handler_input)

    def handle(self, handler_input):
        intent_name = handler_input.request_envelope.request.intent.name
        return play_shortcut_response(handler_input, self.INTENT_KINDS[intent_name])


# ------------------------------------------------------------- now playing

class NowPlayingHandler(AbstractRequestHandler):
    """Say the title and artist of what is playing on this Echo."""

    def can_handle(self, handler_input):
        return is_intent_name("NowPlayingIntent")(handler_input)

    def handle(self, handler_input):
        player_id = upstream.player_for(handler_input)
        info = ma_extras.get_now_playing(player_id) if player_id else None
        if not info or not info.get("title"):
            # Fall back to the metadata MA pushed with the current stream.
            pushed = upstream.current_info()
            info = {"title": pushed.get("primaryText") or "",
                    "artist": (pushed.get("secondaryText") or "").split(" - ")[0]}
        if info.get("title") and info.get("artist"):
            speech = messages.NOW_PLAYING_BOTH.format(title=info["title"], artist=info["artist"])
        elif info.get("title"):
            speech = messages.NOW_PLAYING_TITLE.format(title=info["title"])
        else:
            speech = messages.NOW_PLAYING_NONE
        return _speak_and_end(handler_input, speech)


# ------------------------------------------------------------- sleep timer

def duration_seconds(value):
    """Parse an ISO-8601 AMAZON.DURATION value such as PT30M or PT1H15M."""
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "")
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def spoken_duration(seconds):
    hours, minutes = divmod(round(seconds / 60), 60)
    parts = []
    if hours:
        parts.append("1 hora" if hours == 1 else f"{hours} horas")
    if minutes:
        parts.append("1 minuto" if minutes == 1 else f"{minutes} minutos")
    return " e ".join(parts) or "menos de um minuto"


class SleepTimerHandler(AbstractRequestHandler):
    """Pause the music on this Echo after a spoken duration."""

    MAX_SECONDS = 4 * 3600
    MIN_SECONDS = 60

    def can_handle(self, handler_input):
        return is_intent_name("SleepTimerIntent")(handler_input)

    def handle(self, handler_input):
        slots = getattr(handler_input.request_envelope.request.intent, "slots", None) or {}
        seconds = duration_seconds(getattr(slots.get("duration"), "value", None))
        if not seconds or seconds < self.MIN_SECONDS or seconds > self.MAX_SECONDS:
            return _speak_and_ask(handler_input, messages.SLEEP_ASK, messages.SLEEP_ASK)

        device_id = upstream.device_id(handler_input)
        player_id = upstream.player_for(handler_input)
        if not player_id:
            return _speak_and_end(handler_input, messages.NOT_MAPPED)

        ma_extras.set_sleep_timer(device_id, player_id, seconds)
        logger.info("Sleep timer set player_id=%s seconds=%d", player_id, seconds)
        return _speak_and_end(
            handler_input, messages.SLEEP_SET.format(spoken_duration(seconds)))


class CancelSleepTimerHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("CancelSleepTimerIntent")(handler_input)

    def handle(self, handler_input):
        cancelled = ma_extras.cancel_sleep_timer(upstream.device_id(handler_input))
        return _speak_and_end(
            handler_input, messages.SLEEP_CANCELLED if cancelled else messages.SLEEP_NONE)


# --------------------------------------------------------- shuffle / repeat

class ShuffleRepeatHandler(AbstractRequestHandler):
    """Shuffle and repeat, applied to the mapped Music Assistant queue."""

    COMMANDS = {
        "AMAZON.ShuffleOnIntent": ("shuffle_on", messages.SHUFFLE_ON),
        "AMAZON.ShuffleOffIntent": ("shuffle_off", messages.SHUFFLE_OFF),
        "AMAZON.LoopOnIntent": ("repeat_on", messages.REPEAT_ON),
        "AMAZON.LoopOffIntent": ("repeat_off", messages.REPEAT_OFF),
    }

    def can_handle(self, handler_input):
        intent = getattr(handler_input.request_envelope.request, "intent", None)
        return bool(intent and intent.name in self.COMMANDS)

    def handle(self, handler_input):
        player_id = upstream.player_for(handler_input)
        if not player_id:
            logger.warning("No MA player mapped for this device (shuffle/loop)")
            return _speak_and_end(handler_input, messages.NOT_MAPPED)
        command, speech = self.COMMANDS[handler_input.request_envelope.request.intent.name]
        if not ma_extras.send_queue_command(player_id, command):
            speech = messages.QUEUE_CHANGE_FAILED
        return _speak_and_end(handler_input, speech)


# Handlers tried before upstream's own voice handler. Upstream's English model
# uses some of the same intent names (PlayArtistIntent, PlayAlbumIntent,
# PlayPlaylistIntent, PlayFavoritesIntent), so these must win for pt-BR.
PRIORITY_HANDLERS = (
    LatestJoanaHandler,
    SpecificMediaHandler,
    ShortcutHandler,
)

# The remaining handlers, tried just before upstream's launch handler.
REQUEST_HANDLERS = (
    LaunchHandler,
    NowPlayingHandler,
    SleepTimerHandler,
    CancelSleepTimerHandler,
    ShuffleRepeatHandler,
    HelpHandler,
    FallbackHandler,
)


class PortugueseStrings(AbstractRequestInterceptor):
    """Say upstream's remaining messages in European Portuguese.

    Runs after upstream's localisation interceptor and only wraps the lookup:
    a message is replaced when its English original is one of upstream's
    `data` constants named in `messages.UPSTREAM_OVERRIDES`; anything else
    (including messages upstream adds later) keeps upstream's own translation.
    """

    def process(self, handler_input):
        request = handler_input.request_envelope.request
        if not (getattr(request, "locale", None) or "").startswith("pt"):
            return
        attributes = handler_input.attributes_manager.request_attributes
        upstream_translate = attributes.get("_")
        if upstream_translate is None:
            return
        replacements = {}
        for name, text in messages.UPSTREAM_OVERRIDES.items():
            original = getattr(upstream.data, name, None)
            if original:
                replacements[original] = text
        attributes["_"] = lambda message: replacements.get(message) or upstream_translate(message)
