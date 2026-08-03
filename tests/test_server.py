"""Server tests: tool registration, tool behaviour with a fake client, auth."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.server.fastmcp import FastMCP
from openwebui_sdk import OpenWebUIClient
from openwebui_sdk.chat import ChatResult
from openwebui_sdk.models import Model

from openwebui_mcp.config import Settings
from openwebui_mcp.server import StaticTokenVerifier, create_server

MS_SAMPLE = [
    Model(id="m1", name="Model One", tool_ids=["t1", "t2"]),
    Model(id="m2", name="Model Two", tool_ids=None),
]


class FakeClient:
    """Duck-typed stand-in for OpenWebUIClient; records calls, returns canned data."""

    def __init__(
        self,
        *,
        models: list[Model] | None = None,
        tool_ids: list[str] | None = None,
        result: ChatResult | None = None,
    ) -> None:
        self.models = models or []
        self.tool_ids = tool_ids or []
        self.result = result or ChatResult(
            answer="hi", reasoning="rt", tool_calls=[{"name": "x"}]
        )
        self.resolve_calls: list[str] = []
        self.chat_calls: list[dict[str, Any]] = []

    def list_models(self) -> list[Model]:
        return self.models

    def resolve_tools(self, model_id: str) -> list[str]:
        self.resolve_calls.append(model_id)
        return self.tool_ids

    def run_chat(self, **kwargs: Any) -> ChatResult:
        self.chat_calls.append(kwargs)
        return self.result


def _tool_fn(mcp: FastMCP, name: str) -> Any:
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name!r} not registered"
    return tool.fn


def _fake_settings(**kw: Any) -> Settings:
    base = {"base_url": "http://owui:8080", "token": "sk-x", **kw}
    return Settings(**base)


def test_server_exposes_both_tools() -> None:
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, FakeClient()))
    names = {t.name for t in server._tool_manager.list_tools()}
    assert names == {"ask", "list_models"}


def test_ask_requires_model_and_prompt_params() -> None:
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, FakeClient()))
    tool = server._tool_manager.get_tool("ask")
    assert tool is not None
    assert "model" in tool.parameters.get("required", [])
    assert "prompt" in tool.parameters.get("required", [])


def test_ask_calls_client_with_tools_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient(tool_ids=["t1"])
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, fake))
    out = _tool_fn(server, "ask")(model="m1", prompt="What time is it?", use_tools=True)
    assert fake.resolve_calls == ["m1"]
    call = fake.chat_calls[0]
    assert call["model"] == "m1"
    assert call["tool_ids"] == ["t1"]
    assert call["messages"] == [{"role": "user", "content": "What time is it?"}]
    assert out == {"answer": "hi", "reasoning": "rt", "tool_calls": [{"name": "x"}]}


def test_ask_system_prompt_prepended() -> None:
    fake = FakeClient()
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, fake))
    _tool_fn(server, "ask")(model="m1", prompt="hi", system="Be terse", use_tools=False)
    call = fake.chat_calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "Be terse"},
        {"role": "user", "content": "hi"},
    ]
    assert call["tool_ids"] == []


def test_ask_passes_temperature() -> None:
    fake = FakeClient()
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, fake))
    _tool_fn(server, "ask")(model="m1", prompt="hi", temperature=0.5, use_tools=False)
    assert fake.chat_calls[0]["temperature"] == 0.5


def test_list_models_shape() -> None:
    server = create_server(
        _fake_settings(), client=cast(OpenWebUIClient, FakeClient(models=MS_SAMPLE))
    )
    out = _tool_fn(server, "list_models")()
    assert out == [
        {"id": "m1", "name": "Model One", "tool_ids": ["t1", "t2"]},
        {"id": "m2", "name": "Model Two", "tool_ids": []},
    ]


def test_mcp_auth_wired_when_token_set() -> None:
    server = create_server(
        _fake_settings(mcp_token="s3cret"), client=cast(OpenWebUIClient, FakeClient())
    )
    verifier = server._token_verifier
    assert verifier is not None


def test_mcp_auth_absent_without_token() -> None:
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, FakeClient()))
    assert server._token_verifier is None


@pytest.mark.anyio
async def test_static_verifier_accepts_and_rejects() -> None:
    verifier = StaticTokenVerifier("right-token")
    ok = await verifier.verify_token("right-token")
    assert ok is not None
    assert await verifier.verify_token("wrong-token") is None
    assert await verifier.verify_token("") is None


@pytest.mark.anyio
async def test_call_tool_end_to_end() -> None:
    """Drive both tools through FastMCP's call_tool pipeline (protocol level)."""
    fake = FakeClient(
        models=MS_SAMPLE,
        tool_ids=["t1"],
        result=ChatResult(answer="42", tool_calls=[]),
    )
    server = create_server(_fake_settings(), client=cast(OpenWebUIClient, fake))

    content, structured = cast(Any, await server.call_tool("list_models", {}))
    assert isinstance(content, list)
    results = structured["result"]
    assert [r["id"] for r in results] == ["m1", "m2"]

    out, structured_out = cast(
        Any,
        await server.call_tool(
            "ask",
            {"model": "m1", "prompt": "what is 6*7?", "use_tools": True},
        ),
    )
    assert fake.resolve_calls == ["m1"]
    assert fake.chat_calls[0]["tool_ids"] == ["t1"]
    assert structured_out["answer"] == "42"
