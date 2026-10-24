"""Simulated dark-store temperature sensors and excursion handling."""
import random
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import BREACH_GRACE_HOURS, CRITICAL_CHILL_C, CRITICAL_HOT_C
from app.models import DarkStore, ReliefBundle, SurplusBatch, TemperatureReading
from app.services.common import audit
from app.services.safety import temp_in_range

NORMAL = {"HOT": (68.0, 1.2), "CHILLED": (3.2, 0.5)}
EXCURSION = {"HOT": (58.5, 1.0), "CHILLED": (7.6, 0.4)}

# (dark_store_id, zone) -> excursion active until
_excursions: dict[tuple[int, str], datetime] = {}


def start_excursion(dark_store_id: int, zone: str, minutes: int, at: datetime) -> None:
    _excursions[(dark_store_id, zone)] = at + timedelta(minutes=minutes)


def record_readings(db: Session, at: datetime) -> list[TemperatureReading]:
    readings = []
    for store in db.scalars(select(DarkStore)):
        for zone in ("HOT", "CHILLED"):
            active = _excursions.get((store.id, zone), datetime.min) > at
            mu, sd = (EXCURSION if active else NORMAL)[zone]
            temp = round(random.gauss(mu, sd), 1)
            r = TemperatureReading(dark_store_id=store.id, zone=zone, temp_c=temp, recorded_at=at,
                                   in_range=temp_in_range(zone, temp))
            db.add(r)
            readings.append(r)
            if not r.in_range:
                _apply_excursion(db, store.id, zone, temp, at)
    db.execute(delete(TemperatureReading).where(TemperatureReading.recorded_at < at - timedelta(hours=24)))
    return readings


def _apply_excursion(db: Session, dark_store_id: int, zone: str, temp: float, at: datetime) -> None:
    critical = temp < CRITICAL_HOT_C if zone == "HOT" else temp > CRITICAL_CHILL_C
    batches = db.scalars(select(SurplusBatch).where(
        SurplusBatch.dark_store_id == dark_store_id, SurplusBatch.storage == zone,
        SurplusBatch.status.in_(["AVAILABLE", "RELIEF"]))).all()
    unit = "Hot cabinet" if zone == "HOT" else "Fridge"
    for b in batches:
        if critical:
            b.status, b.available_qty = "DISCARDED", 0
            b.status_note = f"Thrown away: {unit.lower()} reached {temp} °C, which is unsafe"
            bundle = db.scalar(select(ReliefBundle).where(ReliefBundle.batch_id == b.id,
                                                          ReliefBundle.status.in_(["FLAGGED", "CLAIMED"])))
            if bundle:
                bundle.status = "EXPIRED"
            audit(db, "discard", f"Threw away {b.dish_name}: {unit.lower()} reached {temp} °C", dark_store_id)
        elif not b.temp_breach:
            b.temp_breach = True
            b.expires_at = min(b.expires_at, at + timedelta(hours=BREACH_GRACE_HOURS))
            problem = "got too warm" if zone == "CHILLED" else "cooled down"
            b.status_note = f"{unit} {problem} ({temp} °C), so it must be eaten sooner"
            audit(db, "excursion", f"{unit} at {temp} °C: {b.dish_name} must now be eaten by {b.expires_at:%H:%M}",
                  dark_store_id)
