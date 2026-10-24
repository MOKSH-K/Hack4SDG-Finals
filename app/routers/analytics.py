"""Module D: SDG impact metrics and executive analytics (public, read-only)."""
import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import CO2E_PER_KG_FOOD, KG_PER_SERVING
from app.database import get_db
from app.ml.forecaster import forecaster
from app.models import NGO, DailyOperations, DailySurplusLog, NgoDistributionLog, ReliefBundle
from app.services.impact import impact_summary

router = APIRouter(prefix="/api/v1", tags=["analytics"])

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/impact/summary")
def summary(db: Session = Depends(get_db)):
    return impact_summary(db)


@router.get("/analytics/dashboard")
def dashboard(db: Session = Depends(get_db)):
    logs = pd.read_sql(select(DailySurplusLog.log_date, DailySurplusLog.weather_condition,
                              DailySurplusLog.temperature, DailySurplusLog.is_festival,
                              DailySurplusLog.is_holiday, DailySurplusLog.prep_count,
                              DailySurplusLog.sold_count, DailySurplusLog.surplus_servings), db.bind)
    ops = pd.read_sql(select(DailyOperations), db.bind)

    out: dict = {"impact": impact_summary(db), "model": forecaster.meta}
    if logs.empty:
        return out

    daily = logs.groupby("log_date").agg(
        prep=("prep_count", "sum"), sold=("sold_count", "sum"), surplus=("surplus_servings", "sum"),
        temperature=("temperature", "first"), weather=("weather_condition", "first"),
        festival=("is_festival", "max"), holiday=("is_holiday", "max")).reset_index()
    daily["surplus_pct"] = (100 * daily["surplus"] / daily["prep"].clip(lower=1)).round(1)
    daily["dow"] = pd.to_datetime(daily["log_date"]).dt.dayofweek

    ops_daily = ops.groupby("op_date")[["servings_received", "servings_sold", "servings_donated",
                                        "servings_wasted"]].sum().reset_index() if not ops.empty else pd.DataFrame()
    timeline = daily.merge(ops_daily, left_on="log_date", right_on="op_date", how="left").fillna(0)

    dow = daily.groupby("dow").agg(surplus=("surplus", "mean"), prep=("prep", "mean"),
                                   sold=("sold", "mean")).reindex(range(7)).fillna(0)
    weather = (daily.groupby("weather")["surplus_pct"].agg(["mean", "count"])
               .reindex(["Clear", "Cloudy", "Rain", "Heavy Rain", "Heatwave"]).dropna())
    special = {
        "festival": round(float(daily.loc[daily.festival.astype(bool), "surplus_pct"].mean() or 0), 1),
        "holiday": round(float(daily.loc[daily.holiday.astype(bool), "surplus_pct"].mean() or 0), 1),
        "regular": round(float(daily.loc[~daily.festival.astype(bool) & ~daily.holiday.astype(bool),
                                         "surplus_pct"].mean() or 0), 1),
    }

    ngo_hist = {n: (s, p) for n, s, p in db.execute(
        select(NgoDistributionLog.ngo_id, func.sum(NgoDistributionLog.servings), func.sum(NgoDistributionLog.people_fed))
        .group_by(NgoDistributionLog.ngo_id))}
    ngo_live = {n: (s, p) for n, s, p in db.execute(
        select(ReliefBundle.ngo_id, func.sum(ReliefBundle.servings), func.sum(ReliefBundle.people_fed))
        .where(ReliefBundle.status == "DISTRIBUTED").group_by(ReliefBundle.ngo_id))}
    ngos = [{"ngo": n.name,
             "servings": int(ngo_hist.get(n.id, (0, 0))[0] + ngo_live.get(n.id, (0, 0))[0]),
             "people_fed": int(ngo_hist.get(n.id, (0, 0))[1] + (ngo_live.get(n.id, (0, 0))[1] or 0))}
            for n in db.scalars(select(NGO).order_by(NGO.id))]

    out.update({
        "timeline": [{"date": str(r.log_date), "prep": int(r.prep), "sold": int(r.sold),
                      "overprep": int(r.surplus), "rescued_sold": int(r.servings_sold),
                      "donated": int(r.servings_donated), "wasted": int(r.servings_wasted),
                      "co2e_kg": round((r.servings_sold + r.servings_donated) * KG_PER_SERVING * CO2E_PER_KG_FOOD, 1)}
                     for r in timeline.itertuples()],
        "day_of_week": [{"day": DAYS[i], "avg_surplus": round(float(row.surplus), 1),
                         "avg_prep": round(float(row.prep), 1), "avg_sold": round(float(row.sold), 1)}
                        for i, row in dow.iterrows()],
        "weather": [{"weather": w, "avg_surplus_pct": round(float(r["mean"]), 1), "nights": int(r["count"])}
                    for w, r in weather.iterrows()],
        "temperature_scatter": [{"temperature": float(r.temperature), "surplus_pct": float(r.surplus_pct),
                                 "weather": r.weather} for r in daily.itertuples()],
        "special_days": special,
        "ngos": ngos,
    })
    return out
