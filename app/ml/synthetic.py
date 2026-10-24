"""Generative model of a restaurant night, used to seed realistic training history.

Restaurants prep from habit (a 15-22% cushion, weekend and festival over-prep) and only partly
react to weather, while diners react fully - that gap is the surplus the forecaster learns.
"""
import random
from datetime import date

CATEGORY_SHARE = {
    "Biryani & Rice": 0.30,
    "Curries": 0.25,
    "Breads": 0.20,
    "Snacks": 0.15,
    "Desserts": 0.10,
}
CATEGORIES = list(CATEGORY_SHARE)

# How much more than expected demand kitchens habitually prepare, by category.
PREP_CUSHION = {"Biryani & Rice": 1.20, "Curries": 1.18, "Breads": 1.22, "Snacks": 1.15, "Desserts": 1.15}

WEATHER_FOOTFALL = {"Clear": 1.0, "Cloudy": 0.97, "Rain": 0.80, "Heavy Rain": 0.66, "Heatwave": 0.86}


def simulate_day(avg_footfall: int, d: date, category: str, weather: str, temp: float,
                 is_holiday: bool, is_festival: bool, rng: random.Random) -> dict:
    share = CATEGORY_SHARE[category]
    weekend = d.weekday() >= 4  # Fri-Sun

    footfall_mult = WEATHER_FOOTFALL[weather]
    if weekend:
        footfall_mult *= 1.15
    if is_holiday:
        footfall_mult *= 0.85   # offices closed, corporate dinner crowd gone
    if is_festival:
        footfall_mult *= 1.08   # some celebration dining, but many eat at home
    if temp > 36:
        footfall_mult *= 0.95
    if category == "Desserts" and is_festival:
        footfall_mult *= 1.15

    prep_mult = PREP_CUSHION[category]
    if weekend:
        prep_mult *= 1.12
    if is_festival:
        prep_mult *= 1.22       # anticipatory over-prep
    # Kitchens see the forecast but only partly cut prep (~60% of the expected drop).
    prep_mult *= 1 - 0.60 * (1 - WEATHER_FOOTFALL[weather])

    actual_footfall = max(20, int(avg_footfall * footfall_mult * rng.uniform(0.92, 1.08)))
    prep = max(5, int(round(avg_footfall * share * prep_mult * rng.uniform(0.96, 1.04))))
    demand = int(round(actual_footfall * share * rng.uniform(0.90, 1.10)))
    sold = min(prep, demand)
    return {
        "actual_footfall": actual_footfall,
        "prep_count": prep,
        "sold_count": sold,
        "surplus_servings": prep - sold,
    }
