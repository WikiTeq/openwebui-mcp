"""FastMCP server exposing Open WebUI as MCP tools.

Two tools, per spec:

* ``ask`` - send a prompt to a model. The model is a required argument. Runs
  the Open WebUI tool-calling loop: any tools attached to the model are
  enabled automatically (``OpenWebUIClient.resolve_tools``) and results come
  back as a structured dict (answer, reasoning, tool_calls).
* ``list_models`` - list the models the connected Open WebUI user can see.

Authentication: the server itself talks to Open WebUI with a bearer token (as
``Authorization: Bearer``) carried by the SDK client. When a static MCP token
is configured (``OPENWEBUI_MCP_TOKEN``) the MCP endpoint additionally requires
``Authorization: Bearer <token>`` on every request.

Note on the event loop: the SDK ships a sync ``run_chat`` that wraps its async
Socket.IO runner in ``asyncio.run``. That works for the CLI (main thread, no
loop running) but NOT inside an async server: ``asyncio.run`` from a running
loop raises, and nesting it in ``asyncio.to_thread`` starves the socket
background tasks so completion events never arrive (the tool hangs). So the
``ask`` handler awaits the SDK's async ``sockets.run_chat_with_tools`` directly
on this event loop - one loop owns the aiohttp session, the socketio client and
the completion event, exactly like the CLI's single loop. Blocking SDK work
without tools (plain HTTP) is still offloaded via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import threading
from concurrent.futures import Future
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from openwebui_sdk import OpenWebUIClient
from pydantic import AnyHttpUrl

from openwebui_mcp.config import Settings

logger = logging.getLogger(__name__)

# SDK models attach tools under info.meta.toolIds; we surface that in
# list_models so callers can see which tools a model can invoke.
_TOOL_FIELDS = "tool_ids"


class StaticTokenVerifier(TokenVerifier):
    """Accepts exactly one configured bearer token (constant-time compare)."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) != len(self._token):
            return None
        if sum(a != b for a, b in zip(token, self._token, strict=True)) != 0:
            return None
        return AccessToken(
            token=token,
            client_id="openwebui-mcp",
            scopes=["all"],
            subject=token,
        )


def _mcp_endpoint_url(settings: Settings) -> AnyHttpUrl:
    """Dummy discovery URL used by AuthSettings when MCP token auth is on.

    Bearer verification via ``StaticTokenVerifier`` never consults OAuth
    discovery, so the URL only needs to be syntactically valid.
    """
    return AnyHttpUrl(f"http://{settings.host}:{settings.port}/mcp")


def apply_tls_settings(settings: Settings) -> None:
    """Apply TLS trust config to this process before any SDK request.

    ``OPENWEBUI_CA_BUNDLE`` - set ``SSL_CERT_FILE`` so every
    ``create_default_context`` caller (urllib for the JSON routes, aiohttp for
    the Socket.IO tool loop) trusts the given PEM CA. ``OPENWEBUI_SSL_VERIFY``
    false - drop certificate verification for the JSON (urllib) routes against
    self-signed / private-CA servers. No-op (and safe to call always) when
    neither is configured.
    """
    if settings.ssl_ca_bundle:
        os.environ["SSL_CERT_FILE"] = settings.ssl_ca_bundle
    if not settings.ssl_verify and hasattr(ssl, "_create_default_https_context"):
        ssl._create_default_https_context = ssl._create_unverified_context


