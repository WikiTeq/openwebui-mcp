"""Real-ASGI integration tests: drive the actual FastMCP HTTP app (not a
mocked get_http_request()) to prove two properties the unit tests can't:

* streamable-http: concurrent requests with different api_key query params
  never cross-talk (the race the per-request-client design exists to
  prevent).
* SSE: ?api_key= on the /sse connection URL is silently ignored - it can't
  reach the tool call, so the fixed settings.token is always used. This is a
  protocol-level limitation (see resolve_request_token's docstring), not a
  bug; this test pins that documented behavior so a future change doesn't
  silently "fix" it into something that looks like it works but doesn't
  (the failure mode is invisible - no error, just the wrong/right key).

Uses httpx2 (fastmcp's vendored httpx client) and uvicorn - both locked
transitive dependencies of fastmcp, not new project dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any

import httpx2
import pytest
import uvicorn
from openwebui_sdk.models import Model

from openwebui_mcp import server as server_mod
from openwebui_mcp.config import Settings


class _RecordingClient:
    """Stand-in for OpenWebUIClient: records the token it was built with and
    echoes it back in the response, so a caller can tell which identity
    answered its specific request."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token

    def list_models(self) -> list[Model]:
        return [Model(id=f"model-for-{self.token}", name="M", tool_ids=[])]


@pytest.fixture
def recording_app(monkeypatch: pytest.MonkeyPatch):
    """A real create_server() app with OpenWebUIClient swapped for the
    recorder, and no client= injected - the actual production code path."""
    monkeypatch.setattr(server_mod, "OpenWebUIClient", _RecordingClient)
    settings = Settings(base_url="http://fake-owui:8080", token="sk-fixed-fallback")
    mcp = server_mod.create_server(settings)
    return mcp.http_app(transport="streamable-http")


async def _call_list_models(client: httpx2.AsyncClient, query_suffix: str) -> str:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init_resp = await client.post(
        f"/mcp/{query_suffix}",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        headers=headers,
    )
    assert init_resp.status_code == 200, init_resp.text
    session_id = init_resp.headers["mcp-session-id"]
    headers["mcp-session-id"] = session_id

    await client.post(
        f"/mcp/{query_suffix}",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    call_resp = await client.post(
        f"/mcp/{query_suffix}",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_models", "arguments": {}},
        },
        headers=headers,
    )
    assert call_resp.status_code == 200, call_resp.text
    return call_resp.text


@pytest.mark.anyio
async def test_streamable_http_concurrent_requests_no_token_crosstalk(
    recording_app: Any,
) -> None:
    """Two overlapping in-flight requests, different api_keys, on separate
    client connections (separate MCP sessions). Each must see only its own
    token - the actual race resolve_request_token()'s per-call local client
    exists to prevent."""
    transport = httpx2.ASGITransport(app=recording_app)
    async with (
        recording_app.router.lifespan_context(recording_app),
        httpx2.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client_a,
        httpx2.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client_b,
    ):
        text_a, text_b = await asyncio.gather(
            _call_list_models(client_a, "?api_key=sk-user-a"),
            _call_list_models(client_b, "?api_key=sk-user-b"),
        )
    assert "model-for-sk-user-a" in text_a, text_a
    assert "model-for-sk-user-b" in text_b, text_b
    assert "sk-user-b" not in text_a
    assert "sk-user-a" not in text_b


@pytest.mark.anyio
async def test_streamable_http_fallback_when_api_key_absent(
    recording_app: Any,
) -> None:
    transport = httpx2.ASGITransport(app=recording_app)
    async with (
        recording_app.router.lifespan_context(recording_app),
        httpx2.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client,
    ):
        text = await _call_list_models(client, "")
    assert "model-for-sk-fixed-fallback" in text, text


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _sse_events(resp: httpx2.Response):
    event_type, data_lines = "message", []
    async for line in resp.aiter_lines():
        line = line.rstrip("\n")
        if line == "":
            if data_lines:
                yield event_type, "\n".join(data_lines)
            event_type, data_lines = "message", []
        elif line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())


@pytest.mark.anyio
async def test_sse_ignores_api_key_uses_fixed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the documented SSE limitation: ?api_key= on the /sse connection
    URL cannot reach the tool call (the MCP SDK's own SSE client resolves the
    server's relative message-endpoint URL via urljoin, dropping the
    connection URL's query string - see resolve_request_token's docstring).
    Requires a real bound socket: httpx2.ASGITransport can't drive a
    long-lived streaming ASGI app like SSE (it buffers until the app
    returns, which never happens for an open SSE connection)."""
    monkeypatch.setattr(server_mod, "OpenWebUIClient", _RecordingClient)
    settings = Settings(base_url="http://fake-owui:8080", token="sk-fixed-fallback")
    mcp = server_mod.create_server(settings)
    app = mcp.http_app(transport="sse")

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uv_server = uvicorn.Server(config)
    server_task = asyncio.create_task(uv_server.serve())
    try:
        for _ in range(100):
            if uv_server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("uvicorn did not start in time")

        base_url = f"http://127.0.0.1:{port}"
        events: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        message_url_ready: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        async def read_sse(client: httpx2.AsyncClient) -> None:
            async with client.stream(
                "GET", f"{base_url}/sse?api_key=sk-sse-caller"
            ) as resp:
                assert resp.status_code == 200, resp.status_code
                async for event_type, data in _sse_events(resp):
                    if event_type == "endpoint" and not message_url_ready.done():
                        message_url_ready.set_result(data)
                    else:
                        await events.put((event_type, data))

        async with httpx2.AsyncClient() as client:
            reader = asyncio.create_task(read_sse(client))
            try:
                message_path = await asyncio.wait_for(message_url_ready, timeout=10)
                message_url = base_url + message_path

                async def post(payload: dict) -> None:
                    r = await client.post(message_url, json=payload)
                    assert r.status_code == 202, (r.status_code, r.text)

                await post(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test-sse", "version": "0"},
                        },
                    }
                )
                await asyncio.wait_for(events.get(), timeout=10)  # initialize response

                await post({"jsonrpc": "2.0", "method": "notifications/initialized"})
                await post(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "list_models", "arguments": {}},
                    }
                )
                _, call_data = await asyncio.wait_for(events.get(), timeout=10)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
    finally:
        uv_server.should_exit = True
        await server_task

    # The api_key on the /sse connection URL never reaches the tool call;
    # it always falls back to the fixed settings.token, same as stdio.
    assert "model-for-sk-fixed-fallback" in call_data, call_data
    assert "sk-sse-caller" not in call_data
