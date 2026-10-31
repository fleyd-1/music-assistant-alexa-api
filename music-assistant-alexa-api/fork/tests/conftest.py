"""Tests run inside the add-on image (or anywhere SKILL_APP_PATH points at the
upstream skill's `src` directory with the fork_ext package copied into it)."""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

_app_src = os.environ.get("SKILL_APP_PATH", "/app/src")
if _app_src not in sys.path:
    sys.path.insert(0, _app_src)
# When run from a checkout, the extension lives beside the tests.
_fork_dir = str(Path(__file__).resolve().parents[1])
if _fork_dir not in sys.path:
    sys.path.append(_fork_dir)

import fork_ext  # noqa: E402

fork_ext.install()


@pytest.fixture
def invoke(monkeypatch, tmp_path):
    """Send an Alexa request through the complete skill and return its response."""
    from skill import lambda_function

    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "devices.json"))
    handler = lambda_function.sb.lambda_handler()

    def send(request_type="IntentRequest", intent=None, slots=None, device_id="test-device",
             locale="pt-BR", session_new=True):
        payload = {
            "version": "1.0",
            "session": {"new": session_new, "sessionId": "test",
                        "application": {"applicationId": "test"}, "user": {"userId": "test"}},
            "context": {"System": {
                "application": {"applicationId": "test"}, "user": {"userId": "test"},
                "device": {"deviceId": device_id, "supportedInterfaces": {"AudioPlayer": {}}}}},
            "request": {"type": request_type, "requestId": "test",
                        "timestamp": "2026-10-31T17:00:00Z", "locale": locale},
        }
        if intent:
            body = {"name": intent, "confirmationStatus": "NONE"}
            if slots:
                body["slots"] = {name: {"name": name, "value": value, "confirmationStatus": "NONE"}
                                 for name, value in slots.items()}
            payload["request"]["intent"] = body
        return handler(payload, None)["response"]

    return send
