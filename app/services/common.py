import math
import secrets

from sqlalchemy.orm import Session

from app.models import AuditEvent


def make_code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def lock_for_write(db: Session) -> None:
    """Open an IMMEDIATE transaction on SQLite so a read-check-write sequence is serialized.

    The conditional UPDATEs used for reservations are already the real guard against
    double-selling; this just makes contention fail fast-and-clean instead of mid-way.
    """
    raw = db.connection().connection.dbapi_connection
    if db.bind.dialect.name == "sqlite" and not raw.in_transaction:
        raw.execute("BEGIN IMMEDIATE")


def audit(db: Session, kind: str, message: str) -> None:
    db.add(AuditEvent(kind=kind, message=message))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)
