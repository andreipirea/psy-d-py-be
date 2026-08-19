"""Idempotent migration: rename the status column to role in the users table.

Run with: python migrate_rename_status_to_role.py
Safe to run multiple times (existence checks guard both renames).
"""
from sqlalchemy import text
from app.database import engine

STATEMENTS = [
    # Rename the column only if it still exists (guards against re-runs).
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'status'
        ) THEN
            ALTER TABLE users RENAME COLUMN status TO role;
        END IF;
    END $$;
    """,
    # Rename the index created by the model's index=True on status. Guarded so
    # a fresh schema (role already indexed) is left untouched.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_indexes WHERE indexname = 'ix_users_status'
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_indexes WHERE indexname = 'ix_users_role'
        ) THEN
            ALTER INDEX ix_users_status RENAME TO ix_users_role;
        END IF;
    END $$;
    """,
]


def main() -> None:
    with engine.begin() as conn:
        for statement in STATEMENTS:
            conn.execute(text(statement))
            print(f"OK: {statement.splitlines()[2].strip()[:80]}")


if __name__ == "__main__":
    main()