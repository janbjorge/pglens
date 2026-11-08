"""Read-only PostgreSQL introspection for AI agents, served over MCP."""

# Importing the tools module registers the nine tools on the server.
from pglens.tools import mcp

__all__ = ["mcp"]
