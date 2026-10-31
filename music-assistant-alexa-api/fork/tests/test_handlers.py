"""What Luis hears: every voice command, through the complete skill."""

import threading
from types import SimpleNamespace

import pytest
import shared_store
from ask_sdk_core.serialize import DefaultSerializer
from fork_ext import handlers, ma_extras, upstream_api, voice_search
from skill import data, device_mapping, util

DOT_STREAM = "https://music.example/flow/server/Dot/stream1/Dot.mp3"


def speech(response):
    return response.get("outputSpeech", {}).get("ssml", "")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(shared_store, "_ma", None)
    monkeypatch.setattr(shared_store, "_player_snapshots", {})
    monkeypatch.setattr(ma_extras, "_sleep_timers", {})


@pytest.fixture
def mapped(monkeypatch):
    """The test Echo is mapped to the Music Assistant player 'Dot'."""
    monkeypatch.setattr(device_mapping, "get_player_for_device", {"test-device": "Dot"}.get)


@pytest.fixture
def playable(monkeypatch):
    """Streams are reachable and the public host needs no rewriting."""
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kwargs: "music.example")
    monkeypatch.setattr(util, "replace_ip_in_url", lambda url, hostname: url)
    monkeypatch.setattr(util, "get_setting",
                        lambda name, default="": "true" if name == "SKIP_URL_VALIDATION" else default)
    monkeypatch.setattr(util, "push_alexa_metadata", lambda url: None)


def push(title="Lisboa", artist="Ana Moura", url=DOT_STREAM):
    shared_store.set_ma({"streamUrl": url, "title": title, "artist": artist,
                         "imageUrl": "https://music.example/cover.jpg"})


def directive(response):
    return DefaultSerializer().serialize(response["directives"][0])


# ------------------------------------------------------------- opening the skill

def test_launch_without_stream_keeps_session_open(invoke):
    response = invoke("LaunchRequest", device_id="new-device")
    assert response["shouldEndSession"] is False
    assert "reprompt" in response
    assert "Diz-me o que queres ouvir" in speech(response)
    assert not response.get("directives")


def test_launch_with_an_old_stream_still_asks_what_to_play(invoke, mapped, playable):
    push()
    shared_store._player_snapshots["Dot"]["timestamp"] -= 3600
    response = invoke("LaunchRequest")
    assert response["shouldEndSession"] is False
    assert "Diz-me o que queres ouvir" in speech(response)
    assert not response.get("directives")


def test_launch_right_after_music_started_in_music_assistant_plays_it(invoke, mapped, playable):
    push()
    response = invoke("LaunchRequest")
    assert response["directives"][0]["playBehavior"] == "REPLACE_ALL"


def test_play_audio_intent_emits_play_when_stream_available(invoke, mapped, playable):
    push()
    shared_store._player_snapshots["Dot"]["timestamp"] -= 3600
    response = invoke(intent="PlayAudio")
    assert response["directives"][0]["playBehavior"] == "REPLACE_ALL"


def test_launch_with_expired_stream_prompts_instead_of_ending(invoke, monkeypatch, caplog):
    expired = "https://music.example/single/secret-token/Dot/expired/Dot.mp3"
    push(url=expired)
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kwargs: "music.example")
    monkeypatch.setattr(util, "replace_ip_in_url", lambda url, hostname: url)
    monkeypatch.setattr(util, "get_setting", lambda name, default="": default)
    failed = lambda *args, **kwargs: SimpleNamespace(status_code=404, close=lambda: None)  # noqa: E731
    monkeypatch.setattr(util.requests, "head", failed)
    monkeypatch.setattr(util.requests, "get", failed)

    response = invoke("LaunchRequest", device_id="new-device")

    assert response["shouldEndSession"] is False
    assert "Diz-me o que queres ouvir" in speech(response)
    assert "reprompt" in response
    assert not response.get("directives")


