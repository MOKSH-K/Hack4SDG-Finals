"""Seed FoodForAll with partners, 90 nights of history and a live 'tonight' scenario.

Runs automatically on first startup. To wipe and reseed:
    python seed_data.py --reset
"""
import random
import sys
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import KG_PER_SERVING
from app.database import Base, SessionLocal, engine, now
from app.ml.calendar_data import festival_name, holiday_name
from app.ml.synthetic import CATEGORIES, simulate_day
from app.models import (
    NGO, AuditEvent, DailyOperations, DailySurplusLog, DarkStore, NgoDistributionLog, Order, OrderItem, ReliefBundle,
    Restaurant, SurplusBatch, TemperatureReading, User,
)
from app.security import hash_password
from app.services import weather as weather_service
from app.services.common import make_code
from app.services.forecasting import service_date
from app.services.safety import compute_expiry

HISTORY_DAYS = 90
DEFAULT_PASSWORD = "foodforall2026"

DARK_STORES = [
    dict(name="Koramangala Night Hub", area="Koramangala", lat=12.9352, lng=77.6245, hot_capacity=400, chilled_capacity=600),
    dict(name="Whitefield Tech-Park Hub", area="Whitefield", lat=12.9698, lng=77.7500, hot_capacity=350, chilled_capacity=500),
    dict(name="HSR Layout Night Hub", area="HSR Layout", lat=12.9116, lng=77.6474, hot_capacity=300, chilled_capacity=450),
]

# (profile, dark-store index, menu: dish, category, diet, allergens, storage, price)
RESTAURANTS = [
    (dict(name="Midnight Biryani Co.", area="Koramangala", lat=12.9340, lng=77.6200, closing_time="23:30", avg_footfall=420), 0, [
        ("Hyderabadi Chicken Dum Biryani", "Biryani & Rice", "NON_VEG", "Dairy", "HOT", 149),
        ("Veg Dum Biryani", "Biryani & Rice", "VEG", "Dairy,Nuts", "HOT", 119),
        ("Mirchi Ka Salan", "Curries", "VEG", "Peanuts,Sesame", "HOT", 59),
        ("Double Ka Meetha", "Desserts", "VEG", "Dairy,Gluten,Nuts", "CHILLED", 69),
    ]),
    (dict(name="Green Leaf Café", area="Indiranagar", lat=12.9784, lng=77.6408, closing_time="22:30", avg_footfall=260), 0, [
        ("Paneer Tikka Wrap", "Snacks", "VEG", "Dairy,Gluten", "CHILLED", 99),
        ("Vegan Millet Khichdi", "Biryani & Rice", "VEGAN", "", "HOT", 89),
        ("Chana Masala", "Curries", "VEGAN", "", "HOT", 79),
        ("Carrot Halwa", "Desserts", "VEG", "Dairy,Nuts", "CHILLED", 59),
    ]),
    (dict(name="Coastal Curry House", area="Whitefield", lat=12.9716, lng=77.7413, closing_time="23:00", avg_footfall=340), 1, [
        ("Kerala Fish Curry", "Curries", "NON_VEG", "Fish", "HOT", 139),
        ("Lemon Rice", "Biryani & Rice", "VEGAN", "Peanuts", "HOT", 69),
        ("Malabar Parotta", "Breads", "VEG", "Gluten,Dairy", "HOT", 30),
        ("Chicken 65", "Snacks", "NON_VEG", "Gluten,Egg", "HOT", 109),
    ]),
    (dict(name="Tandoor Tales", area="Marathahalli", lat=12.9569, lng=77.7011, closing_time="23:15", avg_footfall=380), 1, [
        ("Butter Chicken", "Curries", "NON_VEG", "Dairy", "HOT", 159),
        ("Dal Makhani", "Curries", "VEG", "Dairy", "HOT", 99),
        ("Garlic Naan", "Breads", "VEG", "Gluten,Dairy", "HOT", 35),
        ("Gulab Jamun", "Desserts", "VEG", "Dairy,Gluten", "CHILLED", 49),
        ("Punjabi Samosa", "Snacks", "VEGAN", "Gluten", "HOT", 25),
    ]),
    (dict(name="Saffron Courtyard", area="HSR Layout", lat=12.9121, lng=77.6446, closing_time="23:00", avg_footfall=310), 2, [
        ("Mutton Rogan Josh", "Curries", "NON_VEG", "Dairy", "HOT", 179),
        ("Jeera Rice", "Biryani & Rice", "VEGAN", "", "HOT", 59),
        ("Butter Naan", "Breads", "VEG", "Gluten,Dairy", "HOT", 35),
        ("Rasmalai", "Desserts", "VEG", "Dairy,Nuts", "CHILLED", 79),
    ]),
    (dict(name="Udupi Night Café", area="Jayanagar", lat=12.9250, lng=77.5938, closing_time="22:45", avg_footfall=290), 2, [
        ("Bisi Bele Bath", "Biryani & Rice", "VEG", "Dairy,Peanuts", "HOT", 69),
        ("Vegetable Sagu", "Curries", "VEGAN", "Mustard", "HOT", 55),
        ("Masala Vada", "Snacks", "VEGAN", "", "HOT", 30),
        ("Kesari Bath", "Desserts", "VEG", "Dairy,Nuts", "CHILLED", 40),
    ]),
    (dict(name="Konkan Kitchen", area="Bellandur", lat=12.9260, lng=77.6762, closing_time="23:30", avg_footfall=270), 1, [
        ("Goan Prawn Curry", "Curries", "NON_VEG", "Shellfish", "HOT", 189),
        ("Kokum Rice", "Biryani & Rice", "VEGAN", "", "HOT", 65),
        ("Neer Dosa", "Breads", "VEGAN", "", "HOT", 40),
        ("Kaju Kothimbir Vadi", "Snacks", "VEG", "Nuts,Gluten", "HOT", 49),
    ]),
]

