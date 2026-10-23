import shared_store
from skill import lambda_function as handlers
from skill import util
from types import SimpleNamespace


def test_streams_are_isolated_by_music_assistant_player():
    shared_store.set_ma({'streamUrl': 'https://music.example/flow/server/Dot/stream/Dot.mp3'})
    shared_store.set_ma({'streamUrl': 'https://music.example/single/server/Flex/stream/Flex.mp3'})
    assert '/Dot/' in shared_store.get_ma('Dot')['streamUrl']
    assert '/Flex/' in shared_store.get_ma('Flex')['streamUrl']
    assert shared_store.get_ma('Unknown') is None


def test_launch_without_stream_keeps_session_open(monkeypatch):
    monkeypatch.setattr(shared_store, '_ma', None)
    payload = {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
        'context': {'System': {'application': {'applicationId': 'test'},
            'user': {'userId': 'test'}, 'device': {'deviceId': 'new-device',
            'supportedInterfaces': {'AudioPlayer': {}}}}},
        'request': {'type': 'LaunchRequest', 'requestId': 'test',
                    'timestamp': '2026-10-10T17:00:00Z', 'locale': 'pt-BR'}}
    response = handlers.lambda_handler(payload, None)['response']
    assert response['shouldEndSession'] is False
    assert 'reprompt' in response
    assert 'Diz-me o que queres ouvir' in response['outputSpeech']['ssml']
    assert not response.get('directives')


def test_joana_intent_routes_by_request_device_and_rejects_unknown(monkeypatch):
    seen = []
    mapping = {'dot-device': "Inês's Echo Dot", 'flex-device': "Inês's Echo Flex"}
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', mapping.get)
    monkeypatch.setattr(handlers.ma_voice_search, 'play_latest_joana', seen.append)
    monkeypatch.setattr(handlers.data, 'begin_request', lambda player_id: None)
    monkeypatch.setattr(handlers, '_get_stream_url', lambda request: ('https://audio.example/episode.mp3', {}))

    def fake_play(**kwargs):
        return kwargs['response_builder'].speak('A tocar a Joana.').set_should_end_session(True).response

    monkeypatch.setattr(handlers.util, 'play', fake_play)

    for device_id in ('dot-device', 'flex-device', 'unknown-device'):
        payload = {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
            'context': {'System': {'application': {'applicationId': 'test'},
                'user': {'userId': 'test'}, 'device': {'deviceId': device_id,
                'supportedInterfaces': {'AudioPlayer': {}}}}},
            'request': {'type': 'IntentRequest', 'requestId': 'test',
                'timestamp': '2026-10-10T17:00:00Z', 'locale': 'pt-BR',
                'intent': {'name': 'LatestJoanaIntent', 'confirmationStatus': 'NONE'}}}
        result = handlers.lambda_handler(payload, None)['response']
        assert 'outputSpeech' in result

    assert seen == ["Inês's Echo Dot", "Inês's Echo Flex"]


def test_launch_with_expired_stream_prompts_instead_of_ending(monkeypatch, caplog):
    expired = 'https://music.example/single/secret-token/Dot/expired/Dot.mp3'
    monkeypatch.setattr(handlers, '_get_stream_url', lambda request: (expired, {}))
    monkeypatch.setattr(util, 'get_ma_hostname', lambda **kwargs: 'music.example')
    monkeypatch.setattr(util, 'replace_ip_in_url', lambda url, hostname: url)
    monkeypatch.setattr(util, 'get_setting', lambda name, default='': default)
    failed = lambda *args, **kwargs: SimpleNamespace(status_code=404, close=lambda: None)
    monkeypatch.setattr(util.requests, 'head', failed)
    monkeypatch.setattr(util.requests, 'get', failed)
    payload = {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
        'context': {'System': {'application': {'applicationId': 'test'},
            'user': {'userId': 'test'}, 'device': {'deviceId': 'new-device',
            'supportedInterfaces': {'AudioPlayer': {}}}}},
        'request': {'type': 'LaunchRequest', 'requestId': 'test',
                    'timestamp': '2026-10-10T17:00:00Z', 'locale': 'pt-BR'}}
    response = handlers.lambda_handler(payload, None)['response']
    assert response['shouldEndSession'] is False
    assert 'Diz-me o que queres ouvir' in response['outputSpeech']['ssml']
    assert 'reprompt' in response
    assert not response.get('directives')
    assert 'secret-token' not in caplog.text