def test_help_and_fallback_keep_talking_in_portuguese(invoke):
    help_response = invoke(intent="AMAZON.HelpIntent")
    assert "Bem-vindo ao Music Assistant" in speech(help_response)
    assert help_response["shouldEndSession"] is False
    fallback = invoke(intent="AMAZON.FallbackIntent")
    assert "Não percebi" in speech(fallback)
    assert fallback["shouldEndSession"] is False


def test_unexpected_errors_keep_the_dialogue_open(invoke, mapped, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(voice_search, "play_search_result", boom)
    response = invoke(intent="PlayTrackIntent", slots={"query": "Lisboa"})
    assert response["shouldEndSession"] is False
    assert "Ocorreu um erro" in speech(response)


# ------------------------------------------------------- asking for something

@pytest.mark.parametrize(("intent", "media_type"), [
    ("PlayTrackIntent", "track"), ("PlayArtistIntent", "artist"), ("PlayRadioIntent", "radio"),
    ("PlayAlbumIntent", "album"), ("PlayPlaylistIntent", "playlist"),
    ("PlayPodcastIntent", "podcast"),
])
def test_asking_for_media_searches_and_plays_the_pushed_stream(
        invoke, mapped, playable, monkeypatch, intent, media_type):
    calls = []

    def fake_search(query, kind, player_id, request_id=None):
        calls.append((query, kind, player_id))
        push(title="Sola Sistim", artist="Underworld")

    monkeypatch.setattr(voice_search, "play_search_result", fake_search)

    response = invoke(intent=intent, slots={"query": "Sola Sistim"})

    assert calls == [("Sola Sistim", media_type, "Dot")]
    assert "A tocar Sola Sistim." in speech(response)
    play = directive(response)
    assert play["type"] == "AudioPlayer.Play"
    assert play["audioItem"]["stream"]["url"] == DOT_STREAM


def test_play_directive_carries_title_artist_and_cover_art(invoke, mapped, playable, monkeypatch):
    monkeypatch.setattr(voice_search, "play_search_result",
                        lambda *args, **kwargs: push("Fé (Ao Vivo)", "IZA"))
    response = invoke(intent="PlayTrackIntent", slots={"query": "Fé"})
    metadata = directive(response)["audioItem"]["metadata"]
    assert metadata["title"] == "Fé (Ao Vivo)"
    assert metadata["subtitle"] == "IZA"
    assert metadata["art"]["sources"][0]["url"] == "https://music.example/cover.jpg"


def test_metadata_falls_back_to_default_art_when_nothing_is_known(monkeypatch):
    monkeypatch.setattr(data, "get_info", lambda: {})
    metadata = upstream_api._audio_metadata().to_dict()
    assert metadata["title"] == "Music Assistant"
    assert metadata["subtitle"] == "Music Assistant"
    assert metadata["art"]["sources"][0]["url"].endswith("ma_512x512.png")


def test_voice_playback_skips_the_slow_stream_probe(invoke, mapped, monkeypatch):
    """The stream was just pushed by Music Assistant; Alexa validates it itself."""
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kwargs: "music.example")
    monkeypatch.setattr(util, "replace_ip_in_url", lambda url, hostname: url)
    monkeypatch.setattr(util, "get_setting", lambda name, default="": default)
    monkeypatch.setattr(util, "push_alexa_metadata", lambda url: None)
    monkeypatch.setattr(util.requests, "head",
                        lambda *args, **kwargs: pytest.fail("stream must not be probed"))
    monkeypatch.setattr(voice_search, "play_search_result", lambda *args, **kwargs: push())
    response = invoke(intent="PlayTrackIntent", slots={"query": "Lisboa"})
    assert response["directives"][0]["playBehavior"] == "REPLACE_ALL"


def test_search_failure_keeps_session_open_with_reprompt(invoke, mapped, monkeypatch):
    def fail(*args, **kwargs):
        raise voice_search.SearchPlaybackError("not_found")

    monkeypatch.setattr(voice_search, "play_search_result", fail)
    response = invoke(intent="PlayTrackIntent", slots={"query": "nonexistent"})
    assert response["shouldEndSession"] is False
    assert "Não encontrei isso no Music Assistant" in speech(response)
    assert "Diz-me o que queres ouvir" in speech(response)
    assert "reprompt" in response


