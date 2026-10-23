"""telephony/agent_link.py -- the connector's connection to the voice agent's /ws/audio WebSocket.

What this file is for
---------------------
The connector talks to main_pcm.py through a tiny interface (AgentLink: send text, send bytes, receive, close) so that
unit tests can swap in an in-memory fake and never need a network, a GPU or the real agent. The one real implementation
wraps the `websockets` client library -- the same library scripts/e2e_call.py and tools/bench_transport.py already use to
talk to /ws/audio (it is installed with uvicorn[standard], so no new dependency).

Where to point it: the host/port come from telephony/config.py (VOICE_PCM_HOST / VOICE_PCM_PORT). This file never builds
a URL on its own from anything but those values. There is NO authentication on /ws/audio today; keep this link on
localhost or a private network (see telephony/README.md).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

# Largest single WebSocket message accepted from the agent. A TTS clip is a few hundred KB; this only stops a runaway.
MAX_AGENT_MESSAGE_BYTES = 16 * 1024 * 1024


class AgentLinkClosed(Exception):
    """The connection to the voice agent is closed (cleanly or not)."""


class AgentLink(Protocol):
    """What the connector needs from its connection to /ws/audio."""

    async def send_text(self, text: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


# A factory returns a NEW, already-open link each time it is awaited: one link per call, never shared.
LinkFactory = Callable[[], Awaitable[AgentLink]]


# sourav: build the /ws/audio address from the configured host and port (default: this machine, port 8101).
# Flow: bracket an IPv6 literal, join into ws://host:port/ws/audio.
def agent_ws_url(host: str, port: int) -> str:
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"ws://{host_part}:{port}/ws/audio"


class WebSocketAgentLink:
    """AgentLink over a real `websockets` connection. Turns the library's closed-connection errors into AgentLinkClosed
    so the connector never depends on that library's exception types."""

    # sourav: wrap an already-open websockets connection. Flow: keep it and the exception class to translate.
    def __init__(self, ws: Any, closed_error: type[Exception]):
        self._ws = ws
        self._closed_error = closed_error

    # sourav: control messages (hello, playback_done, interrupt) go out as text frames. Flow: send, translate errors.
    async def send_text(self, text: str) -> None:
        try:
            await self._ws.send(text)
        except self._closed_error as e:
            raise AgentLinkClosed(str(e)) from e

    # sourav: caller audio goes out as binary frames. Flow: send, translate errors.
    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._ws.send(data)
        except self._closed_error as e:
            raise AgentLinkClosed(str(e)) from e

    # sourav: the agent sends WAV clips (binary) and control/transcript frames (text). Flow: await the next frame.
    async def recv(self) -> str | bytes:
        try:
            message = await self._ws.recv()
        except self._closed_error as e:
            raise AgentLinkClosed(str(e)) from e
        return message

    # sourav: ending the call closes the socket, which is how the agent notices the caller left. Flow: close, ignore errors.
    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001 - already closing/closed is fine
            return


# sourav: the connector needs a fresh connection per call without importing the websockets library at module load (tests
# never touch it). Flow: return an async factory that imports the library lazily, opens the connection, wraps it.
def websocket_link_factory(url: str, max_message_bytes: int = MAX_AGENT_MESSAGE_BYTES) -> LinkFactory:
    async def factory() -> AgentLink:
        import websockets
        from websockets.exceptions import ConnectionClosed

        ws = await websockets.connect(url, max_size=max_message_bytes)
        return WebSocketAgentLink(ws, ConnectionClosed)

    return factory
