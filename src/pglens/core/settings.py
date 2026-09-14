"""Environment-driven settings, parsed and validated by pydantic-settings.

``Settings`` is instantiated exactly once, in ``app_lifespan``, and passed down
from there. Nothing else reads the environment.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_DB = "default"

StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


class DatabaseAliases(BaseModel):
    """Which databases to open a pool for, and which one tools target by default."""

    model_config = ConfigDict(frozen=True)

    names: list[str] = Field(description="Every configured alias, in declaration order.")
    default: str = Field(description="Alias used when a tool omits `database=`.")


class PoolOptions(BaseModel):
    """Everything the asyncpg pool needs to be safe against a production server."""

    model_config = ConfigDict(frozen=True)

    server_settings: dict[str, str] = Field(
        description="GUCs applied to every connection in the pool."
    )
    command_timeout: float | None = Field(
        description=(
            "Client-side deadline for a single asyncpg command, or None if disabled. "
            "Server-side statement_timeout cannot fire if the connection is blackholed "
            "(dropped NAT entry, unreachable host), which would leak a pool slot for good. "
            "Sits a few seconds above statement_timeout so the server normally wins the "
            "race and returns a proper Postgres error instead."
        )
    )


class Settings(BaseSettings):
    """All pglens configuration: which databases to open, and the timeouts
    bounding what pglens can do to them.

    Every timeout is a whole number of seconds, and ``0`` disables the
    corresponding timeout, matching Postgres semantics for these GUCs.

    Database selection uses libpq env vars only (no connection strings).
    ``PGHOST``, ``PGUSER``, ``PGPASSWORD``, ``PGSSLMODE`` (etc.) supply host and
    credentials shared by every pool, and are read by asyncpg rather than here.
    """

    model_config = SettingsConfigDict(env_prefix="PGLENS_", extra="ignore")

    primary_database: StrippedStr = Field(
        default="",
        validation_alias="PGDATABASE",
        description=(
            "Primary dbname, and the default alias when a tool omits `database=`. "
            "Empty means unset, leaving the dbname to libpq's own default."
        ),
    )
    # NoDecode opts out of pydantic-settings' JSON decoding for complex types,
    # handing the raw env string to the validator below: the documented format
    # is comma-separated, not JSON.
    extra_databases: Annotated[list[StrippedStr], NoDecode] = Field(
        default_factory=list,
        validation_alias="PGLENS_DATABASES",
        description="Further dbnames on the same host, comma-separated, each with its own pool.",
    )
    statement_timeout: NonNegativeInt = Field(
        default=60,
        description=(
            "Seconds any single query may run, including EXPLAIN ANALYZE "
            "and an unqualified count(*)."
        ),
    )
    lock_timeout: NonNegativeInt = Field(
        default=5,
        description=(
            "Seconds a statement waits for a lock. Even a pure SELECT takes "
            "ACCESS SHARE, so without this pglens can sit in a lock queue for "
            "the whole statement timeout instead of failing fast and getting "
            "out of the way."
        ),
    )
    idle_tx_timeout: NonNegativeInt = Field(
        default=30,
        description=(
            "Seconds a session may sit idle inside an open transaction. The "
            "statement timeout does not cover this: a stalled client would "
            "keep holding ACCESS SHARE on every table it touched (blocking "
            "any pending ALTER/DROP/REINDEX, and everything queued behind it) "
            "and would pin the xmin horizon so autovacuum cannot reclaim dead "
            "tuples."
        ),
    )

    @field_validator("extra_databases", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: str | list[str]) -> list[str]:
        return value.split(",") if isinstance(value, str) else value

    @computed_field
    @property
    def database_aliases(self) -> DatabaseAliases:
        """Resolve the configured aliases and the default alias.

        If neither ``PGDATABASE`` nor ``PGLENS_DATABASES`` is set, a single
        ``default`` alias is configured that relies entirely on libpq's own
        default behavior (e.g. dbname = ``PGUSER``).

        Names are used verbatim as Postgres dbnames, which are case-sensitive.
        """
        primary = self.primary_database or None

        extras: list[str] = []
        for name in self.extra_databases:
            if name and name not in extras and name != primary:
                extras.append(name)

        if primary is not None:
            return DatabaseAliases(names=[primary, *extras], default=primary)
        if extras:
            return DatabaseAliases(names=extras, default=extras[0])
        return DatabaseAliases(names=[DEFAULT_DB], default=DEFAULT_DB)

    @computed_field
    @property
    def pool_options(self) -> PoolOptions:
        return PoolOptions(
            server_settings={
                "application_name": "pglens",
                # Belt and braces: methods also use explicit readonly
                # transactions, but this makes every connection read-only.
                "default_transaction_read_only": "on",
                "statement_timeout": str(self.statement_timeout * 1000),
                "lock_timeout": str(self.lock_timeout * 1000),
                "idle_in_transaction_session_timeout": str(self.idle_tx_timeout * 1000),
            },
            command_timeout=None if self.statement_timeout == 0 else self.statement_timeout + 5.0,
        )
