"""Engine and session factory: the only place that decides how we connect."""
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


# pool_pre_ping tests a pooled connection before use, so a restarted Postgres
# does not surface as one random failed request.
def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


# expire_on_commit=False keeps objects readable after commit (no hidden re-query,
# and API code can still return a lead it just saved).
def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
