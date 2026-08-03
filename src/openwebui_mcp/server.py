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

Note on threading: FastMCP invokes async tool handlers directly in its event
loop. The SDK's ``run_chat`` with tools spawns its own event loop via
``asyncio.run``, which fails from a running loop. The handlers are therefore
async and offload all blocking SDK work to a worker thread with
``asyncio.to_thread``, so the SDK can create its own loop there.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
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

    def _ask_sync(
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        use_tools: bool,
        timeout_s: int,
    ) -> dict[str, Any]:
        import time

        started = time.monotonic()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        tool_ids = owui.resolve_tools(model) if use_tools else []
        logger.info(
            "ask: model=%s tools=%s timeout=%ss",
            model,
            tool_ids or "(none)",
            timeout_s,
        )

        result = owui.run_chat(
            model=model,
            messages=messages,
            tool_ids=tool_ids,
            temperature=temperature,
            timeout=timeout_s,
            on_status=lambda s: logger.info("ask[%s]: status: %s", model, s),
            on_tool=lambda s: logger.info("ask[%s]: tool: %s", model, s),
            on_reasoning=lambda s: logger.debug(
                "ask[%s]: reasoning: %s", model, (s or "")[:200]
            ),
        )
        logger.info(
            "ask: done in %.1fs (tools=%d)",
            time.monotonic() - started,
            len(result.tool_calls or []),
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
        # SDK timeouts are in SECONDS (http.DEFAULT_TIMEOUT=60), settings in ms.
        timeout_s = max(1, settings.timeout_ms // 1000)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    _ask_sync, model, prompt, system, temperature, use_tools, timeout_s
                ),
                # Bound the whole tool call: socket connect + chat + completion
                # must finish inside the budget plus a small margin.
                timeout=timeout_s + 60,
            )
        except TimeoutError as exc:
            logger.error("ask: timed out after %ss for model=%s", timeout_s + 60, model)
            raise RuntimeError(
                f"Open WebUI did not complete within {timeout_s + 60}s"
            ) from exc

    return mcp