def test_export_icon_references_compare_the_bundled_bytes():
    import json
    from skill_deployment import package_fingerprint
    def package(uri, icon=b'approved-image'):
        return {'skill.json': json.dumps({'manifest': {'publishingInformation': {
            'locales': {'pt-BR': {'smallIconUri': uri}}}}}).encode(),
            'assets/images/pt-BR_smallIconUri.png': icon}
    expected = package('https://example.com/icon.png')
    exported = package('file://assets/images/pt-BR_smallIconUri.png')
    assert package_fingerprint(expected) == package_fingerprint(exported)
    assert package_fingerprint(expected) != package_fingerprint(package('file://icon', b'changed-image'))
    exported.pop('assets/images/pt-BR_smallIconUri.png')
    assert package_fingerprint(expected) != package_fingerprint(exported)


def test_shuffle_and_loop_intents_send_commands_to_mapped_player(monkeypatch):
    commands_sent = []
    mapping = {'test-device': "Inês's Echo Dot"}
    monkeypatch.setattr(handlers.device_mapping, 'get_player_for_device', mapping.get)
    monkeypatch.setattr(handlers.ma_control, 'send_player_command',
                        lambda player_id, cmd: commands_sent.append((player_id, cmd)) or True)

    for intent_name, expected_cmd in [
        ('AMAZON.ShuffleOnIntent', 'shuffle_on'),
        ('AMAZON.ShuffleOffIntent', 'shuffle_off'),
        ('AMAZON.LoopOnIntent', 'repeat_on'),
        ('AMAZON.LoopOffIntent', 'repeat_off'),
    ]:
        payload = {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
            'context': {'System': {'application': {'applicationId': 'test'},
                'user': {'userId': 'test'}, 'device': {'deviceId': 'test-device',
                'supportedInterfaces': {'AudioPlayer': {}}}}},
            'request': {'type': 'IntentRequest', 'requestId': 'test',
                'timestamp': '2026-10-10T17:00:00Z', 'locale': 'pt-BR',
                'intent': {'name': intent_name, 'confirmationStatus': 'NONE'}}}
        result = handlers.lambda_handler(payload, None)['response']
        assert 'outputSpeech' in result

    assert commands_sent == [
        ("Inês's Echo Dot", 'shuffle_on'),
        ("Inês's Echo Dot", 'shuffle_off'),
        ("Inês's Echo Dot", 'repeat_on'),
        ("Inês's Echo Dot", 'repeat_off'),
    ]


def test_play_audio_intent_emits_play_when_stream_available(monkeypatch):
    stream_url = 'https://music.example/single/server/Dot/stream.mp3'
    monkeypatch.setattr(handlers, '_get_stream_url', lambda request: (stream_url, {}))
    monkeypatch.setattr(util, 'get_ma_hostname', lambda **kwargs: 'music.example')
    monkeypatch.setattr(util, 'replace_ip_in_url', lambda url, hostname: url)
    monkeypatch.setattr(util, 'get_setting', lambda name, default='': 'true' if name == 'SKIP_URL_VALIDATION' else default)

    payload = {'version': '1.0', 'session': {'new': True, 'sessionId': 'test'},
        'context': {'System': {'application': {'applicationId': 'test'},
            'user': {'userId': 'test'}, 'device': {'deviceId': 'test-device',
            'supportedInterfaces': {'AudioPlayer': {}}}}},
        'request': {'type': 'IntentRequest', 'requestId': 'test',
            'timestamp': '2026-10-10T17:00:00Z', 'locale': 'pt-BR',
            'intent': {'name': 'PlayAudio', 'confirmationStatus': 'NONE'}}}
    response = handlers.lambda_handler(payload, None)['response']
    assert response['directives'][0]['playBehavior'] == 'REPLACE_ALL'
