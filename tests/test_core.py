from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import SessionLocal, now
from app.models import ReliefBundle, SurplusBatch, User
from app.services import relief
from app.services.inventory import dispatch_batch, place_order
from app.services.routing import run_sweep


def make_batch(qty=5, hours_left=4.0, storage="HOT", status="AVAILABLE", store_id=1) -> int:
    with SessionLocal() as db:
        t = now()
        shelf = 6.0 if storage == "HOT" else 24.0
        b = dispatch_batch(db, restaurant_id=1, dark_store_id=store_id, dish_name="Test Dish", category="Curries",
                           diet="VEG", allergens="", storage=storage, cooked_at=t - timedelta(hours=shelf - hours_left),
                           shelf_life_hours=shelf, quantity=qty, price=50)
        b.status, b.entry_temp_c, b.received_at = status, 67.0 if storage == "HOT" else 3.0, t
        db.commit()
        return b.id


# ---------- ML ----------

def test_model_trains_on_startup_and_beats_baseline(client):
    info = client.get("/api/v1/ml/model-info").json()
    assert info["ready"] and info["rows"] >= 365 * 7 * 5
    m = info["metrics"]
    assert m["windows"] == 4 and m["mae"] < m["baseline_mae"]
    assert m["mae_ordinary"] is not None and m["festival_test_rows"] >= 0


def test_prediction_explains_rain_and_festival(client):
    r = client.post("/api/v1/predict-surplus", json={
        "date": "2026-11-08", "weather_condition": "Heavy Rain", "temperature": 22, "restaurant_id": 1}).json()
    factors = {d["factor"] for d in r["drivers"]}
    assert {"Heavy Rain", "Festival over-prep"} <= factors
    assert any("Diwali" in d["text"] for d in r["drivers"])
    assert 0 < r["ngo_prenotification_qty"] < r["predicted_surplus_servings"]
    assert r["interval_80"][0] <= r["predicted_surplus_servings"] <= r["interval_80"][1]


def test_zero_surplus_fallback(client):
    r = client.post("/api/v1/predict-surplus", json={
        "date": "2026-10-28", "weather_condition": "Clear", "temperature": 28, "historical_avg_footfall": 10}).json()
    assert r["zero_surplus"] and r["ngo_prenotification_qty"] == 0


def test_rarely_seen_weather_warns(client, monkeypatch):
    from app.ml.forecaster import forecaster
    monkeypatch.setitem(forecaster.meta["weather_support"], "Heatwave", 2)
    r = client.post("/api/v1/predict-surplus", json={
        "date": "2026-10-28", "weather_condition": "Heatwave", "temperature": 40, "restaurant_id": 1}).json()
    assert r["warnings"] and "heatwave nights yet (2 so far)" in r["warnings"][0]


# ---------- concurrency ----------

def test_concurrent_checkout_never_oversells():
    batch_id = make_batch(qty=5)
    with SessionLocal() as db:
        worker = db.scalar(select(User).where(User.role == "worker"))

    def buy(_):
        with SessionLocal() as db:
            try:
                place_order(db, worker, 1, [(batch_id, 1)], "Night desk, Tower B")
                return "ok"
            except HTTPException as e:
                return e.status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(buy, range(25)))
    assert results.count("ok") == 5
    assert all(r in ("ok", 409) for r in results)
    with SessionLocal() as db:
        b = db.get(SurplusBatch, batch_id)
        assert (b.available_qty, b.sold_qty, b.status) == (0, 5, "SOLD_OUT")


def test_multi_line_order_is_all_or_nothing(client, auth):
    plenty, scarce = make_batch(qty=10), make_batch(qty=1)
    r = client.post("/api/v1/store/orders", headers=auth("worker"), json={
        "dark_store_id": 1, "delivery_address": "Gate 3, hospital",
        "items": [{"batch_id": plenty, "qty": 2}, {"batch_id": scarce, "qty": 2}]})
    assert r.status_code == 409
    with SessionLocal() as db:
        assert db.get(SurplusBatch, plenty).available_qty == 10  # rolled back


