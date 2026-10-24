"""Glue between the database and the ML forecaster (training data, tonight's early warning)."""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ml.calendar_data import festival_name, holiday_name
from app.ml.forecaster import forecaster
from app.models import DailyOperations, DailySurplusLog, Restaurant
from app.services import weather


def service_date(at: datetime) -> date:
    """The 'night' a timestamp belongs to: 2 AM on the 25th is still the night of the 24th."""
    return (at - timedelta(hours=12)).date()


def training_records(db: Session) -> list[dict]:
    rows = db.scalars(select(DailySurplusLog)).all()
    return [{
        "date": r.log_date, "weather_condition": r.weather_condition, "temperature": r.temperature,
        "is_holiday": r.is_holiday, "is_festival": r.is_festival,
        "historical_avg_footfall": r.historical_avg_footfall, "dish_category": r.dish_category,
        "surplus_servings": r.surplus_servings,
    } for r in rows]


def retrain(db: Session) -> dict:
    meta = forecaster.train(training_records(db))
    _cache.clear()
    return meta


def rolling_footfall(db: Session, restaurant: Restaurant, before: date, days: int = 28) -> int:
    """Average actual footfall over the last `days` logged nights; falls back to the profile value."""
    sub = (select(DailySurplusLog.log_date, func.max(DailySurplusLog.actual_footfall).label("ff"))
           .where(DailySurplusLog.restaurant_id == restaurant.id,
                  DailySurplusLog.log_date < before,
                  DailySurplusLog.log_date >= before - timedelta(days=days))
           .group_by(DailySurplusLog.log_date).subquery())
    avg = db.scalar(select(func.avg(sub.c.ff)))
    return int(round(avg)) if avg else restaurant.avg_footfall


def conditions_for(db: Session, d: date) -> dict:
    """Automatic conditions for a night: live weather plus the holiday/festival calendar."""
    return {**weather.conditions(db, d), "holiday": holiday_name(d), "festival": festival_name(d)}


def relief_share(db: Session) -> float:
    """Historical share of dark-store intake left unsold and routed to NGOs (default 35%)."""
    received, donated, expired = db.execute(select(
        func.coalesce(func.sum(DailyOperations.servings_received), 0),
        func.coalesce(func.sum(DailyOperations.servings_donated), 0),
        func.coalesce(func.sum(DailyOperations.relief_expired), 0))).one()
    return (donated + expired) / received if received else 0.35


_cache: dict[tuple, dict] = {}


def tonight_early_warning(db: Session, at: datetime) -> dict:
    d = service_date(at)
    cond = conditions_for(db, d)
    key = (d, forecaster.meta.get("trained_at"), cond["weather_condition"], cond["temperature"])
    if key in _cache:
        return _cache[key]
    share = relief_share(db)
    restaurants = []
    for r in db.scalars(select(Restaurant).order_by(Restaurant.closing_time)):
        footfall = rolling_footfall(db, r, d)
        p = forecaster.predict(on=d, weather_condition=cond["weather_condition"], temperature=cond["temperature"],
                               is_holiday=bool(cond["holiday"]), is_festival=bool(cond["festival"]),
                               historical_avg_footfall=footfall)
        restaurants.append({
            "restaurant_id": r.id, "restaurant": r.name, "area": r.area, "closing_time": r.closing_time,
            "lat": r.lat, "lng": r.lng, "dark_store_id": r.dark_store_id,
            "predicted_surplus": p["predicted_surplus_servings"], "interval_80": p["interval_80"],
            "ngo_prenotification_qty": p["ngo_prenotification_qty"], "zero_surplus": p["zero_surplus"],
            "expected_relief_servings": int(p["ngo_prenotification_qty"] * share),
            "drivers": p["drivers"][:3],
        })
    out = {
        "service_date": d.isoformat(), "conditions": cond, "restaurants": restaurants,
        "total_predicted": sum(r["predicted_surplus"] for r in restaurants),
        "total_prenotified": sum(r["ngo_prenotification_qty"] for r in restaurants),
        "relief_share": round(share, 3),
        "total_expected_relief": sum(r["expected_relief_servings"] for r in restaurants),
    }
    _cache.clear()
    _cache[key] = out
    return out
