"""tests/telephony/test_telephony_main.py -- tests for the single entry point `python -m telephony` (telephony/__main__.py).

What this file is for: proves the master entry point works for the offline adapter without any agent running, and that every
misconfiguration ends with exit code 2 and a readable message that contains no secret.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import make_wav, tone  # noqa: E402

from telephony.__main__ import main  # noqa: E402

CANARY = "canary-value-123"


def test_missing_provider_exits_2(caplog):
    with caplog.at_level(logging.ERROR):
        assert main({}) == 2
    assert "TELEPHONY_PROVIDER is not set" in caplog.text


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"TELEPHONY_PROVIDER": "auto"}, "needs TELEPHONY_ENDPOINT"),
        (
            {"TELEPHONY_PROVIDER": "auto", "TELEPHONY_ENDPOINT": "https://api.example.com/v?k=" + CANARY},
            "cannot auto-detect",
        ),
        ({"TELEPHONY_PROVIDER": "nonsense"}, "unknown provider"),
        (
            {"TELEPHONY_PROVIDER": "sip", "TELEPHONY_ENDPOINT": "sip:a@b.example.com", "TELEPHONY_PASSWORD": CANARY},
            "recognized",
        ),
        ({"TELEPHONY_PROVIDER": "twilio", "TELEPHONY_API_SECRET": CANARY}, "TELEPHONY_ENDPOINT"),
        ({"TELEPHONY_PROVIDER": "custom_http", "TELEPHONY_API_SECRET": CANARY}, "recognized"),
        ({"TELEPHONY_PROVIDER": "websocket", "TELEPHONY_ENDPOINT": "place your url here"}, "TELEPHONY_ENDPOINT"),
    ],
)
def test_configuration_errors_exit_2_without_secrets(env, needle, caplog):
    with caplog.at_level(logging.DEBUG):
        assert main(env) == 2
    assert needle in caplog.text
    assert CANARY not in caplog.text


def test_wavfile_runs_end_to_end_without_an_agent(tmp_path, caplog):
    wav = tmp_path / "q.wav"
    wav.write_bytes(make_wav(tone(300, 16000, 0.1), 16000))
    env = {
        "TELEPHONY_PROVIDER": "wavfile",
        "TELEPHONY_ENDPOINT": str(wav),
        "VOICE_PCM_PORT": "1",
    }  # nothing listens on :1
    with caplog.at_level(logging.INFO):
        assert main(env) == 0
    assert "cannot reach the voice agent" in caplog.text  # the call ended cleanly, the process did not crash
    assert "provider=wavfile" in caplog.text


def test_auto_resolves_and_logs_the_chosen_provider(caplog):
    env = {"TELEPHONY_PROVIDER": "auto", "TELEPHONY_ENDPOINT": "ws://127.0.0.1:1/media", "VOICE_PCM_PORT": "1"}
    with caplog.at_level(logging.INFO):
        assert main(env) == 2  # the endpoint is unreachable -> clear error, exit 2
    assert "provider=websocket" in caplog.text
    assert "cannot connect to the telephony endpoint ws://127.0.0.1:1/..." in caplog.text
