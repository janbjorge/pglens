"""Guard the packaging metadata that CI tagging depends on.

The version is derived from the git tag by hatch-vcs, so `v1.2.0` builds
pglens 1.2.0 with no second place to bump. Two things can quietly break that:
someone re-adds a static `version = "..."` to pyproject.toml (which then drifts
from the tag), or the build backend loses hatch-vcs and stops resolving a
version at all.
"""

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# Permissive PEP 440: release segment, optional pre/post/dev, optional +local.
PEP440 = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?(\+[a-zA-Z0-9.]+)?$")


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _table(parent: dict[str, object], key: str) -> dict[str, object]:
    """Fetch a nested TOML table, keeping the value type known to the type checker."""
    value = parent[key]
    assert isinstance(value, dict), f"expected [{key}] to be a table, got {type(value).__name__}"
    return {str(k): v for k, v in value.items()}


class TestDynamicVersion:
    def test_version_is_declared_dynamic(self) -> None:
        project = _table(_pyproject(), "project")
        dynamic = project["dynamic"]
        assert isinstance(dynamic, list)
        assert "version" in dynamic, (
            "project.dynamic must list 'version' so hatch-vcs supplies it from the git tag"
        )

    def test_no_static_version_pinned(self) -> None:
        project = _table(_pyproject(), "project")
        assert "version" not in project, (
            "a static project.version shadows the git tag; remove it and let hatch-vcs derive it"
        )

    def test_hatch_vcs_is_wired_up(self) -> None:
        data = _pyproject()
        requires = _table(data, "build-system")["requires"]
        assert isinstance(requires, list)
        assert "hatch-vcs" in requires
        hatch_version = _table(_table(_table(data, "tool"), "hatch"), "version")
        assert hatch_version["source"] == "vcs"

    def test_installed_version_resolves(self) -> None:
        """`pglens --version` reads this; it must resolve and be PEP 440 valid."""
        installed = version("pglens")
        assert PEP440.match(installed), f"{installed!r} is not a valid PEP 440 version"
