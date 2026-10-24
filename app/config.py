"""Central configuration. Every value can be overridden with an FFA_* environment variable."""
import os
import secrets
from datetime import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Serverless (Vercel): the bundle is read-only and there is no long-lived process, so data lives
# in /tmp and periodic work runs on incoming requests instead of a background loop.
SERVERLESS = bool(os.getenv("VERCEL"))
DATA_DIR = Path(os.getenv("FFA_DATA_DIR") or ("/tmp/foodforall" if SERVERLESS else BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATABASE_URL = os.getenv("FFA_DATABASE_URL", f"sqlite:///{DATA_DIR / 'foodforall.db'}")
MODEL_PATH = Path(os.getenv("FFA_MODEL_PATH", str(DATA_DIR / "surplus_model.joblib")))

# JWT. A random secret per process is fine for local runs; set FFA_JWT_SECRET in production.
JWT_SECRET = os.getenv("FFA_JWT_SECRET") or secrets.token_urlsafe(48)
JWT_ALGORITHM = "HS256"
JWT_TTL_MINUTES = int(os.getenv("FFA_JWT_TTL_MINUTES", "720"))

# All operational timestamps are naive local time in this zone.
TIMEZONE = os.getenv("FFA_TIMEZONE", "Asia/Kolkata")

# Late-night store window (crosses midnight).
STORE_OPEN = time(23, 0)
STORE_CLOSE = time(5, 0)
# Hard NGO cut-off: anything still unsold at this time is routed to relief.
NGO_CUTOFF = time(3, 30)
# Items are pulled from sale this long before their safety expiry and become relief bundles.
RELIEF_LEAD_HOURS = float(os.getenv("FFA_RELIEF_LEAD_HOURS", "2"))

# Open 24x7 lets people order outside 11 PM-5 AM (handy for daytime walkthroughs).
OPEN_24X7 = _env_bool("FFA_OPEN_24X7", True)

# Weather: Open-Meteo (free, no API key). Set FFA_WEATHER_PROVIDER=offline to use the seasonal estimate only.
WEATHER_PROVIDER = os.getenv("FFA_WEATHER_PROVIDER", "open-meteo")
WEATHER_LAT = float(os.getenv("FFA_WEATHER_LAT", "12.9716"))   # Bengaluru city centre
WEATHER_LNG = float(os.getenv("FFA_WEATHER_LNG", "77.5946"))
WEATHER_REFRESH_MINUTES = int(os.getenv("FFA_WEATHER_REFRESH_MINUTES", "60"))
WEATHER_TIMEOUT_SECONDS = float(os.getenv("FFA_WEATHER_TIMEOUT", "8"))

# Background sweep cadence (expiry routing, telemetry, delivery progress).
SWEEP_INTERVAL_SECONDS = int(os.getenv("FFA_SWEEP_INTERVAL", "30"))

# Food-safety rules (HACCP-style; see README for sources and assumptions).
HOT_HOLD_MIN_C = 63.0      # hot-held food must stay at or above this
CHILL_MAX_C = 5.0          # refrigerated food must stay at or below this
MAX_SHELF_LIFE_HOURS = {"HOT": 6.0, "CHILLED": 24.0}
BREACH_GRACE_HOURS = 2.0   # after a temperature excursion, food must be used within this window
CRITICAL_HOT_C = 50.0      # below this a hot batch is discarded outright
CRITICAL_CHILL_C = 10.0    # above this a chilled batch is discarded outright

# Impact conversion factors (assumptions, documented in README).
KG_PER_SERVING = 0.35
CO2E_PER_KG_FOOD = 2.5
