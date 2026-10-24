"""Generate app/ml/india_calendar.json: public holidays and festivals for Bengaluru, 2026-2031.

Dates come from the maintained `holidays` package (lunar/Islamic dates are computed; far-future
Islamic dates carry its "(estimated)" marker because they depend on moon sighting).

    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python scripts/build_calendar.py
"""
import json
from datetime import date, timedelta
from pathlib import Path

import holidays

FIRST_YEAR, LAST_YEAR = 2026, 2031
OUT = Path(__file__).resolve().parent.parent / "app" / "ml" / "india_calendar.json"

# Public holidays that are celebrations (kitchens over-prep); the rest only close offices.
FESTIVE_PUBLIC = ("Holi", "Ugadi", "Eid al-Fitr", "Eid al-Adha", "Ram Navami", "Prophet's Birthday", "Janmashtami",
                  "Ganesh Chaturthi", "Dussehra", "Diwali", "Guru Nanak", "Christmas", "Makar Sankranti",
                  "Buddha Purnima", "Mahavira", "Maha Shivaratri")
# Optional-category days worth treating as festivals, mapped to the name we display.
FESTIVE_OPTIONAL = {
    "New Year's Day": "New Year's Day", "Pongal": "Makar Sankranti / Pongal", "Maha Shivaratri": "Maha Shivaratri",
    "Holika Dahan": "Holika Dahan", "Ugadi": "Ugadi", "Easter Sunday": "Easter", "Onam": "Onam",
    "Raksha Bandhan": "Raksha Bandhan", "Ganesh Chaturthi": "Ganesh Chaturthi", "Karwa Chouth": "Karwa Chauth",
    "Naraka Chaturdashi": "Diwali (Naraka Chaturdashi)", "Govardhan Puja": "Govardhan Puja", "Bhai Duj": "Bhai Dooj",
    "Christmas Eve": "Christmas Eve",
}
FIXED_FESTIVALS = {(2, 14): "Valentine's Day", (10, 31): "Halloween", (12, 31): "New Year's Eve"}
FIXED_STATE_HOLIDAYS = {(11, 1): "Kannada Rajyotsava"}   # Karnataka state holiday missing from the package


def build() -> dict:
    years = range(FIRST_YEAR, LAST_YEAR + 1)
    public = holidays.India(subdiv="KA", years=years)
    optional = holidays.India(subdiv="KA", categories=("optional",), years=years)

    hol: dict[date, list[str]] = {}
    fest: dict[date, list[str]] = {}

    def add(table, d, name):
        if name not in table.setdefault(d, []):
            table[d].append(name)

    for d, names in public.items():
        for name in names.split("; "):
            add(hol, d, name)
            if any(k in name for k in FESTIVE_PUBLIC):
                add(fest, d, name)
    for d, names in optional.items():
        for name in names.split("; "):
            for key, label in FESTIVE_OPTIONAL.items():
                # Skip labels already covered that day (e.g. "Diwali (Naraka Chaturdashi)" on Diwali).
                if key in name and not any(existing in label or label in existing for existing in fest.get(d, [])):
                    add(fest, d, label)
    for y in years:
        for (m, day), name in FIXED_FESTIVALS.items():
            add(fest, date(y, m, day), name)
        for (m, day), name in FIXED_STATE_HOLIDAYS.items():
            add(hol, date(y, m, day), name)
    # Navratri: the nine nights before Dussehra.
    for d, names in public.items():
        if "Dussehra" in names:
            for i in range(1, 10):
                add(fest, d - timedelta(days=i), "Navratri")

    as_json = lambda t: {d.isoformat(): " / ".join(n) for d, n in sorted(t.items())}  # noqa: E731
    return {
        "source": f"python-holidays {holidays.__version__} (India, subdivision KA) + fixed dates",
        "range": [f"{FIRST_YEAR}-01-01", f"{LAST_YEAR}-12-31"],
        "public_holidays": as_json(hol),
        "festivals": as_json(fest),
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}: "
          f"{len(data['public_holidays'])} holiday dates, {len(data['festivals'])} festival dates, {data['range']}")
