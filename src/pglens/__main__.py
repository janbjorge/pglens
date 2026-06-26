"""Entry point: python -m pglens / uv run pglens."""

import argparse
from importlib.metadata import version

from pglens.adapters.mcp_adapter import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="pglens MCP server")
    parser.add_argument(
        "--version",
        action="version",
        version=f"pglens {version('pglens')}",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
