"""Module B (hub side): dark-store inventory, telemetry and manual controls."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import CHILL_MAX_C, HOT_HOLD_MIN_C
from app.database import get_db, now, to_local_naive
from app.models import AuditEvent, DarkStore, Order, OrderItem, ReliefBundle, SurplusBatch, TemperatureReading, User
from app.schemas import ExcursionIn, ReceiveIn, ShelfLifeIn, SweepIn
from app.security import require_roles
from app.services.common import audit, lock_for_write
from app.services.inventory import override_shelf_life, receive_batch
from app.services.routing import run_sweep
from app.services.serializers import batch_out, bundle_out, order_out
from app.services.telemetry import start_excursion

router = APIRouter(prefix="/api/v1/darkstore", tags=["darkstore"])

ACTIVE = ["INCOMING", "AVAILABLE", "SOLD_OUT", "RELIEF"]


def _store_id(user: User, db: Session, requested: int | None = None) -> int:
    if user.role == "admin":
        sid = requested or db.scalar(select(DarkStore.id).order_by(DarkStore.id))
    else:
        sid = user.dark_store_id
    if sid is None:
        raise HTTPException(400, "Your account isn't linked to a hub yet. Please contact the FoodForAll team.")
    return sid


def popularity(db: Session, dark_store_id: int | None = None) -> dict[str, int]:
    q = select(OrderItem.dish_name, func.sum(OrderItem.qty)).join(Order).group_by(OrderItem.dish_name)
    if dark_store_id:
        q = q.where(Order.dark_store_id == dark_store_id)
    return {name: int(n) for name, n in db.execute(q)}


@router.get("/overview")
def overview(store_id: int | None = None, db: Session = Depends(get_db),
             user: User = Depends(require_roles("darkstore"))):
    sid = _store_id(user, db, store_id)
    store = db.get(DarkStore, sid)
    t = now()
    pop = popularity(db, sid)
    batches = db.scalars(select(SurplusBatch).where(SurplusBatch.dark_store_id == sid)
                         .order_by(SurplusBatch.expires_at)).all()
    readings = db.scalars(select(TemperatureReading)
                          .where(TemperatureReading.dark_store_id == sid,
                                 TemperatureReading.recorded_at >= t - timedelta(minutes=60))
                          .order_by(TemperatureReading.recorded_at)).all()
    telemetry = {"HOT": [], "CHILLED": []}
    for r in readings[-160:]:
        telemetry[r.zone].append({"t": r.recorded_at.isoformat(), "c": r.temp_c, "ok": r.in_range})
    used = {"HOT": 0, "CHILLED": 0}
    for b in batches:
        if b.status == "AVAILABLE":
            used[b.storage] += b.available_qty
    bundles = db.scalars(select(ReliefBundle).where(ReliefBundle.dark_store_id == sid)
                         .order_by(ReliefBundle.flagged_at.desc())).all()
    orders = db.scalars(select(Order).where(Order.dark_store_id == sid).order_by(Order.created_at.desc()).limit(15)).all()
    events = db.scalars(select(AuditEvent).where(AuditEvent.dark_store_id == sid)
                        .order_by(AuditEvent.created_at.desc()).limit(25)).all()
    return {
        "store": {"id": store.id, "name": store.name, "area": store.area,
                  "hot_capacity": store.hot_capacity, "chilled_capacity": store.chilled_capacity},
        "stores": [{"id": s.id, "name": s.name} for s in db.scalars(select(DarkStore))] if user.role == "admin" else [],
        "server_time": t.isoformat(),
        "limits": {"HOT": {"min": HOT_HOLD_MIN_C}, "CHILLED": {"max": CHILL_MAX_C}},
        "capacity_used": used,
        "telemetry": telemetry,
        "batches": [batch_out(b, t, pop.get(b.dish_name, 0)) for b in batches if b.status in ACTIVE],
        "closed_batches": [batch_out(b, t) for b in batches if b.status not in ACTIVE][-20:],
        "popularity": sorted(({"dish_name": k, "orders": v} for k, v in pop.items()), key=lambda x: -x["orders"])[:8],
        "relief": [bundle_out(r, t) for r in bundles],
        "orders": [order_out(o) for o in orders],
        "events": [{"message": e.message, "at": e.created_at.isoformat()} for e in events],
    }


@router.post("/batches/{batch_id}/receive")
def receive(batch_id: int, body: ReceiveIn, db: Session = Depends(get_db),
            user: User = Depends(require_roles("darkstore"))):
    b = receive_batch(db, batch_id, _store_id(user, db, _batch_store(db, batch_id, user)), body.entry_temp_c)
    return batch_out(b, now())


@router.post("/batches/{batch_id}/shelf-life")
def shelf_life(batch_id: int, body: ShelfLifeIn, db: Session = Depends(get_db),
               user: User = Depends(require_roles("darkstore"))):
    b = override_shelf_life(db, batch_id, _store_id(user, db, _batch_store(db, batch_id, user)),
                            to_local_naive(body.expires_at), body.reason)
    return batch_out(b, now())


@router.post("/batches/{batch_id}/discard")
def discard(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("darkstore"))):
    sid = _store_id(user, db, _batch_store(db, batch_id, user))
    lock_for_write(db)
    b = db.get(SurplusBatch, batch_id)
    if b is None or b.dark_store_id != sid:
        raise HTTPException(404, "We couldn't find that food at this hub.")
    if b.status not in ("INCOMING", "AVAILABLE"):
        raise HTTPException(409, "This food is no longer on sale, so it can't be thrown away from here.")
    audit(db, "discard", f"Threw away {b.available_qty} × {b.dish_name}", sid)
    b.status, b.available_qty, b.status_note = "DISCARDED", 0, "Thrown away by the hub manager"
    db.commit()
    return batch_out(b, now())


@router.post("/sweep")
def sweep(body: SweepIn, db: Session = Depends(get_db), _: User = Depends(require_roles("darkstore"))):
    """Run the NGO routing sweep now; `force_cutoff` simulates reaching the 3:30 AM cut-off."""
    return run_sweep(db, force_cutoff=body.force_cutoff, telemetry=False)


@router.post("/telemetry/excursion")
def excursion(body: ExcursionIn, store_id: int | None = None, db: Session = Depends(get_db),
              user: User = Depends(require_roles("darkstore"))):
    """Safety drill (operations lead only in the UI): simulate a fridge / hot-cabinet failure for a few minutes."""
    sid = _store_id(user, db, store_id)
    start_excursion(sid, body.zone, body.minutes, now())
    run_sweep(db, telemetry=True)
    unit = "Hot cabinet" if body.zone == "HOT" else "Fridge"
    return {"ok": True, "message": f"Drill started: {unit.lower()} fault for {body.minutes} minutes"}


def _batch_store(db: Session, batch_id: int, user: User) -> int | None:
    if user.role != "admin":
        return None
    b = db.get(SurplusBatch, batch_id)
    return b.dark_store_id if b else None
