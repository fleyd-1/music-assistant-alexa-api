import json
import threading
from types import SimpleNamespace

import pytest
from music_assistant_models.errors import MediaNotFoundError

import shared_store
from skill import lambda_function as handlers
from skill import ma_control, ma_voice_search


def _intent_payload(intent_name, slots=None, application_id='test', device_id='test-device'):
    intent = {'name': intent_name, 'confirmationStatus': 'NONE'}
    if slots:
        intent['slots'] = {name: {'name': name, 'value': value, 'confirmationStatus': 'NONE'}
                           for name, value in slots.items()}
    return {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
        'context': {'System': {'application': {'applicationId': application_id},
            'user': {'userId': 'test'}, 'device': {'deviceId': device_id,
            'supportedInterfaces': {'AudioPlayer': {}}}}},
        'request': {'type': 'IntentRequest', 'requestId': 'test',
            'timestamp': '2026-10-29T17:00:00Z', 'locale': 'pt-BR',
            'intent': intent}}


def _launch_payload(application_id):
    payload = _intent_payload('unused', application_id=application_id)
    payload['request'] = {'type': 'LaunchRequest', 'requestId': 'test',
                          'timestamp': '2026-10-29T17:00:00Z', 'locale': 'pt-BR'}
    return payload


def _speech(response):
    return response.get('outputSpeech', {}).get('ssml', '')


# ---------------------------------------------------------------- skill ID

def test_foreign_skill_requests_are_rejected_silently(monkeypatch, tmp_path):
    deployment = tmp_path / 'skill-deployment.json'
    deployment.write_text(json.dumps({'skill_id': 'amzn1.ask.skill.mine'}))
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(deployment))
    monkeypatch.setattr(shared_store, '_ma', None)

    response = handlers.lambda_handler(_launch_payload('amzn1.ask.skill.someone-else'), None)['response']

    assert response.get('shouldEndSession') is True
    assert 'outputSpeech' not in response
    assert not response.get('directives')


def test_own_skill_launch_still_opens_the_dialogue(monkeypatch, tmp_path):
    """'Alexa, hora da música' must keep asking what to play."""
    deployment = tmp_path / 'skill-deployment.json'
    deployment.write_text(json.dumps({'skill_id': 'amzn1.ask.skill.mine'}))
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(deployment))
    monkeypatch.setattr(shared_store, '_ma', None)

    response = handlers.lambda_handler(_launch_payload('amzn1.ask.skill.mine'), None)['response']

    assert response['shouldEndSession'] is False
    assert 'Diz-me o que queres ouvir' in _speech(response)


def test_requests_are_allowed_before_any_skill_is_deployed(monkeypatch, tmp_path):
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(tmp_path / 'missing.json'))
    monkeypatch.setattr(shared_store, '_ma', None)

    response = handlers.lambda_handler(_launch_payload('anything'), None)['response']

    assert 'Diz-me o que queres ouvir' in _speech(response)


# ---------------------------------------------------------------- now playing

def test_now_playing_says_title_and_artist(monkeypatch):
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', {'test-device': 'Dot'}.get)
    monkeypatch.setattr(handlers.ma_control, 'get_now_playing',
                        lambda player_id: {'title': 'Lisboa', 'artist': 'Ana Moura'})

    response = handlers.lambda_handler(_intent_payload('NowPlayingIntent'), None)['response']

    assert 'Lisboa' in _speech(response) and 'Ana Moura' in _speech(response)
    assert response['shouldEndSession'] is True


def test_now_playing_falls_back_to_pushed_metadata(monkeypatch):
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', {'test-device': 'Dot'}.get)
    monkeypatch.setattr(handlers.ma_control, 'get_now_playing', lambda player_id: None)
    monkeypatch.setattr(handlers.data, 'get_info',
                        lambda: {'primaryText': 'Rádio Comercial', 'secondaryText': ''})

    response = handlers.lambda_handler(_intent_payload('NowPlayingIntent'), None)['response']

    assert 'Rádio Comercial' in _speech(response)


