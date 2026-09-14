"""Environment-driven settings, parsed and validated by pydantic-settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Timeouts bounding what pglens can do to a server.

    Every value is a whole number of seconds, and ``0`` disables the
    corresponding timeout, matching Postgres semantics for these GUCs.
    """

    model_config = SettingsConfigDict(env_prefix="PGLENS_", extra="ignore")

    statement_timeout: int = Field(
        default=60,
        ge=0,
        description=(
            "How long any single query may run, including EXPLAIN ANALYZE "
            "and an unqualified count(*)."
        ),
    )
    lock_timeout: int = Field(
        default=5,
        ge=0,
        description=(
            "How long a statement waits for a lock. Even a pure SELECT takes "
            "ACCESS SHARE, so without this pglens can sit in a lock queue for "
            "the whole statement timeout instead of failing fast and getting "
            "out of the way."
        ),
    )
    idle_tx_timeout: int = Field(
        default=30,
        ge=0,
        description=(
            "How long a session may sit idle inside an open transaction. The "
            "statement timeout does not cover this: a stalled client would "
            "keep holding ACCESS SHARE on every table it touched (blocking "
            "any pending ALTER/DROP/REINDEX, and everything queued behind it) "
            "and would pin the xmin horizon so autovacuum cannot reclaim dead "
            "tuples."
        ),
    )

    def server_settings(self) -> dict[str, str]:
        """GUCs applied to every connection in the pool."""
        return {
            "application_name": "pglens",
            # Belt and braces: methods also use explicit readonly transactions,
            # but this makes every connection read-only by default.
            "default_transaction_read_only": "on",
            "statement_timeout": str(self.statement_timeout * 1000),
            "lock_timeout": str(self.lock_timeout * 1000),
            "idle_in_transaction_session_timeout": str(self.idle_tx_timeout * 1000),
        }

    @property
    def command_timeout(self) -> float | None:
        """Client-side deadline for a single asyncpg command, or None if disabled.

        Server-side statement_timeout cannot fire if the connection is
        blackholed (dropped NAT entry, unreachable host), which would leak a
        pool slot for good. Sits a few seconds above statement_timeout so the
        server normally wins the race and returns a proper Postgres error
        instead.
        """
        if self.statement_timeout == 0:
            return None
        return self.statement_timeout + 5.0
