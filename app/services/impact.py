"""SDG impact metrics: seeded nightly history plus tonight's live operations."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import CO2E_PER_KG_FOOD, KG_PER_SERVING
from app.models import DailyOperations, DarkStore, Order, ReliefBundle, SurplusBatch

WASTE_STATUSES = ("EXPIRED", "DISCARDED", "REJECTED")


def live_tonight(db: Session) -> dict:
    received = db.scalar(select(func.coalesce(func.sum(SurplusBatch.initial_qty), 0))
                         .where(SurplusBatch.status != "INCOMING")) or 0
    sold = db.scalar(select(func.coalesce(func.sum(SurplusBatch.sold_qty), 0))) or 0
    donated = db.scalar(select(func.coalesce(func.sum(ReliefBundle.servings), 0))
                        .where(ReliefBundle.status == "DISTRIBUTED")) or 0
    relief_expired = db.scalar(select(func.coalesce(func.sum(ReliefBundle.servings), 0))
                               .where(ReliefBundle.status == "EXPIRED")) or 0
    in_relief_pipeline = db.scalar(select(func.coalesce(func.sum(ReliefBundle.servings), 0))
                                   .where(ReliefBundle.status.in_(["FLAGGED", "CLAIMED", "IN_TRANSIT"]))) or 0
    # Wasted batches: whatever was neither sold nor handed to the relief pipeline.
    wasted_batches = 0
    for b in db.scalars(select(SurplusBatch).where(SurplusBatch.status.in_(WASTE_STATUSES))):
        routed = db.scalar(select(func.coalesce(func.sum(ReliefBundle.servings), 0))
                           .where(ReliefBundle.batch_id == b.id)) or 0
        wasted_batches += max(0, b.initial_qty - b.sold_qty - routed)
    return {
        "received": int(received), "sold": int(sold), "donated": int(donated),
        "relief_expired": int(relief_expired), "in_relief_pipeline": int(in_relief_pipeline),
        "wasted": int(wasted_batches + relief_expired),
        "orders": db.scalar(select(func.count(Order.id))) or 0,
        "people_fed": db.scalar(select(func.coalesce(func.sum(ReliefBundle.people_fed), 0))
                                .where(ReliefBundle.status == "DISTRIBUTED")) or 0,
    }


def impact_summary(db: Session) -> dict:
    h = db.execute(select(
        func.coalesce(func.sum(DailyOperations.servings_received), 0),
        func.coalesce(func.sum(DailyOperations.servings_sold), 0),
        func.coalesce(func.sum(DailyOperations.servings_donated), 0),
        func.coalesce(func.sum(DailyOperations.servings_wasted), 0),
        func.coalesce(func.sum(DailyOperations.relief_expired), 0),
        func.coalesce(func.sum(DailyOperations.workers_served), 0),
        func.coalesce(func.sum(DailyOperations.people_fed), 0),
    )).one()
    live = live_tonight(db)
    received = h[0] + live["received"]
    sold = h[1] + live["sold"]
    donated = h[2] + live["donated"]
    wasted = h[3] + live["wasted"]
    relief_expired = h[4] + live["relief_expired"]
    saved_servings = sold + donated
    saved_kg = saved_servings * KG_PER_SERVING
    return {
        "meals_saved_servings": int(saved_servings),
        "meals_saved_kg": round(saved_kg, 1),
        "co2e_prevented_kg": round(saved_kg * CO2E_PER_KG_FOOD, 1),
        "night_workers_served": int(h[5] + live["orders"]),
        "people_fed": int(h[6] + live["people_fed"]),
        "servings_donated": int(donated),
        "servings_wasted": int(wasted),
        "active_dark_stores": db.scalar(select(func.count(DarkStore.id))) or 0,
        # SDG 12.3: share of collected surplus kept out of landfill.
        "sdg12_index": round(100 * saved_servings / max(received, 1), 1),
        # SDG 2.1: share of food routed to NGOs that actually reached people.
        "sdg2_index": round(100 * donated / max(donated + relief_expired, 1), 1),
        "tonight": live,
        "assumptions": {"kg_per_serving": KG_PER_SERVING, "kg_co2e_per_kg_food": CO2E_PER_KG_FOOD},
    }
