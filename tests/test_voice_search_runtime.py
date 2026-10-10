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
            shared_store.set_ma({"streamUrl": "https://streams.example/new.mp3", "player_id": "echo-dot"})

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


@pytest.mark.parametrize('query', ['Sola Sistim dos Underworld', 'Sola Sistim Underworld'])
def test_title_and_artist_are_verified(query):
    import asyncio
    from skill.ma_voice_search import find_media
    good = SimpleNamespace(uri='library://track/744', name='Sola Sistim',
                           artists=[SimpleNamespace(name='Underworld')])
    wrong = SimpleNamespace(uri='library://track/1', name='Sola Sistim',
                            artists=[SimpleNamespace(name='Other artist')])
    async def search(**kwargs):
        return SimpleNamespace(tracks=[wrong, good] if kwargs['search_query'] == 'Sola Sistim' else [])
    client = SimpleNamespace(music=SimpleNamespace(search=search))
    assert asyncio.run(find_media(client, query, 'track')) is good


def test_unknown_artist_does_not_play_another_artist():
    import asyncio
    from skill.ma_voice_search import find_media, SearchPlaybackError
    async def search(**kwargs):
        return SimpleNamespace(tracks=[SimpleNamespace(uri='library://track/744',
            name='Sola Sistim', artists=[SimpleNamespace(name='Underworld')])])
    with pytest.raises(SearchPlaybackError, match='not_found'):
        asyncio.run(find_media(SimpleNamespace(music=SimpleNamespace(search=search)),
                              'Sola Sistim dos Unknown', 'track'))


def test_radio_prefers_saved_library_station():
    import asyncio
    from skill.ma_voice_search import find_media
    async def search(**kwargs):
        return SimpleNamespace(radio=[SimpleNamespace(uri='radiobrowser://radio/other'),
                                      SimpleNamespace(uri='library://radio/3')])
    result = asyncio.run(find_media(SimpleNamespace(music=SimpleNamespace(search=search)),
                                   'Rádio Comercial', 'radio'))
    assert result.uri == 'library://radio/3'
