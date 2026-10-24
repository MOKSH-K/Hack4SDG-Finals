# FoodForAll 🌙

**When the city's kitchens close, FoodForAll opens.**

Swiggy and Zomato stop delivering when restaurants shut, but restaurants still have surplus cooked food and night-shift workers still need to eat. FoodForAll connects the two and makes sure nothing is wasted:

1. **Predict:** an ML model forecasts each partner restaurant's surplus before it closes, using weather, holidays, festivals and footfall. NGOs see tonight's volume early.
2. **Collect:** restaurants dispatch surplus (with allergens, storage class and cook time) to temperature-controlled **dark stores**, which check it in at a verified temperature.
3. **Serve:** night-shift workers (nurses, BPO, data-centre and security staff) order from live dark-store stock, **11 PM – 5 AM**.
4. **Donate:** anything within 2 h of its safety expiry, or unsold at the **3:30 AM cut-off**, is automatically locked from sale and becomes a free **relief bundle**. Exactly one NGO can claim it, collect it and distribute it.

Supports **UN SDG 2 (Zero Hunger)** and **SDG 12.3 (halve food waste)**.

---

## Quick start

Requires Python 3.10+ (developed on 3.14).

```bash
git clone https://github.com/Hussain13620/FoodForAll.git
cd FoodForAll
python3 -m venv .venv          # or: virtualenv .venv  (if python3-venv isn't installed)
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Open **http://localhost:8000**. On first start the app automatically:

- creates the SQLite database in `data/`;
- seeds 7 restaurants, 3 dark stores, 4 NGOs, 23 user accounts, **a year (365 nights) of history** and a live "tonight" scenario;
- trains the surplus model (about 15–20 s on first start; later starts load the saved model) and saves it to `data/surplus_model.joblib`.

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\uvicorn`.

To wipe everything and reseed: `.venv/bin/python seed_data.py --reset` (the model retrains on the next start).

### Seeded accounts (password `foodforall2026` for all)

| Role | Name | Email | Lands on |
|---|---|---|---|
| Night-shift worker | Priya Sharma | `priya.sharma@foodforall.in` | `/store` |
| Restaurant (Midnight Biryani Co.) | Arvind Rao | `arvind.rao@foodforall.in` | `/restaurant` |
| Restaurant (Tandoor Tales / Saffron Courtyard) | Harpreet Kaur / Deepa Kulkarni | `harpreet.kaur@…` / `deepa.kulkarni@…` | `/restaurant` |
| Dark-store manager (Koramangala / Whitefield / HSR) | Kiran Hegde / Farhan Ali / Sneha Iyer | `kiran.hegde@…` / `farhan.ali@…` / `sneha.iyer@…` | `/darkstore` |
| NGO coordinator (Annapoorna / Shelter Bridge / Sahaya) | Lakshmi Nair / Joseph Mathew / Ritu Bansal | `lakshmi.nair@…` / `joseph.mathew@…` / `ritu.bansal@…` | `/ngo` |
| Operations lead (every portal) | Nikhil Menon | `nikhil.menon@foodforall.in` | `/analytics` |

All emails are `@foodforall.in`. The login page's **Quick sign-in** list fills in the main accounts.

---

## 5-minute walkthrough

1. **`/`** Live impact tickers (kg rescued, CO₂e, workers served, people fed) and the SDG 2 / SDG 12 indices.
2. **`/restaurant`** (restaurant account)
   - Toggle *Heavy Rain* or set the date to **8 Nov 2026 (Diwali)**. The forecast updates with explained drivers, e.g. *"Diwali festival over-prep (+46%)"*, *"Heavy Rain (+45%)"*.
   - Enter a closing log, or click **Nothing left tonight** to see the zero-surplus path.
   - Dispatch a batch with a 12 h hot-held shelf life and watch it get capped at 6 h.
3. **`/darkstore`** (dark-store account)
   - **Check in** the incoming batch.
   - **Simulate fridge fault**: the chiller goes red and affected batches get shortened safety clocks.
   - **Simulate 3:30 AM cut-off**: all unsold stock becomes relief bundles.
