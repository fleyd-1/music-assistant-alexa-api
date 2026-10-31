import importlib
from types import SimpleNamespace

import pytest

@pytest.mark.parametrize("player_id", ["Inês's Echo Dot", "Inês's Echo Flex"])
def test_latest_joana_uses_requesting_player_queue(monkeypatch, player_id):
    import shared_store
    from music_assistant_models.enums import MediaType

    voice = importlib.import_module("fork_ext.voice_search")
    called = {}

    class FakeClient:
        def __init__(self, server_url, session, token=None):
            self.music = SimpleNamespace(get_item_by_uri=self.get_item_by_uri)
            self.player_queues = SimpleNamespace(
                get_active_queue=self.get_active_queue, play_media=self.play_media)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_item_by_uri(self, uri):
            called["podcast_uri"] = uri
            return SimpleNamespace(media_type=MediaType.PODCAST)

        async def get_active_queue(self, requested_player):
            called["player_id"] = requested_player
            return SimpleNamespace(queue_id=f"queue:{requested_player}")

        async def play_media(self, **kwargs):
            called["play"] = kwargs
            shared_store.set_ma({
                "streamUrl": "https://streams.example/new-episode.mp3",
                "playerId": player_id,
            })

    monkeypatch.setattr(voice, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(voice, "get_env_secret", lambda key: {
        "MA_API_URL": "http://music-assistant:8095",
        "MA_API_TOKEN": "test-token",
    }.get(key))

    stream = voice.play_latest_joana(player_id)

    assert called["podcast_uri"] == "library://podcast/1"
    assert called["player_id"] == player_id
    assert called["play"]["queue_id"] == f"queue:{player_id}"
    assert called["play"]["media"] == "library://podcast/1"
    assert called["play"]["start_item"] == "latest"
    assert called["play"]["option"].value == "replace"
    assert stream["playerId"] == player_id


@pytest.mark.parametrize(("media_type", "result_attr", "uri"), [
    ("track", "tracks", "spotify://track/123"),
    ("artist", "artists", "library://artist/iza"),
    ("radio", "radio", "radiobrowser://station/456"),
    ("album", "albums", "library://album/81"),
    ("playlist", "playlists", "library://playlist/rock"),
    ("podcast", "podcasts", "library://podcast/desagradavel"),
])
def test_voice_search_plays_result_and_waits_for_pushed_stream(
    monkeypatch, media_type, result_attr, uri
):
    import shared_store

    voice = importlib.import_module("fork_ext.voice_search")

    shared_store.set_ma({"streamUrl": "https://old.example/old.mp3"})
    selected = SimpleNamespace(uri=uri, name={
        "track": "Sola Sistim", "artist": "IZA", "radio": "Other Radio",
        "album": "A Hundred Days Off", "playlist": "Rock", "podcast": "Extremamente Desagradável"
    }.get(media_type), artists=[SimpleNamespace(name="Underworld")])
    called = {}

    class FakeClient:
        def __init__(self, server_url, session, token=None):
            called["server_url"] = server_url
            called["token"] = token
            self.music = SimpleNamespace(
                search=self.search, get_artist_tracks=self.get_artist_tracks
            )
            self.player_queues = SimpleNamespace(play_media=self.play_media)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def search(self, **kwargs):
            called["search"] = kwargs
            return SimpleNamespace(**{result_attr: [selected]})

        async def get_artist_tracks(self, **kwargs):
            called["artist_tracks"] = kwargs
            return [SimpleNamespace(uri="library://track/iza-1")]

        async def play_media(self, **kwargs):
            called["play"] = kwargs
            shared_store.set_ma({"streamUrl": "https://streams.example/new.mp3", "playerId": "echo-dot"})

    monkeypatch.setattr(voice, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(voice, "get_env_secret", lambda key: {
        "MA_API_URL": "http://music-assistant:8095",
        "MA_API_TOKEN": "test-token",
    }.get(key))

    query = {
        "track": "Sola Sistim", "artist": "IZA", "radio": "Other Radio",
        "album": "A Hundred Days Off", "playlist": "Rock", "podcast": "Extremamente Desagradável"
    }[media_type]
    stream = voice.play_search_result(query, media_type, "echo-dot")

    assert called["search"]["search_query"] == query
    assert called["play"]["queue_id"] == "echo-dot"
    if media_type == "artist":
        assert called["artist_tracks"]["item_id"] == "iza"
        assert called["play"]["media"] == ["library://track/iza-1"]
    else:
        assert called["play"]["media"] == uri
    if media_type == "podcast":
        assert called["play"]["start_item"] == "latest"
    assert called["play"]["option"].value == "replace"
    assert stream["streamUrl"] == "https://streams.example/new.mp3"


@pytest.mark.parametrize('query', ['Sola Sistim dos Underworld', 'Sola Sistim Underworld'])
def test_title_and_artist_are_verified(query):
    import asyncio
    from fork_ext.voice_search import find_media
    good = SimpleNamespace(uri='library://track/744', name='Sola Sistim',
                           artists=[SimpleNamespace(name='Underworld')])
    wrong = SimpleNamespace(uri='library://track/1', name='Sola Sistim',
                            artists=[SimpleNamespace(name='Other artist')])
    async def search(**kwargs):
        return SimpleNamespace(tracks=[wrong, good] if kwargs['search_query'] in (
            'Sola Sistim', 'Sola Sistim Underworld') else [])
    client = SimpleNamespace(music=SimpleNamespace(search=search))
    assert asyncio.run(find_media(client, query, 'track')) is good


def test_artist_search_plays_exact_music_assistant_artist():
    import asyncio
    from music_assistant_models.enums import MediaType
    from fork_ext.voice_search import find_media
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
    from fork_ext.voice_search import find_media, SearchPlaybackError
    async def search(**kwargs):
        return SimpleNamespace(artists=[SimpleNamespace(
            uri='library://artist/1', name='IZA Tribute')])
    with pytest.raises(SearchPlaybackError, match='not_found'):
        asyncio.run(find_media(
            SimpleNamespace(music=SimpleNamespace(search=search)), 'IZA', 'artist'))


def test_unknown_artist_does_not_play_another_artist():
    import asyncio
    from fork_ext.voice_search import find_media, SearchPlaybackError
    async def search(**kwargs):
        return SimpleNamespace(tracks=[SimpleNamespace(uri='library://track/744',
            name='Sola Sistim', artists=[SimpleNamespace(name='Underworld')])])
    with pytest.raises(SearchPlaybackError, match='not_found'):
        asyncio.run(find_media(SimpleNamespace(music=SimpleNamespace(search=search)),
                              'Sola Sistim dos Unknown', 'track'))


def test_radio_prefers_saved_library_station():
    import asyncio
    from fork_ext.voice_search import find_media
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
    from fork_ext.voice_search import find_media
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


@pytest.mark.parametrize(("query", "expected"), [
    ("oxigénio 102.6 FM", "oxigénio"),
    ("oxigênio 102.seis FM", "oxigênio"),
    ("rádio comercial 97.4 fm", "comercial"),
])
def test_radio_strips_frequency_and_fm_suffixes(query, expected):
    import asyncio
    from music_assistant_models.enums import MediaType
    from fork_ext.voice_search import find_media
    calls = []
    station = SimpleNamespace(uri='library://radio/10', name=expected)

    async def search(**kwargs):
        calls.append(kwargs)
        if kwargs.get('library_only') and kwargs['search_query'].casefold() == expected.casefold():
            return SimpleNamespace(radio=[station])
        return SimpleNamespace(radio=[])

    result = asyncio.run(find_media(
        SimpleNamespace(music=SimpleNamespace(search=search)), query, 'radio'))
    assert result is station
    assert any(c['search_query'].casefold() == expected.casefold() for c in calls)


def test_futura_voice_uses_live_official_stream_for_stale_saved_item(monkeypatch):
    import shared_store
    voice = importlib.import_module("fork_ext.voice_search")
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
                                 "playerId": "echo-dot"})

    monkeypatch.setattr(voice, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(voice, "get_env_secret", lambda key: {
        "MA_API_URL": "http://music-assistant:8095", "MA_API_TOKEN": "test-token",
    }.get(key))
    stream = voice.play_search_result("rádio futura", "radio", "echo-dot")
    assert called["media"] == "builtin://radio/https://s4.radio.co/s7e7b6c165/listen"
    assert stream["streamUrl"] == "https://stream.example/futura.mp3"


@pytest.mark.parametrize("query", ["iza", "isa", "i z a", "i.z.a."])
def test_artist_query_variants_matches_iza(query):
    import asyncio
    from fork_ext.voice_search import find_media
    calls = []
    artist = SimpleNamespace(uri="library://artist/182", name="IZA")

    async def search(**kwargs):
        calls.append(kwargs)
        if kwargs["search_query"].casefold() == "iza":
            return SimpleNamespace(artists=[artist])
        return SimpleNamespace(artists=[])

    result = asyncio.run(find_media(
        SimpleNamespace(music=SimpleNamespace(search=search)), query, "artist"))
    assert result is artist


@pytest.mark.parametrize(("query", "expected"), [
    ("antena 3", "Antena 3 (Portugal)"),
    ("oxigénio", "OXIGÉNIO 102.6 FM"),
    ("rádio oxigénio", "OXIGÉNIO 102.6 FM"),
    ("rádio antena 3", "Antena 3 (Portugal)"),
])
def test_track_intent_matches_local_radio(query, expected):
    """Ensure generic track play intent directly resolves local radio stations."""
    import asyncio
    from fork_ext.voice_search import find_media
    radio = SimpleNamespace(uri="library://radio/10", name=expected)

    async def search(**kwargs):
        if kwargs.get("library_only"):
            return SimpleNamespace(tracks=[], artists=[], radio=[radio])
        return SimpleNamespace(tracks=[], artists=[], radio=[])

    result = asyncio.run(find_media(
        SimpleNamespace(music=SimpleNamespace(search=search)), query, "track"))
    assert result is radio


