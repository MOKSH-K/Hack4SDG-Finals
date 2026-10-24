"""NGO relief pipeline: Surplus Flagged -> NGO Claimed -> In Transit -> Distributed."""
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import now
from app.models import NGO, ReliefBundle, SurplusBatch
from app.services.common import audit, lock_for_write, make_code

STAGES = ["FLAGGED", "CLAIMED", "IN_TRANSIT", "DISTRIBUTED"]


def convert_to_relief(db: Session, batch: SurplusBatch, reason: str) -> ReliefBundle | None:
    """Pull a batch off consumer sale and turn its leftovers into a free relief bundle.

    The UPDATE is conditioned on the quantity we read, so a checkout that slips in between
    makes this a no-op (retried on the next sweep) rather than donating food that was sold.
    """
    qty = batch.available_qty
    if qty <= 0:
        return None
    res = db.execute(
        update(SurplusBatch)
        .where(SurplusBatch.id == batch.id, SurplusBatch.status == "AVAILABLE",
               SurplusBatch.available_qty == qty)
        .values(status="RELIEF", available_qty=0, status_note=reason)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        return None
    bundle = ReliefBundle(code=make_code("REL"), batch_id=batch.id, dark_store_id=batch.dark_store_id,
                          servings=qty, reason=reason, status="FLAGGED", flagged_at=now())
    db.add(bundle)
    audit(db, "relief", f"Offered {qty} × {batch.dish_name} free to NGOs", batch.dark_store_id)
    return bundle


def _transition(db: Session, bundle_id: int, ngo_id: int, from_status: str, values: dict) -> ReliefBundle:
    lock_for_write(db)
    conds = [ReliefBundle.id == bundle_id, ReliefBundle.status == from_status]
    if from_status != "FLAGGED":
        conds.append(ReliefBundle.ngo_id == ngo_id)
    res = db.execute(update(ReliefBundle).where(*conds).values(**values)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        bundle = db.get(ReliefBundle, bundle_id)
        if bundle is None:
            raise HTTPException(404, "We couldn't find that donation.")
        if from_status == "FLAGGED" and bundle.status != "FLAGGED":
            raise HTTPException(409, "Another NGO has already claimed this food.")
        raise HTTPException(409, "This donation has already moved on. Refresh to see its latest status.")
    db.commit()
    bundle = db.get(ReliefBundle, bundle_id)
    db.refresh(bundle)
    return bundle


def claim(db: Session, bundle_id: int, ngo_id: int) -> ReliefBundle:
    t = now()
    # Refuse claims on food that has expired but hasn't been swept yet.
    bundle = db.get(ReliefBundle, bundle_id)
    if bundle and bundle.batch.expires_at <= t:
        raise HTTPException(409, "This food is past its safe time and can no longer be collected.")
    b = _transition(db, bundle_id, ngo_id, "FLAGGED", {"status": "CLAIMED", "ngo_id": ngo_id, "claimed_at": t})
    audit(db, "claim", f"{b.ngo.name} claimed {b.servings} × {b.batch.dish_name}", b.dark_store_id)
    db.commit()
    return b


def pick_up(db: Session, bundle_id: int, ngo_id: int) -> ReliefBundle:
    b = _transition(db, bundle_id, ngo_id, "CLAIMED", {"status": "IN_TRANSIT", "picked_up_at": now()})
    audit(db, "pickup", f"{b.ngo.name} picked up {b.servings} × {b.batch.dish_name}", b.dark_store_id)
    db.commit()
    return b


def distribute(db: Session, bundle_id: int, ngo_id: int, shelter_name: str, people_fed: int) -> ReliefBundle:
    b = _transition(db, bundle_id, ngo_id, "IN_TRANSIT", {
        "status": "DISTRIBUTED", "distributed_at": now(), "shelter_name": shelter_name, "people_fed": people_fed})
    audit(db, "distribute", f"{b.ngo.name} fed {people_fed} people at {shelter_name}", b.dark_store_id)
    db.commit()
    return b


def release(db: Session, bundle_id: int, ngo_id: int) -> ReliefBundle:
    """An NGO that can't make it releases its claim back to the pool."""
    ngo = db.get(NGO, ngo_id)
    b = _transition(db, bundle_id, ngo_id, "CLAIMED", {"status": "FLAGGED", "ngo_id": None, "claimed_at": None})
    audit(db, "release", f"{ngo.name} can't collect {b.servings} × {b.batch.dish_name}; offered to other NGOs again",
          b.dark_store_id)
    db.commit()
    return b