4. **`/store`** (worker account) Add dishes and check out. Stock is reserved atomically; try buying more than is left.
5. **`/ngo`** (NGO account)
   - Early-warning forecast and proximity map.
   - Claim a bundle, then move it through **Picked up → Distributed**.
   - Sign in as `joseph.mathew@…` in a private window and try to claim the same bundle: it's refused.
6. **`/analytics`** year-long timeline, day-of-week, weather and festival effects, NGO totals, model card.

**Open 24×7** (on by default, not shown anywhere in the UI) lets people order outside 11 PM–5 AM and limits the automatic cut-off to 03:30–05:00, so daytime walkthroughs aren't emptied. Set `FFA_OPEN_24X7=0` for strict night hours.

---

## Architecture

```
FoodForAll/
├── app/
│   ├── main.py              FastAPI app, startup (seed → train), background sweep loop
│   ├── config.py            hours, cut-off, food-safety limits, impact factors (FFA_* env overrides)
│   ├── database.py          SQLAlchemy engine (SQLite WAL), sessions, local-time helpers
│   ├── models.py            ORM: batches, orders, relief bundles, telemetry, history logs…
│   ├── schemas.py           Pydantic v2 request validation
│   ├── security.py          Argon2 password hashing, JWT, role guards
│   ├── ml/
│   │   ├── forecaster.py    RandomForest pipeline, uncertainty, counterfactual explanations
│   │   ├── synthetic.py     generative model of a restaurant night (seeds training data)
│   │   └── calendar_data.py Indian holidays/festivals + offline weather simulator
│   ├── services/
│   │   ├── inventory.py     dispatch, intake, atomic checkout, shelf-life override
│   │   ├── relief.py        NGO pipeline with claim locking
│   │   ├── routing.py       periodic sweep: expiry → relief, cut-off, delivery progress
│   │   ├── telemetry.py     simulated sensors + excursion handling
│   │   ├── safety.py        shelf-life caps, safety clock, HACCP tags, operating hours
│   │   ├── forecasting.py   training data, rolling footfall, NGO early warning
│   │   └── impact.py        SDG metrics
│   └── routers/             auth, restaurant (+ /predict-surplus), darkstore, store, ngo, analytics, pages
├── templates/               Jinja2 + Tailwind + Alpine.js pages
├── static/                  app.js (API client, icons, chart theme), css, vendored libraries
├── seed_data.py             one year of history + live scenario
└── tests/                   pytest suite (24 tests, including race conditions)
```

**Stack:**
- **Backend:** FastAPI (ASGI) with Pydantic v2, and SQLAlchemy 2 on SQLite.
- **Security:** Argon2id passwords via `pwdlib`, JWT via PyJWT.
- **ML:** scikit-learn, pandas, NumPy.
- **Frontend:** Tailwind, Alpine.js, Chart.js, Lucide. The frontend libraries are vendored in `static/vendor/`, so the app runs offline.

Interactive API docs: **http://localhost:8000/docs**.

### Key API endpoints

| Method | Path | Who |
|---|---|---|
| POST | `/api/v1/predict-surplus` | public |
| GET | `/api/v1/ml/model-info` · POST `/api/v1/ml/retrain` | public · restaurant |
| GET / POST | `/api/v1/restaurant/overview`, `/daily-log`, `/dispatch` | restaurant |
| GET / POST | `/api/v1/darkstore/overview`, `/batches/{id}/receive`, `/shelf-life`, `/discard`, `/sweep`, `/telemetry/excursion` | darkstore |
| GET / POST | `/api/v1/store/menu`, `/orders`, `/orders/mine` | public menu · worker |
| GET / POST | `/api/v1/ngo/overview`, `/bundles/{id}/claim`, `/pickup`, `/distribute`, `/release` | ngo |
| GET | `/api/v1/impact/summary`, `/api/v1/analytics/dashboard` | public |

---

## How the hard parts work

### Surplus forecasting (Module A)
- **Features:**
  - day of week and weekend flag
  - weather condition (Clear / Cloudy / Rain / Heavy Rain / Heatwave) and temperature
  - public holiday and festival flags, looked up from the calendar automatically
  - 28-night rolling average footfall
  - dish category