NGOS = [
    dict(name="Annapoorna Night Relief", area="Koramangala", lat=12.9279, lng=77.6271, capacity_per_night=250),
    dict(name="Shelter Bridge Trust", area="Whitefield", lat=12.9850, lng=77.7300, capacity_per_night=180),
    dict(name="Hope Kitchen Collective", area="Shivajinagar", lat=12.9857, lng=77.6057, capacity_per_night=150),
    dict(name="Sahaya Community Food Bank", area="Bommanahalli", lat=12.9030, lng=77.6240, capacity_per_night=200),
]

# email, name, role, link
USERS = [
    ("priya.sharma@foodforall.in", "Priya Sharma", "worker", {}),
    ("arvind.rao@foodforall.in", "Arvind Rao", "restaurant", {"restaurant_id": 1}),
    ("harpreet.kaur@foodforall.in", "Harpreet Kaur", "restaurant", {"restaurant_id": 4}),
    ("deepa.kulkarni@foodforall.in", "Deepa Kulkarni", "restaurant", {"restaurant_id": 5}),
    ("kiran.hegde@foodforall.in", "Kiran Hegde", "darkstore", {"dark_store_id": 1}),
    ("farhan.ali@foodforall.in", "Farhan Ali", "darkstore", {"dark_store_id": 2}),
    ("sneha.iyer@foodforall.in", "Sneha Iyer", "darkstore", {"dark_store_id": 3}),
    ("lakshmi.nair@foodforall.in", "Lakshmi Nair", "ngo", {"ngo_id": 1}),
    ("joseph.mathew@foodforall.in", "Joseph Mathew", "ngo", {"ngo_id": 2}),
    ("ritu.bansal@foodforall.in", "Ritu Bansal", "ngo", {"ngo_id": 4}),
    ("nikhil.menon@foodforall.in", "Nikhil Menon", "admin", {}),
]
# Night-shift customers whose past orders make up tonight's order history.
SEED_WORKERS = ["Arjun Pillai", "Meera Krishnan", "Kabir Singh", "Sana Qureshi", "Rahul Verma", "Divya Reddy",
                "Ananya Ghosh", "Vikram Shetty", "Fatima Shaikh", "Rohit Joshi", "Neha Gupta", "Suresh Babu"]


def seed_if_empty(db: Session) -> bool:
    if db.scalar(select(func.count(User.id))):
        return False
    seed(db)
    return True


