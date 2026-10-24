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
        raise HTTPException(422, "Cooked time can't be in the future.")
    expires_at, effective = compute_expiry(cooked_at, shelf_life_hours, storage)
    if expires_at <= t:
        raise HTTPException(422, "This food is already past its safe time, so it can't be sent.")
    batch = SurplusBatch(
        code=make_code("B"), restaurant_id=restaurant_id, dark_store_id=dark_store_id,
        dish_name=dish_name, category=category, diet=diet, allergens=allergens, storage=storage,
        price=price, cooked_at=cooked_at, shelf_life_hours=effective, expires_at=expires_at,
        dispatched_at=t, initial_qty=quantity, available_qty=quantity, status="INCOMING",
        status_note="" if effective == shelf_life_hours else
        f"Safe time limited to {effective:g} hours for {'food kept hot' if storage == 'HOT' else 'chilled food'}",
    )
    db.add(batch)
    db.flush()
    audit(db, "dispatch", f"{batch.restaurant.name} sent {quantity} × {dish_name}", dark_store_id)
    return batch


def receive_batch(db: Session, batch_id: int, dark_store_id: int, entry_temp_c: float) -> SurplusBatch:
    lock_for_write(db)
    batch = db.get(SurplusBatch, batch_id)
    if batch is None or batch.dark_store_id != dark_store_id:
        raise HTTPException(404, "We couldn't find that delivery at this hub.")
    if batch.status != "INCOMING":
        raise HTTPException(409, "This delivery has already been checked in.")
    t = now()
    batch.entry_temp_c = entry_temp_c
    batch.received_at = t
    if not temp_in_range(batch.storage, entry_temp_c):
        batch.status, batch.available_qty = "REJECTED", 0
        need = "at least 63 °C" if batch.storage == "HOT" else "5 °C or colder"
        batch.status_note = f"Turned away: arrived at {entry_temp_c} °C (needs {need})"
        audit(db, "receive", f"Turned away {batch.dish_name} from {batch.restaurant.name}: {entry_temp_c} °C", dark_store_id)
    elif batch.expires_at <= t:
        batch.status, batch.available_qty = "EXPIRED", 0
        batch.status_note = "Arrived after its safe time ended"
        audit(db, "receive", f"{batch.dish_name} from {batch.restaurant.name} arrived too late to be safe", dark_store_id)
    else:
        batch.status = "AVAILABLE"
        audit(db, "receive", f"Checked in {batch.initial_qty} × {batch.dish_name} at {entry_temp_c} °C", dark_store_id)
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
        raise HTTPException(409, "The night store is closed right now. It's open 11 PM – 5 AM.")
    merged: dict[int, int] = defaultdict(int)
    for batch_id, qty in items:
        merged[batch_id] += qty
    if not merged:
        raise HTTPException(422, "Your cart is empty.")
    if any(q < 1 or q > MAX_QTY_PER_LINE for q in merged.values()):
        raise HTTPException(422, f"You can order 1 to {MAX_QTY_PER_LINE} portions of each dish.")

    try:
        lock_for_write(db)
        batches = {b.id: b for b in db.scalars(select(SurplusBatch).where(SurplusBatch.id.in_(merged)))}
        order = Order(code=make_code("ORD"), user_id=user.id, dark_store_id=dark_store_id,
                      delivery_address=delivery_address, total=0.0, status="PLACED", created_at=t, updated_at=t)
        total = 0.0
        for batch_id, qty in merged.items():
            batch = batches.get(batch_id)
            if batch is None or batch.dark_store_id != dark_store_id:
                raise HTTPException(404, "A dish in your cart isn't available from this hub any more. Please refresh the menu.")
            res = db.execute(
                update(SurplusBatch)
                .where(SurplusBatch.id == batch_id, SurplusBatch.status == "AVAILABLE",
                       SurplusBatch.available_qty >= qty, SurplusBatch.expires_at > t)
                .values(available_qty=SurplusBatch.available_qty - qty,
                        sold_qty=SurplusBatch.sold_qty + qty)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                raise HTTPException(409, f"Sorry, {batch.dish_name} just sold out.")
            db.execute(update(SurplusBatch)
                       .where(SurplusBatch.id == batch_id, SurplusBatch.available_qty == 0,
                              SurplusBatch.status == "AVAILABLE")
                       .values(status="SOLD_OUT").execution_options(synchronize_session=False))
            order.items.append(OrderItem(batch_id=batch_id, dish_name=batch.dish_name, qty=qty,
                                         unit_price=batch.price))
            total += qty * batch.price
        order.total = round(total, 2)
        db.add(order)
        n = sum(merged.values())
        audit(db, "order", f"New order: {n} portion{'s' if n != 1 else ''} for delivery", dark_store_id)
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
        raise HTTPException(404, "We couldn't find that food at this hub.")
    if batch.status not in ("INCOMING", "AVAILABLE"):
        raise HTTPException(409, "This food is no longer on sale, so its safe time can't be changed.")
    cap = max_allowed_expiry(batch)
    if new_expires_at > cap:
        kind = "food kept hot" if batch.storage == "HOT" else "chilled food"
        raise HTTPException(422, f"That's too late: {kind} must be eaten by {cap:%I:%M %p on %d %b}.")
    if batch.temp_breach and new_expires_at > batch.expires_at:
        raise HTTPException(422, "This food had a temperature problem earlier, so its safe time can only be shortened.")
    if new_expires_at <= now():
        raise HTTPException(422, "Pick a time in the future. To remove the food now, use Discard.")
    old = batch.expires_at
    batch.expires_at = new_expires_at
    batch.shelf_life_overridden = True
    batch.status_note = f"Safe time changed by hub: {reason}"[:200]
    audit(db, "override", f"Safe time for {batch.dish_name} changed from {old:%H:%M} to {new_expires_at:%H:%M} ({reason})",
          dark_store_id)
    db.commit()
    return batch