- **Target:** surplus *per 100 diners*, scaled back by footfall at prediction time. This lets one model handle restaurants of any size, including ones smaller than any in the training data.
- **Validation:** a rolling backtest over 4 windows of 14 nights each (the last 8 weeks). Each window is predicted by a model trained only on the nights before it. Errors are pooled and reported separately for ordinary and festival nights, because one fortnight is too small and seasonal to judge on. On the seeded data, average error per row (restaurant × dish category × night, about 22 servings of surplus):

  | Predictor | Overall | Ordinary nights | Festival nights |
  |---|---|---|---|
  | This model | ≈ 4.5 servings | ≈ 4.4 | ≈ 4.9 |
  | Per-category average | ≈ 6.5 | | |
  | Oracle that knows the generating formula | ≈ 4.1 | ≈ 4.2 | ≈ 4.0 |

  R² is about 0.75. Most of the remaining error is the deliberate night-to-night randomness in the seed data (the oracle's 4.1 is the floor), so the model is within about 0.4 servings of the best achievable.
- **Uncertainty:** the spread across the forest's trees gives an 80% interval. The NGO pre-notification quantity holds back a 10–40% safety buffer based on that spread, so NGOs are never promised food that doesn't arrive.
- **Explanations:** counterfactual. Each driver is re-predicted with that factor reset to a neutral night (clear, 28 °C, mid-week, no holiday or festival), and the difference is reported as text such as *"Heavy Rain (+45% surplus expected)"*.
- **Edge cases:**
  - A forecast under 3 servings returns an explicit zero-surplus result, and NGOs are not pre-notified.
  - Weather the model has barely seen (for example Heatwave, absent from Aug–Oct history) comes with a low-confidence warning.

### Automated weather
- **Source:** [Open-Meteo](https://open-meteo.com/) (forecast up to 16 days ahead). It's free, needs no API key, and its licence requires the attribution shown in the footer.
- **Refresh:** a background job pulls the forecast for the city about once an hour (`app/services/weather.py`). Each night is condensed into one condition from the 6–11 PM window, when diners decide whether to go out:

  | Condition | Rule |
  |---|---|
  | Heatwave | day's peak ≥ 38 °C |
  | Heavy Rain | ≥ 4 mm of rain in the evening |
  | Rain | ≥ 0.5 mm, or at least two rainy hours |
  | Cloudy | mostly overcast, foggy or drizzly hours |
  | Clear | anything else |

- **Used automatically by:** the restaurant forecast and its 7-night outlook, the NGO early warning, and restaurant closing logs. Kitchen managers never enter weather. The prediction API still accepts optional `weather_condition`/`temperature` overrides for what-if checks.
- **Seeding:** the year of history uses real past weather from Open-Meteo's archive endpoint.
- **Fallback:** if the API is unreachable, the app uses cached data, then a seasonal estimate. Set `FFA_WEATHER_PROVIDER=offline` to never call the network (the tests do this).

### Holiday and festival calendar (2025–2031)
- **Data file:** `app/ml/india_calendar.json` holds public holidays (national plus Karnataka) and festivals for 2025–2031. The forecaster uses both automatically.
- **Generation:** it's built by `scripts/build_calendar.py` from the maintained [`holidays`](https://pypi.org/project/holidays/) package, which computes lunar and Islamic dates, plus a few fixed dates (Kannada Rajyotsava, Halloween, New Year's Eve, Valentine's Day). Navratri is derived as the nine nights before Dussehra.
- **Uncertain dates:** far-future Islamic dates are marked "(estimated)" because they depend on moon sighting.
- **Extending:** to cover more years, edit `LAST_YEAR` and run `.venv/bin/python scripts/build_calendar.py` (needs `requirements-dev.txt`).

### Atomic reservations (Modules B and C)
- **Checkout:** each line is a conditional `UPDATE … SET available_qty = available_qty - :q WHERE available_qty >= :q AND status = 'AVAILABLE' AND expires_at > now`. If any line matches 0 rows, the whole order rolls back. On SQLite, writes run inside `BEGIN IMMEDIATE`.
- **NGO claims:** `UPDATE … WHERE status = 'FLAGGED'`, so exactly one claim can win.
- **Moving stock to relief:** the conversion is conditioned on the quantity it read, so a checkout that slips in between can never cause sold food to be donated.
- **Tests:** `tests/test_core.py` fires 25 concurrent checkouts at 5 portions (exactly 5 succeed) and 12 concurrent NGO claims at one bundle (exactly 1 wins).

### Food safety (Module D)

| Rule | Value |
|---|---|
| Hot-held | ≥ 63 °C, max 6 h after cooking |
| Chilled | ≤ 5 °C, max 24 h after cooking |
| Declared shelf life | capped at the storage-class maximum |
| Intake temperature outside range | batch is rejected |
| Moves to NGO relief | 2 h before safety expiry, or at the 03:30 cut-off |
| Minor excursion (chilled ≤ 10 °C / hot ≥ 50 °C) | use-by pulled to now + 2 h |
| Critical excursion | batch is discarded and never reaches anyone |
| Manual shelf-life override | may shorten freely; may never extend past the safety cap, or extend at all after an excursion |

- **HACCP tag:** every batch carries a verification tag (`HACCP-…`, a hash of the batch's safety-relevant fields) together with its list of checks.
- **Background sweep:** every 30 s it records simulated sensor readings, routes or expires batches, expires uncollected relief bundles, and moves orders through Placed → Packed → On the way → Delivered.

### Impact metrics

| Metric | Definition |
|---|---|
| Meals saved | servings sold to night workers + servings distributed by NGOs |
| kg rescued | meals saved × 0.35 kg per serving |
| CO₂e prevented | kg rescued × 2.5 kg CO₂e per kg of food kept out of landfill |
| SDG 12 index | share of dark-store intake that was eaten rather than wasted |
| SDG 2 index | share of relief food that reached people before its expiry |

Both conversion factors are stated assumptions; edit them in `app/config.py`.

---

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FFA_OPEN_24X7` | `1` | Allow ordering 24×7; limit the automatic cut-off to 03:30–05:00 |
| `FFA_DATABASE_URL` | `sqlite:///data/foodforall.db` | SQLAlchemy URL |
| `FFA_JWT_SECRET` | random per process | **Set this in production.** Otherwise tokens are invalidated on restart. |
| `FFA_RELIEF_LEAD_HOURS` | `2` | How long before expiry food moves to NGO relief |
| `FFA_SWEEP_INTERVAL` | `30` | Background sweep period (seconds) |
| `FFA_TIMEZONE` | `Asia/Kolkata` | All operational times are local to this zone |
| `FFA_WEATHER_PROVIDER` | `open-meteo` | `offline` = seasonal estimate only, no network |
| `FFA_WEATHER_LAT` / `FFA_WEATHER_LNG` | Bengaluru | Location used for weather |
| `FFA_WEATHER_REFRESH_MINUTES` | `60` | How often to refresh the forecast |

## Tests

```bash
.venv/bin/python -m pytest -q
```

## What's simulated (be upfront with judges)

- **Data:** all seeded data (partners, accounts, orders and history) is synthetic and fictional, though nothing in the UI labels it so. It is produced by an explicit generative model (`app/ml/synthetic.py`). The ML pipeline is real and will retrain on real closing logs: every restaurant log is stored and used by `POST /api/v1/ml/retrain`.
- **Weather:** real (Open-Meteo), with an offline seasonal estimate only as a fallback.
- **Calendar:** generated from the `holidays` package; far-future Islamic dates are estimates.
- **Sensors:** temperature readings are simulated.
- **Map:** the NGO map is schematic (no tile server), so the app stays offline.
- **Fonts:** load from Google Fonts when online and fall back to system fonts when offline.
- **Tailwind:** uses the in-browser Play CDN build. For production, compile the CSS with the Tailwind CLI.
- **Partner onboarding:** self-registration is for night workers only. Hotels, dark stores and NGOs are onboarded by an admin, since they need verification.
