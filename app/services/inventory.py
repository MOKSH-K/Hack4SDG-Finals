"""Dark-store inventory: dispatch, receiving, atomic checkout, shelf-life overrides."""
from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, SurplusBatch, User
from app.services.common import audit, lock_for_write, make_code
from app.services.safety import compute_expiry, max_allowed_expiry, store_is_open, temp_in_range
from app.database import now

MAX_QTY_PER_LINE = 10


def dispatch_batch(db: Session, *, restaurant_id: int, dark_store_id: int, dish_name: str, category: str,
                   diet: str, allergens: str, storage: str, cooked_at: datetime, shelf_life_hours: float,
                   quantity: int, price: float) -> SurplusBatch:
    t = now()
    if cooked_at > t:
        raise HTTPException(422, "Cooked time cannot be in the future")
    expires_at, effective = compute_expiry(cooked_at, shelf_life_hours, storage)
    if expires_at <= t:
        raise HTTPException(422, "This food is already past its safe shelf life - it cannot be dispatched")
    batch = SurplusBatch(
        code=make_code("B"), restaurant_id=restaurant_id, dark_store_id=dark_store_id,
        dish_name=dish_name, category=category, diet=diet, allergens=allergens, storage=storage,
        price=price, cooked_at=cooked_at, shelf_life_hours=effective, expires_at=expires_at,
        dispatched_at=t, initial_qty=quantity, available_qty=quantity, status="INCOMING",
        status_note="" if effective == shelf_life_hours else
        f"Shelf life capped at {effective:g} h for {storage.lower()} storage",
    )
    db.add(batch)
    db.flush()
    audit(db, "dispatch", f"{batch.code}: {quantity} x {dish_name} dispatched to dark store #{dark_store_id}")
    return batch


def receive_batch(db: Session, batch_id: int, dark_store_id: int, entry_temp_c: float) -> SurplusBatch:
    lock_for_write(db)
    batch = db.get(SurplusBatch, batch_id)
    if batch is None or batch.dark_store_id != dark_store_id:
        raise HTTPException(404, "Batch not found at this dark store")
    if batch.status != "INCOMING":
        raise HTTPException(409, f"Batch is already {batch.status}")
    t = now()
    batch.entry_temp_c = entry_temp_c
    batch.received_at = t
    if not temp_in_range(batch.storage, entry_temp_c):
        batch.status, batch.available_qty = "REJECTED", 0
        batch.status_note = f"Rejected at intake: {entry_temp_c} °C outside {batch.storage} range"
    elif batch.expires_at <= t:
        batch.status, batch.available_qty = "EXPIRED", 0
        batch.status_note = "Arrived after shelf-life expiry"
    else:
        batch.status = "AVAILABLE"
    audit(db, "receive", f"{batch.code} received at {entry_temp_c} °C -> {batch.status}")
    db.commit()
    return batch


def place_order(db: Session, user: User, dark_store_id: int, items: list[tuple[int, int]],
                delivery_address: str) -> Order:
    """Reserve every line atomically or nothing at all.

    Each line is a conditional UPDATE (`available_qty >= qty AND status = 'AVAILABLE' AND not expired`);
    if any line affects 0 rows the whole transaction is rolled back, so two workers racing for the
    last portion can never both get it.
    """
    t = now()
    if not store_is_open(t):
        raise HTTPException(409, "The late-night store is closed (open 11:00 PM - 5:00 AM)")
    merged: dict[int, int] = defaultdict(int)
    for batch_id, qty in items:
        merged[batch_id] += qty
    if not merged:
        raise HTTPException(422, "Cart is empty")
    if any(q < 1 or q > MAX_QTY_PER_LINE for q in merged.values()):
        raise HTTPException(422, f"Each dish must be between 1 and {MAX_QTY_PER_LINE} portions")

    try:
        lock_for_write(db)
        batches = {b.id: b for b in db.scalars(select(SurplusBatch).where(SurplusBatch.id.in_(merged)))}
        order = Order(code=make_code("ORD"), user_id=user.id, dark_store_id=dark_store_id,
                      delivery_address=delivery_address, total=0.0, status="PLACED", created_at=t, updated_at=t)
        total = 0.0
        for batch_id, qty in merged.items():
            batch = batches.get(batch_id)
            if batch is None or batch.dark_store_id != dark_store_id:
                raise HTTPException(404, f"Item #{batch_id} is not sold by this dark store")
            res = db.execute(
                update(SurplusBatch)
                .where(SurplusBatch.id == batch_id, SurplusBatch.status == "AVAILABLE",
                       SurplusBatch.available_qty >= qty, SurplusBatch.expires_at > t)
                .values(available_qty=SurplusBatch.available_qty - qty,
                        sold_qty=SurplusBatch.sold_qty + qty)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                raise HTTPException(409, f"Sorry - '{batch.dish_name}' just sold out or is no longer on sale")
            db.execute(update(SurplusBatch)
                       .where(SurplusBatch.id == batch_id, SurplusBatch.available_qty == 0,
                              SurplusBatch.status == "AVAILABLE")
                       .values(status="SOLD_OUT").execution_options(synchronize_session=False))
            order.items.append(OrderItem(batch_id=batch_id, dish_name=batch.dish_name, qty=qty,
                                         unit_price=batch.price))
            total += qty * batch.price
        order.total = round(total, 2)
        db.add(order)
        audit(db, "order", f"{order.code}: {sum(merged.values())} items for {user.email}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def override_shelf_life(db: Session, batch_id: int, dark_store_id: int, new_expires_at: datetime,
                        reason: str) -> SurplusBatch:
    lock_for_write(db)
    batch = db.get(SurplusBatch, batch_id)
    if batch is None or batch.dark_store_id != dark_store_id:
        raise HTTPException(404, "Batch not found at this dark store")
    if batch.status not in ("INCOMING", "AVAILABLE"):
        raise HTTPException(409, f"Cannot change shelf life of a {batch.status} batch")
    cap = max_allowed_expiry(batch)
    if new_expires_at > cap:
        raise HTTPException(422, f"Cannot extend beyond the {batch.storage} safety cap ({cap:%d %b %H:%M})")
    if batch.temp_breach and new_expires_at > batch.expires_at:
        raise HTTPException(422, "Batch had a temperature excursion - shelf life can only be shortened")
    if new_expires_at <= now():
        raise HTTPException(422, "New expiry must be in the future (use discard instead)")
    old = batch.expires_at
    batch.expires_at = new_expires_at
    batch.shelf_life_overridden = True
    batch.status_note = f"Manual override: {reason}"[:200]
    audit(db, "override", f"{batch.code} expiry {old:%H:%M} -> {new_expires_at:%H:%M} ({reason})")
    db.commit()
    return batch
