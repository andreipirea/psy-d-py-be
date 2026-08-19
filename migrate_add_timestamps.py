"""Idempotent migration: add created_at, updated_at, deleted_at to the users table,
and make email uniqueness respect soft deletes.

Run with: python migrate_add_timestamps.py
Safe to run multiple times (ADD COLUMN IF NOT EXISTS / IF NOT EXISTS index).
"""
from sqlalchemy import text
from app.database import engine

STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE",
    # Drop the OLD unique index on email (created from unique=True) so a
    # soft-deleted email can be re-registered. Only drops the non-partial variant.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'users' AND indexname = 'ix_users_email'
              AND indexdef NOT LIKE '%deleted_at IS NULL%'
        ) THEN
            DROP INDEX ix_users_email;
        END IF;
    END $$;
    """,
    # Partial unique index: active emails are unique; deleted emails are reusable.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email) WHERE deleted_at IS NULL",
]


def main() -> None:
    with engine.begin() as conn:
        for statement in STATEMENTS:
            conn.execute(text(statement))
            print(f"OK: {statement.splitlines()[0][:80]}")


if __name__ == "__main__":
    main()