def run_on_dedicated_loop(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run an async coroutine on a dedicated thread + its own fresh event loop.

    FastMCP streamable-http executes tool handlers inside an anyio cancel
    scope that tears down python-socketio background tasks, so the SDK's
    Socket.IO receive loop never pumps (the chat completes on the server but
    completion events never arrive -> the tool hangs). The CLI works because it
    runs ``asyncio.run`` on a loop it fully owns in the main thread.

    This mirrors the CLI: spawn a plain thread, build a fresh event loop there,
    run the coroutine to completion, and propagate the result/exception. The
    aiohttp session, the socketio AsyncClient and the completion event all live
    on that one loop, with no anyio parent to cancel them.
    """
    result: Future[Any] = Future()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result.set_result(loop.run_until_complete(coro_fn(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001 - propagate to caller
            result.set_exception(exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return result.result()  # blocks the calling (FastMCP) coroutine until done


def create_server(
    settings: Settings, *, client: OpenWebUIClient | None = None
) -> FastMCP:
    """Build a configured FastMCP instance exposing the Open WebUI tools.

    ``client`` is injectable for tests; when omitted a client is created from
    ``settings`` (base URL + bearer token). When ``settings.mcp_token`` is set
    the MCP endpoint requires ``Authorization: Bearer <token>`` on requests.
    """
    apply_tls_settings(settings)
    owui = client or OpenWebUIClient(base_url=settings.base_url, token=settings.token)

    mcp = FastMCP(
        settings.name,
        host=settings.host,
        port=settings.port,
        auth=(
            AuthSettings(
                issuer_url=_mcp_endpoint_url(settings),
                resource_server_url=_mcp_endpoint_url(settings),
            )
            if settings.mcp_token
            else None
        ),
        token_verifier=(
            StaticTokenVerifier(settings.mcp_token) if settings.mcp_token else None
        ),
    )

    def _list_models_sync() -> list[dict[str, Any]]:
        models = owui.list_models()
        return [
            {
                "id": m.id,
                "name": m.name,
                _TOOL_FIELDS: m.tool_ids or [],
            }
            for m in models
        ]

    @mcp.tool()
    async def list_models() -> list[dict[str, Any]]:
        """List the models available on the connected Open WebUI server.

        Returns one entry per model with its id, display name, and the ids of
        any tools attached to it. Use the returned ids as the ``model``
        argument of the ``ask`` tool.
        """
        return await asyncio.to_thread(_list_models_sync)

    def _ask_no_tools_sync(
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        """Plain HTTP streaming path (no Socket.IO). Blocking; run in a thread."""
        result = owui.run_chat(
            model=model,
            messages=messages,
            tool_ids=[],
            temperature=temperature,
            timeout=timeout_s,
        )
        return {
            "answer": result.answer,
            "reasoning": result.reasoning,
            "tool_calls": result.tool_calls,
        }

    @mcp.tool()
    async def ask(
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        use_tools: bool = True,
    ) -> dict[str, Any]:
        """Ask an Open WebUI model a question, with tool support.

        The model is required, e.g. one of the ids returned by ``list_models``.
        Tools attached to the model are enabled by default (``use_tools``) and
        run server-side through Open WebUI's Socket.IO tool loop, so answers may
        be produced with real tool calls.

        Args:
            model: Open WebUI model id to ask, e.g. "sample-workspace-model-1".
            prompt: The user message to send to the model.
            system: Optional system prompt leading the conversation.
            temperature: Optional sampling temperature override.
            use_tools: Enable tools attached to the model (default True).

        Returns:
            Dict with the answer text, optional reasoning, and any tool calls
            made: {"answer", "reasoning", "tool_calls"}.
        """
        import time

        from openwebui_sdk import sockets

        # SDK timeouts are in SECONDS (http.DEFAULT_TIMEOUT=60); settings in ms.
        timeout_s = max(1, settings.timeout_ms // 1000)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        tool_ids = (
            await asyncio.to_thread(owui.resolve_tools, model) if use_tools else []
        )
        logger.info(
            "ask: model=%s tools=%s timeout=%ss", model, tool_ids or "(none)", timeout_s
        )

        started = time.monotonic()
        try:
            if tool_ids:
                # Run the SDK's async Socket.IO runner on a DEDICATED thread
                # with its own fresh event loop (mirrors the CLI, which owns its
                # loop). FastMCP streamable-http runs handlers inside an anyio
                # cancel scope that tears down python-socketio background tasks
                # - so awaiting the runner directly on the FastMCP loop starves
                # the socket receive path and the tool hangs. A dedicated loop
                # in a plain thread has no anyio parent, so the aiohttp session,
                # the socketio client and the completion event all live on one
                # loop and chat-events flow back.
                def _run() -> dict[str, Any]:
                    data = run_on_dedicated_loop(
                        sockets.run_chat_with_tools,
                        base_url=settings.base_url,
                        token=settings.token,
                        model=model,
                        messages=messages,
                        tool_ids=tool_ids,
                        timeout=timeout_s,
                        on_status=lambda s: logger.info(
                            "ask[%s]: status: %s", model, s
                        ),
                        on_tool=lambda s: logger.info("ask[%s]: tool: %s", model, s),
                        on_reasoning=lambda s: logger.debug(
                            "ask[%s]: reasoning: %s", model, (s or "")[:200]
                        ),
                    )
                    return {
                        "answer": data.get("answer", ""),
                        "reasoning": data.get("reasoning"),
                        "tool_calls": data.get("tool_calls", []),
                    }

                # Offload the blocking run_on_dedicated_loop call so the
                # FastMCP loop stays responsive while the worker thread drives
                # the SDK loop to completion.
                result = await asyncio.wait_for(
                    asyncio.to_thread(_run), timeout=timeout_s + 60
                )
            else:
                # No tools: plain HTTP streaming path (blocking) - offload.
                result = await asyncio.to_thread(
                    _ask_no_tools_sync,
                    model,
                    messages,
                    temperature,
                    timeout_s,
                )
        except TimeoutError:
            logger.error(
                "ask: timed out after %ss for model=%s", timeout_s + 60, model
            )
            raise RuntimeError(
                f"Open WebUI did not complete within {timeout_s + 60}s"
            ) from None
        logger.info(
            "ask: done in %.1fs (tools=%d)",
            time.monotonic() - started,
            len(result.get("tool_calls") or []),
        )
        return result

    return mcp
