"""Indian public holidays / festivals and a deterministic local-weather simulator.

Lunar-calendar festival dates are approximate for 2026-27; verify against an official
gazette before relying on them operationally.
"""
import hashlib
import random
from datetime import date

# Gazetted / public holidays (offices shut -> corporate-area footfall drops).
HOLIDAYS: dict[date, str] = {
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 4): "Holi",
    date(2026, 3, 20): "Eid al-Fitr",
    date(2026, 4, 3): "Good Friday",
    date(2026, 5, 1): "Labour Day",
    date(2026, 5, 27): "Eid al-Adha",
    date(2026, 8, 15): "Independence Day",
    date(2026, 9, 4): "Janmashtami",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 8): "Diwali",
    date(2026, 11, 24): "Guru Nanak Jayanti",
    date(2026, 12, 25): "Christmas",
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 26): "Republic Day",
}

# Festivals / big social events (restaurants over-prep in anticipation).
FESTIVALS: dict[date, str] = {
    date(2026, 8, 26): "Onam",
    date(2026, 8, 28): "Raksha Bandhan",
    date(2026, 9, 4): "Janmashtami",
    date(2026, 9, 14): "Ganesh Chaturthi",
    **{date(2026, 10, d): "Navratri" for d in range(11, 20)},
    date(2026, 10, 20): "Dussehra",
    date(2026, 10, 31): "Halloween",
    date(2026, 11, 7): "Diwali Eve",
    date(2026, 11, 8): "Diwali",
    date(2026, 11, 11): "Bhai Dooj",
    date(2026, 11, 24): "Guru Nanak Jayanti",
    date(2026, 12, 24): "Christmas Eve",
    date(2026, 12, 25): "Christmas",
    date(2026, 12, 31): "New Year's Eve",
}

WEATHER_CONDITIONS = ["Clear", "Cloudy", "Rain", "Heavy Rain", "Heatwave"]


def holiday_name(d: date) -> str | None:
    return HOLIDAYS.get(d)


def festival_name(d: date) -> str | None:
    return FESTIVALS.get(d)


def simulated_weather(d: date) -> tuple[str, float]:
    """Deterministic plausible weather for a South-Indian metro on a given date.

    Stands in for a live weather API so the app works offline; same date -> same weather.
    """
    seed = int(hashlib.sha256(d.isoformat().encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    m = d.month
    if m in (6, 7, 8, 9):            # south-west monsoon
        probs = {"Clear": 0.15, "Cloudy": 0.30, "Rain": 0.38, "Heavy Rain": 0.17, "Heatwave": 0.0}
        base_t = 24.5
    elif m in (10, 11):              # north-east monsoon / retreating
        probs = {"Clear": 0.35, "Cloudy": 0.30, "Rain": 0.25, "Heavy Rain": 0.10, "Heatwave": 0.0}
        base_t = 23.5
    elif m in (3, 4, 5):             # summer
        probs = {"Clear": 0.45, "Cloudy": 0.15, "Rain": 0.10, "Heavy Rain": 0.02, "Heatwave": 0.28}
        base_t = 32.0
    else:                            # winter
        probs = {"Clear": 0.65, "Cloudy": 0.25, "Rain": 0.08, "Heavy Rain": 0.02, "Heatwave": 0.0}
        base_t = 22.0
    cond = rng.choices(list(probs), weights=list(probs.values()))[0]
    temp = base_t + rng.uniform(-2.5, 2.5)
    if cond == "Heatwave":
        temp = rng.uniform(37.0, 41.5)
    elif cond in ("Rain", "Heavy Rain"):
        temp -= rng.uniform(1.0, 3.0)
    return cond, round(temp, 1)
