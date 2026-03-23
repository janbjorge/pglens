"""Entry point: python -m pglens / uv run pglens."""

from pglens.adapters.mcp_adapter import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