def seed(db: Session) -> None:
    rng = random.Random(42)
    t = now()
    tonight = service_date(t)

    stores = [DarkStore(**s) for s in DARK_STORES]
    db.add_all(stores)
    db.flush()
    restaurants = []
    for profile, store_idx, _ in RESTAURANTS:
        r = Restaurant(**profile, dark_store_id=stores[store_idx].id)
        restaurants.append(r)
    ngos = [NGO(**n) for n in NGOS]
    db.add_all(restaurants + ngos)
    db.flush()

    pw = hash_password(DEFAULT_PASSWORD)
    for email, name, role, link in USERS:
        db.add(User(email=email, name=name, role=role, password_hash=pw, **link))
    workers = [User(email=f"{n.lower().replace(' ', '.')}@foodforall.in", name=n, role="worker", password_hash=pw)
               for n in SEED_WORKERS]
    db.add_all(workers)
    db.flush()

    # Real recent weather (Open-Meteo) for the history when reachable; seasonal estimate otherwise.
    weather_service.refresh(db, force=True, backfill_days=HISTORY_DAYS + 2)
    _seed_history(db, rng, restaurants, stores, ngos, tonight)
    _seed_tonight(db, rng, restaurants, stores, ngos, workers, t)
    db.commit()


def _seed_history(db, rng, restaurants, stores, ngos, tonight) -> None:
    footfall_hist = {r.id: [] for r in restaurants}
    for back in range(HISTORY_DAYS, 0, -1):
        d = tonight - timedelta(days=back)
        w = weather_service.conditions(db, d)
        weather, temp = w["weather_condition"], w["temperature"]
        hol, fest = bool(holiday_name(d)), bool(festival_name(d))
        store_received = {s.id: 0 for s in stores}
        for r in restaurants:
            recent = footfall_hist[r.id][-28:]
            hist_avg = int(sum(recent) / len(recent)) if len(recent) >= 7 else r.avg_footfall
            night_footfall = None
            for cat in CATEGORIES:
                sim = simulate_day(r.avg_footfall, d, cat, weather, temp, hol, fest, rng)
                night_footfall = night_footfall or sim["actual_footfall"]
                db.add(DailySurplusLog(
                    restaurant_id=r.id, log_date=d, dish_category=cat, weather_condition=weather,
                    temperature=temp, is_holiday=hol, is_festival=fest, historical_avg_footfall=hist_avg,
                    actual_footfall=night_footfall, prep_count=sim["prep_count"], sold_count=sim["sold_count"],
                    surplus_servings=sim["surplus_servings"], closing_time=r.closing_time))
                store_received[r.dark_store_id] += sim["surplus_servings"]
            footfall_hist[r.id].append(night_footfall)

        for s in stores:
            received = int(store_received[s.id] * rng.uniform(0.86, 0.94))  # some surplus unfit/uncollected
            # Bad weather keeps workers indoors -> more orders at night.
            sell_rate = rng.uniform(0.48, 0.62) + (0.08 if weather in ("Rain", "Heavy Rain") else 0)
            sold = int(received * sell_rate)
            remaining = received - sold
            relief_expired = int(remaining * rng.uniform(0.03, 0.08))
            direct_waste = int(remaining * rng.uniform(0.02, 0.05))
            donated = remaining - relief_expired - direct_waste
            people_fed = int(donated * rng.uniform(0.85, 0.95))
            db.add(DailyOperations(
                dark_store_id=s.id, op_date=d, servings_received=received, servings_sold=sold,
                servings_donated=donated, servings_wasted=relief_expired + direct_waste,
                relief_expired=relief_expired, workers_served=int(sold / rng.uniform(1.4, 1.8)),
                people_fed=people_fed))
            # NGOs nearer the store take a bigger share.
            weights = [1 / (1 + abs(n.lat - s.lat) * 50 + abs(n.lng - s.lng) * 50) for n in ngos]
            left_s, left_p = donated, people_fed
            for i, (n, w) in enumerate(zip(ngos, weights)):
                share = w / sum(weights)
                srv = left_s if i == len(ngos) - 1 else int(donated * share)
                ppl = left_p if i == len(ngos) - 1 else int(people_fed * share)
                left_s, left_p = left_s - srv, left_p - ppl
                db.add(NgoDistributionLog(ngo_id=n.id, log_date=d, servings=srv, people_fed=ppl))