# ---------------------------------------------------------------- sleep timer

@pytest.mark.parametrize(('value', 'seconds'), [
    ('PT30M', 1800), ('PT1H', 3600), ('PT1H15M', 4500), ('PT90S', 90), ('P1D', 86400),
    ('', None), ('garbage', None), ('P', None),
])
def test_duration_parsing(value, seconds):
    assert handlers._duration_seconds(value) == seconds


def test_sleep_timer_intent_schedules_pause(monkeypatch):
    scheduled = []
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', {'test-device': 'Dot'}.get)
    monkeypatch.setattr(handlers.ma_control, 'set_sleep_timer',
                        lambda device_id, player_id, seconds: scheduled.append((device_id, player_id, seconds)))

    response = handlers.lambda_handler(
        _intent_payload('SleepTimerIntent', {'duration': 'PT30M'}), None)['response']

    assert scheduled == [('test-device', 'Dot', 1800)]
    assert '30 minutos' in _speech(response)


def test_sleep_timer_without_duration_asks_again(monkeypatch):
    monkeypatch.setattr(handlers.ma_control, 'set_sleep_timer',
                        lambda *args: pytest.fail('must not schedule'))

    response = handlers.lambda_handler(_intent_payload('SleepTimerIntent'), None)['response']

    assert response['shouldEndSession'] is False


def test_sleep_timer_fires_pause_and_marks_the_echo(monkeypatch):
    fired = threading.Event()
    sent = []

    def fake_send(player_id, command):
        sent.append((player_id, command))
        fired.set()
        return True

    monkeypatch.setattr(ma_control, 'send_player_command', fake_send)
    ma_control.set_sleep_timer('echo-1', 'Dot', 0.05)

    assert fired.wait(2)
    assert sent == [('Dot', 'pause')]
    # The echoed AMAZON.PauseIntent must not be sent back to MA.
    assert ma_control.is_echo_of_ma_command('echo-1', 'pause')


def test_sleep_timer_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(ma_control, 'send_player_command',
                        lambda *args: pytest.fail('cancelled timer fired'))
    ma_control.set_sleep_timer('echo-2', 'Dot', 0.2)

    assert ma_control.cancel_sleep_timer('echo-2') is True
    assert ma_control.cancel_sleep_timer('echo-2') is False
    threading.Event().wait(0.4)


# ---------------------------------------------------------------- shortcuts

@pytest.mark.parametrize(('query', 'kind'), [
    ('os meus favoritos', 'favorites'),
    ('as minhas músicas favoritas', 'favorites'),
    ('meus favoritos', 'favorites'),
    ('a última rádio', 'last_radio'),
    ('a rádio que estava a ouvir', 'last_radio'),
    ('o que estava a ouvir', 'recent'),
    ('Ana Moura', None),
    ('rádio comercial', None),
])
def test_misrouted_queries_map_to_shortcuts(query, kind):
    assert ma_voice_search.shortcut_for_query(query) == kind


def test_play_track_with_favourites_query_uses_shortcut(monkeypatch):
    used = []
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', {'test-device': 'Dot'}.get)
    monkeypatch.setattr(handlers.ma_voice_search, 'play_search_result',
                        lambda *args, **kwargs: pytest.fail('must not search'))
    monkeypatch.setattr(handlers.ma_voice_search, 'play_shortcut',
                        lambda kind, player_id, request_id=None: used.append((kind, player_id)))
    monkeypatch.setattr(handlers.data, 'begin_request', lambda player_id: None)
    monkeypatch.setattr(handlers, '_get_stream_url', lambda request: ('https://audio.example/fav.mp3', {}))
    monkeypatch.setattr(handlers.util, 'play',
                        lambda **kwargs: kwargs['response_builder'].speak(kwargs['text']).response)

    handlers.lambda_handler(_intent_payload('PlayTrackIntent', {'query': 'os meus favoritos'}), None)

    assert used == [('favorites', 'Dot')]


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
                             'player_id': 'Dot'})


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
