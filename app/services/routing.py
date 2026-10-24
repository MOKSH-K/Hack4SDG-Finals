"""The periodic sweep: expiry routing to NGOs, telemetry, and delivery progress."""
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import RELIEF_LEAD_HOURS
from app.database import now
from app.models import Order, ReliefBundle, SurplusBatch
from app.services.common import audit, lock_for_write
from app.services.relief import convert_to_relief
from app.services.safety import past_ngo_cutoff
from app.services.telemetry import record_readings

log = logging.getLogger("foodforall.sweep")

ORDER_STAGES = ["PLACED", "PACKED", "OUT_FOR_DELIVERY", "DELIVERED"]
# minutes after placement at which an order reaches each stage
ORDER_PROGRESS = [("PACKED", 2), ("OUT_FOR_DELIVERY", 5), ("DELIVERED", 15)]


def run_sweep(db: Session, *, force_cutoff: bool = False, telemetry: bool = True) -> dict:
    t = now()
    lock_for_write(db)
    counts = {"to_relief": 0, "expired_batches": 0, "expired_bundles": 0, "orders_advanced": 0}

    if telemetry:
        record_readings(db, t)

    cutoff = force_cutoff or past_ngo_cutoff(t)
    lead = timedelta(hours=RELIEF_LEAD_HOURS)
    for b in db.scalars(select(SurplusBatch).where(SurplusBatch.status.in_(["AVAILABLE", "INCOMING"]))).all():
        if b.expires_at <= t:
            b.status_note = "Expired before it could be sold or routed"
            b.status, b.available_qty = "EXPIRED", 0
            counts["expired_batches"] += 1
            audit(db, "expire", f"{b.code} expired ({b.dish_name})")
        elif b.status == "AVAILABLE" and (cutoff or b.expires_at - lead <= t):
            reason = "3:30 AM NGO cut-off" if cutoff else f"Within {RELIEF_LEAD_HOURS:g} h of safety expiry"
            if convert_to_relief(db, b, reason):
                counts["to_relief"] += 1

    for bundle in db.scalars(select(ReliefBundle).where(ReliefBundle.status.in_(["FLAGGED", "CLAIMED"]))).all():
        if bundle.batch.expires_at <= t:
            bundle.status = "EXPIRED"
            counts["expired_bundles"] += 1
            audit(db, "expire", f"{bundle.code} relief bundle expired unclaimed/uncollected")

    # Latest stage first, so an order that is long overdue jumps straight to where it belongs.
    for stage, minutes in reversed(ORDER_PROGRESS):
        earlier = ORDER_STAGES[:ORDER_STAGES.index(stage)]
        res = db.execute(update(Order)
                         .where(Order.status.in_(earlier), Order.created_at <= t - timedelta(minutes=minutes))
                         .values(status=stage, updated_at=t)
                         .execution_options(synchronize_session=False))
        counts["orders_advanced"] += res.rowcount

    db.commit()
    if counts["to_relief"] or counts["expired_batches"] or counts["expired_bundles"]:
        log.info("Sweep: %s", counts)
    return counts
