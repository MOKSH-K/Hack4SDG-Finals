"""Food-safety rules: shelf-life caps, safety clocks, HACCP verification tags, operating hours."""
import hashlib
from datetime import datetime, time, timedelta

from app.config import (
    CHILL_MAX_C, HOT_HOLD_MIN_C, MAX_SHELF_LIFE_HOURS, NGO_CUTOFF, OPEN_24X7, RELIEF_LEAD_HOURS,
    STORE_CLOSE, STORE_OPEN,
)
from app.models import SurplusBatch


def temp_in_range(storage: str, temp_c: float) -> bool:
    return temp_c >= HOT_HOLD_MIN_C if storage == "HOT" else temp_c <= CHILL_MAX_C


def compute_expiry(cooked_at: datetime, shelf_life_hours: float, storage: str) -> tuple[datetime, float]:
    """Expiry is the declared shelf life, capped by the storage-class maximum."""
    effective = min(shelf_life_hours, MAX_SHELF_LIFE_HOURS[storage])
    return cooked_at + timedelta(hours=effective), effective


def max_allowed_expiry(batch: SurplusBatch) -> datetime:
    return batch.cooked_at + timedelta(hours=MAX_SHELF_LIFE_HOURS[batch.storage])


def safety_clock(batch: SurplusBatch, at: datetime) -> dict:
    relief_at = batch.expires_at - timedelta(hours=RELIEF_LEAD_HOURS)
    secs_left = int((batch.expires_at - at).total_seconds())
    if secs_left <= 0:
        phase = "EXPIRED"
    elif at >= relief_at:
        phase = "RELIEF_WINDOW"
    else:
        phase = "SAFE"
    return {
        "expires_at": batch.expires_at.isoformat(),
        "relief_at": relief_at.isoformat(),
        "seconds_left": max(0, secs_left),
        "seconds_to_relief": max(0, int((relief_at - at).total_seconds())),
        "phase": phase,
    }


def haccp_tag(batch: SurplusBatch, at: datetime) -> dict:
    checks = [
        {"check": "Allergens listed", "ok": batch.allergens is not None},
        {"check": "Kept hot (63 °C or above)" if batch.storage == "HOT" else "Kept chilled (5 °C or below)",
         "ok": batch.storage in MAX_SHELF_LIFE_HOURS},
        {"check": "Right temperature on arrival",
         "ok": batch.entry_temp_c is not None and temp_in_range(batch.storage, batch.entry_temp_c)},
        # Critical excursions discard the batch outright (see telemetry), so a live batch can only
        # have had a minor one - still safe, with its use-by already pulled in.
        {"check": "Brief temperature problem: must be eaten sooner" if batch.temp_breach
                  else "No temperature problems", "ok": batch.status != "DISCARDED"},
        {"check": "Still within its safe time", "ok": at < batch.expires_at},
    ]
    digest = hashlib.sha256(
        f"{batch.code}|{batch.cooked_at.isoformat()}|{batch.storage}|{batch.entry_temp_c}|{batch.expires_at.isoformat()}"
        .encode()).hexdigest()[:10].upper()
    return {"tag": f"HACCP-{digest}", "verified": all(c["ok"] for c in checks), "checks": checks}


def _in_window(t: time, start: time, end: time) -> bool:
    return start <= t < end if start < end else (t >= start or t < end)


def store_is_open(at: datetime) -> bool:
    return OPEN_24X7 or _in_window(at.time(), STORE_OPEN, STORE_CLOSE)


def past_ngo_cutoff(at: datetime) -> bool:
    """True once the night's sale window is over (03:30 onwards).

    When open 24x7 only the 03:30-05:00 slice counts, so daytime sessions aren't emptied instantly.
    """
    end = STORE_CLOSE if OPEN_24X7 else STORE_OPEN
    return _in_window(at.time(), NGO_CUTOFF, end)
