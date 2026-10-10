import sys
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "music-assistant-alexa-api"
sys.path[:0] = [
    str(ADDON / "skill-overlay/app"),
    str(ADDON / "skill-api/app"),
]


@pytest.mark.parametrize(("media_type", "result_attr", "uri"), [
    ("track", "tracks", "spotify://track/123"),
    ("radio", "radio", "radiobrowser://station/456"),
])
def test_voice_search_plays_result_and_waits_for_pushed_stream(
    monkeypatch, media_type, result_attr, uri
):
    import shared_store
    import skill

    skill.__path__.insert(0, str(ADDON / "skill-overlay/app/skill"))
    voice = importlib.import_module("skill.ma_voice_search")

    shared_store.set_ma({"streamUrl": "https://old.example/old.mp3"})
    selected = SimpleNamespace(uri=uri)
    called = {}

    class FakeClient:
        def __init__(self, server_url, session, token=None):
            called["server_url"] = server_url
            called["token"] = token
            self.music = SimpleNamespace(search=self.search)
            self.player_queues = SimpleNamespace(play_media=self.play_media)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def search(self, **kwargs):
            called["search"] = kwargs
            return SimpleNamespace(**{result_attr: [selected]})

        async def play_media(self, **kwargs):
            called["play"] = kwargs
            shared_store.set_ma({"streamUrl": "https://streams.example/new.mp3"})

    monkeypatch.setattr(voice, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(voice, "get_env_secret", lambda key: {
        "MA_API_URL": "http://music-assistant:8095",
        "MA_API_TOKEN": "test-token",
    }.get(key))

    stream = voice.play_search_result("Sola Sistim Underworld", media_type, "echo-dot")

    assert called["search"]["search_query"] == "Sola Sistim Underworld"
    assert called["play"]["queue_id"] == "echo-dot"
    assert called["play"]["media"] == uri
    assert called["play"]["option"].value == "replace"
    assert stream["streamUrl"] == "https://streams.example/new.mp3"
