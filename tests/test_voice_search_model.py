import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "music-assistant-alexa-api/skill-overlay/app/models/pt-BR.json"


def test_portuguese_voice_search_intents_are_configured():
    intents = json.loads(MODEL.read_text())["interactionModel"]["languageModel"]["intents"]
    by_name = {intent["name"]: intent for intent in intents}

    for intent_name in ("PlayTrackIntent", "PlayRadioIntent"):
        intent = by_name[intent_name]
        assert intent["slots"] == [{"name": "query", "type": "AMAZON.SearchQuery"}]
        assert intent["samples"]

    assert "toca a música {query}" in by_name["PlayTrackIntent"]["samples"]
    assert "toca a rádio {query}" in by_name["PlayRadioIntent"]["samples"]
