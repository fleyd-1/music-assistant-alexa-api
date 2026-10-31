"""Favourites, last radio and recent shortcuts against a fake Music Assistant."""

from types import SimpleNamespace

import pytest
import shared_store
from fork_ext import voice_search as ma_voice_search
from music_assistant_models.errors import MediaNotFoundError


class _FakeClient:
    calls = {}

    def __init__(self, server_url, session, token=None):
        self.music = SimpleNamespace(get_library_tracks=self.get_library_tracks,
                                     recently_played=self.recently_played)
        self.player_queues = SimpleNamespace(play_media=self.play_media)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_library_tracks(self, **kwargs):
        self.calls['tracks'] = kwargs
        return [SimpleNamespace(uri=f'library://track/{n}') for n in range(3)]

    async def recently_played(self, **kwargs):
        self.calls.setdefault('recent', []).append(kwargs)
        if kwargs.get('queue_id'):
            return []  # nothing on this Echo yet: fall back to global history
        return [SimpleNamespace(uri='library://radio/7', name='Antena 3')]

    async def play_media(self, **kwargs):
        self.calls.setdefault('attempts', []).append(kwargs['media'])
        if kwargs['media'] in self.calls.get('missing', ()):
            raise MediaNotFoundError('gone from provider')
        self.calls['play'] = kwargs
        shared_store.set_ma({'streamUrl': 'https://music.example/flow/s/Dot/x/Dot.mp3',
                             'playerId': 'Dot'})


@pytest.fixture
def fake_ma(monkeypatch):
    _FakeClient.calls = {}
    monkeypatch.setattr(ma_voice_search, 'MusicAssistantClient', _FakeClient)
    monkeypatch.setattr(ma_voice_search, 'get_env_secret', {
        'MA_API_URL': 'http://music-assistant:8095', 'MA_API_TOKEN': 'test-token'}.get)
    return _FakeClient.calls


def test_play_favourites_skips_unavailable_and_queues_the_rest(fake_ma, monkeypatch):
    queued = []
    monkeypatch.setattr(ma_voice_search.random, 'shuffle', lambda items: None)
    monkeypatch.setattr(ma_voice_search, '_enqueue_in_background',
                        lambda url, token, player_id, uris: queued.append((player_id, uris)))
    fake_ma['missing'] = {'library://track/0'}

    ma_voice_search.play_shortcut('favorites', 'Dot')

    assert fake_ma['tracks']['favorite'] is True
    assert fake_ma['attempts'] == ['library://track/0', 'library://track/1']
    assert fake_ma['play']['media'] == 'library://track/1'
    assert fake_ma['play']['option'].value == 'replace'
    assert queued == [('Dot', ['library://track/2'])]


def test_play_favourites_reports_not_found_when_none_play(fake_ma, monkeypatch):
    monkeypatch.setattr(ma_voice_search, '_enqueue_in_background', lambda *args: None)
    fake_ma['missing'] = {f'library://track/{n}' for n in range(3)}

    with pytest.raises(ma_voice_search.SearchPlaybackError, match='not_found'):
        ma_voice_search.play_shortcut('favorites', 'Dot')


def test_play_last_radio_falls_back_to_global_history(fake_ma):
    ma_voice_search.play_shortcut('last_radio', 'Dot')

    assert [call['queue_id'] for call in fake_ma['recent']] == ['Dot', None]
    assert fake_ma['recent'][0]['media_types'][0].value == 'radio'
    assert fake_ma['play']['media'] == 'library://radio/7'
