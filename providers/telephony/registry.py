"""providers/telephony/registry.py -- the list of telephony adapters the master connector knows about.

What this file is for
---------------------
telephony/loader.py is the single place that turns TELEPHONY_PROVIDER into an adapter, and it needs a table of names. That table
lives HERE (on the provider side) so the provider-independent core never has to be edited to learn about a new provider:

  1. add providers/telephony/<name>.py exposing create_provider(settings) -> TelephonyProvider
  2. add one line to PROVIDERS below (and a scheme to SCHEME_TO_PROVIDER only if that scheme unambiguously means this provider)

`implemented=False` means the name is reserved and documented but NO transport exists yet: selecting it fails with a clear error
instead of pretending to work. Modules are imported lazily, so an unused provider costs nothing and its optional libraries are
never required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    module: str  # importable module that defines create_provider(settings)
    implemented: bool  # False = recognised name, transport not written yet
    summary: str  # one line for error messages and docs


PROVIDERS: dict[str, ProviderSpec] = {
    "wavfile": ProviderSpec("providers.telephony.wavfile", True, "offline test adapter: a WAV file is the caller"),
    "websocket": ProviderSpec(
        "providers.telephony.websocket",
        True,
        "generic client WebSocket media endpoint (raw audio frames, see module doc)",
    ),
    "sip": ProviderSpec("providers.telephony.sip", False, "SIP/RTP -- needs the client's SIP setup (not implemented)"),
    "twilio": ProviderSpec(
        "providers.telephony.twilio",
        True,
        "Twilio bidirectional Media Streams (server mode; not yet tested against live Twilio)",
    ),
    "custom_http": ProviderSpec(
        "providers.telephony.custom_http", False, "client-specific HTTP/REST voice API (not implemented)"
    ),
}

# Endpoint URL schemes that, on their own, identify ONE provider for TELEPHONY_PROVIDER=auto. Only schemes that name a transport
# unambiguously belong here. http/https are deliberately absent: an https URL could be a REST API, a webhook, a Twilio account
# URL or anything else, so auto-detection must refuse it. Twilio has no URL scheme of its own and is never auto-detected.
SCHEME_TO_PROVIDER: dict[str, str] = {
    "ws": "websocket",
    "wss": "websocket",
    "sip": "sip",
    "sips": "sip",
}

AMBIGUOUS_SCHEMES = ("http", "https")
