# openwebui-mcp

Open WebUI MCP server built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
and the [openwebui-sdk](https://github.com/vedmaka/openwebui-sdk) library

Lets any MCP client (Claude Desktop, Cursor, agents) ask Open WebUI models
through the full tool-calling loop, not just plain chat

<img width="3680" height="2592" alt="ray-so-export (1)" src="https://github.com/user-attachments/assets/01e7ed7f-35a5-413e-9b26-27ac823d7c74" />

## Quick start

```bash
OPENWEBUI_BASE_URL=http://localhost:8080 OPENWEBUI_API_KEY=sk-... \
  uvx openwebui-mcp   # stdio (default) - installs and runs from PyPI
```

### Connect an MCP client

**Codex CLI** (`~/.codex/config.toml`) - register the server as `owui` and let
it call tools without approval:

```toml
[mcp_servers.owui]
command = "uvx"
args = ["openwebui-mcp"]
env = {
  OPENWEBUI_BASE_URL = "http://localhost:8080",
  OPENWEBUI_API_KEY = "sk-..."
}
default_tools_approval_mode = "auto"
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "openwebui": {
      "command": "uvx",
      "args": ["openwebui-mcp"],
      "env": {
        "OPENWEBUI_BASE_URL": "http://localhost:8080",
        "OPENWEBUI_API_KEY": "sk-..."
      }
    }
  }
}
```

The quick start uses stdio, so each client spawns the server locally. For
remote HTTP transports, open the collapsible options below (or see Run)

<details>
<summary>SSE setup (Claude Desktop and other SSE-capable clients)</summary>

Run the server:

```bash
uvx openwebui-mcp --transport sse --host 0.0.0.0 --port 8000
```

**Claude Desktop** (`claude_desktop_config.json`) - SSE endpoint is `http://<host>:8000/sse`:

```json
{
  "mcpServers": {
    "openwebui": {
      "type": "sse",
      "url": "http://<host>:8000/sse"
    }
  }
}
```

</details>

<details>
<summary>Streamable HTTP setup (Codex CLI, rmcp, agents over HTTP)</summary>

Run the server:

```bash
uvx openwebui-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

**Codex CLI** (`~/.codex/config.toml`) - streamable-http endpoint is `http://<host>:8000/mcp`:

```toml
[mcp_servers.owui]
url = "http://<host>:8000/mcp"
default_tools_approval_mode = "auto"
```

A bare `http://<host>:8000` returns 404 on initialize; point the client URL at
the full `/mcp` path. rmcp and other streamable-http clients use the same URL

</details>

## Proactive ask skill

The repo ships a skill that makes an agent consult the `ask` tool eagerly
instead of only when it happens to choose to:

```text
skills/owui-proactive-ask/SKILL.md
```

It instructs the agent to call `mcp__owui__ask` before answering **every**
explicit or implicit question (advice, explanations, recommendations,
troubleshooting, follow-ups), pass relevant conversation context via
`history`, keep remote tools enabled (`use_tools: true`), and validate the OWUI
answer against local evidence before replying. It also covers failure handling
(retry, fall back, say it failed - never fake an OWUI result).

Install it with the agent skills CLI (`npx skills`) or manually:

```bash
# global install for Codex
npx skills add ./skills/owui-proactive-ask -g -a codex -y

# global install for Claude Code
npx skills add ./skills/owui-proactive-ask -g -a claude-code -y

# or copy the folder into your agent's skills directory
cp -r skills/owui-proactive-ask ~/.agents/skills/
```

The skill references the tool as `mcp__owui__ask`, so register the MCP server
under the name `owui` (Codex-style clients name MCP tools
`mcp__<server>__<tool>`, see `[mcp_servers.owui]` in Quick start above).

The skill pairs with `OPENWEBUI_ASK_DESCRIPTION` and `OPENWEBUI_INSTRUCTIONS`
(see Configure): the skill makes the agent call `ask`, while the description
and instructions tell the model why and when.

## Tools

Exactly two tools are exposed

### `ask`

Send a prompt to a model with Open WebUI tool support

- `prompt` (required) - the user message
- `model` (optional) - the model id to ask, e.g. one returned by `list_models`.
  Omitted, the server uses the configured `OPENWEBUI_DEFAULT_MODEL`; if that
  is also unset the call fails with an error
- `system` (optional) - a system prompt leading the conversation
- `temperature` (optional) - sampling temperature override
- `use_tools` (optional, default true) - enable the tools attached to the model
- `history` (optional) - prior turns `[{"role": "user"|"assistant", "content": "..."}]`
  sent before `prompt` so the remote model keeps context from earlier questions

**Stateless**: each `ask` call is a fresh conversation on the remote model - it
never remembers previous calls. To carry context across questions, pass the
relevant earlier exchanges in `history` (e.g. your prior question and its
answer), or inline the context into `prompt`. Agents that keep their own
conversation log should replay the needed turns via `history`

To lock every call to one model regardless of what the client passes, set
`OPENWEBUI_ENFORCE_DEFAULT_MODEL=true` (requires `OPENWEBUI_DEFAULT_MODEL`);
`ask` then ignores the `model` argument entirely

To control what agents see about this tool, set `OPENWEBUI_ASK_DESCRIPTION` -
it replaces only the first summary line of the tool description (the text
agents read to decide how to call `ask`). The `IMPORTANT - this MCP server is
stateless` block, the argument docs and the default-model guidance are always
present. Unset, the built-in summary line is used

Tools attached to the model run server-side through Open WebUI's Socket.IO
tool loop, so answers can be produced with real tool calls. Result is a struct
with `answer`, `reasoning` and `tool_calls`

### `list_models`

List the models the connected Open WebUI user can see. Each entry carries the
model `id`, display `name` and the `tool_ids` attached to it

## Authentication

Token-based, in two layers

1. **Open WebUI access** (required). The server talks to Open WebUI as
   `Authorization: Bearer <token>`. Provide an API key or JWT via env:

   ```env
   OPENWEBUI_BASE_URL=http://localhost:8080
   OPENWEBUI_API_KEY=sk-...
   ```

2. **MCP endpoint auth** (optional). Set `OPENWEBUI_MCP_TOKEN=<token>` to make
   the MCP server itself require `Authorization: Bearer <token>` on every
   request. Unset, the endpoint is open to its listeners

## TLS to Open WebUI

If Open WebUI is served over https with a certificate the process does not
trust (self-signed, private/Traefik CA, or a MITM proxy CA), the SDK raises
`CERTIFICATE_VERIFY_FAILED`. Fix by trusting the right CA, never by
silently disabling checks unless you must:

```env
OPENWEBUI_CA_BUNDLE=/path/to/ca.pem     # trust a specific PEM CA (covers JSON routes + Socket.IO tool loop)
OPENWEBUI_SSL_VERIFY=false              # skip verification (JSON routes only; not recommended)
```

The CA bundle path is applied as `SSL_CERT_FILE`, so all Python TLS callers
(urllib and aiohttp) pick it up. `OPENWEBUI_SSL_VERIFY=false` only relaxes the
urllib (JSON) routes; the Socket.IO tool loop still verifies, so prefer the CA
bundle for self-signed servers

## Install

Run straight from PyPI, no local checkout needed:

```bash
uvx openwebui-mcp --help
```

Or install it as a tool so the `openwebui-mcp` command is always available:

```bash
uv tool install openwebui-mcp
openwebui-mcp --help
```

Requires Python 3.11+

Cloning the repo is only needed for development: `uv sync` (creates
`.venv-docker`) then `uv run openwebui-mcp --help`

## Configure

Copy `.env.example` to `.env` or export the variables. Precedence is the first
defined env var in each alias list

| Setting | Env vars (first wins) | Default |
| --- | --- | --- |
| Open WebUI URL | `OPENWEBUI_BASE_URL`, `OPENWEBUI_URL`, `OWUI_URL` | required |
| Open WebUI token | `OPENWEBUI_API_KEY`, `OPENWEBUI_TOKEN`, `OWUI_API_KEY`, `OWUI_TOKEN` | required |
| Default model for `ask` | `OPENWEBUI_DEFAULT_MODEL`, `OWUI_DEFAULT_MODEL` | none |
| Enforce default model | `OPENWEBUI_ENFORCE_DEFAULT_MODEL`, `OWUI_ENFORCE_DEFAULT_MODEL` | `false` |
| `ask` tool description | `OPENWEBUI_ASK_DESCRIPTION`, `OWUI_ASK_DESCRIPTION` | built-in docstring |
| Server instructions | `OPENWEBUI_INSTRUCTIONS`, `OWUI_INSTRUCTIONS` | none |
| MCP bearer token | `OPENWEBUI_MCP_TOKEN`, `OWUI_MCP_TOKEN` | none |
| Transport | `OPENWEBUI_MCP_TRANSPORT` | `stdio` |
| Chat timeout ms | `OWUI_TIMEOUT_MS` | `120000` |
| Open WebUI CA bundle | `OPENWEBUI_CA_BUNDLE`, `OWUI_CA_BUNDLE` | none |
| Skip TLS verify | `OPENWEBUI_SSL_VERIFY`, `OWUI_SSL_VERIFY` | `true` |

## Run

```bash
uv run openwebui-mcp                      # stdio (default), for local MCP clients
uv run openwebui-mcp --transport sse      # SSE over HTTP
uv run openwebui-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## Development

```bash
uv run pytest          # 39 tests
uv run pyright src tests
```

## Layout

- `src/openwebui_mcp/server.py` - FastMCP server, the two tools, TLS apply, static token verifier
- `src/openwebui_mcp/config.py` - env-driven settings
- `src/openwebui_mcp/__main__.py` - CLI entry point
- `skills/owui-proactive-ask/` - agent skill that forces proactive use of `ask`
- `tests/` - config + server + TLS unit tests, incl. a protocol-level `call_tool` round trip
