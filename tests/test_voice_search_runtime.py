import sys
import os
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "music-assistant-alexa-api"
sys.path[:0] = [os.environ["SKILL_APP_PATH"]] if os.environ.get("SKILL_APP_PATH") else [
    str(ADDON / "skill-overlay/app"),
    str(ADDON / "skill-api/app"),
]


@pytest.mark.parametrize(("media_type", "result_attr", "uri"), [
    ("track", "tracks", "spotify://track/123"),
    ("artist", "artists", "library://artist/iza"),
    ("radio", "radio", "radiobrowser://station/456"),
])
def test_voice_search_plays_result_and_waits_for_pushed_stream(
    monkeypatch, media_type, result_attr, uri
):
    import shared_store
    import skill

    if not os.environ.get("SKILL_APP_PATH"):
        skill.__path__.insert(0, str(ADDON / "skill-overlay/app/skill"))
    voice = importlib.import_module("skill.ma_voice_search")

    shared_store.set_ma({"streamUrl": "https://old.example/old.mp3"})
    selected = SimpleNamespace(uri=uri, name={
        "artist": "IZA", "radio": "Other Radio"}.get(media_type))
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

    query = "IZA" if media_type == "artist" else "Sola Sistim Underworld"
    stream = voice.play_search_result(query, media_type, "echo-dot")

    assert called["search"]["search_query"] == query
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


def test_artist_search_plays_exact_music_assistant_artist():
    import asyncio
    from music_assistant_models.enums import MediaType
    from skill.ma_voice_search import find_media
    calls = []
    iza = SimpleNamespace(uri='library://artist/182', name='IZA')

    async def search(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(artists=[iza])

    result = asyncio.run(find_media(
        SimpleNamespace(music=SimpleNamespace(search=search)), 'IZA', 'artist'))
    assert result is iza
    assert calls[0]['media_types'] == [MediaType.ARTIST]
    assert calls[0]['library_only'] is True


def test_artist_search_does_not_choose_a_partial_or_different_artist():
    import asyncio
    from skill.ma_voice_search import find_media, SearchPlaybackError
    async def search(**kwargs):
        return SimpleNamespace(artists=[SimpleNamespace(
            uri='library://artist/1', name='IZA Tribute')])
    with pytest.raises(SearchPlaybackError, match='not_found'):
        asyncio.run(find_media(
            SimpleNamespace(music=SimpleNamespace(search=search)), 'IZA', 'artist'))


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


@pytest.mark.parametrize(("query", "expected"), [
    ("rádio RFM", "RFM"),
    ("rádio rádio RFM", "RFM"),
    ("rádio futura rádio de autor", "futura"),
    ("rádio rádio Futura rádio de autor", "Futura"),
])
def test_radio_searches_concise_saved_station_name_first(query, expected):
    import asyncio
    from music_assistant_models.enums import MediaType
    from skill.ma_voice_search import find_media
    calls = []
    station = SimpleNamespace(uri='library://radio/3', name=expected)

    async def search(**kwargs):
        calls.append(kwargs)
        if kwargs.get('library_only') and kwargs['search_query'].casefold() == expected.casefold():
            return SimpleNamespace(radio=[station])
        return SimpleNamespace(radio=[])

    result = asyncio.run(find_media(
        SimpleNamespace(music=SimpleNamespace(search=search)), query, 'radio'))
    assert result is station
    assert calls[0]['search_query'] == expected
    assert calls[0]['media_types'] == [MediaType.RADIO]
    assert calls[0]['library_only'] is True
    assert len(calls) == 1


def test_futura_voice_uses_live_official_stream_for_stale_saved_item(monkeypatch):
    import shared_store
    import skill
    if not os.environ.get("SKILL_APP_PATH"):
        skill.__path__.insert(0, str(ADDON / "skill-overlay/app/skill"))
    voice = importlib.import_module("skill.ma_voice_search")
    saved = SimpleNamespace(uri="library://radio/11", name="FUTURA - Rádio de Autor")
    called = {}

    class FakeClient:
        def __init__(self, server_url, session, token=None):
            self.music = SimpleNamespace(search=self.search)
            self.player_queues = SimpleNamespace(play_media=self.play_media)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def search(self, **kwargs):
            if kwargs.get("library_only"):
                return SimpleNamespace(radio=[saved])
            return SimpleNamespace(radio=[])

        async def play_media(self, **kwargs):
            called.update(kwargs)
            shared_store.set_ma({"streamUrl": "https://stream.example/futura.mp3",
                                 "player_id": "echo-dot"})

    monkeypatch.setattr(voice, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(voice, "get_env_secret", lambda key: {
        "MA_API_URL": "http://music-assistant:8095", "MA_API_TOKEN": "test-token",
    }.get(key))
    stream = voice.play_search_result("rádio futura", "radio", "echo-dot")
    assert called["media"] == "builtin://radio/https://s4.radio.co/s7e7b6c165/listen"
    assert stream["streamUrl"] == "https://stream.example/futura.mp3"
