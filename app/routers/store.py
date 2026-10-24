"""Module B (consumer side): the late-night ordering portal for night-shift workers."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import NGO_CUTOFF, STORE_CLOSE, STORE_OPEN
from app.database import get_db, now
from app.models import DarkStore, Order, SurplusBatch, User
from app.routers.darkstore import popularity
from app.schemas import OrderIn
from app.security import require_roles
from app.services.inventory import place_order
from app.services.safety import store_is_open
from app.services.serializers import batch_out, order_out

router = APIRouter(prefix="/api/v1/store", tags=["store"])


@router.get("/menu")
def menu(dark_store_id: int | None = None, db: Session = Depends(get_db)):
    t = now()
    stores = db.scalars(select(DarkStore).order_by(DarkStore.id)).all()
    sid = dark_store_id or (stores[0].id if stores else None)
    pop = popularity(db, sid)
    batches = db.scalars(select(SurplusBatch).where(
        SurplusBatch.dark_store_id == sid, SurplusBatch.status.in_(["AVAILABLE", "SOLD_OUT"]),
        SurplusBatch.expires_at > t)).all()
    items = [batch_out(b, t, pop.get(b.dish_name, 0)) for b in batches]
    top = max((i for i in items if i["status"] == "AVAILABLE"), key=lambda i: i["popularity"], default=None)
    for i in items:
        i["badge"] = "Most Ordered Night Snack" if top and i["id"] == top["id"] and top["popularity"] > 0 else (
            "Trending" if i["popularity"] >= 5 else None)
    items.sort(key=lambda i: (i["status"] != "AVAILABLE", -i["popularity"]))
    return {
        "server_time": t.isoformat(),
        "open": store_is_open(t),
        "hours": {"open": STORE_OPEN.strftime("%H:%M"), "close": STORE_CLOSE.strftime("%H:%M"),
                  "ngo_cutoff": NGO_CUTOFF.strftime("%H:%M")},
        "dark_store_id": sid,
        "stores": [{"id": s.id, "name": s.name, "area": s.area} for s in stores],
        "items": items,
    }


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, db: Session = Depends(get_db), user: User = Depends(require_roles("worker"))):
    order = place_order(db, user, body.dark_store_id, [(l.batch_id, l.qty) for l in body.items],
                        body.delivery_address.strip())
    return order_out(order)


@router.get("/orders/mine")
def my_orders(db: Session = Depends(get_db), user: User = Depends(require_roles("worker"))):
    orders = db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(10))
    return [order_out(o) for o in orders]
