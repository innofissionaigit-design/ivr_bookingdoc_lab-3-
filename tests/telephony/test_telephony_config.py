"""tests/telephony/test_telephony_config.py -- tests for telephony/config.py.

What this file is for: proves configuration loads from environment variables with safe defaults, that no variable is
required, that placeholders count as unset, that the agent host is limited to private addresses, and that provider secrets
are neither read by the core loader nor shown in repr().
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from telephony.config import (  # noqa: E402
    ConfigError,
    is_private_host,
    load_connector_settings,
    load_provider_settings,
)


def test_defaults_with_empty_environment():
    s = load_connector_settings({})
    assert (s.provider, s.agent_host, s.agent_port, s.drain_timeout_s) == (None, "127.0.0.1", 8101, 30.0)
    p = load_provider_settings({})
    assert (p.endpoint, p.api_key, p.api_secret, p.username, p.password) == (None,) * 5


def test_overrides():
    s = load_connector_settings(
        {
            "TELEPHONY_PROVIDER": "WavFile",
            "VOICE_PCM_HOST": "10.1.2.3",
            "VOICE_PCM_PORT": "9000",
            "TELEPHONY_DRAIN_TIMEOUT_S": "5",
        }
    )
    assert (s.provider, s.agent_host, s.agent_port, s.drain_timeout_s) == ("wavfile", "10.1.2.3", 9000, 5.0)


def test_placeholders_and_blanks_are_unset():
    env = {
        "TELEPHONY_ENDPOINT": "place your url here",
        "TELEPHONY_API_KEY": "  ",
        "TELEPHONY_PROVIDER": "place your provider",
    }
    assert load_provider_settings(env).endpoint is None
    assert load_provider_settings(env).api_key is None
    assert load_connector_settings(env).provider is None


@pytest.mark.parametrize("port", ["0", "70000", "abc", "-1"])
def test_invalid_port(port):
    with pytest.raises(ConfigError):
        load_connector_settings({"VOICE_PCM_PORT": port})


@pytest.mark.parametrize("name", ["../evil", "a.b", "x y", "1abc"])
def test_invalid_provider_name(name):
    with pytest.raises(ConfigError):
        load_connector_settings({"TELEPHONY_PROVIDER": name})


def test_private_host_guard():
    assert is_private_host("localhost") and is_private_host("127.0.0.1") and is_private_host("::1")
    assert is_private_host("192.168.1.5") and is_private_host("10.0.0.2")
    assert not is_private_host("8.8.8.8") and not is_private_host("voice.example.com")
    with pytest.raises(ConfigError):
        load_connector_settings({"VOICE_PCM_HOST": "8.8.8.8"})
    assert (
        load_connector_settings({"VOICE_PCM_HOST": "8.8.8.8", "TELEPHONY_ALLOW_REMOTE_AGENT": "1"}).agent_host
        == "8.8.8.8"
    )


def test_secrets_not_in_repr_and_not_read_by_core_loader():
    env = {"TELEPHONY_API_KEY": "k-12345678", "TELEPHONY_API_SECRET": "s-12345678", "TELEPHONY_PASSWORD": "pw-12345678"}
    p = load_provider_settings(env)
    text = repr(p)
    assert "k-12345678" not in text and "s-12345678" not in text and "pw-12345678" not in text
    assert p.api_key == "k-12345678"
    core = load_connector_settings(env)
    assert "12345678" not in repr(core)
