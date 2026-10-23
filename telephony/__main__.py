"""telephony/__main__.py -- THE entry point: `python -m telephony` runs the master connector with whatever provider is configured.

What this file is for
---------------------
One command for every client telephony system. It reads the environment (telephony/config.py), resolves the provider
(telephony/loader.py: explicit name, or safe auto-detection), lets that provider validate its own configuration, then runs the
connector, which opens one /ws/audio session per call. It adds no behaviour of its own and contains no provider logic.

    TELEPHONY_PROVIDER=websocket TELEPHONY_ENDPOINT=place your url here python -m telephony
    TELEPHONY_PROVIDER=wavfile   TELEPHONY_ENDPOINT=/path/to/question.wav python -m telephony   # offline test, no provider

    python -m telephony --probe     check what TELEPHONY_ENDPOINT speaks (opt-in network check, starts no calls)

Exit codes: 0 normal end / Ctrl+C / SIGTERM, 2 configuration or provider-selection error (the message says what to fix).
Configuration errors never print credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Mapping, Sequence

from providers.telephony.probe import probe_endpoint, require_probe_allows_start
from telephony.config import ConfigError, load_connector_settings, load_provider_settings
from telephony.connector import TelephonyConnector
from telephony.loader import load_provider, resolve_provider_name

logger = logging.getLogger("telephony")
SHUTDOWN_GRACE_S = 5.0  # time given to live calls to hang up after SIGTERM/Ctrl+C before the provider is cancelled


# sourav: wires configuration -> provider -> connector -> shutdown. Flow: load connector settings, require a provider name, load
# the provider settings only now (secrets are read only for the chosen provider), resolve + build + validate the provider (all
# before any call is accepted), run the connector; SIGINT/SIGTERM ask live sessions to stop, then cancel the provider after a grace.
async def _run(env: Mapping[str, str] | None = None) -> int:
    settings = load_connector_settings(env)
    if settings.provider is None:
        logger.error("TELEPHONY_PROVIDER is not set; nothing to run (see telephony/README.md)")
        return 2
    provider_settings = load_provider_settings(env)
    if settings.provider == "auto" and settings.auto_probe:
        result = await probe_endpoint(provider_settings)
        logger.info("endpoint probe: %s", result.summary())
        require_probe_allows_start(result)
    provider = load_provider(settings.provider, provider_settings)
    connector = TelephonyConnector(settings)
    logger.info(
        "telephony connector starting: provider=%s agent=%s:%d",
        resolve_provider_name(settings.provider, provider_settings.endpoint),
        settings.agent_host,
        settings.agent_port,
    )
    loop = asyncio.get_running_loop()
    run_task = asyncio.create_task(connector.run(provider), name="telephony-run")

    def _shutdown() -> None:
        logger.info("shutdown requested; ending live calls")
        connector.stop_all()
        loop.call_later(SHUTDOWN_GRACE_S, run_task.cancel)

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):  # not available on every platform
            loop.add_signal_handler(sig, _shutdown)
    try:
        await run_task
    except asyncio.CancelledError:
        logger.info("telephony connector stopped")
    return 0


# sourav: `python -m telephony --probe` -- a diagnostic that starts no calls. Flow: read TELEPHONY_ENDPOINT (and optional
# credentials), contact it once, print the verdict (what the URL speaks, redacted, no secrets), exit 0 if it answered usefully.
async def _probe_only(env: Mapping[str, str] | None = None) -> int:
    result = await probe_endpoint(load_provider_settings(env))
    print(result.summary())  # noqa: T201 - this is the command's output
    return 0 if result.reachable else 2


# sourav: process entry point. Flow: configure logging, run, turn a configuration error into a clear message and exit code 2,
# Ctrl+C into a clean exit.
def main(env: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = list(argv or ())
    try:
        if "--probe" in args:
            return asyncio.run(_probe_only(env))
        if args:
            logger.error("unknown argument(s) %s; the only option is --probe", args)
            return 2
        return asyncio.run(_run(env))
    except ConfigError as e:
        logger.error("configuration error: %s", e)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
