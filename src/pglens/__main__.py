"""Entry point: python -m pglens / uv run pglens."""

import argparse

from pglens.adapters.mcp_adapter import configure, mcp
from pglens.core.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="pglens MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )
    parser.add_argument(
        "--schemas",
        help="Comma-separated list of allowed schemas (overrides PGLENS_SCHEMAS)",
    )
    args = parser.parse_args()

    overrides: dict[str, object] = {}
    if args.schemas is not None:
        overrides["schemas"] = args.schemas

    settings = Settings(**overrides)  # type: ignore[arg-type]
    configure(settings)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
