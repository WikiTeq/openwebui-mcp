"""CLI entry point for the Open WebUI MCP server.

Run ``openwebui-mcp`` (stdio by default) or ``python -m openwebui_mcp``.
Configure via env vars or a ``.env`` file in the working directory.
"""

from __future__ import annotations

import argparse
import logging
import sys

from openwebui_mcp.config import Settings, load_dotenv
from openwebui_mcp.server import create_server

VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openwebui-mcp",
        description="Open WebUI MCP server: ask models with tool support over MCP",
    )
    parser.add_argument(
        "--transport",
        choices=VALID_TRANSPORTS,
        help="MCP transport (default: stdio). Overrides OPENWEBUI_MCP_TRANSPORT.",
    )
    parser.add_argument(
        "--name",
        help="Server name shown to clients (default: %(default)r)",
        default="openwebui",
    )
    parser.add_argument("--host", help="HTTP bind host for sse/streamable-http")
    parser.add_argument("--port", type=int, help="HTTP bind port for sse/streamable-http")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    overrides: dict[str, str] = {}
    if args.transport:
        overrides["transport"] = args.transport
    if args.name:
        overrides["name"] = args.name
    if args.host:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = str(args.port)

    settings = Settings.from_env(**overrides)
    server = create_server(settings)
    server.run(transport=settings.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