@pytest.mark.parametrize(("code", "text"), [
    ("missing_api", "ainda não está configurada"),
    ("stream_timeout", "não enviou o áudio a tempo"),
    ("timeout", "demorou demasiado"),
    ("api_error", "Não consegui pesquisar"),
])
def test_search_errors_are_explained(invoke, mapped, monkeypatch, code, text):
    def fail(*args, **kwargs):
        raise voice_search.SearchPlaybackError(code)

    monkeypatch.setattr(voice_search, "play_search_result", fail)
    response = invoke(intent="PlayTrackIntent", slots={"query": "x"})
    assert text in speech(response)


def test_missing_stream_after_playing_reports_timeout(invoke, mapped, monkeypatch):
    monkeypatch.setattr(voice_search, "play_search_result", lambda *args, **kwargs: None)
    response = invoke(intent="PlayTrackIntent", slots={"query": "x"})
    assert "não enviou o áudio a tempo" in speech(response)


def test_empty_query_asks_again(invoke, mapped, monkeypatch):
    monkeypatch.setattr(voice_search, "play_search_result",
                        lambda *args, **kwargs: pytest.fail("must not search"))
    response = invoke(intent="PlayTrackIntent")
    assert response["shouldEndSession"] is False
    assert "Diz-me o que queres ouvir" in speech(response)


def test_unmapped_echo_is_told_to_pair_it(invoke, monkeypatch):
    monkeypatch.setattr(voice_search, "play_search_result",
                        lambda *args, **kwargs: pytest.fail("must not search"))
    response = invoke(intent="PlayTrackIntent", slots={"query": "Lisboa"}, device_id="stranger")
    assert "ainda não está associada" in speech(response)
    assert response["shouldEndSession"] is True


def test_joana_routes_by_request_device_and_rejects_unknown(invoke, playable, monkeypatch):
    seen = []
    mapping = {"dot-device": "Inês's Echo Dot", "flex-device": "Inês's Echo Flex"}
    monkeypatch.setattr(device_mapping, "get_player_for_device", mapping.get)
    monkeypatch.setattr(voice_search, "play_latest_joana", lambda player: (seen.append(player), push())[1])

    for device_id in ("dot-device", "flex-device", "unknown-device"):
        response = invoke(intent="LatestJoanaIntent", device_id=device_id)
        assert "outputSpeech" in response

    assert seen == ["Inês's Echo Dot", "Inês's Echo Flex"]


def test_joana_reports_a_missing_podcast(invoke, mapped, monkeypatch):
    def fail(player_id):
        raise voice_search.SearchPlaybackError("not_found")

    monkeypatch.setattr(voice_search, "play_latest_joana", fail)
    response = invoke(intent="LatestJoanaIntent")
    assert "Não encontrei o podcast" in speech(response)


# --------------------------------------------------------------- shortcuts

@pytest.mark.parametrize(("query", "kind"), [
    ("os meus favoritos", "favorites"), ("as minhas músicas favoritas", "favorites"),
    ("meus favoritos", "favorites"), ("a última rádio", "last_radio"),
    ("a rádio que estava a ouvir", "last_radio"), ("o que estava a ouvir", "recent"),
    ("Ana Moura", None), ("rádio comercial", None),
])
def test_misrouted_queries_map_to_shortcuts(query, kind):
    assert voice_search.shortcut_for_query(query) == kind


def test_play_track_with_favourites_query_uses_the_shortcut(invoke, mapped, playable, monkeypatch):
    used = []
    monkeypatch.setattr(voice_search, "play_search_result",
                        lambda *args, **kwargs: pytest.fail("must not search"))
    monkeypatch.setattr(voice_search, "play_shortcut",
                        lambda kind, player_id, request_id=None: (used.append((kind, player_id)), push())[1])
    invoke(intent="PlayTrackIntent", slots={"query": "os meus favoritos"})
    assert used == [("favorites", "Dot")]