def _new_batch(rng, r, dish, t, *, cooked_ago_h, shelf_h, qty, status="AVAILABLE"):
    name, cat, diet, allergens, storage, price = dish
    cooked = t - timedelta(hours=cooked_ago_h)
    expires, effective = compute_expiry(cooked, shelf_h, storage)
    dispatched = cooked + timedelta(minutes=rng.randint(20, 45))
    received = dispatched + timedelta(minutes=rng.randint(15, 30)) if status != "INCOMING" else None
    entry = (round(rng.uniform(66, 71), 1) if storage == "HOT" else round(rng.uniform(2, 4.5), 1)) if received else None
    return SurplusBatch(
        code=make_code("B"), restaurant_id=r.id, dark_store_id=r.dark_store_id, dish_name=name, category=cat,
        diet=diet, allergens=allergens, storage=storage, price=price, cooked_at=cooked, shelf_life_hours=effective,
        expires_at=expires, dispatched_at=min(dispatched, t), received_at=received, entry_temp_c=entry,
        initial_qty=qty, available_qty=qty, status=status)


def _seed_tonight(db, rng, restaurants, stores, ngos, workers, t) -> None:
    available: list[SurplusBatch] = []
    for r, (_, _, menu) in zip(restaurants, RESTAURANTS):
        for dish in menu:
            hot = dish[4] == "HOT"
            b = _new_batch(rng, r, dish, t, cooked_ago_h=rng.uniform(1.0, 2.5),
                           shelf_h=rng.uniform(5.2, 6.0) if hot else rng.uniform(10, 16), qty=rng.randint(12, 32))
            db.add(b)
            available.append(b)
    # Two dispatches still on the road, waiting for the hub to check them in.
    db.add(_new_batch(rng, restaurants[1], RESTAURANTS[1][2][2], t, cooked_ago_h=0.6, shelf_h=5, qty=18, status="INCOMING"))
    db.add(_new_batch(rng, restaurants[2], RESTAURANTS[2][2][0], t, cooked_ago_h=0.5, shelf_h=5, qty=14, status="INCOMING"))
    db.flush()

    # Tonight's orders so far: popular dishes get ordered more.
    favourites = {"Hyderabadi Chicken Dum Biryani": 5, "Punjabi Samosa": 4, "Butter Chicken": 4, "Chicken 65": 3,
                  "Paneer Tikka Wrap": 3, "Garlic Naan": 3, "Gulab Jamun": 2}
    weights = [favourites.get(b.dish_name, 1) for b in available]
    for _ in range(48):
        store_id = rng.choice(stores).id
        pool = [(b, w) for b, w in zip(available, weights) if b.dark_store_id == store_id and b.available_qty > 0]
        if not pool:
            continue
        created = t - timedelta(minutes=rng.randint(4, 110))
        order = Order(code=make_code("ORD"), user_id=rng.choice(workers).id, dark_store_id=store_id,
                      delivery_address=rng.choice(["Tower B, Embassy Tech Village", "Manipal Hospital staff gate",
                                                   "WeWork Galaxy, Residency Rd", "RMZ Ecospace Block 4",
                                                   "Sakra Hospital night desk", "Bagmane Tech Park, Gate 5",
                                                   "St. John's Hospital, Block C", "HSR BDA Complex security cabin"]),
                      total=0, created_at=created, updated_at=created, status="DELIVERED")
        for b, _ in rng.choices(pool, weights=[w for _, w in pool], k=rng.randint(1, 3)):
            q = min(b.available_qty, rng.randint(1, 3))
            if q <= 0:
                continue
            b.available_qty -= q
            b.sold_qty += q
            order.items.append(OrderItem(batch_id=b.id, dish_name=b.dish_name, qty=q, unit_price=b.price))
            order.total += q * b.price
        if order.items:
            db.add(order)
    for b in available:
        if b.available_qty == 0:
            b.status = "SOLD_OUT"
    # One nearly-gone batch to show scarcity badges.
    scarce = next(b for b in available if b.dish_name == "Hyderabadi Chicken Dum Biryani")
    if scarce.available_qty > 2:
        scarce.sold_qty += scarce.available_qty - 2
        scarce.available_qty = 2

    # Relief pipeline across all four stages.
    relief_specs = [  # restaurant idx, dish idx, minutes to expiry, stage, ngo idx
        (0, 2, 95, "FLAGGED", None), (3, 1, 80, "FLAGGED", None), (2, 1, 105, "FLAGGED", None),
        (3, 2, 70, "CLAIMED", 0), (1, 0, 300, "IN_TRANSIT", 0), (0, 1, 60, "DISTRIBUTED", 1),
    ]
    for ri, di, mins, stage, ngo_i in relief_specs:
        r, dish = restaurants[ri], RESTAURANTS[ri][2][di]
        storage = dish[4]
        shelf = 6.0 if storage == "HOT" else 14.0
        b = _new_batch(rng, r, dish, t, cooked_ago_h=shelf - mins / 60, shelf_h=shelf, qty=rng.randint(20, 34))
        sold = rng.randint(4, 10)
        servings = b.initial_qty - sold
        b.sold_qty, b.available_qty, b.status = sold, 0, "RELIEF"
        b.status_note = "Within 2 h of safety expiry"
        db.add(b)
        db.flush()
        flagged = t - timedelta(minutes=rng.randint(15, 40))
        bundle = ReliefBundle(code=make_code("REL"), batch_id=b.id, dark_store_id=b.dark_store_id, servings=servings,
                              reason="Within 2 h of safety expiry", status=stage, flagged_at=flagged)
        if ngo_i is not None:
            bundle.ngo_id = ngos[ngo_i].id
            bundle.claimed_at = flagged + timedelta(minutes=4)
        if stage in ("IN_TRANSIT", "DISTRIBUTED"):
            bundle.picked_up_at = flagged + timedelta(minutes=12)
        if stage == "DISTRIBUTED":
            bundle.distributed_at = flagged + timedelta(minutes=30)
            bundle.shelter_name = "Ejipura Night Shelter"
            bundle.people_fed = servings - 2
        db.add(bundle)

    # Opening entries for the hub activity log.
    for minutes, kind, msg in [
        (95, "dispatch", "Restaurants began dispatching tonight's surplus to both hubs"),
        (70, "receive", f"{len(available)} batches checked in at verified hot-hold / chill temperatures"),
        (40, "relief", "Batches within 2 h of safety expiry converted to free relief bundles"),
        (35, "receive", "HSR Layout Night Hub checked in Saffron Courtyard and Udupi Night Café dispatches"),
        (30, "claim", "Annapoorna Night Relief claimed 2 relief bundles"),
        (8, "distribute", "Shelter Bridge Trust distributed a bundle at Ejipura Night Shelter"),
    ]:
        db.add(AuditEvent(kind=kind, message=msg, created_at=t - timedelta(minutes=minutes)))

    # Last 30 minutes of sensor telemetry.
    for s in stores:
        for i in range(60, 0, -1):
            at = t - timedelta(seconds=30 * i)
            db.add(TemperatureReading(dark_store_id=s.id, zone="HOT", temp_c=round(rng.gauss(68, 1.1), 1), recorded_at=at))
            db.add(TemperatureReading(dark_store_id=s.id, zone="CHILLED", temp_c=round(rng.gauss(3.2, 0.45), 1), recorded_at=at))


if __name__ == "__main__":
    from app.config import MODEL_PATH

    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
        MODEL_PATH.unlink(missing_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if seed_if_empty(session):
            rows = session.scalar(select(func.count(DailySurplusLog.id)))
            print(f"Seeded {rows} history rows ({HISTORY_DAYS} nights). Default password for every account: {DEFAULT_PASSWORD}")
            print(f"(1 serving ~ {KG_PER_SERVING} kg). The model trains on next app start.")
        else:
            print("Database already seeded. Use --reset to wipe and reseed.")
