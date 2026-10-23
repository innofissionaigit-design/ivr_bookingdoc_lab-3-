"""scripts/twilio_preflight.py -- can this machine take a Twilio call right now, and what still has to be done by hand?

What this file is for
---------------------
Every failure mode of a first Twilio call is either a configuration mismatch or a missing manual step in the Twilio console,
and both are invisible until a real caller hears silence. This script checks everything that can be checked locally, then
prints the exact TwiML and webhook URL to paste into the console. It contacts Twilio never, and the voice agent only to see
whether its socket accepts a connection.

    source deploy/telephony-twilio.env.sh
    python scripts/twilio_preflight.py

Exit codes: 0 = ready to take a call, 1 = something listed as BLOCKED must be fixed first.
Nothing it prints contains a credential: secrets are reported only as present/absent and by length.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telephony.config import (  # noqa: E402
    ConfigError,
    load_connector_settings,
    load_provider_settings,
    redact_endpoint,
)

OK = "  ok      "
WARN = "  WARNING "
BAD = "  BLOCKED "

blockers: list[str] = []
warnings: list[str] = []


# sourav: one reporting style for every check, so the output can be read top to bottom and acted on. Flow: print the line,
# remember anything that has to be fixed (BLOCKED) or looked at (WARNING) for the summary.
def report(level: str, message: str, fix: str = "") -> None:
    print(level + message)
    if fix:
        print(" " * 10 + "-> " + fix)
    if level is BAD:
        blockers.append(message)
    elif level is WARN:
        warnings.append(message)


# sourav: a secret must be confirmed without being shown. Flow: say whether it is set and how long it is, never the value.
def describe_secret(value: str | None) -> str:
    return f"set ({len(value)} characters)" if value else "not set"


# sourav: the agent is a plain TCP listener as far as we can tell from outside. Flow: try to connect, with a short timeout;
# a refused connection means main_pcm.py is not running, which is the most common reason a call connects but stays silent.
def agent_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# sourav: the port the provider will bind must be free, or startup fails after Twilio is already pointed at us.
def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    env = os.environ
    print("Twilio preflight -- checks what can be checked without calling Twilio\n")

    print("1. configuration")
    try:
        connector = load_connector_settings(env)
        provider_settings = load_provider_settings(env)
    except ConfigError as e:
        report(BAD, f"configuration is invalid: {e}")
        print("\nFix the configuration and run this again.")
        return 1

    if connector.provider != "twilio":
        report(
            BAD,
            f"TELEPHONY_PROVIDER is {connector.provider!r}, not 'twilio'",
            "source deploy/telephony-twilio.env.sh",
        )
    else:
        report(OK, "TELEPHONY_PROVIDER=twilio")

    endpoint = provider_settings.endpoint
    if endpoint is None:
        report(
            BAD,
            "TELEPHONY_ENDPOINT is not set (it is the public wss:// URL Twilio connects to)",
            'run a tunnel to port 8200 and export it, e.g. TELEPHONY_ENDPOINT="wss://<id>.ngrok-free.app/twilio/media"',
        )
    else:
        parts = urlsplit(endpoint)
        if parts.scheme.lower() != "wss":
            report(BAD, f"TELEPHONY_ENDPOINT is {parts.scheme}://, but Twilio requires wss://")
        elif parts.hostname in ("localhost", "127.0.0.1", "::1"):
            report(
                BAD,
                "TELEPHONY_ENDPOINT points at localhost; Twilio dials in from the internet and cannot reach it",
                "expose port 8200 with a tunnel or reverse proxy and use that public host",
            )
        else:
            report(OK, f"TELEPHONY_ENDPOINT={redact_endpoint(endpoint)}")

    # credentials: say what each one can and cannot do, without printing any of them
    token = provider_settings.password
    account_sid = provider_settings.username
    api_key = provider_settings.api_key
    api_secret = provider_settings.api_secret
    allow_unverified = (env.get("TELEPHONY_TWILIO_ALLOW_UNVERIFIED") or "").strip().lower() in ("1", "true", "yes")

    print("\n2. credentials")
    print(f"           Auth Token (TELEPHONY_PASSWORD):  {describe_secret(token)}")
    print(f"           Account SID (TELEPHONY_USERNAME): {account_sid or 'not set'}")
    print(f"           API Key SID (TELEPHONY_API_KEY):  {api_key or 'not set'}")
    print(f"           API Key secret:                   {describe_secret(api_secret)}")
    print()

    if token:
        report(OK, "Auth Token present: every inbound call will be signature-verified")
    elif allow_unverified:
        report(
            WARN,
            "no Auth Token, and TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1: calls will be accepted WITHOUT verification",
            "fine for one test call on a short-lived tunnel; set TELEPHONY_PASSWORD before anyone else can reach this URL",
        )
    else:
        report(
            BAD,
            "no Auth Token: inbound calls cannot be verified, so every call will be refused with 403",
            "copy the Auth Token from the Twilio console home page into TELEPHONY_PASSWORD, or set "
            "TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1 for a development-only test call",
        )

    if api_key and not api_secret:
        report(BAD, "TELEPHONY_API_KEY is set without TELEPHONY_API_SECRET", "set both, or neither")
    elif api_secret and not api_key:
        report(BAD, "TELEPHONY_API_SECRET is set without TELEPHONY_API_KEY", "set both, or neither")
    elif api_key:
        report(OK, "API key pair present: it will authenticate the hand-off REST call")
        if not token:
            report(
                WARN,
                "an API key cannot verify inbound calls -- Twilio signs them with the Auth Token only",
                "this pair is useful for hand-off, but it does not replace TELEPHONY_PASSWORD",
            )

    if not account_sid:
        report(
            WARN,
            "no Account SID: hand-off is unsupported and streams from other Twilio accounts are not filtered out",
            "copy the AC... value from the Twilio console home page into TELEPHONY_USERNAME",
        )

    # the provider validates its own settings; run that now rather than at the first call
    print("\n3. provider startup check")
    try:
        from providers.telephony.twilio import create_provider

        provider = create_provider(provider_settings, env=env)
        report(OK, f"the twilio adapter accepts this configuration (will listen on {provider._host}:{provider._port})")
    except ConfigError as e:
        report(BAD, f"the twilio adapter refuses this configuration: {e}")
        provider = None
    except ImportError as e:
        report(BAD, f"a dependency is missing: {e}", "pip install -r requirements.txt")
        provider = None

    print("\n4. local ports and the voice agent")
    listen_host = provider_settings.listen_host or "127.0.0.1"
    listen_port = provider_settings.listen_port or 8200
    if port_is_free(listen_host, listen_port):
        report(OK, f"port {listen_host}:{listen_port} is free for the connector to bind")
    else:
        report(
            BAD,
            f"port {listen_host}:{listen_port} is already in use",
            "stop whatever holds it, or set TELEPHONY_LISTEN_PORT to a free port (and point the tunnel at that port)",
        )

    if agent_reachable(connector.agent_host, connector.agent_port):
        report(OK, f"the voice agent answers on {connector.agent_host}:{connector.agent_port} (/ws/audio)")
    else:
        report(
            BAD,
            f"nothing is listening on {connector.agent_host}:{connector.agent_port}: the AI pipeline is not running",
            "start it first (main_pcm.py on VOICE_PCM_PORT); a call would otherwise connect and then hear silence",
        )

    # the two steps no script can do for you
    print("\n5. do this in the Twilio console (nothing here can do it for you)")
    if endpoint:
        stream_url = endpoint
    else:
        stream_url = "wss://YOUR-PUBLIC-HOST/twilio/media"
    twiml = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="{stream_url}" />\n'
        "  </Connect>\n"
        "</Response>"
    )
    print("   a) create a TwiML Bin (Twilio console > TwiML Bins > Create) with exactly this body:\n")
    for line in twiml.splitlines():
        print("        " + line)
    print(
        "\n      The url above must match TELEPHONY_ENDPOINT character for character: the signature is computed over it,\n"
        "      so a trailing slash or a different host makes every call fail with 403."
    )
    print(
        "\n   b) Phone Numbers > Manage > Active numbers > your number > Voice Configuration:\n"
        '      set "A call comes in" to the TwiML Bin from step (a), and Save.'
    )
    if endpoint:
        http_base = urlunsplit(("https", urlsplit(endpoint).netloc, "", "", ""))
        print(f"\n      (Your tunnel's public base is {http_base} -- the number must reach it while a call is in progress.)")

    print("\n" + "-" * 110)
    if blockers:
        print(f"NOT READY: {len(blockers)} blocking problem(s) to fix first:")
        for item in blockers:
            print("  - " + item)
        if warnings:
            print(f"plus {len(warnings)} warning(s) above.")
        return 1
    print("READY to take a call." + (f" {len(warnings)} warning(s) above -- read them." if warnings else ""))
    print("\nStart the connector with:  python -m telephony")
    print("Then call your Twilio number. Expect, in the connector's log: a 'start' for the stream, then audio flowing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
