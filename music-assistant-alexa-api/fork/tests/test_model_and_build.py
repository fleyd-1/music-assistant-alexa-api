"""The pt-BR interaction model and the build-time patch step."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

FORK_DIR = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("apply_fork", FORK_DIR / "apply_fork.py")
apply_fork = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_fork)


def _intents(model):
    return {i["name"]: i for i in model["interactionModel"]["languageModel"]["intents"]}


def _fork_intents():
    return json.loads((FORK_DIR / "models/pt-BR.intents.json").read_text(encoding="utf-8"))["intents"]


def _sample_model(tmp_path):
    model = {"interactionModel": {"languageModel": {
        "invocationName": "music assistant", "types": [],
        "intents": [
            {"name": "PlayAudio", "samples": ["upstream phrase"]},
            {"name": "AMAZON.HelpIntent", "samples": ["ajuda"]},
            {"name": "UpstreamOnly", "samples": ["novo no original"]},
        ]}}}
    path = tmp_path / "pt-BR.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    return path


def test_merge_keeps_upstream_intents_and_applies_fork_ones(tmp_path):
    path = _sample_model(tmp_path)
    count = apply_fork.merge_model(path, FORK_DIR / "models/pt-BR.intents.json")
    merged = _intents(json.loads(path.read_text(encoding="utf-8")))

    assert count == len(_fork_intents())
    assert "UpstreamOnly" in merged and "AMAZON.HelpIntent" in merged
    # the fork's own sample phrases win for an intent both sides define
    assert merged["PlayAudio"]["samples"] != ["upstream phrase"]
    for name in ("PlayTrackIntent", "NowPlayingIntent", "SleepTimerIntent", "LatestJoanaIntent"):
        assert name in merged


def test_merge_keeps_upstream_order_first(tmp_path):
    path = _sample_model(tmp_path)
    apply_fork.merge_model(path, FORK_DIR / "models/pt-BR.intents.json")
    language = json.loads(path.read_text(encoding="utf-8"))["interactionModel"]["languageModel"]
    assert [i["name"] for i in language["intents"]][:3] == [
        "PlayAudio", "AMAZON.HelpIntent", "UpstreamOnly"]


def test_entrypoint_patch_is_applied_once(tmp_path):
    entry = tmp_path / "entrypoint.sh"
    entry.write_text("exec gunicorn --workers 1 app:app --chdir /app/src\n", encoding="utf-8")
    apply_fork.patch_entrypoint(entry)
    text = entry.read_text(encoding="utf-8")
    assert "fork_ext.wsgi:app" in text and " app:app " not in text


@pytest.mark.parametrize("content", [
    "exec gunicorn something_else\n",
    "x app:app y app:app z\n",
])
def test_entrypoint_patch_fails_loudly_when_upstream_changed(tmp_path, content):
    entry = tmp_path / "entrypoint.sh"
    entry.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match="update apply_fork.py"):
        apply_fork.patch_entrypoint(entry)


def test_portuguese_voice_search_intents_are_configured():
    intents = {i["name"]: i for i in _fork_intents()}

    for name in ("PlayTrackIntent", "PlayArtistIntent", "PlayRadioIntent", "PlayAlbumIntent",
                 "PlayPlaylistIntent", "PlayPodcastIntent"):
        assert intents[name]["slots"] == [{"name": "query", "type": "AMAZON.SearchQuery"}]
        assert intents[name]["samples"]

    assert "toca a música {query}" in intents["PlayTrackIntent"]["samples"]
    assert "toca músicas da {query}" in intents["PlayArtistIntent"]["samples"]
    assert "tocar músicas de {query}" in intents["PlayArtistIntent"]["samples"]
    assert "toca a rádio {query}" in intents["PlayRadioIntent"]["samples"]
    assert "toca o álbum {query}" in intents["PlayAlbumIntent"]["samples"]
    assert "toca a playlist {query}" in intents["PlayPlaylistIntent"]["samples"]
    assert "toca o podcast {query}" in intents["PlayPodcastIntent"]["samples"]
    assert intents["LatestJoanaIntent"]["slots"] == []
    assert "quero ouvir a Joana" in intents["LatestJoanaIntent"]["samples"]

    for name in ("AMAZON.ShuffleOnIntent", "AMAZON.ShuffleOffIntent",
                 "AMAZON.LoopOnIntent", "AMAZON.LoopOffIntent"):
        assert intents[name]["samples"] == []  # Amazon's model test needs these empty

    # The no-slot playback intent must not steal track/radio queries.
    assert not any(word in sample for sample in intents["PlayAudio"]["samples"]
                   for word in ("música", "canção", "rádio", "estação"))
    assert intents["SleepTimerIntent"]["slots"] == [{"name": "duration", "type": "AMAZON.DURATION"}]
    for name in ("NowPlayingIntent", "CancelSleepTimerIntent", "PlayFavoritesIntent",
                 "PlayLastRadioIntent", "PlayRecentIntent"):
        assert intents[name]["samples"]


def test_built_image_model_contains_every_fork_intent():
    """In the image, the merged model is what the setup wizard deploys."""
    model_file = Path(os.environ.get("SKILL_APP_PATH", "/app/src")) / "models" / "pt-BR.json"
    model = json.loads(model_file.read_text(encoding="utf-8"))
    built = _intents(model)
    if "PlayTrackIntent" not in built:
        pytest.skip("run against a built image to check the merged model")
    for intent in _fork_intents():
        assert built[intent["name"]] == intent
    assert model["interactionModel"]["languageModel"]["invocationName"] == "music assistant"
