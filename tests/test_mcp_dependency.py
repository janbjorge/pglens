"""Guard the `mcp` dependency range that pglens is actually compatible with.

pglens targets the mcp 2.x server API (`mcp.server.mcpserver.MCPServer`). The
1.x API it used before (`mcp.server.fastmcp.FastMCP`) is gone in 2.0, and the
two are not interchangeable:

- mcp < 2.0 has no `mcp.server.mcpserver` module, so importing the adapter
  raises ModuleNotFoundError and the server dies at startup (the client sees
  a bare -32000 with no tools).
- mcp 2.x `Context` is generic over two parameters (lifespan context, request);
  1.x also took a `ServerSession` parameter, so the alias in mcp_adapter.py
  raises TypeError at import under 1.x even if the module path resolves.

An unpinned `mcp` dependency lets either edge land on a fresh install or a
`uv tool upgrade`, which is how this broke before. The bounds are duplicated
here on purpose: the constants below and the requirement string in
pyproject.toml are asserted to agree, so widening the pin without revisiting
compatibility fails the suite.
"""

import importlib
import tomllib
from importlib.metadata import version
from pathlib import Path

# Inclusive lower bound / exclusive upper bound of the supported mcp range.
MIN_MCP = (2, 0)
MAX_MCP_EXCLUSIVE = (3, 0)

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _version_tuple(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _render(bound: tuple[int, ...]) -> str:
    """Format a version bound the way it appears in pyproject.toml (2.0 -> '2')."""
    trimmed = list(bound)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
    return ".".join(str(part) for part in trimmed)


def _mcp_requirement() -> str:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    deps = data["project"]["dependencies"]
    matches = [d for d in deps if d.split(">")[0].split("<")[0].split("=")[0].strip() == "mcp"]
    assert len(matches) == 1, f"expected exactly one mcp dependency, got {matches}"
    return matches[0].replace(" ", "")


class TestServerApiImportPath:
    def test_mcpserver_module_importable(self) -> None:
        """mcp.server.mcpserver exists (it does not in mcp 1.x)."""
        module = importlib.import_module("mcp.server.mcpserver")
        assert hasattr(module, "MCPServer")
        assert hasattr(module, "Context")

    def test_context_takes_two_type_parameters(self) -> None:
        """The 2.x Context signature the adapter's Ctx alias depends on."""
        mcpserver = importlib.import_module("mcp.server.mcpserver")
        mcpserver.Context[dict, object]

    def test_adapter_imports_and_builds_server(self) -> None:
        adapter = importlib.import_module("pglens.adapters.mcp_adapter")
        mcpserver = importlib.import_module("mcp.server.mcpserver")
        assert isinstance(adapter.mcp, mcpserver.MCPServer)

    def test_entry_point_imports(self) -> None:
        """`pglens` console script target imports without touching a database."""
        main = importlib.import_module("pglens.__main__")
        assert callable(main.main)

    def test_declared_transports_are_supported(self) -> None:
        """--transport choices in __main__ must all be accepted by MCPServer.run."""
        import inspect

        mcpserver = importlib.import_module("mcp.server.mcpserver")
        source = inspect.getsource(mcpserver.MCPServer.run)
        for transport in ("stdio", "streamable-http"):
            assert transport in source, f"MCPServer.run does not mention {transport!r}"


class TestMcpVersionRange:
    def test_installed_mcp_within_supported_range(self) -> None:
        installed = _version_tuple(version("mcp"))
        assert installed >= MIN_MCP, (
            f"mcp {version('mcp')} predates the 2.x server API "
            f"(minimum {_render(MIN_MCP)}); mcp.server.mcpserver does not exist there"
        )
        assert installed < MAX_MCP_EXCLUSIVE, (
            f"mcp {version('mcp')} is at or beyond {_render(MAX_MCP_EXCLUSIVE)}; "
            "re-verify the server API before widening the pin"
        )

    def test_pyproject_pin_matches_supported_range(self) -> None:
        requirement = _mcp_requirement()
        expected = f"mcp>={_render(MIN_MCP)},<{_render(MAX_MCP_EXCLUSIVE)}"
        assert requirement == expected, (
            f"pyproject.toml pins '{requirement}' but tests expect '{expected}'; "
            "update both together after verifying compatibility"
        )