def test_concurrent_ngo_claims_have_one_winner():
    batch_id = make_batch(qty=12)
    with SessionLocal() as db:
        bundle = relief.convert_to_relief(db, db.get(SurplusBatch, batch_id), "test")
        db.commit()
        bundle_id = bundle.id

    def claim(ngo_id):
        with SessionLocal() as db:
            try:
                relief.claim(db, bundle_id, ngo_id)
                return ngo_id
            except HTTPException as e:
                return e.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, [1, 2, 3] * 4))
    winners = [r for r in results if r in (1, 2, 3)]
    assert len(winners) == 1 and results.count(409) == len(results) - 1
    with SessionLocal() as db:
        assert db.get(ReliefBundle, bundle_id).ngo_id == winners[0]


# ---------- routing & food safety ----------

def test_sweep_routes_near_expiry_and_expires_stale():
    near = make_batch(qty=8, hours_left=1.0)   # inside the 2 h relief window
    fresh = make_batch(qty=8, hours_left=5.0)
    with SessionLocal() as db:
        stale = db.get(SurplusBatch, make_batch(qty=3, hours_left=3))
        stale.expires_at = now() - timedelta(minutes=1)
        db.commit()
        stale_id = stale.id
    with SessionLocal() as db:
        run_sweep(db, telemetry=False)
    with SessionLocal() as db:
        assert db.get(SurplusBatch, near).status == "RELIEF"
        assert db.scalar(select(ReliefBundle).where(ReliefBundle.batch_id == near)).servings == 8
        assert db.get(SurplusBatch, fresh).status == "AVAILABLE"
        assert db.get(SurplusBatch, stale_id).status == "EXPIRED"


def test_cutoff_routes_everything_left(client, auth):
    make_batch(qty=4, hours_left=5)
    r = client.post("/api/v1/darkstore/sweep", headers=auth("darkstore"), json={"force_cutoff": True})
    assert r.status_code == 200 and r.json()["to_relief"] >= 1
    with SessionLocal() as db:
        assert not db.scalars(select(SurplusBatch).where(SurplusBatch.status == "AVAILABLE",
                                                         SurplusBatch.available_qty > 0)).all()


def test_relief_items_cannot_be_bought(client, auth):
    with SessionLocal() as db:
        b = db.scalar(select(SurplusBatch).where(SurplusBatch.status == "RELIEF"))
    r = client.post("/api/v1/store/orders", headers=auth("worker"), json={
        "dark_store_id": b.dark_store_id, "delivery_address": "Gate 3, hospital", "items": [{"batch_id": b.id, "qty": 1}]})
    assert r.status_code == 409


def test_shelf_life_capped_by_storage_class(client, auth):
    r = client.post("/api/v1/restaurant/dispatch", headers=auth("restaurant"), json={
        "dish_name": "Dal", "dish_category": "Curries", "diet": "VEG", "storage": "HOT",
        "cooked_at": now().isoformat(), "shelf_life_hours": 12, "quantity": 10})
    assert r.status_code == 201
    assert r.json()["shelf_life_hours"] == 6.0 and r.json()["status"] == "INCOMING"


def test_dispatch_of_already_expired_food_is_refused(client, auth):
    r = client.post("/api/v1/restaurant/dispatch", headers=auth("restaurant"), json={
        "dish_name": "Old Dal", "dish_category": "Curries", "diet": "VEG", "storage": "HOT",
        "cooked_at": (now() - timedelta(hours=7)).isoformat(), "shelf_life_hours": 6, "quantity": 10})
    assert r.status_code == 422


def test_intake_rejects_out_of_range_temperature(client, auth):
    batch_id = make_batch(qty=5, status="INCOMING")
    r = client.post(f"/api/v1/darkstore/batches/{batch_id}/receive", headers=auth("darkstore"), json={"entry_temp_c": 55})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert not r.json()["haccp"]["verified"]


def test_override_cannot_exceed_safety_cap(client, auth):
    batch_id = make_batch(qty=5, hours_left=3)
    h = auth("darkstore")
    too_late = (now() + timedelta(hours=8)).isoformat()
    r = client.post(f"/api/v1/darkstore/batches/{batch_id}/shelf-life", headers=h, json={"expires_at": too_late, "reason": "test"})
    assert r.status_code == 422
    sooner = (now() + timedelta(hours=1)).isoformat()
    r = client.post(f"/api/v1/darkstore/batches/{batch_id}/shelf-life", headers=h, json={"expires_at": sooner, "reason": "probe check"})
    assert r.status_code == 200 and r.json()["shelf_life_overridden"]


