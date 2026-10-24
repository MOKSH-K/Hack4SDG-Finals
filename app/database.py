from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL, TIMEZONE

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    future=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

_tz = ZoneInfo(TIMEZONE)


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    """Current local time, naive (the whole app stores naive local timestamps)."""
    return datetime.now(_tz).replace(tzinfo=None, microsecond=0)


def to_local_naive(dt: datetime) -> datetime:
    """Normalise client timestamps: aware -> converted to local; naive -> assumed local already."""
    return dt.astimezone(_tz).replace(tzinfo=None) if dt.tzinfo else dt


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
