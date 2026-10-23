"""tests/telephony/test_telephony_loader.py -- tests for the master provider resolver (telephony/loader.py) and the registry.

What this file is for: proves provider selection is explicit or safely auto-detected, that ambiguous or unknown input fails with a
clear error instead of a guess, that recognised-but-unwritten providers (sip, twilio, custom_http) refuse to start rather than
pretend, that the registry is consistent, and that no credential ever appears in an error message.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import make_wav, tone  # noqa: E402

from providers.telephony.registry import AMBIGUOUS_SCHEMES, PROVIDERS, SCHEME_TO_PROVIDER  # noqa: E402
from telephony.adapter import TelephonyProvider  # noqa: E402
from telephony.config import (  # noqa: E402
    ConfigError,
    ProviderNotImplementedError,
    ProviderSettings,
    load_connector_settings,
    load_provider_settings,
    redact_endpoint,
)
from telephony.loader import detect_provider, load_provider, resolve_provider_name  # noqa: E402

CANARY = "canary-value-123"


@pytest.fixture
def wav_path(tmp_path):
    p = tmp_path / "q.wav"
    p.write_bytes(make_wav(tone(300, 16000, 0.1), 16000))
    return str(p)


# ---- registry ---------------------------------------------------------------------------------------------------------------


def test_registry_lists_the_expected_providers_and_modules_exist():
    assert set(PROVIDERS) == {"wavfile", "websocket", "sip", "twilio", "custom_http"}
    for name, spec in PROVIDERS.items():
        module = importlib.import_module(spec.module)
        assert callable(module.create_provider), name


def test_only_real_transports_are_marked_implemented():
    assert {n for n, s in PROVIDERS.items() if s.implemented} == {"wavfile", "websocket", "twilio"}


def test_scheme_table_is_safe():
    assert SCHEME_TO_PROVIDER == {"ws": "websocket", "wss": "websocket", "sip": "sip", "sips": "sip"}
    assert not set(AMBIGUOUS_SCHEMES) & set(SCHEME_TO_PROVIDER)
    assert "twilio" not in SCHEME_TO_PROVIDER.values()  # a vendor has no URL scheme of its own (never auto-detected)
    assert set(SCHEME_TO_PROVIDER.values()) <= set(PROVIDERS)


def test_core_loader_names_no_vendor():
    import telephony.loader as loader

    text = Path(loader.__file__).read_text(encoding="utf-8").lower()
    for vendor in ("twilio", "zoiper", "asterisk", "freeswitch", "kamailio"):
        assert vendor not in text


# ---- explicit selection -----------------------------------------------------------------------------------------------------


def test_explicit_wavfile(wav_path):
    assert isinstance(load_provider("wavfile", ProviderSettings(endpoint=wav_path)), TelephonyProvider)


def test_explicit_twilio_needs_its_own_settings():
    settings = ProviderSettings(endpoint="wss://voice.example.com/twilio/media", password=CANARY)
    assert type(load_provider("twilio", settings)).__name__ == "TwilioProvider"
    with pytest.raises(ConfigError, match="TELEPHONY_PASSWORD"):
        load_provider("twilio", ProviderSettings(endpoint="wss://voice.example.com/twilio/media"))


def test_explicit_websocket():
    provider = load_provider("websocket", ProviderSettings(endpoint="wss://client.example.com/media"))
    assert isinstance(provider, TelephonyProvider)


@pytest.mark.parametrize("name", ["sip", "custom_http"])
def test_unimplemented_providers_fail_clearly(name):
    with pytest.raises(ProviderNotImplementedError) as info:
        load_provider(name, ProviderSettings(endpoint="place-holder", api_key=CANARY, password=CANARY))
    assert f"Provider '{name}' is recognized but no" in str(info.value)
    assert "implemented yet" in str(info.value)
    assert CANARY not in str(info.value)
    assert isinstance(info.value, ConfigError)


def test_unknown_provider_lists_known_ones():
    with pytest.raises(ConfigError) as info:
        load_provider("asterisk", ProviderSettings(endpoint="x"))
    message = str(info.value)
    assert "unknown provider 'asterisk'" in message
    assert "websocket" in message and "wavfile" in message and "sip (not implemented)" in message


@pytest.mark.parametrize("bad", ["../x", "a.b", "Twilio Inc", "", "1abc"])
def test_invalid_names_rejected(bad):
    with pytest.raises(ConfigError):
        load_provider(bad, ProviderSettings())


# ---- auto detection ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("ws://127.0.0.1:9000/media", "websocket"),
        ("wss://client.example.com/media", "websocket"),
        ("WSS://client.example.com/media", "websocket"),
        ("sip:agent@sip.example.com", "sip"),
        ("sips:agent@sip.example.com", "sip"),
    ],
)
def test_auto_detects_unambiguous_schemes(endpoint, expected):
    assert detect_provider(endpoint) == expected
    assert resolve_provider_name("auto", endpoint) == expected


def test_resolve_leaves_explicit_names_alone():
    assert resolve_provider_name("twilio", "wss://x.example.com") == "twilio"


def test_auto_ws_loads_the_websocket_adapter():
    provider = load_provider("auto", ProviderSettings(endpoint="wss://client.example.com/media"))
    assert type(provider).__name__ == "WebSocketProvider"


def test_auto_wss_never_selects_twilio_or_http():
    assert detect_provider("wss://media.twilio.com/anything") == "websocket"  # a plain WebSocket, not a vendor guess


def test_auto_sip_is_recognised_but_not_implemented():
    with pytest.raises(ProviderNotImplementedError) as info:
        load_provider("auto", ProviderSettings(endpoint="sip:agent@sip.example.com", username="u", password=CANARY))
    assert "Provider 'sip' is recognized" in str(info.value)
    assert CANARY not in str(info.value)


@pytest.mark.parametrize("endpoint", ["https://api.client.example.com/voice", "http://10.0.0.5/voice"])
def test_auto_refuses_ambiguous_http(endpoint):
    with pytest.raises(ConfigError) as info:
        detect_provider(endpoint)
    message = str(info.value)
    assert "cannot auto-detect" in message
    assert "TELEPHONY_PROVIDER" in message
    assert "protocol" in message


@pytest.mark.parametrize("endpoint", ["ftp://example.com/x", "just-a-hostname", "C:\\calls\\q.wav", "/tmp/q.wav"])
def test_auto_refuses_unknown_or_missing_scheme(endpoint):
    with pytest.raises(ConfigError, match="cannot auto-detect"):
        detect_provider(endpoint)


def test_auto_missing_endpoint():
    with pytest.raises(ConfigError, match="needs TELEPHONY_ENDPOINT"):
        load_provider("auto", ProviderSettings())


def test_auto_with_placeholder_endpoint_is_treated_as_missing():
    settings = load_provider_settings({"TELEPHONY_ENDPOINT": "place your url here"})
    assert settings.endpoint is None
    with pytest.raises(ConfigError, match="needs TELEPHONY_ENDPOINT"):
        load_provider("auto", settings)


def test_explicit_provider_with_placeholder_endpoint_is_missing_endpoint():
    settings = load_provider_settings({"TELEPHONY_ENDPOINT": "place your url here"})
    for name in ("websocket", "wavfile"):
        with pytest.raises(ConfigError, match="TELEPHONY_ENDPOINT"):
            load_provider(name, settings)


# ---- configuration compatibility --------------------------------------------------------------------------------------------


def test_provider_names_load_from_environment():
    for name in ("auto", "websocket", "twilio", "sip", "wavfile", "custom_http"):
        assert load_connector_settings({"TELEPHONY_PROVIDER": name}).provider == name


def test_audio_settings_are_validated():
    ok = load_provider_settings({"TELEPHONY_AUDIO_CODEC": "MULAW", "TELEPHONY_AUDIO_SAMPLE_RATE": "8000"})
    assert (ok.audio_codec, ok.audio_sample_rate) == ("mulaw", 8000)
    for env in (
        {"TELEPHONY_AUDIO_CODEC": "opus"},
        {"TELEPHONY_AUDIO_SAMPLE_RATE": "12"},
        {"TELEPHONY_AUDIO_SAMPLE_RATE": "x"},
    ):
        with pytest.raises(ConfigError):
            load_provider_settings(env)
    unset = load_provider_settings({})
    assert (unset.audio_codec, unset.audio_sample_rate) == (None, None)


# ---- secrets ----------------------------------------------------------------------------------------------------------------


def test_redact_endpoint_hides_userinfo_query_and_path():
    shown = redact_endpoint(f"wss://user:{CANARY}@host.example.com:8443/media/{CANARY}?token={CANARY}")
    assert shown == "wss://host.example.com:8443/..."
    assert CANARY not in shown
    assert redact_endpoint(None) == "(not set)"
    assert redact_endpoint("/tmp/q.wav") == "/tmp/q.wav"


def test_errors_never_contain_endpoint_secrets():
    url = f"https://user:{CANARY}@api.example.com/voice?key={CANARY}"
    with pytest.raises(ConfigError) as info:
        detect_provider(url)
    assert CANARY not in str(info.value)
    assert "api.example.com" in str(info.value)


def test_provider_settings_repr_hides_secrets():
    s = ProviderSettings(endpoint="wss://x.example.com", api_key=CANARY, api_secret=CANARY, password=CANARY)
    assert CANARY not in repr(s)
