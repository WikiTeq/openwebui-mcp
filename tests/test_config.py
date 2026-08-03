"""Config resolution tests (env-driven Settings)."""

from __future__ import annotations

import pytest

from openwebui_mcp.config import Settings

# Clear any ambient Open WebUI env vars so tests are hermetic.
_AMBIENT = (
    "OPENWEBUI_BASE_URL",
    "OPENWEBUI_URL",
    "OWUI_URL",
    "OPENWEBUI_API_KEY",
    "OPENWEBUI_TOKEN",
    "OWUI_API_KEY",
    "OWUI_TOKEN",
    "OWUI_TIMEOUT_MS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AMBIENT:
        monkeypatch.delenv(name, raising=False)


def test_from_env_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "http://owui:8080")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "sk-123")
    s = Settings.from_env()
    assert s.base_url == "http://owui:8080"
    assert s.token == "sk-123"
    assert s.transport == "stdio"
    assert s.mcp_token is None
    assert s.timeout_ms == 120_000


def test_from_env_missing_raises() -> None:
    with pytest.raises(ValueError, match="not configured"):
        Settings.from_env()


def test_from_env_alias_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    # First listed alias wins over the later fallbacks.
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "first")
    monkeypatch.setenv("OPENWEBUI_URL", "second")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key-first")
    monkeypatch.setenv("OPENWEBUI_TOKEN", "key-second")
    s = Settings.from_env()
    assert s.base_url == "first"
    assert s.token == "key-first"


def test_from_env_mcp_token_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OVERRIDE_BASE_URL", "http://h:1")
    monkeypatch.setenv("OVERRIDE_KEY", "k")
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "http://h:1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "k")
    monkeypatch.setenv("OPENWEBUI_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("OWUI_TIMEOUT_MS", "30000")
    s = Settings.from_env()
    assert s.mcp_token == "s3cret"
    assert s.timeout_ms == 30_000


def test_from_env_invalid_transport_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "http://h:1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "k")
    monkeypatch.setenv("OPENWEBUI_MCP_TRANSPORT", "telepathy")
    with pytest.raises(ValueError, match="invalid transport"):
        Settings.from_env()


def test_from_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "http://h:1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "k")
    s = Settings.from_env(transport="streamable-http", name="custom")
    assert s.transport == "streamable-http"
    assert s.name == "custom"
