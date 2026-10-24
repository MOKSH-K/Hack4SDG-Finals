from datetime import datetime

from app.models import Order, ReliefBundle, SurplusBatch
from app.services.safety import haccp_tag, safety_clock


def batch_out(b: SurplusBatch, at: datetime, popularity: int = 0) -> dict:
    return {
        "id": b.id, "code": b.code, "dish_name": b.dish_name, "category": b.category, "diet": b.diet,
        "allergens": [a for a in b.allergens.split(",") if a], "storage": b.storage, "price": b.price,
        "restaurant": b.restaurant.name, "restaurant_id": b.restaurant_id, "dark_store_id": b.dark_store_id,
        "cooked_at": b.cooked_at.isoformat(), "shelf_life_hours": b.shelf_life_hours,
        "dispatched_at": b.dispatched_at.isoformat() if b.dispatched_at else None,
        "received_at": b.received_at.isoformat() if b.received_at else None,
        "entry_temp_c": b.entry_temp_c, "initial_qty": b.initial_qty, "available_qty": b.available_qty,
        "sold_qty": b.sold_qty, "status": b.status, "status_note": b.status_note,
        "temp_breach": b.temp_breach, "shelf_life_overridden": b.shelf_life_overridden,
        "popularity": popularity, "clock": safety_clock(b, at), "haccp": haccp_tag(b, at),
    }


def bundle_out(r: ReliefBundle, at: datetime, distance_km: float | None = None) -> dict:
    b = r.batch
    return {
        "id": r.id, "code": r.code, "servings": r.servings, "reason": r.reason, "status": r.status,
        "dish_name": b.dish_name, "category": b.category, "diet": b.diet,
        "allergens": [a for a in b.allergens.split(",") if a], "storage": b.storage,
        "restaurant": b.restaurant.name, "dark_store": r.dark_store.name, "dark_store_area": r.dark_store.area,
        "ngo": r.ngo.name if r.ngo else None, "ngo_id": r.ngo_id, "distance_km": distance_km,
        "flagged_at": r.flagged_at.isoformat(),
        "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None,
        "picked_up_at": r.picked_up_at.isoformat() if r.picked_up_at else None,
        "distributed_at": r.distributed_at.isoformat() if r.distributed_at else None,
        "shelter_name": r.shelter_name, "people_fed": r.people_fed,
        "clock": safety_clock(b, at), "haccp": haccp_tag(b, at),
    }


def order_out(o: Order) -> dict:
    return {
        "id": o.id, "code": o.code, "status": o.status, "total": o.total,
        "delivery_address": o.delivery_address, "created_at": o.created_at.isoformat(),
        "items": [{"dish_name": i.dish_name, "qty": i.qty, "unit_price": i.unit_price} for i in o.items],
    }
