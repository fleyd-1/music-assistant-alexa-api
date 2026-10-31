"""The only module of this fork that touches the upstream skill's internals.

Everything the Portuguese extension needs from the upstream skill goes through
the small functions below. When a new upstream release renames or reshapes
something, this file is the one place to adjust; `tests/test_fork_contract.py`
fails first and says exactly which function broke.
"""

import logging

import shared_store
from ask_sdk_model.interfaces.audioplayer import AudioItemMetadata, PlayDirective
from ask_sdk_model.interfaces.display import Image, ImageInstance
from skill import data, device_mapping, ma_control, util
from skill import lambda_function as _upstream_skill

logger = logging.getLogger(__name__)

_FALLBACK_ART_URL = (
    "https://raw.githubusercontent.com/timlaing/music-assistant-alexa-skill/"
    "main/assets/icons/ma_512x512.png"
)

# Upstream objects the extension builds on, re-exported under stable names.
skill_builder = _upstream_skill.sb
mark_ma_triggered = ma_control.mark_ma_triggered
send_player_command = ma_control.send_player_command


# ----------------------------------------------------------------- requests

def device_id(handler_input):
    return _upstream_skill._device_id_from(handler_input)


def player_for(handler_input):
    """Music Assistant player mapped to the Echo that sent this request."""
    return device_mapping.get_player_for_device(device_id(handler_input))


def supports_apl(handler_input):
    return _upstream_skill._supports_apl(handler_input)


# ------------------------------------------------------------------ streams

def latest_snapshot(player_id):
    """Latest stream Music Assistant pushed for this player (has a 'version')."""
    if not player_id:
        return shared_store.get_ma()  # unmapped Echo: the latest push, as upstream does
    return shared_store.get_player_snapshot(player_id)


def pin_stream(player_id):
    """Make this request's metadata and URL those of `player_id`'s own stream."""
    data.select_stream(latest_snapshot(player_id))


def current_info():
    return data.get_info()


def stream_url(request):
    """(url, audio_data) of the stream pinned for this request."""
    return _upstream_skill._get_stream_url(request)


def begin_voice_request(request_id, player_id, old_version):
    """Hook kept for log correlation; upstream tracks pushes per player itself."""


def end_voice_request(request_id, player_id):
    """Counterpart of begin_voice_request."""


# ------------------------------------------------------------------ playing

def _audio_metadata():
    info = current_info()
    title = (info.get("primaryText") or "Music Assistant").strip()
    subtitle = (info.get("secondaryText") or "Music Assistant").strip()
    art_url = (info.get("coverImageSource") or _FALLBACK_ART_URL).strip()
    background_url = (info.get("backgroundImageSource") or art_url).strip()
    return AudioItemMetadata(
        title=title,
        subtitle=subtitle,
        art=Image(sources=[ImageInstance(url=art_url)]),
        background_image=Image(sources=[ImageInstance(url=background_url)]),
    )


def play(handler_input, url, text):
    """Start `url` on this Echo straight away, with title and cover art attached.

    The URL was just pushed by Music Assistant, so the pre-flight reachability
    check is skipped: Alexa itself validates the stream with its playback GET.
    """
    response = util.play(
        url=url,
        offset=0,
        text=text,
        response_builder=handler_input.response_builder,
        supports_apl=supports_apl(handler_input),
        validate=False,
    )
    for directive in getattr(response, "directives", None) or []:
        if isinstance(directive, PlayDirective) and directive.audio_item:
            try:
                directive.audio_item.metadata = _audio_metadata()
            except Exception:
                logger.exception("Could not attach audio metadata")
    return response


# ---------------------------------------------------------- skill assembly

def upstream_handler_index(class_name):
    """Position of the upstream request handler called `class_name`."""
    for index, chain in enumerate(skill_builder.runtime_configuration_builder.request_handler_chains):
        if type(chain.request_handler).__name__ == class_name:
            return index
    raise RuntimeError(
        f"Upstream skill has no request handler named {class_name}; "
        "update fork_ext/upstream_api.py for this upstream release."
    )


def default_launch_handler():
    """Upstream's own launch / PlayAudio handler, used when the stream is playable."""
    return _upstream_skill.LaunchRequestOrPlayAudioHandler()


def register_first(request_handlers=(), exception_handlers=(), before="LaunchRequestOrPlayAudioHandler"):
    """Register handlers so they are tried before the upstream ones.

    Request handlers go in front of the upstream handler called `before`
    (after the device-capability check); exception handlers go to the front.
    """
    builder = skill_builder.runtime_configuration_builder
    position = upstream_handler_index(before)
    for offset, handler in enumerate(request_handlers):
        skill_builder.add_request_handler(handler)
        chain = builder.request_handler_chains.pop()
        builder.request_handler_chains.insert(position + offset, chain)
    for offset, handler in enumerate(exception_handlers):
        skill_builder.add_exception_handler(handler)
        builder.exception_handlers.insert(offset, builder.exception_handlers.pop())


def device_supports_audio(handler_input):
    """True unless the device reports that it cannot play audio at all."""
    try:
        interfaces = handler_input.request_envelope.context.system.device.supported_interfaces
    except AttributeError:
        return True
    return interfaces is None or interfaces.audio_player is not None
