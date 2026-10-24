"""Module C: NGO & volunteer relief portal."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, now
from app.ml.forecaster import forecaster
from app.models import DarkStore, NGO, ReliefBundle, User
from app.schemas import DistributeIn
from app.security import require_roles
from app.services import relief
from app.services.common import haversine_km
from app.services.forecasting import tonight_early_warning
from app.services.serializers import bundle_out

router = APIRouter(prefix="/api/v1/ngo", tags=["ngo"])


def _ngo(db: Session, user: User) -> NGO:
    ngo_id = user.ngo_id or (db.scalar(select(NGO.id).order_by(NGO.id)) if user.role == "admin" else None)
    if ngo_id is None:
        raise HTTPException(400, "Your account isn't linked to an NGO yet. Please contact the FoodForAll team.")
    return db.get(NGO, ngo_id)


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require_roles("ngo"))):
    ngo = _ngo(db, user)
    t = now()
    stores = {s.id: s for s in db.scalars(select(DarkStore))}

    def dist(b: ReliefBundle) -> float:
        s = stores[b.dark_store_id]
        return haversine_km(ngo.lat, ngo.lng, s.lat, s.lng)

    available = db.scalars(select(ReliefBundle).where(ReliefBundle.status == "FLAGGED")).all()
    mine = db.scalars(select(ReliefBundle).where(ReliefBundle.ngo_id == ngo.id)
                      .order_by(ReliefBundle.flagged_at.desc())).all()
    early = tonight_early_warning(db, t) if forecaster.ready else None
    if early:
        early = {**early, "restaurants": [
            {**r, "distance_km": haversine_km(ngo.lat, ngo.lng, r["lat"], r["lng"])} for r in early["restaurants"]]}
    return {
        "ngo": {"id": ngo.id, "name": ngo.name, "area": ngo.area, "verified": ngo.verified,
                "capacity_per_night": ngo.capacity_per_night, "lat": ngo.lat, "lng": ngo.lng},
        "server_time": t.isoformat(),
        "available": sorted((bundle_out(b, t, dist(b)) for b in available if b.batch.expires_at > t),
                            key=lambda b: (b["clock"]["seconds_left"], b["distance_km"])),
        "mine": [bundle_out(b, t, dist(b)) for b in mine],
        "dark_stores": [{"id": s.id, "name": s.name, "area": s.area, "lat": s.lat, "lng": s.lng,
                         "distance_km": haversine_km(ngo.lat, ngo.lng, s.lat, s.lng)} for s in stores.values()],
        "early_warning": early,
    }


@router.post("/bundles/{bundle_id}/claim")
def claim(bundle_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("ngo"))):
    return bundle_out(relief.claim(db, bundle_id, _ngo(db, user).id), now())


@router.post("/bundles/{bundle_id}/pickup")
def pickup(bundle_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("ngo"))):
    return bundle_out(relief.pick_up(db, bundle_id, _ngo(db, user).id), now())


@router.post("/bundles/{bundle_id}/distribute")
def distribute(bundle_id: int, body: DistributeIn, db: Session = Depends(get_db),
               user: User = Depends(require_roles("ngo"))):
    return bundle_out(relief.distribute(db, bundle_id, _ngo(db, user).id, body.shelter_name.strip(),
                                        body.people_fed), now())


@router.post("/bundles/{bundle_id}/release")
def release(bundle_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("ngo"))):
    return bundle_out(relief.release(db, bundle_id, _ngo(db, user).id), now())
