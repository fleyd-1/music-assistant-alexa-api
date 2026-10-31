"""Guards for the seams between this extension and the upstream skill.

When an upstream release changes something the extension relies on, these fail
first and name the function in `fork_ext/upstream_api.py` to adjust.
"""

import inspect
import json
from pathlib import Path

import pytest
import shared_store
from fork_ext import handlers, upstream_api
from skill import data, device_mapping, lambda_function, ma_control, util

FORK_DIR = Path(__file__).resolve().parents[1]


def test_upstream_functions_used_by_the_adapter_exist():
    assert callable(lambda_function._device_id_from)
    assert callable(lambda_function._supports_apl)
    assert callable(lambda_function._get_stream_url)
    assert callable(device_mapping.get_player_for_device)
    assert callable(shared_store.get_player_snapshot)
    assert callable(data.select_stream)
    assert callable(data.get_info)
    assert callable(ma_control.mark_ma_triggered)
    assert callable(ma_control.send_player_command)
    assert "pause" in ma_control.SUPPORTED_COMMANDS


def test_util_play_still_accepts_what_the_adapter_passes():
    parameters = inspect.signature(util.play).parameters
    for name in ("url", "offset", "text", "response_builder", "supports_apl", "validate"):
        assert name in parameters


def test_upstream_launch_handler_is_available_for_the_playable_case():
    handler = upstream_api.default_launch_handler()
    assert type(handler).__name__ == "LaunchRequestOrPlayAudioHandler"


def test_skill_id_restriction_is_enforced_by_upstream():
    """The fork carries no skill-ID check of its own; upstream rejects foreign skills."""
    import private_access

    assert callable(private_access.check_private_access)
    assert callable(private_access.get_allowed_skill_id)


def test_skill_id_fallback_to_deployment_state_when_access_file_missing(tmp_path, monkeypatch):
    """When skill-access.json is missing and phase != complete, fork_ext bridges the saved skill ID."""
    import json
    import private_access
    import fork_ext

    dep_file = tmp_path / "skill-deployment.json"
    dep_file.write_text(json.dumps({
        "phase": "failed",
        "skill_id": "amzn1.ask.skill.fallback-id",
        "verified_at": None,
    }))
    access_file = tmp_path / "skill-access.json"
    monkeypatch.setattr(private_access, "deployment_path", lambda: dep_file)
    monkeypatch.setattr(private_access, "access_path", lambda state_path=None: access_file)

    fork_ext._ensure_skill_access()

    assert private_access.get_allowed_skill_id() == "amzn1.ask.skill.fallback-id"
    assert access_file.exists()
    assert json.loads(access_file.read_text())["skill_id"] == "amzn1.ask.skill.fallback-id"


def test_extension_handlers_run_before_the_upstream_ones():
    builder = upstream_api.skill_builder.runtime_configuration_builder
    names = [type(chain.request_handler).__name__ for chain in builder.request_handler_chains]
    for cls in handlers.PRIORITY_HANDLERS:
        assert names.index(cls.__name__) < names.index("VoiceMusicHandler")
    upstream_launch = names.index("LaunchRequestOrPlayAudioHandler")
    for cls in handlers.REQUEST_HANDLERS:
        assert names.index(cls.__name__) < upstream_launch
    # The device-capability check still comes first.
    assert names.index("CheckAudioInterfaceHandler") < names.index("LaunchHandler")
    assert type(builder.exception_handlers[0]).__name__ == "ErrorHandler"


def test_intents_shared_with_upstream_voice_handler_are_won_by_the_fork():
    """Upstream's English model reuses names such as PlayArtistIntent; the fork's
    Portuguese handlers must be tried first or upstream would answer in English."""
    from skill import voice_music

    shared = set(voice_music.HANDLED_INTENTS) & {
        i["name"] for i in json.loads(
            (FORK_DIR / "models/pt-BR.intents.json").read_text(encoding="utf-8"))["intents"]}
    owned = set()
    for cls in handlers.PRIORITY_HANDLERS:
        owned |= set(getattr(cls, "INTENT_TYPES", {})) | set(getattr(cls, "INTENT_KINDS", {}))
    assert shared <= owned, f"upstream would answer these in English: {shared - owned}"


def test_installing_twice_does_not_duplicate_handlers():
    import fork_ext

    builder = upstream_api.skill_builder.runtime_configuration_builder
    before = len(builder.request_handler_chains)
    fork_ext.install()
    assert len(builder.request_handler_chains) == before


def test_registering_before_a_missing_handler_fails_loudly():
    with pytest.raises(RuntimeError, match="no request handler named"):
        upstream_api.upstream_handler_index("DoesNotExist")


def test_wsgi_entry_point_builds_the_upstream_app():
    import fork_ext.wsgi as wsgi

    assert wsgi.app is not None
    assert wsgi.app.url_map is not None


def test_shipped_intents_file_is_valid():
    intents = json.loads(
        (FORK_DIR / "models/pt-BR.intents.json").read_text(encoding="utf-8"))["intents"]
    names = [intent["name"] for intent in intents]
    assert len(names) == len(set(names))
