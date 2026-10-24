"""FoodForAll - late-night surplus food redistribution platform.

Run:  uvicorn app.main:app --reload
"""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, SERVERLESS, SWEEP_INTERVAL_SECONDS
from app.database import Base, SessionLocal, engine
from app.ml.forecaster import forecaster
from app.routers import analytics, auth, darkstore, ngo, pages, restaurant, store
from app.services.forecasting import retrain
from app.services import weather
from app.services.routing import run_sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("foodforall")

_boot_lock = threading.Lock()
_booted = False
_sweep_lock = threading.Lock()
_last_sweep = 0.0


def bootstrap() -> None:
    """Create tables, seed data on first run, and make sure the model is trained."""
    from seed_data import seed_if_empty

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seeded = seed_if_empty(db)
        if seeded or not forecaster.load():
            log.info("Training surplus forecaster...")
            retrain(db)
        else:
            log.info("Loaded saved surplus model (trained %s)", forecaster.meta.get("trained_at"))


def ensure_ready() -> None:
    """Bootstrap exactly once per process, whichever comes first: startup or the first request."""
    global _booted
    if _booted:
        return
    with _boot_lock:
        if not _booted:
            bootstrap()
            _booted = True


def _sweep_once() -> None:
    global _last_sweep
    with SessionLocal() as db:
        weather.refresh(db)   # rate-limited to once an hour; no-op otherwise
        run_sweep(db)
    _last_sweep = time.monotonic()


def _sweep_if_due() -> None:
    """Serverless stand-in for the background loop: sweep on a request if the last one is stale."""
    if time.monotonic() - _last_sweep < SWEEP_INTERVAL_SECONDS or not _sweep_lock.acquire(blocking=False):
        return
    try:
        _sweep_once()
    except Exception:
        log.exception("Routing sweep failed")
    finally:
        _sweep_lock.release()


async def sweeper() -> None:
    while True:
        try:
            await asyncio.to_thread(_sweep_once)
        except Exception:
            log.exception("Routing sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(ensure_ready)
    task = None if SERVERLESS else asyncio.create_task(sweeper())
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="FoodForAll API",
    description="Late-night surplus food redistribution: hotels -> dark stores -> night workers -> NGOs.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
for r in (auth.router, restaurant.router, darkstore.router, store.router, ngo.router, analytics.router, pages.router):
    app.include_router(r)


@app.middleware("http")
async def ready_and_swept(request: Request, call_next):
    # Covers runtimes that skip ASGI lifespan events, and drives the sweep when there is no background loop.
    if not request.url.path.startswith("/static/"):
        await asyncio.to_thread(ensure_ready)
        if SERVERLESS:
            await asyncio.to_thread(_sweep_if_due)
    return await call_next(request)


# Form-field names as people see them on screen, for validation messages.
FIELD_LABELS = {
    "delivery_address": "Delivery address", "email": "Email", "password": "Password", "name": "Name",
    "dish_name": "Dish name", "quantity": "Servings", "shelf_life_hours": "Shelf life", "price": "Price",
    "cooked_at": "Cooked at", "entry_temp_c": "Temperature", "reason": "Reason", "shelter_name": "Shelter name",
    "people_fed": "People fed", "closing_time": "Closing time", "actual_footfall": "Diners tonight",
    "prep_count": "Prepared", "sold_count": "Sold", "expires_at": "Safe-until time", "qty": "Quantity",
    "items": "Cart", "date": "Date", "allergens": "Allergens",
}


def _friendly(e: dict) -> str:
    field = next((str(p) for p in reversed(e["loc"]) if isinstance(p, str) and p not in ("body", "query", "path")), "")
    label = FIELD_LABELS.get(field, "This field")
    kind, ctx = e.get("type", ""), e.get("ctx") or {}
    if kind == "value_error":
        return str(ctx.get("error") or e["msg"]).removeprefix("Value error, ")
    if kind == "missing":
        return f"{label} is required"
    if kind == "string_too_short":
        return f"{label} is too short"
    if kind == "string_too_long":
        return f"{label} is too long"
    if kind in ("greater_than_equal", "greater_than"):
        return f"{label} must be at least {ctx.get('ge', ctx.get('gt'))}"
    if kind in ("less_than_equal", "less_than"):
        return f"{label} can be at most {ctx.get('le', ctx.get('lt'))}"
    if kind == "too_short" and field == "items":
        return "Your cart is empty"
    return f"{label} doesn't look right"


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    """Turn validation errors into one plain sentence for the on-screen messages (no field paths or codes)."""
    msgs = list(dict.fromkeys(_friendly(e) for e in exc.errors()))
    return JSONResponse(status_code=422, content={"detail": ". ".join(msgs) + "."})
