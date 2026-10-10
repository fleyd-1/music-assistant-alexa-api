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
    assert 'Que música' in response['outputSpeech']['ssml']
    assert not response.get('directives')


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
    assert 'Que música' in response['outputSpeech']['ssml']
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
