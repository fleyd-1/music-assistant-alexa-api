import sys
from pathlib import Path


ADDON = Path(__file__).resolve().parents[1] / "music-assistant-alexa-api"
sys.path.insert(0, str(ADDON / "skill-api/app"))
sys.path.insert(0, str(ADDON / "skill-overlay/app"))


def test_play_directive_contains_music_assistant_metadata(monkeypatch):
    from skill import util

    class ResponseBuilder:
        def __init__(self):
            self.directives = []
            self.response = {"directives": self.directives}

        def add_directive(self, directive):
            self.directives.append(directive)
            return self

        def set_should_end_session(self, value):
            self.should_end_session = value
            return self

    monkeypatch.setattr(util.data, "get_info", lambda: {
        "primaryText": "Fé (Ao Vivo)",
        "secondaryText": "IZA - IZA Rock In Rio (Ao Vivo)",
        "coverImageSource": "https://music.example/cover.jpg",
        "backgroundImageSource": "https://music.example/background.jpg",
    })
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kwargs: "https://music.example")
    monkeypatch.setattr(util, "replace_ip_in_url", lambda url, hostname: url)
    monkeypatch.setattr(util, "get_setting", lambda name, default=None: "true")
    monkeypatch.setattr(util, "push_alexa_metadata", lambda url: None)

    response_builder = ResponseBuilder()
    util.play(
        url="https://music.example/flow/queue/track/song.mp3",
        offset=0,
        text=None,
        response_builder=response_builder,
    )

    from ask_sdk_core.serialize import DefaultSerializer

    directive = DefaultSerializer().serialize(response_builder.directives[0])
    metadata = directive["audioItem"]["metadata"]
    assert metadata["title"] == "Fé (Ao Vivo)"
    assert metadata["subtitle"] == "IZA - IZA Rock In Rio (Ao Vivo)"
    assert metadata["art"]["sources"][0]["url"] == "https://music.example/cover.jpg"
    assert metadata["backgroundImage"]["sources"][0]["url"] == "https://music.example/background.jpg"


def test_audio_metadata_has_fallback_art_and_nonempty_text(monkeypatch):
    from skill import util

    monkeypatch.setattr(util.data, "get_info", lambda: {})

    metadata = util._audio_metadata().to_dict()
    assert metadata["title"] == "Music Assistant"
    assert metadata["subtitle"] == "Music Assistant"
    assert metadata["art"]["sources"][0]["url"].endswith("ma_512x512.png")
    assert metadata["background_image"]["sources"][0]["url"] == metadata["art"]["sources"][0]["url"]
