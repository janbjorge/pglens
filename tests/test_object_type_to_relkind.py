import pytest

from pglens.adapters.asyncpg_adapter import object_type_to_relkind


@pytest.mark.parametrize(
    "input_type, expected",
    [
        ("table", "r"),
        ("view", "v"),
        ("matview", "m"),
        ("sequence", "S"),
        ("index", "i"),
        ("unknown", "r"),
        ("", "r"),
    ],
)
def test_object_type_to_relkind(input_type: str, expected: str) -> None:
    assert object_type_to_relkind(input_type) == expected
