"""Fixtures for the database tests.

They need a real Postgres (SQLite would hide CHECK, timestamptz and advisory-lock
behaviour). If it is not running the tests SKIP with the fix, they do not fail.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config as AlembicConfig
from lead_responder import config
from lead_responder.db.models import Base
from lead_responder.db.session import make_engine

PROJECT_DIR = Path(__file__).resolve().parents[2]


# Builds the Alembic config the same way the CLI would, but pointed at the TEST database.
def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(PROJECT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_DIR / "alembic"))
    cfg.attributes["url"] = url  # env.py prefers this over config.DATABASE_URL
    return cfg


# Skip text for an unreachable database: the required prefix and fix hint, plus WHAT went wrong
# (exception class and first line of its message), so a wrong password is not mistaken for "not started".
def skip_message(url: str, error: Exception) -> str:
    first_line = (str(error).strip().splitlines() or [""])[0]
    return (
        f"Postgres not reachable at {url}: run 'docker compose up -d postgres' in track-a-dental/a1-lead-responder"
        f" ({type(error).__name__}: {first_line})"
    )


# One engine for the whole run. Skips if Postgres is down, otherwise rebuilds the
# schema from scratch through the real migrations (so the migrations are tested too).
@pytest.fixture(scope="session")
def engine():
    url = config.DATABASE_URL_TEST
    eng = make_engine(url)
    # A separate probe with a 2-second connect timeout, so a dead port skips fast instead of hanging.
    probe = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        pytest.skip(skip_message(url, e))
    finally:
        probe.dispose()
    # Drop everything (including alembic_version) so upgrade starts from zero.
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(alembic_config(url), "head")
    yield eng
    eng.dispose()


# A fresh, empty database for every test: truncate instead of rolling back, because
# later tests commit and use several sessions at once (advisory lock test).
@pytest.fixture
def session(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE leads, replies, staff_users, audit_log RESTART IDENTITY CASCADE"))
    with Session(engine, expire_on_commit=False) as s:
        yield s