# ---------- API / auth ----------

def test_zero_surplus_night_is_logged(client, auth):
    r = client.post("/api/v1/restaurant/daily-log", headers=auth("restaurant"), json={
        "closing_time": "23:30", "actual_footfall": 350,
        "entries": [{"dish_category": "Curries", "prep_count": 40, "sold_count": 40}]})
    assert r.status_code == 200 and r.json()["zero_surplus"]


def test_sold_cannot_exceed_prep(client, auth):
    r = client.post("/api/v1/restaurant/daily-log", headers=auth("restaurant"), json={
        "closing_time": "23:30", "actual_footfall": 350,
        "entries": [{"dish_category": "Curries", "prep_count": 10, "sold_count": 40}]})
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/restaurant/overview", "/api/v1/darkstore/overview", "/api/v1/ngo/overview"])
def test_partner_apis_require_auth(client, path):
    assert client.get(path).status_code == 401


def test_roles_are_enforced(client, auth):
    assert client.get("/api/v1/ngo/overview", headers=auth("worker")).status_code == 403
    assert client.get("/api/v1/darkstore/overview", headers=auth("ngo")).status_code == 403


def test_worker_registration_and_duplicate(client):
    body = {"name": "Test Nurse", "email": "nurse@example.org", "password": "longpassword"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert client.post("/api/v1/auth/register", json=body).status_code == 409
    assert client.post("/api/v1/auth/login", json={"email": body["email"], "password": "wrong"}).status_code == 401


def test_public_pages_and_impact(client):
    for path in ["/", "/login", "/store", "/restaurant", "/darkstore", "/ngo", "/analytics"]:
        assert client.get(path).status_code == 200
    s = client.get("/api/v1/impact/summary").json()
    assert s["meals_saved_kg"] > 0 and 0 <= s["sdg12_index"] <= 100


def test_minor_excursion_shortens_clock_but_stays_verified():
    from app.services.serializers import batch_out
    from app.services.telemetry import _apply_excursion

    batch_id = make_batch(qty=6, hours_left=10, storage="CHILLED")
    with SessionLocal() as db:
        t = now()
        _apply_excursion(db, 1, "CHILLED", 7.8, t)
        db.commit()
        b = db.get(SurplusBatch, batch_id)
        assert b.temp_breach and b.expires_at <= t + timedelta(hours=2)
        assert batch_out(b, t)["haccp"]["verified"]


def test_critical_excursion_discards():
    batch_id = make_batch(qty=6, hours_left=10, storage="CHILLED")
    from app.services.telemetry import _apply_excursion
    with SessionLocal() as db:
        _apply_excursion(db, 1, "CHILLED", 12.5, now())
        db.commit()
        assert db.get(SurplusBatch, batch_id).status == "DISCARDED"


# ---------- weather ----------

def test_weather_classification():
    from app.services.weather import classify
    assert classify(39.0, 0, [0] * 6) == "Heatwave"
    assert classify(30.0, 6.5, [63] * 6) == "Heavy Rain"
    assert classify(28.0, 1.0, [61, 0, 0, 0, 0, 0]) == "Rain"
    assert classify(26.0, 0, [3, 3, 3, 1, 0, 0]) == "Cloudy"
    assert classify(26.0, 0, [0, 1, 1, 2, 0, 0]) == "Clear"


def test_weather_refresh_stores_live_nights(monkeypatch):
    from datetime import date
    from app.services import weather

    hours = [f"2026-10-20T{h:02d}:00" for h in range(24)] + [f"2026-10-21T{h:02d}:00" for h in range(24)]
    payload = {
        "hourly": {"time": hours, "temperature_2m": [22.0] * 48,
                   "precipitation": [0.0] * 24 + [0.0] * 18 + [2.0] * 6,   # heavy evening rain on the 21st
                   "weather_code": [0] * 24 + [0] * 18 + [63] * 6},
        "daily": {"time": ["2026-10-20", "2026-10-21"], "temperature_2m_max": [29.0, 27.0]},
    }
    monkeypatch.setattr(weather, "WEATHER_PROVIDER", "open-meteo")
    monkeypatch.setattr(weather, "_fetch", lambda: payload)
    with SessionLocal() as db:
        assert weather.refresh(db, force=True) == 2
        assert weather.conditions(db, date(2026, 10, 21))["weather_condition"] == "Heavy Rain"
        clear = weather.conditions(db, date(2026, 10, 20))
        assert (clear["weather_condition"], clear["temperature"], clear["source"]) == ("Clear", 22.0, "live")
        assert weather.conditions(db, date(2026, 10, 21))["evening_rain_mm"] == 12.0


def test_weather_falls_back_when_api_down(monkeypatch, client):
    from app.services import weather

    def boom():
        raise OSError("network unreachable")
    monkeypatch.setattr(weather, "WEATHER_PROVIDER", "open-meteo")
    monkeypatch.setattr(weather, "_fetch", boom)
    monkeypatch.setattr(weather, "_fetch_archive", lambda *a: boom())
    with SessionLocal() as db:
        assert weather.refresh(db, force=True) == 0
    r = client.get("/api/v1/weather?on=2027-03-01").json()
    assert r["source"] == "estimate" and r["weather_condition"]


def test_prediction_fills_weather_automatically(client):
    r = client.post("/api/v1/predict-surplus", json={"date": "2026-11-08", "restaurant_id": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["weather"]["weather_condition"] and body["weather"]["overridden"] is False
    assert body["inputs"]["festival_name"] and "Diwali" in body["inputs"]["festival_name"]


def test_outlook_lists_upcoming_nights(client, auth):
    r = client.get("/api/v1/restaurant/outlook?days=7", headers=auth("restaurant"))
    assert r.status_code == 200, r.text
    nights = r.json()["nights"]
    assert len(nights) == 7
    assert all({"weather_condition", "temperature", "predicted_surplus", "holiday", "festival"} <= n.keys() for n in nights)


def test_calendar_covers_next_five_years():
    from datetime import date
    from app.ml.calendar_data import CALENDAR_RANGE, FESTIVALS, HOLIDAYS
    assert CALENDAR_RANGE == (date(2025, 1, 1), date(2031, 12, 31))
    for year in range(2025, 2032):
        assert HOLIDAYS[date(year, 1, 26)] == "Republic Day"
        assert any("Diwali" in n for d, n in FESTIVALS.items() if d.year == year)
        assert any("Holi" in n for d, n in HOLIDAYS.items() if d.year == year)


# ---------- plain language for end users ----------

def test_validation_errors_are_plain_sentences(client, auth):
    r = client.post("/api/v1/store/orders", headers=auth("worker"),
                    json={"dark_store_id": 1, "items": [{"batch_id": 1, "qty": 1}], "delivery_address": "x"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Delivery address is too short."
    r = client.post("/api/v1/restaurant/daily-log", headers=auth("restaurant"), json={
        "closing_time": "23:30", "actual_footfall": 350,
        "entries": [{"dish_category": "Curries", "prep_count": 10, "sold_count": 40}]})
    assert r.json()["detail"] == "Sold can't be more than prepared."


def test_activity_log_is_per_hub_and_free_of_codes(client, auth):
    import re
    events = client.get("/api/v1/darkstore/overview", headers=auth("darkstore")).json()["events"]
    assert events
    for e in events:
        assert not re.search(r"\b(B|REL|ORD)-[0-9A-F]{6}\b|#\d|@|\b[A-Z]{4,}_?[A-Z]*\b", e["message"]), e["message"]
    with SessionLocal() as db:
        from app.models import AuditEvent
        other = db.scalars(select(AuditEvent).where(AuditEvent.dark_store_id != 1)).all()
    assert other and not {e.message for e in other} & {e["message"] for e in events}


def test_operations_lead_can_open_restaurant_hub(client):
    r = client.post("/api/v1/auth/login", json={"email": "nikhil.menon@foodforall.in", "password": "foodforall2026"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/v1/restaurant/overview", headers=h).status_code == 200
