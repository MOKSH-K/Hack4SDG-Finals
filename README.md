<div align="center">

# <img src="static/icons/favicon.svg" width="40" align="absmiddle" alt="FoodForAll logo"> FoodForAll

### Predict. Rescue. Feed.

**When the city's kitchens close, FoodForAll opens.**
A late-night food platform that serves restaurant surplus to night-shift workers and routes every unsold meal to NGOs and shelters, before it's unsafe.

[🌐 Live Demo](https://food-for-all-frk.vercel.app/) · [📂 Repository](https://github.com/Ron102008/FoodForAll)

![SDG 2](https://img.shields.io/badge/SDG%202-Zero%20Hunger-355e3b) ![SDG 12.3](https://img.shields.io/badge/SDG%2012.3-Halve%20Food%20Waste-9a6212) ![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-0.162-009688?logo=fastapi&logoColor=white) ![scikit-learn](https://img.shields.io/badge/scikit--learn-1.9-F7931E?logo=scikitlearn&logoColor=white)

</div>

---

## 📖 About the Project

Food delivery apps stop when restaurants close, but restaurants still have surplus cooked food, and **nurses, BPO teams, data-centre staff and security guards** still need to eat. Meanwhile, that surplus usually ends up in a landfill.

**FoodForAll** closes that gap in one night-long loop:

1. **Plan:** a machine-learning model forecasts each restaurant's leftovers *before* it closes, using live weather, festivals, public holidays and footfall.
2. **Collect:** restaurants send surplus to temperature-controlled **dark stores**, which check it in at a verified safe temperature.
3. **Serve:** night-shift workers order fresh restaurant food at a fair price, **11 PM – 5 AM**.
4. **Share:** anything unsold 2 hours before its safe time ends, or at the **3:30 AM cut-off**, becomes a **free donation** that one verified NGO can claim, collect and deliver to a shelter.

Nothing reaches a landfill.

---

## ✨ Key Features

| For | What they get |
|---|---|
| 🍛 **Kitchen managers** | A 7-night outlook of expected leftovers with live weather and festivals filled in automatically, plain-English explanations (*"Diwali festival over-prep: +46% leftovers"*), a closing log and one-click dispatch to a dark store |
| 🏬 **Dark-store managers** | Live hot-cabinet and fridge temperatures, delivery check-in that turns away unsafe food, a safety countdown for every dish, "Close for the night", and an activity log |
| 🌙 **Night-shift workers** | A late-night menu with diet filters, popularity and "Only 2 left!" badges, instant reservation at checkout, and live order tracking |
| 🤝 **NGO coordinators** | An early warning of tonight's expected food before kitchens close, a nearby-hubs map, and claim → pick up → deliver tracking with people-fed logging |
| 📊 **Operations lead** | Access to every portal, SDG impact analytics, forecast accuracy, and safety drills |

**Under the hood:**

- 🔒 **No double-selling, no double-claiming.** Stock is reserved atomically: 25 simultaneous buyers for 5 portions means exactly 5 orders succeed. Only one NGO can claim each donation.
- 🌡️ **Food safety enforced automatically.** Hot food must stay at or above 63 °C (max 6 h) and chilled food at or below 5 °C (max 24 h). Temperature problems shorten a dish's safe time, and serious ones discard it.
- 🌦️ **Zero manual input.** Weather comes from [Open-Meteo](https://open-meteo.com/) (16-day forecast, refreshed hourly). Holidays and festivals come from a generated 2025–2031 Indian and Karnataka calendar.
- 🗣️ **Plain language everywhere.** No codes or jargon on any page, and each person only sees the pages they use.

---

## 🧭 Try It Yourself

Open the **[live site](https://food-for-all-frk.vercel.app/)** and use the **Quick sign-in** list on the login page. Every account uses the password **`foodforall2026`**.

| Role | Account | Start here |
|---|---|---|
| Night-shift worker | `priya.sharma@foodforall.in` | Night Store |
| Kitchen manager | `arvind.rao@foodforall.in` | Restaurant Hub |
| Dark-store manager | `kiran.hegde@foodforall.in` | Dark Store |
| NGO coordinator | `lakshmi.nair@foodforall.in` | NGO Portal |
| Second NGO (try claiming the same donation) | `joseph.mathew@foodforall.in` | NGO Portal |
| Operations lead (all portals) | `nikhil.menon@foodforall.in` | SDG Impact |

> ⏳ **Note:** the hosted version runs on serverless infrastructure. The first visit after a quiet spell can take up to a minute while it wakes up, and sample data resets periodically.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, **FastAPI**, Pydantic v2, SQLAlchemy 2 (SQLite) |
| Machine learning | **scikit-learn** (Random Forest), pandas, NumPy |
| Frontend | Jinja2, **Tailwind CSS**, Alpine.js, Chart.js, Lucide icons |
| Security | Argon2 password hashing, JWT authentication, role-based access |
| Data | Open-Meteo weather API, `holidays` package (Indian calendar) |
| Hosting | **Vercel** |

---

## 🧠 How the Forecast Works

- **Inputs:** weather, evening temperature, day of week, festivals, public holidays, restaurant size and dish category.
- **Model:** a Random Forest trained on **a year of nightly history** across 7 restaurants, using real Bengaluru weather.
- **Explanations:** each prediction comes with a likely range and the reasons behind it (*"Rain: +32% leftovers"*).
- **Accuracy:** tested on 8 weeks of nights the model never saw. Average error is about **4.4 servings per dish category on ordinary nights** and **about 4.9 on festival nights**, against about 6.5 for a simple average.
- **NGO promise:** NGOs are promised a safe portion of the forecast, with a reserve held back in case the night is busier.

---

## 🚀 Getting Started

```bash
# 1. Clone
git clone https://github.com/Ron102008/FoodForAll.git
cd FoodForAll

# 2. Install (Python 3.10+)
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 3. Run
.venv/bin/uvicorn app.main:app --reload
```

Open **http://localhost:8000**. On first start the app creates its database, seeds a year of sample history, fetches live weather and trains the forecast model (about 20 seconds).

```bash
# Run the test suite (34 tests, including race-condition tests)
.venv/bin/python -m pytest -q

# Wipe and reseed the sample data
.venv/bin/python seed_data.py --reset
```

---

## 📁 Project Structure

```
FoodForAll/
├── app/
│   ├── main.py          # FastAPI app, startup, background routing
│   ├── ml/              # forecasting model, calendar, synthetic history
│   ├── services/        # weather, inventory, NGO routing, food safety, impact
│   └── routers/         # API + page routes for each role
├── templates/           # pages: landing, store, restaurant, dark store, NGO, analytics
├── static/              # JS, CSS, icons, vendored libraries
├── scripts/             # calendar + icon generators
├── seed_data.py         # one year of sample history + tonight's scenario
└── tests/               # pytest suite
```

---

## 🌍 Impact

FoodForAll tracks food rescued (kg), CO₂e prevented, night-shift orders served, people fed through NGOs, and progress indices for:

- **SDG 2: Zero Hunger.** The share of donated food that reached people before its safe time ran out.
- **SDG 12.3: Halve Food Waste.** The share of collected surplus that was eaten rather than wasted.

---

## ⚠️ What's Simulated

- **Data:** restaurants, accounts, orders and history are sample data generated for demonstration. The forecasting pipeline is real and retrains on actual closing logs.
- **Sensors:** temperature sensor readings are simulated.
- **Weather and calendar:** both are real (Open-Meteo and the `holidays` package).

---

## 🙏 Acknowledgements

- Weather data by [Open-Meteo.com](https://open-meteo.com/)
- Holiday calendar generated with [python-holidays](https://pypi.org/project/holidays/)

TEAM MEMBERS-
  1. Rohan Naik
  2. Moksh Karamchandani
  3. Hussain

<div align="center">

**Built with 💚 for the night shift**

</div>
