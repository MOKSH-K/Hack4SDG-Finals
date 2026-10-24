"""Automated weather: Open-Meteo forecast + history, refreshed in the background.

The forecast endpoint covers roughly the last 6 weeks and the next 16 days; older nights (used to
seed training history) come from Open-Meteo's historical archive. Hourly data is condensed into one
row per night. Nights are classified from the 6-11 PM window - the hours when diners decide
whether to go out - plus the day's peak temperature for heatwaves. If the API is unreachable we
fall back to the offline seasonal estimate, so the app never blocks on the network.
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    TIMEZONE, WEATHER_LAT, WEATHER_LNG, WEATHER_PROVIDER, WEATHER_REFRESH_MINUTES, WEATHER_TIMEOUT_SECONDS,
)
from app.database import now
from app.ml.calendar_data import simulated_weather
from app.models import WeatherDay

log = logging.getLogger("foodforall.weather")

API_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_DAYS = 16   # Open-Meteo maximum
EVENING_HOURS = range(18, 24)
RAIN_CODES = set(range(61, 68)) | set(range(80, 83)) | set(range(95, 100))   # rain, showers, thunderstorms
OVERCAST_CODES = {3, 45, 48} | set(range(51, 58))                             # overcast, fog, drizzle

HEATWAVE_MAX_C = 38.0
HEAVY_RAIN_EVENING_MM = 4.0
RAIN_EVENING_MM = 0.5

_last_attempt = 0.0  # monotonic time of the last fetch attempt (success or failure)


def classify(temp_max: float, evening_precip_mm: float, evening_codes: list[int]) -> str:
    if temp_max >= HEATWAVE_MAX_C:
        return "Heatwave"
    if evening_precip_mm >= HEAVY_RAIN_EVENING_MM:
        return "Heavy Rain"
    if evening_precip_mm >= RAIN_EVENING_MM or sum(c in RAIN_CODES for c in evening_codes) >= 2:
        return "Rain"
    if sum(c in OVERCAST_CODES for c in evening_codes) >= len(evening_codes) / 2:
        return "Cloudy"
    return "Clear"


def summarise(payload: dict) -> list[dict]:
    """Turn an Open-Meteo hourly/daily payload into one record per date."""
    hourly = payload["hourly"]
    evenings: dict[str, dict] = defaultdict(lambda: {"temps": [], "precip": 0.0, "codes": []})
    for ts, temp, precip, code in zip(hourly["time"], hourly["temperature_2m"], hourly["precipitation"],
                                      hourly["weather_code"]):
        if int(ts[11:13]) in EVENING_HOURS and temp is not None:
            e = evenings[ts[:10]]
            e["temps"].append(temp)
            e["precip"] += precip or 0.0
            e["codes"].append(int(code or 0))
    daily_max = dict(zip(payload["daily"]["time"], payload["daily"]["temperature_2m_max"]))
    out = []
    for day, e in sorted(evenings.items()):
        if len(e["temps"]) < 3 or daily_max.get(day) is None:
            continue
        out.append({
            "day": date.fromisoformat(day),
            "condition": classify(daily_max[day], e["precip"], e["codes"]),
            "temperature": round(sum(e["temps"]) / len(e["temps"]), 1),
            "temp_max": round(daily_max[day], 1),
            "evening_precip_mm": round(e["precip"], 1),
        })
    return out


def _get(url: str, **extra) -> dict:
    params = urllib.parse.urlencode({
        "latitude": WEATHER_LAT, "longitude": WEATHER_LNG, "timezone": TIMEZONE,
        "hourly": "temperature_2m,precipitation,weather_code", "daily": "temperature_2m_max", **extra,
    })
    req = urllib.request.Request(f"{url}?{params}", headers={"User-Agent": "FoodForAll/1.0"})
    with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def _fetch() -> dict:
    return _get(API_URL, past_days=92, forecast_days=FORECAST_DAYS)


def _fetch_archive(start: date, end: date) -> dict:
    return _get(ARCHIVE_URL, start_date=start.isoformat(), end_date=end.isoformat())


def refresh(db: Session, force: bool = False, backfill_days: int = 0) -> int:
    """Pull the latest forecast (and optionally `backfill_days` of archive history) into the weather table.

    Rate-limited unless forced; never raises.
    """
    global _last_attempt
    if WEATHER_PROVIDER != "open-meteo":
        return 0
    if not force and time.monotonic() - _last_attempt < WEATHER_REFRESH_MINUTES * 60 and _last_attempt:
        return 0
    _last_attempt = time.monotonic()
    records: list[dict] = []
    if backfill_days:
        today = now().date()
        try:
            records += summarise(_fetch_archive(today - timedelta(days=backfill_days), today - timedelta(days=2)))
        except Exception as exc:
            log.warning("Weather archive backfill failed (%s)", exc)
    try:
        records += summarise(_fetch())   # later records win: the forecast endpoint is fresher for recent nights
    except Exception as exc:
        log.warning("Weather refresh failed (%s); using cached/estimated weather", exc)
    if not records:
        return 0
    records = list({r["day"]: r for r in records}.values())
    t = now()
    for r in records:
        row = db.get(WeatherDay, r["day"]) or WeatherDay(day=r["day"])
        row.condition, row.temperature = r["condition"], r["temperature"]
        row.temp_max, row.evening_precip_mm = r["temp_max"], r["evening_precip_mm"]
        row.source, row.fetched_at = "open-meteo", t
        db.merge(row)
    db.commit()
    log.info("Weather refreshed: %d nights from Open-Meteo", len(records))
    return len(records)


def conditions(db: Session, d: date) -> dict:
    """Weather for the night of `d`: live/cached API data when we have it, else the seasonal estimate."""
    row = db.get(WeatherDay, d)
    if row:
        return {"weather_condition": row.condition, "temperature": row.temperature, "temp_max": row.temp_max,
                "evening_rain_mm": row.evening_precip_mm, "source": "live",
                "updated_at": row.fetched_at.isoformat() if row.fetched_at else None}
    cond, temp = simulated_weather(d)
    return {"weather_condition": cond, "temperature": temp, "temp_max": None, "evening_rain_mm": None,
            "source": "estimate", "updated_at": None}


def last_updated(db: Session) -> datetime | None:
    return db.scalar(select(func.max(WeatherDay.fetched_at)))
