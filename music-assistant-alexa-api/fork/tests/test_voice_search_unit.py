import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fork_ext import voice_search as ma_voice_search
from music_assistant_models.enums import MediaType


def artist(name, uri):
    return SimpleNamespace(name=name, uri=uri)


def track(name, uri, artists=()):
    return SimpleNamespace(name=name, uri=uri, artists=list(artists))


def item_with_name(name, uri):
    return SimpleNamespace(name=name, uri=uri)


def result(media_type, items=()):
    field = {
        "artist": "artists",
        "track": "tracks",
        "radio": "radio",
        "album": "albums",
        "playlist": "playlists",
        "podcast": "podcasts",
    }[media_type]
    return SimpleNamespace(**{field: list(items)})


def client_for(responses):
    async def search(**kwargs):
        query = kwargs["search_query"]
        library_only = kwargs.get("library_only", False)
        values = {}
        for requested_type in kwargs["media_types"]:
            media_type = requested_type.value
            key = (query, media_type, library_only)
            field = {
                "artist": "artists",
                "track": "tracks",
                "radio": "radio",
                "album": "albums",
                "playlist": "playlists",
                "podcast": "podcasts",
            }[media_type]
            values[field] = getattr(responses.get(key, result(media_type)), field)
        return SimpleNamespace(**values)

    return SimpleNamespace(music=SimpleNamespace(search=AsyncMock(side_effect=search)))


def test_artist_prefers_exact_local_result_and_skips_global_search():
    local_artist = artist("Underworld", "library://artist/33")
    client = client_for({("Underworld", "artist", True): result("artist", [local_artist])})

    selected = asyncio.run(ma_voice_search.find_media(client, "Underworld", "artist"))

    assert selected is local_artist
    client.music.search.assert_awaited_once()
    assert client.music.search.await_args.kwargs["library_only"] is True


def test_track_intent_bare_artist_uses_exact_artist_fallback_not_fuzzy_track():
    exact_artist = artist("Pedro Abrunhosa", "deezer://artist/183216")
    fuzzy_track = track("Tudo o Que Eu Te Dou (Ao Vivo)", "deezer://track/1")
    client = client_for({
        ("Pedro Abrunhosa", "track", False): result("track", [fuzzy_track]),
        ("Pedro Abrunhosa", "artist", False): result("artist", [exact_artist]),
    })

    selected = asyncio.run(ma_voice_search.find_media(client, "Pedro Abrunhosa", "track"))

    assert selected is exact_artist
    assert [call.kwargs["media_types"][0] for call in client.music.search.await_args_list] == [
        MediaType.TRACK, MediaType.TRACK, MediaType.ARTIST
    ]


def test_track_intent_prefers_exact_local_artist_before_global_track_search():
    exact_artist = artist("Underworld", "library://artist/33")
    client = client_for({("Underworld", "artist", True): result("artist", [exact_artist])})

    selected = asyncio.run(ma_voice_search.find_media(client, "Underworld", "track"))

    assert selected is exact_artist
    client.music.search.assert_awaited_once()
    assert client.music.search.await_args.kwargs["media_types"] == [
        MediaType.TRACK, MediaType.ARTIST, MediaType.RADIO
    ]
    assert client.music.search.await_args.kwargs["library_only"] is True


def test_artist_track_queue_uses_library_artist_and_caps_discography():
    tracks = [track(f"Track {index}", f"library://track/{index}") for index in range(12)]
    get_artist_tracks = AsyncMock(return_value=tracks)
    client = SimpleNamespace(music=SimpleNamespace(get_artist_tracks=get_artist_tracks))

    queued = asyncio.run(ma_voice_search._artist_track_uris(
        client, artist("Underworld", "library://artist/33"),
        {"request_id": "req-local"},
    ))

    assert queued == [f"library://track/{index}" for index in range(8)]
    assert get_artist_tracks.await_args.kwargs == {
        "item_id": "33",
        "provider_instance_id_or_domain": "library",
        "in_library_only": True,
    }


