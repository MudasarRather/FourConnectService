from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import get_settings

settings = get_settings()

from sqlalchemy.pool import StaticPool

# Create SQLAlchemy engine - Use StaticPool for stability on experimental Python versions.
# pool_pre_ping: the remote Postgres drops idle/aborted connections ("Software caused
# connection abort"); StaticPool holds a single connection and won't auto-recover, so
# every query 500s until a restart. pre_ping tests the connection on checkout and
# transparently reconnects a dead one. (Compatible with StaticPool — unlike pool_size.)
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=StaticPool,
    pool_pre_ping=True,
)


# When the remote Postgres drops the single StaticPool connection between requests, the
# next checkout can surface as a psycopg2 ProgrammingError ("set_session cannot be used
# inside a transaction") or InterfaceError ("cursor already closed") rather than a
# recognised disconnect — so pre_ping doesn't auto-recover and the request 500s once
# before self-healing. These error signatures ARE stale-connection conditions; tagging
# them as disconnects lets SQLAlchemy invalidate the dead connection and pre_ping
# transparently reconnect on the next checkout instead of serving a hard error. Safe and
# additive — it only fires on these exact messages and never affects a live connection.
@event.listens_for(engine, "handle_error")
def _reclassify_stale_connection(ctx):  # noqa: ANN001
    msg = str(ctx.original_exception or "")
    if any(s in msg for s in (
        "set_session cannot be used inside a transaction",
        "cursor already closed",
        "connection already closed",
        "server closed the connection unexpectedly",
        "SSL connection has been closed unexpectedly",
    )):
        ctx.is_disconnect = True

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# StaticPool holds a SINGLE DBAPI connection, and a psycopg2 connection is NOT safe for
# concurrent use. FastAPI runs sync endpoints in a threadpool, so when a page fires
# several requests at once they race on that one connection and one of them dies with
# "cursor already closed" / "set_session cannot be used inside a transaction" — surfacing
# as an intermittent 500. Serialising the connection at the request boundary makes
# overlapping requests queue (they already share one connection — this just turns a crash
# into a brief wait) instead of colliding. This is the correct guard for StaticPool's
# single-connection model; it does NOT change the pool class (which must stay StaticPool
# until Python 3.14 SQLAlchemy stability is verified — see CLAUDE.md). FastAPI caches the
# get_db dependency per request, so the lock is acquired exactly once per request.
import threading

_conn_lock = threading.Lock()


def get_db():
    _conn_lock.acquire()
    try:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    finally:
        _conn_lock.release()
