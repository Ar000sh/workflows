"""Alembic environment: connects to config.DATABASE_URL and compares against Base.metadata."""
from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Before importing config, so a DATABASE_URL in .env applies to alembic exactly as it does to the
# app and scripts/run_retention.py. load_dotenv() searches upward from THIS file, not from the cwd,
# so it behaves the same wherever alembic is started.
load_dotenv()

from lead_responder import config  # noqa: E402
from lead_responder.db.models import Base

target_metadata = Base.metadata


# Tests pass their own URL through cfg.attributes; the CLI falls back to the dev database.
def _url() -> str:
    return context.config.attributes.get("url") or config.DATABASE_URL


# Offline mode only prints SQL; we never use it, but Alembic requires the branch.
def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# Online mode: open a connection and apply the pending revisions in one transaction.
def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