@pytest.mark.parametrize(("intent", "kind"), [
    ("PlayFavoritesIntent", "favorites"), ("PlayLastRadioIntent", "last_radio"),
    ("PlayRecentIntent", "recent"),
])
def test_shortcut_intents_start_playback(invoke, mapped, playable, monkeypatch, intent, kind):
    used = []
    monkeypatch.setattr(voice_search, "play_shortcut",
                        lambda k, player_id, request_id=None: (used.append((k, player_id)), push())[1])
    response = invoke(intent=intent)
    assert used == [(kind, "Dot")]
    assert response["directives"][0]["playBehavior"] == "REPLACE_ALL"


def test_shortcut_with_nothing_found_says_so_and_keeps_listening(invoke, mapped, monkeypatch):
    def fail(kind, player_id, request_id=None):
        raise voice_search.SearchPlaybackError("not_found")

    monkeypatch.setattr(voice_search, "play_shortcut", fail)
    response = invoke(intent="PlayLastRadioIntent")
    assert "Não encontrei nenhuma rádio ouvida recentemente" in speech(response)
    assert response["shouldEndSession"] is False


# --------------------------------------------------------------- now playing

def test_now_playing_says_title_and_artist(invoke, mapped, monkeypatch):
    monkeypatch.setattr(ma_extras, "get_now_playing",
                        lambda player_id: {"title": "Lisboa", "artist": "Ana Moura"})
    response = invoke(intent="NowPlayingIntent")
    assert "Está a tocar Lisboa, de Ana Moura." in speech(response)
    assert response["shouldEndSession"] is True


def test_now_playing_title_only(invoke, mapped, monkeypatch):
    monkeypatch.setattr(ma_extras, "get_now_playing",
                        lambda player_id: {"title": "Rádio Comercial", "artist": ""})
    assert "Está a tocar Rádio Comercial." in speech(invoke(intent="NowPlayingIntent"))


def test_now_playing_falls_back_to_pushed_metadata(invoke, mapped, monkeypatch):
    monkeypatch.setattr(ma_extras, "get_now_playing", lambda player_id: None)
    push(title="Rádio Comercial", artist="")
    assert "Rádio Comercial" in speech(invoke(intent="NowPlayingIntent"))


def test_now_playing_with_nothing_known(invoke, mapped, monkeypatch):
    monkeypatch.setattr(ma_extras, "get_now_playing", lambda player_id: None)
    assert "Não consegui saber" in speech(invoke(intent="NowPlayingIntent"))


# --------------------------------------------------------------- sleep timer

@pytest.mark.parametrize(("value", "seconds"), [
    ("PT30M", 1800), ("PT1H", 3600), ("PT1H15M", 4500), ("PT90S", 90), ("P1D", 86400),
    ("", None), ("garbage", None), ("P", None), (None, None),
])
def test_duration_parsing(value, seconds):
    assert handlers.duration_seconds(value) == seconds


@pytest.mark.parametrize(("seconds", "spoken"), [
    (1800, "30 minutos"), (3600, "1 hora"), (4500, "1 hora e 15 minutos"),
    (7200, "2 horas"), (60, "1 minuto"), (20, "menos de um minuto"),
])
def test_spoken_duration(seconds, spoken):
    assert handlers.spoken_duration(seconds) == spoken


def test_sleep_timer_intent_schedules_pause(invoke, mapped, monkeypatch):
    scheduled = []
    monkeypatch.setattr(ma_extras, "set_sleep_timer",
                        lambda device_id, player_id, seconds: scheduled.append((device_id, player_id, seconds)))
    response = invoke(intent="SleepTimerIntent", slots={"duration": "PT30M"})
    assert scheduled == [("test-device", "Dot", 1800)]
    assert "30 minutos" in speech(response)


@pytest.mark.parametrize("duration", [None, "PT10S", "PT5H", "garbage"])
def test_sleep_timer_with_unusable_duration_asks_again(invoke, mapped, monkeypatch, duration):
    monkeypatch.setattr(ma_extras, "set_sleep_timer", lambda *args: pytest.fail("must not schedule"))
    slots = {"duration": duration} if duration else None
    response = invoke(intent="SleepTimerIntent", slots=slots)
    assert response["shouldEndSession"] is False


