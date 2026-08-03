"""Open WebUI MCP server - ask models with tool support over MCP.

Public surface:

* ``config.Settings`` - server/connection configuration (env driven)
* ``server.create_server`` - build a ``FastMCP`` instance exposing the tools
* ``__main__.main`` - CLI entry point (``openwebui-mcp``)
"""

from openwebui_mcp.config import Settings
from openwebui_mcp.server import create_server

__version__ = "0.1.0"

__all__ = ["Settings", "__version__", "create_server"]
