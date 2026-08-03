# openwebui-mcp

Open WebUI MCP server built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
and the [openwebui-sdk](https://github.com/vedmaka/openwebui-sdk) library

Lets any MCP client (Claude Desktop, Cursor, agents) ask Open WebUI models
through the full tool-calling loop, not just plain chat

## Tools

Exactly two tools are exposed

### `ask`

Send a prompt to a model with Open WebUI tool support

- `model` (required) - the model id to ask, e.g. one returned by `list_models`
- `prompt` (required) - the user message
- `system` (optional) - a system prompt leading the conversation
- `temperature` (optional) - sampling temperature override
- `use_tools` (optional, default true) - enable the tools attached to the model

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

   Note: MCP OAuth discovery wants an HTTPS endpoint, so http deployments
   should rely on transport security or a private network instead

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

```bash
uv sync           # creates .venv-docker, installs deps incl. openwebui-sdk from git
uv run openwebui-mcp --help
```

Requires Python 3.11+

## Configure

Copy `.env.example` to `.env` or export the variables. Precedence is the first
defined env var in each alias list

| Setting | Env vars (first wins) | Default |
| --- | --- | --- |
| Open WebUI URL | `OPENWEBUI_BASE_URL`, `OPENWEBUI_URL`, `OWUI_URL` | required |
| Open WebUI token | `OPENWEBUI_API_KEY`, `OPENWEBUI_TOKEN`, `OWUI_API_KEY`, `OWUI_TOKEN` | required |
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

### Client config example (Claude Desktop)

```json
{
  "mcpServers": {
    "openwebui": {
      "command": "/path/to/openwebui-mcp/.venv-docker/bin/openwebui-mcp",
      "env": {
        "OPENWEBUI_BASE_URL": "http://localhost:8080",
        "OPENWEBUI_API_KEY": "sk-..."
      }
    }
  }
}
```

### Remote HTTP clients (codex, rmcp, ...)

For `--transport streamable-http` the MCP endpoint lives at
`/mcp`, so point the client URL at the full path:

```text
http://<host>:<port>/mcp
```

A bare `http://<host>:<port>` returns 404 on initialize. With `--transport sse`
the endpoint is `/sse` instead

## Development

```bash
uv run pytest          # 22 tests
uv run pyright src tests
```

Note: the venv is `.venv-docker` (not the host-occupied `.venv`). Typechecking
is configured via `pyrightconfig.json` for that path

## Layout

- `src/openwebui_mcp/server.py` - FastMCP server, the two tools, TLS apply, static token verifier
- `src/openwebui_mcp/config.py` - env-driven settings
- `src/openwebui_mcp/__main__.py` - CLI entry point
- `tests/` - config + server + TLS unit tests, incl. a protocol-level `call_tool` round trip