def test_artist_track_queue_uses_provider_artist_without_library_filter():
    tracks = [track("Toxic", "deezer--account://track/7")]
    get_artist_tracks = AsyncMock(return_value=tracks)
    client = SimpleNamespace(music=SimpleNamespace(get_artist_tracks=get_artist_tracks))

    queued = asyncio.run(ma_voice_search._artist_track_uris(
        client, artist("Britney Spears", "deezer--account://artist/9"),
        {"request_id": "req-provider"},
    ))

    assert queued == ["deezer--account://track/7"]
    assert get_artist_tracks.await_args.kwargs["provider_instance_id_or_domain"] == "deezer--account"
    assert get_artist_tracks.await_args.kwargs["in_library_only"] is False


def test_artist_search_queues_tracks_instead_of_full_artist_uri(monkeypatch):
    local_artist = artist("Underworld", "library://artist/33")
    tracks = [track(f"Track {index}", f"library://track/{index}") for index in range(11)]
    played = {}
    state = {"version": 0}

    class DummyContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.music = SimpleNamespace(
                search=AsyncMock(return_value=result("artist", [local_artist])),
                get_artist_tracks=AsyncMock(return_value=tracks),
            )

            async def play_media(**kwargs):
                played.update(kwargs)
                state.update({"version": 1, "streamUrl": "https://music.example/flow/server/echo/1/file.mp3"})

            self.player_queues = SimpleNamespace(play_media=AsyncMock(side_effect=play_media))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(ma_voice_search.aiohttp, "ClientSession", DummyContext)
    monkeypatch.setattr(ma_voice_search, "MusicAssistantClient", FakeClient)
    monkeypatch.setattr(ma_voice_search.upstream, "latest_snapshot", lambda _player=None: state)
    diag = {"request_id": "req-artist"}

    pushed = asyncio.run(ma_voice_search._search_and_play(
        "Underworld", "artist", "Echo Flex", 0, "ws://ma", "token", diag
    ))

    assert played["media"] == [f"library://track/{index}" for index in range(8)]
    assert "library://artist/33" not in played["media"]
    assert pushed["streamUrl"].startswith("https://music.example/flow/")
    assert diag["selected_media_type"] == "artist"


def test_exact_title_with_de_phrase_is_not_split_as_artist():
    exact_track = track("Lohengrin - Sonho de Elsa", "library://track/160")
    client = client_for({
        ("Lohengrin - Sonho de Elsa", "track", True): result("track", [exact_track]),
    })

    selected = asyncio.run(ma_voice_search.find_media(
        client, "Lohengrin - Sonho de Elsa", "track"
    ))

    assert selected is exact_track
    client.music.search.assert_awaited_once()


def test_fuzzy_track_without_exact_artist_match_is_rejected():
    fuzzy_track = track("Sola System", "deezer://track/1")
    client = client_for({
        ("Underworld", "track", False): result("track", [fuzzy_track]),
    })

    with pytest.raises(ma_voice_search.SearchPlaybackError, match="not_found"):
        asyncio.run(ma_voice_search.find_media(client, "Underworld", "track"))


def test_album_search_prefers_local_and_strips_prefix():
    local_album = item_with_name("A Hundred Days Off", "library://album/81")
    client = client_for({
        ("A Hundred Days Off", "album", True): result("album", [local_album]),
    })

    selected = asyncio.run(ma_voice_search.find_media(client, "álbum A Hundred Days Off", "album"))
    assert selected is local_album


def test_playlist_search_finds_exact_or_substring():
    rock = item_with_name("Rock Anos 80", "library://playlist/rock80")
    client = client_for({
        ("Rock Anos 80", "playlist", True): result("playlist", [rock]),
    })

    selected = asyncio.run(ma_voice_search.find_media(client, "playlist Rock Anos 80", "playlist"))
    assert selected is rock


def test_podcast_search_finds_matching_podcast_with_network_prefix():
    desagradavel = item_with_name("Renascença - Extremamente Desagradável", "library://podcast/1")
    client = client_for({
        ("Extremamente Desagradável", "podcast", True): result("podcast", [desagradavel]),
    })

    selected = asyncio.run(ma_voice_search.find_media(client, "podcast Extremamente Desagradável", "podcast"))
    assert selected is desagradavel