def test_sleep_timer_on_unmapped_echo(invoke, monkeypatch):
    monkeypatch.setattr(ma_extras, "set_sleep_timer", lambda *args: pytest.fail("must not schedule"))
    response = invoke(intent="SleepTimerIntent", slots={"duration": "PT30M"}, device_id="stranger")
    assert "ainda não está associada" in speech(response)


def test_sleep_timer_fires_pause_and_marks_the_echo(monkeypatch):
    fired = threading.Event()
    sent = []
    marked = []
    monkeypatch.setattr(upstream_api, "send_player_command",
                        lambda player_id, command: (sent.append((player_id, command)), fired.set(), True)[2])
    monkeypatch.setattr(upstream_api, "mark_ma_triggered",
                        lambda device_id, command: marked.append((device_id, command)))
    ma_extras.set_sleep_timer("echo-1", "Dot", 0.05)
    assert fired.wait(2)
    assert sent == [("Dot", "pause")]
    # The echoed AMAZON.PauseIntent must not be sent back to Music Assistant.
    assert marked == [("echo-1", "pause")]


def test_sleep_timer_restart_replaces_the_old_one(monkeypatch):
    sent = []
    done = threading.Event()
    monkeypatch.setattr(upstream_api, "mark_ma_triggered", lambda *args: None)
    monkeypatch.setattr(upstream_api, "send_player_command",
                        lambda player_id, command: (sent.append(player_id), done.set(), True)[2])
    ma_extras.set_sleep_timer("echo-3", "Dot", 0.3)
    ma_extras.set_sleep_timer("echo-3", "Flex", 0.05)
    assert done.wait(2)
    threading.Event().wait(0.5)
    assert sent == ["Flex"]


def test_sleep_timer_can_be_cancelled(invoke, mapped, monkeypatch):
    monkeypatch.setattr(upstream_api, "send_player_command",
                        lambda *args: pytest.fail("cancelled timer fired"))
    ma_extras.set_sleep_timer("test-device", "Dot", 0.2)
    response = invoke(intent="CancelSleepTimerIntent")
    assert "Cancelei" in speech(response)
    threading.Event().wait(0.4)
    assert "Não havia" in speech(invoke(intent="CancelSleepTimerIntent"))


# --------------------------------------------------------- shuffle / repeat

def test_shuffle_and_loop_intents_send_commands_to_mapped_player(invoke, mapped, monkeypatch):
    sent = []
    monkeypatch.setattr(ma_extras, "send_queue_command",
                        lambda player_id, command: sent.append((player_id, command)) or True)
    spoken = {}
    for intent, command in [("AMAZON.ShuffleOnIntent", "shuffle_on"),
                            ("AMAZON.ShuffleOffIntent", "shuffle_off"),
                            ("AMAZON.LoopOnIntent", "repeat_on"),
                            ("AMAZON.LoopOffIntent", "repeat_off")]:
        spoken[command] = speech(invoke(intent=intent))
    assert sent == [("Dot", "shuffle_on"), ("Dot", "shuffle_off"),
                    ("Dot", "repeat_on"), ("Dot", "repeat_off")]
    assert "aleatório ativado" in spoken["shuffle_on"]
    assert "Repetição desativada" in spoken["repeat_off"]


def test_shuffle_failure_is_reported(invoke, mapped, monkeypatch):
    monkeypatch.setattr(ma_extras, "send_queue_command", lambda *args: False)
    assert "Não foi possível" in speech(invoke(intent="AMAZON.ShuffleOnIntent"))


def test_shuffle_on_unmapped_echo(invoke, monkeypatch):
    monkeypatch.setattr(ma_extras, "send_queue_command", lambda *args: pytest.fail("no player"))
    response = invoke(intent="AMAZON.ShuffleOnIntent", device_id="stranger")
    assert "ainda não está associada" in speech(response)


