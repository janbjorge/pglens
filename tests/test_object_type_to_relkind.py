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
    ],
)
def test_object_type_to_relkind(input_type: str, expected: str) -> None:
    assert object_type_to_relkind(input_type) == expected


@pytest.mark.parametrize("input_type", ["unknown", "", "veiw", "TABLE"])
def test_object_type_to_relkind_rejects_unknown(input_type: str) -> None:
    with pytest.raises(ValueError, match="Unknown object_type"):
        object_type_to_relkind(input_type)
