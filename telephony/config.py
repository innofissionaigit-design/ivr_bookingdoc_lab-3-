"""telephony/config.py -- the single place telephony settings are read from environment variables.

What this file is for
---------------------
The repository configures everything through environment variables (deploy/env.sh), so the connector does the same, in one
module, with safe defaults. Two separate loaders keep provider secrets out of the provider-independent core:

  load_connector_settings()  provider-independent: which provider to load, and where the voice agent listens
  load_provider_settings()   the optional per-provider values (endpoint, key, secret, username, password); called ONLY once a
                             provider has been selected, and handed to that provider alone

Variables (none is required; copy .env.example and fill in only what your provider needs):

  TELEPHONY_PROVIDER      adapter name listed in providers/telephony/registry.py,
                          or "auto" to pick one from the TELEPHONY_ENDPOINT scheme where that is safe (telephony/loader.py)
  VOICE_PCM_HOST          where main_pcm.py listens            (default 127.0.0.1)
  VOICE_PCM_PORT          its port                              (default 8101, deploy/env.sh VOICE_PCM_PORT)
  TELEPHONY_ALLOW_REMOTE_AGENT   set to 1 to allow a non-private agent host (see the security note below)
  TELEPHONY_AUTO_PROBE    set to 1 so provider=auto briefly contacts the endpoint to check what it speaks (opt-in, see README)
  TELEPHONY_DRAIN_TIMEOUT_S      seconds to let queued speech finish after the agent closes the socket (default 30)
  TELEPHONY_ENDPOINT / _API_KEY / _API_SECRET / _USERNAME / _PASSWORD    provider-specific, read only for the chosen provider
  TELEPHONY_LISTEN_HOST / TELEPHONY_LISTEN_PORT    where a provider that ACCEPTS connections (e.g. a cloud voice service that dials
                          into us) listens; default 127.0.0.1 and the provider's own default port. Put a TLS proxy in front.
  TELEPHONY_TRANSFER_TO   number/address a provider should hand a call to on a human hand-off (provider-specific meaning)
  TELEPHONY_AUDIO_CODEC / TELEPHONY_AUDIO_SAMPLE_RATE    audio format of a provider that does not declare its own (the generic
                          websocket adapter); codec is pcm_s16le | mulaw | alaw

A value left empty, or still reading "place your url here" (the .env.example placeholder), counts as not set.

Security note: /ws/audio has NO authentication today. The agent host is therefore restricted to loopback or a private-network
address unless you explicitly opt out. That check stops accidents; it is not authentication.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from telephony.audio import SUPPORTED_CODECS

DEFAULT_AGENT_HOST = "127.0.0.1"
DEFAULT_AGENT_PORT = 8101
DEFAULT_DRAIN_TIMEOUT_S = 30.0
PLACEHOLDER_PREFIX = "place your"

_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class ConfigError(ValueError):
    """The telephony configuration is invalid; the message says which variable and why."""


class ProviderNotImplementedError(ConfigError):
    """The provider name is known (so it can be selected and documented) but its transport is not written yet."""


# sourav: error messages and logs must never carry credentials, and URLs often do (user:pass@host, ?token=..., /path/<token>).
# Flow: keep only scheme://host[:port]; anything after the host is replaced by "/..."; a non-URL (e.g. a file path) is shown as-is.
def redact_endpoint(endpoint: str | None) -> str:
    if not endpoint:
        return "(not set)"
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        return endpoint if "@" not in endpoint and "?" not in endpoint else "(redacted)"
    host = parts.hostname or "?"
    host = f"[{host}]" if ":" in host else host
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    more = "/..." if (parts.path not in ("", "/") or parts.query or parts.fragment or "@" in parts.netloc) else ""
    return f"{parts.scheme}://{host}{port}{more}"


# sourav: .env.example ships "place your url here" style placeholders; copying it unchanged must behave as "not configured".
# Flow: trim, treat empty and placeholder text as missing.
def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.lower().startswith(PLACEHOLDER_PREFIX):
        return None
    return text


# sourav: /ws/audio is unauthenticated, so the connector refuses to talk to it across the public internet by accident.
# Flow: accept "localhost" and loopback / private / link-local IP literals; everything else must be opted into explicitly.
def is_private_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


@dataclass(frozen=True)
class ConnectorSettings:
    """Provider-independent settings."""

    provider: str | None = None
    agent_host: str = DEFAULT_AGENT_HOST
    agent_port: int = DEFAULT_AGENT_PORT
    drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S
    auto_probe: bool = False  # with provider=auto: briefly contact the endpoint to check what it speaks (opt-in)


@dataclass(frozen=True)
class ProviderSettings:
    """Optional provider-specific values. Secrets are hidden from repr() so they cannot leak into a log line by accident."""

    endpoint: str | None = None
    api_key: str | None = field(default=None, repr=False)
    api_secret: str | None = field(default=None, repr=False)
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    audio_codec: str | None = None  # only for providers that cannot declare their own audio format
    audio_sample_rate: int | None = None
    transfer_to: str | None = None  # where a human hand-off goes, for providers that can transfer
    listen_host: str | None = None  # for providers that accept inbound connections
    listen_port: int | None = None


# sourav: one parser for the two numeric settings. Flow: parse, range-check, raise ConfigError naming the variable.
def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float, integer: bool) -> float:
    raw = _clean(env.get(name))
    if raw is None:
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from e
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


# sourav: the connector's only startup configuration. Flow: read provider name (validated as a plain module name so it can
# never point outside providers/telephony/), the agent host/port, apply the private-network guard, return a frozen object.
# It does not touch any TELEPHONY_API_* / USERNAME / PASSWORD variable.
def load_connector_settings(env: Mapping[str, str] | None = None) -> ConnectorSettings:
    env = os.environ if env is None else env
    provider = _clean(env.get("TELEPHONY_PROVIDER"))
    if provider is not None:
        provider = provider.lower()
        if not _PROVIDER_NAME.match(provider):
            raise ConfigError("TELEPHONY_PROVIDER must be a plain name such as 'wavfile' (letters, digits, underscore)")
    host = _clean(env.get("VOICE_PCM_HOST")) or DEFAULT_AGENT_HOST
    port = int(_number(env, "VOICE_PCM_PORT", DEFAULT_AGENT_PORT, 1, 65535, integer=True))
    allow_remote = (_clean(env.get("TELEPHONY_ALLOW_REMOTE_AGENT")) or "").lower() in ("1", "true", "yes")
    if not allow_remote and not is_private_host(host):
        raise ConfigError(
            f"VOICE_PCM_HOST={host!r} is not a loopback or private-network address. /ws/audio has no authentication; "
            "add authentication first, then set TELEPHONY_ALLOW_REMOTE_AGENT=1 (see telephony/README.md)."
        )
    drain = float(_number(env, "TELEPHONY_DRAIN_TIMEOUT_S", DEFAULT_DRAIN_TIMEOUT_S, 0.0, 600.0, integer=False))
    probe = (_clean(env.get("TELEPHONY_AUTO_PROBE")) or "").lower() in ("1", "true", "yes")
    return ConnectorSettings(
        provider=provider, agent_host=host, agent_port=port, drain_timeout_s=drain, auto_probe=probe
    )


# sourav: the values a real provider adapter needs (URL, keys, login). Flow: read each variable, treating blank and
# placeholder values as unset; the CALLER decides which of them its provider actually requires.
def load_provider_settings(env: Mapping[str, str] | None = None) -> ProviderSettings:
    env = os.environ if env is None else env
    codec = _clean(env.get("TELEPHONY_AUDIO_CODEC"))
    if codec is not None:
        codec = codec.lower()
        if codec not in SUPPORTED_CODECS:
            raise ConfigError(f"TELEPHONY_AUDIO_CODEC must be one of {', '.join(SUPPORTED_CODECS)}")
    rate = None
    if _clean(env.get("TELEPHONY_AUDIO_SAMPLE_RATE")) is not None:
        rate = int(_number(env, "TELEPHONY_AUDIO_SAMPLE_RATE", 0, 1000, 192_000, integer=True))
    listen_port = None
    if _clean(env.get("TELEPHONY_LISTEN_PORT")) is not None:
        listen_port = int(_number(env, "TELEPHONY_LISTEN_PORT", 0, 1, 65535, integer=True))
    return ProviderSettings(
        transfer_to=_clean(env.get("TELEPHONY_TRANSFER_TO")),
        listen_host=_clean(env.get("TELEPHONY_LISTEN_HOST")),
        listen_port=listen_port,
        audio_codec=codec,
        audio_sample_rate=rate,
        endpoint=_clean(env.get("TELEPHONY_ENDPOINT")),
        api_key=_clean(env.get("TELEPHONY_API_KEY")),
        api_secret=_clean(env.get("TELEPHONY_API_SECRET")),
        username=_clean(env.get("TELEPHONY_USERNAME")),
        password=_clean(env.get("TELEPHONY_PASSWORD")),
    )