@pytest.mark.parametrize("command", ["shuffle_on", "shuffle_off", "repeat_on", "repeat_off"])
def test_queue_commands_reach_the_music_assistant_queue(monkeypatch, command):
    calls = []

    class FakeQueues:
        async def shuffle(self, player_id, enabled):
            calls.append(("shuffle", player_id, enabled))

        async def repeat(self, player_id, mode):
            calls.append(("repeat", player_id, mode.value))

    class FakeClient:
        def __init__(self, server_url, session, token=None):
            self.player_queues = FakeQueues()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(ma_extras, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(ma_extras, "get_env_secret",
                        {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "t"}.get)
    assert ma_extras.send_queue_command("Dot", command) is True
    expected = {"shuffle_on": ("shuffle", "Dot", True), "shuffle_off": ("shuffle", "Dot", False),
                "repeat_on": ("repeat", "Dot", "all"), "repeat_off": ("repeat", "Dot", "off")}
    assert calls == [expected[command]]


def test_queue_command_rejects_unknown_commands_and_missing_config(monkeypatch):
    monkeypatch.setattr(ma_extras, "get_env_secret", lambda key: None)
    assert ma_extras.send_queue_command("Dot", "shuffle_on") is False
    monkeypatch.setattr(ma_extras, "get_env_secret", {"MA_API_URL": "http://ma"}.get)
    assert ma_extras.send_queue_command("Dot", "explode") is False


# -------------------------------------------- upstream behaviour is untouched

def test_pause_and_resume_still_reach_the_upstream_handlers(invoke, mapped):
    pause = invoke(intent="AMAZON.PauseIntent")
    assert "outputSpeech" in pause or pause.get("directives") is not None
    stop = invoke(intent="AMAZON.StopIntent")
    assert stop["shouldEndSession"] is True


def test_devices_without_audio_support_are_still_refused_first(invoke):
    from skill import lambda_function  # noqa: F401

    response = lambda_function.sb.lambda_handler()({
        "version": "1.0", "session": {"new": True, "sessionId": "t"},
        "context": {"System": {"application": {"applicationId": "test"}, "user": {"userId": "t"},
                               "device": {"deviceId": "x", "supportedInterfaces": {}}}},
        "request": {"type": "LaunchRequest", "requestId": "t", "timestamp": "2026-10-31T17:00:00Z",
                    "locale": "pt-BR"}}, None)["response"]
    assert response["shouldEndSession"] is True
    assert not response.get("directives")


# ------------------------------------------------- upstream messages in pt-PT

def _interceptor_input(locale, translate):
    attributes = {"_": translate}
    return SimpleNamespace(
        request_envelope=SimpleNamespace(request=SimpleNamespace(locale=locale)),
        attributes_manager=SimpleNamespace(request_attributes=attributes))


def test_upstream_messages_are_replaced_by_portuguese_pt(monkeypatch):
    handler_input = _interceptor_input("pt-BR", lambda message: f"upstream:{message}")
    handlers.PortugueseStrings().process(handler_input)
    translate = handler_input.attributes_manager.request_attributes["_"]
    assert translate(data.RESUME_MSG).format("Lisboa") == "A retomar Lisboa"
    assert "não é possível saltar" in translate(data.CANNOT_SKIP_MSG)
    # messages the fork does not override keep upstream's translation
    assert translate("a brand new upstream message") == "upstream:a brand new upstream message"


def test_other_languages_are_left_alone():
    handler_input = _interceptor_input("en-US", lambda message: message)
    handlers.PortugueseStrings().process(handler_input)
    translate = handler_input.attributes_manager.request_attributes["_"]
    assert translate(data.RESUME_MSG) == data.RESUME_MSG


def test_every_overridden_upstream_message_still_exists():
    """If upstream renames one of these constants the override silently stops;
    this makes that visible so the list in messages.py can be updated."""
    from fork_ext import messages

    missing = [name for name in messages.UPSTREAM_OVERRIDES if not getattr(data, name, None)]
    assert not missing, f"upstream no longer defines: {missing}"
