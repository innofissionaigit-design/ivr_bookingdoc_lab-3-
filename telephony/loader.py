"""telephony/loader.py -- the master provider resolver: turns TELEPHONY_PROVIDER (+ endpoint) into a ready provider adapter.

What this file is for
---------------------
This is the ONE place that decides which provider adapter runs. It contains no provider logic and no provider names: the table of
known adapters is providers/telephony/registry.py, and each adapter lives in its own small module under providers/telephony/.
Adding a provider therefore never changes this file or the connector.

  TELEPHONY_PROVIDER=<name>   explicit: load that adapter (any name in the registry)
  TELEPHONY_PROVIDER=auto     detect from the TELEPHONY_ENDPOINT URL scheme, ONLY where the scheme names one transport
                              unambiguously (ws://, wss:// -> websocket; sip:, sips: -> sip). http/https, unknown schemes and a
                              missing endpoint are refused with an explanation -- a protocol is never guessed.

A recognised provider whose transport is not written yet (`implemented=False` in the registry) stops with a clear error; it never
falls back to something else. Error messages show the endpoint redacted (scheme://host only) and never any credential.
"""

from __future__ import annotations

import importlib
import re
from urllib.parse import urlsplit

from providers.telephony.registry import AMBIGUOUS_SCHEMES, PROVIDERS, SCHEME_TO_PROVIDER
from telephony.adapter import TelephonyProvider
from telephony.config import ConfigError, ProviderSettings, redact_endpoint

AUTO = "auto"
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


# sourav: a single readable list for error messages. Flow: registry names, marking the ones with no transport yet.
def known_providers() -> str:
    return ", ".join(
        name + ("" if spec.implemented else " (not implemented)") for name, spec in sorted(PROVIDERS.items())
    )


# sourav: safe auto-detection. Flow: only trust a URL scheme that names ONE transport; refuse http(s) (REST? webhook? vendor API?),
# unknown schemes and a missing endpoint, telling the operator to set TELEPHONY_PROVIDER explicitly.
def detect_provider(endpoint: str | None) -> str:
    if endpoint is None:
        raise ConfigError(
            "TELEPHONY_PROVIDER=auto needs TELEPHONY_ENDPOINT to detect the provider from; "
            "set TELEPHONY_ENDPOINT, or set TELEPHONY_PROVIDER explicitly"
        )
    scheme = urlsplit(endpoint).scheme.lower()
    if scheme in SCHEME_TO_PROVIDER:
        return SCHEME_TO_PROVIDER[scheme]
    if scheme in AMBIGUOUS_SCHEMES:
        raise ConfigError(
            f"cannot auto-detect the provider for a {scheme}:// endpoint ({redact_endpoint(endpoint)}): an HTTP(S) URL does not "
            "say which voice protocol or API it speaks. Ask the client for the API/protocol type and set TELEPHONY_PROVIDER "
            f"explicitly. Known providers: {known_providers()}"
        )
    shown = f"'{scheme}:'" if scheme else "no URL scheme"
    raise ConfigError(
        f"cannot auto-detect the provider: the endpoint has {shown}. Auto-detection understands "
        f"{', '.join(sorted(SCHEME_TO_PROVIDER))} only; set TELEPHONY_PROVIDER explicitly. Known providers: {known_providers()}"
    )


# sourav: the name that will actually be loaded. Flow: "auto" -> detect from the endpoint, anything else unchanged.
def resolve_provider_name(name: str, endpoint: str | None) -> str:
    return detect_provider(endpoint) if name == AUTO else name


# sourav: turn the configured provider into a ready adapter. Flow: resolve auto, validate the name, look it up in the registry
# (unknown -> error listing the known ones), import that adapter module lazily, call its create_provider() with the provider
# settings (it validates its own configuration), check the result's type.
def load_provider(name: str, settings: ProviderSettings) -> TelephonyProvider:
    name = resolve_provider_name(name, settings.endpoint)
    if not _PROVIDER_NAME.match(name):
        raise ConfigError(f"invalid provider name {name!r}; known providers: {known_providers()}")
    spec = PROVIDERS.get(name)
    if spec is None:
        raise ConfigError(f"unknown provider {name!r}; known providers: {known_providers()}")
    try:
        module = importlib.import_module(spec.module)
    except ModuleNotFoundError as e:
        if e.name == spec.module:
            raise ConfigError(f"provider {name!r} is registered but its module {spec.module} is missing") from e
        raise ConfigError(f"provider {name!r} needs a Python package that is not installed ({e.name})") from e
    factory = getattr(module, "create_provider", None)
    if not callable(factory):
        raise ConfigError(f"{spec.module} must define create_provider(settings)")
    provider = factory(settings)
    if not isinstance(provider, TelephonyProvider):
        raise ConfigError(f"{spec.module}.create_provider() must return a TelephonyProvider")
    return provider
