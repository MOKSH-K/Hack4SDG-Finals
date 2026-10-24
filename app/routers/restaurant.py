"""Module A: partner hotel operations and the surplus prediction API."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, now, to_local_naive
from app.ml.calendar_data import festival_name, holiday_name
from app.ml.forecaster import forecaster
from app.ml.synthetic import CATEGORIES
from app.models import DailySurplusLog, DarkStore, Restaurant, SurplusBatch, User
from app.schemas import DailyLogIn, DispatchIn, PredictIn
from app.security import require_roles
from app.services import weather
from app.services.forecasting import conditions_for, retrain, rolling_footfall, service_date
from app.services.inventory import dispatch_batch
from app.services.serializers import batch_out

router = APIRouter(prefix="/api/v1", tags=["restaurant"])


def _my_restaurant(db: Session, user: User) -> Restaurant:
    if user.restaurant_id is None:
        raise HTTPException(400, "This account is not linked to a partner restaurant")
    return db.get(Restaurant, user.restaurant_id)


@router.post("/predict-surplus")
def predict_surplus(body: PredictIn, db: Session = Depends(get_db)):
    if not forecaster.ready:
        raise HTTPException(503, "Model is still training - try again in a few seconds")
    footfall = body.historical_avg_footfall
    if footfall is None:
        r = db.get(Restaurant, body.restaurant_id) if body.restaurant_id else None
        if body.restaurant_id and r is None:
            raise HTTPException(404, "Restaurant not found")
        footfall = rolling_footfall(db, r, body.date) if r else 300
    holiday = holiday_name(body.date)
    festival = festival_name(body.date)
    auto = weather.conditions(db, body.date)
    weather_condition = body.weather_condition or auto["weather_condition"]
    temperature = body.temperature if body.temperature is not None else auto["temperature"]
    result = forecaster.predict(
        on=body.date, weather_condition=weather_condition, temperature=temperature,
        is_holiday=bool(holiday) if body.is_holiday is None else body.is_holiday,
        is_festival=bool(festival) if body.is_festival is None else body.is_festival,
        historical_avg_footfall=footfall,
        categories=[body.dish_category] if body.dish_category else None,
    )
    # Name the festival in its driver text when the calendar knows it.
    for d in result["drivers"]:
        if d["factor"] == "Festival over-prep" and festival:
            d["text"] = d["text"].replace("Festival over-prep", f"{festival} festival over-prep")
        if d["factor"] == "Public holiday" and holiday:
            d["text"] = d["text"].replace("Public holiday", f"{holiday} public holiday")
    return {**result, "inputs": {**body.model_dump(mode="json"), "weather_condition": weather_condition,
                                 "temperature": temperature, "historical_avg_footfall": footfall,
                                 "holiday_name": holiday, "festival_name": festival},
            "weather": {**auto, "overridden": body.weather_condition is not None or body.temperature is not None},
            "model": {"trained_at": forecaster.meta.get("trained_at"), "metrics": forecaster.meta.get("metrics")}}


@router.get("/weather")
def weather_for(on: date | None = None, db: Session = Depends(get_db)):
    """Automatic conditions for a night (defaults to tonight), used to pre-fill forecasts."""
    d = on or service_date(now())
    return {"date": d.isoformat(), **conditions_for(db, d)}


@router.get("/restaurant/outlook")
def outlook(days: int = Query(default=7, ge=1, le=16), db: Session = Depends(get_db),
            user: User = Depends(require_roles("restaurant"))):
    """The next `days` nights: automatic weather + calendar, and the predicted surplus for each."""
    if not forecaster.ready:
        raise HTTPException(503, "Model is still training - try again in a few seconds")
    r = _my_restaurant(db, user)
    start = service_date(now())
    footfall = rolling_footfall(db, r, start)
    nights = []
    for i in range(days):
        d = start + timedelta(days=i)
        cond = conditions_for(db, d)
        p = forecaster.predict(on=d, weather_condition=cond["weather_condition"], temperature=cond["temperature"],
                               is_holiday=bool(cond["holiday"]), is_festival=bool(cond["festival"]),
                               historical_avg_footfall=footfall)
        nights.append({"date": d.isoformat(), **cond, "predicted_surplus": p["predicted_surplus_servings"],
                       "interval_80": p["interval_80"], "zero_surplus": p["zero_surplus"],
                       "ngo_prenotification_qty": p["ngo_prenotification_qty"]})
    return {"restaurant_id": r.id, "nights": nights, "weather_updated_at": nights[0]["updated_at"] if nights else None}


@router.get("/ml/model-info")
def model_info():
    return {"ready": forecaster.ready, **forecaster.meta}


@router.post("/ml/retrain")
def ml_retrain(db: Session = Depends(get_db), _: User = Depends(require_roles("restaurant"))):
    return retrain(db)


@router.get("/restaurant/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require_roles("restaurant"))):
    r = _my_restaurant(db, user)
    t = now()
    d = service_date(t)
    logs = db.scalars(select(DailySurplusLog).where(DailySurplusLog.restaurant_id == r.id)
                      .order_by(DailySurplusLog.log_date.desc()).limit(len(CATEGORIES) * 14)).all()
    history: dict[str, dict] = {}
    for lg in logs:
        h = history.setdefault(lg.log_date.isoformat(), {"date": lg.log_date.isoformat(), "prep": 0, "sold": 0,
                                                         "surplus": 0, "weather": lg.weather_condition})
        h["prep"] += lg.prep_count
        h["sold"] += lg.sold_count
        h["surplus"] += lg.surplus_servings
    batches = db.scalars(select(SurplusBatch).where(SurplusBatch.restaurant_id == r.id)
                         .order_by(SurplusBatch.dispatched_at.desc()).limit(30)).all()
    return {
        "restaurant": {"id": r.id, "name": r.name, "area": r.area, "closing_time": r.closing_time,
                       "avg_footfall": rolling_footfall(db, r, d), "dark_store_id": r.dark_store_id},
        "service_date": d.isoformat(),
        "tonight_conditions": conditions_for(db, d),
        "logged_tonight": any(lg.log_date == d for lg in logs),
        "categories": CATEGORIES,
        "dark_stores": [{"id": s.id, "name": s.name, "area": s.area} for s in db.scalars(select(DarkStore))],
        "history": sorted(history.values(), key=lambda h: h["date"]),
        "dispatches": [batch_out(b, t) for b in batches],
    }


@router.post("/restaurant/daily-log")
def daily_log(body: DailyLogIn, db: Session = Depends(get_db), user: User = Depends(require_roles("restaurant"))):
    """Record tonight's prep vs sales. Zero-surplus nights are valid and still train the model."""
    r = _my_restaurant(db, user)
    d = service_date(now())
    cond = conditions_for(db, d)
    weather = body.weather_condition or cond["weather_condition"]
    temp = body.temperature if body.temperature is not None else cond["temperature"]
    avg_ff = rolling_footfall(db, r, d)
    total_surplus = 0
    for e in body.entries:
        row = db.scalar(select(DailySurplusLog).where(DailySurplusLog.restaurant_id == r.id,
                                                      DailySurplusLog.log_date == d,
                                                      DailySurplusLog.dish_category == e.dish_category))
        if row is None:
            row = DailySurplusLog(restaurant_id=r.id, log_date=d, dish_category=e.dish_category)
            db.add(row)
        row.weather_condition, row.temperature = weather, temp
        row.is_holiday, row.is_festival = bool(cond["holiday"]), bool(cond["festival"])
        row.historical_avg_footfall, row.actual_footfall = avg_ff, body.actual_footfall
        row.prep_count, row.sold_count = e.prep_count, e.sold_count
        row.surplus_servings = e.prep_count - e.sold_count
        row.closing_time = body.closing_time
        total_surplus += row.surplus_servings
    r.closing_time = body.closing_time
    db.commit()
    return {
        "service_date": d.isoformat(), "total_surplus": total_surplus, "zero_surplus": total_surplus == 0,
        "message": ("Zero surplus tonight - nothing to dispatch. Logged for model training."
                    if total_surplus == 0 else
                    f"{total_surplus} surplus servings logged. Dispatch them to a dark store below."),
    }


@router.post("/restaurant/dispatch", status_code=201)
def dispatch(body: DispatchIn, db: Session = Depends(get_db), user: User = Depends(require_roles("restaurant"))):
    r = _my_restaurant(db, user)
    store_id = body.dark_store_id or r.dark_store_id
    if store_id is None or db.get(DarkStore, store_id) is None:
        raise HTTPException(422, "Choose a valid dark store")
    allergens = ",".join(sorted({a.strip().title() for a in body.allergens if a.strip()}))
    batch = dispatch_batch(db, restaurant_id=r.id, dark_store_id=store_id, dish_name=body.dish_name.strip(),
                           category=body.dish_category, diet=body.diet, allergens=allergens, storage=body.storage,
                           cooked_at=to_local_naive(body.cooked_at), shelf_life_hours=body.shelf_life_hours,
                           quantity=body.quantity, price=body.price)
    db.commit()
    db.refresh(batch)
    return batch_out(batch, now())
