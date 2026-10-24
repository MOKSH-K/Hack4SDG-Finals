import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated database + model per test session; must be set before `app` is imported.
_tmp = Path(tempfile.mkdtemp(prefix="ffa-test-"))
os.environ["FFA_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["FFA_MODEL_PATH"] = str(_tmp / "model.joblib")
os.environ["FFA_OPEN_24X7"] = "1"
os.environ["FFA_SWEEP_INTERVAL"] = "3600"  # keep the background sweep out of the way
os.environ["FFA_WEATHER_PROVIDER"] = "offline"  # no network in tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def client():
    """Runs the app lifespan (tables, seed data, model training) once for every test."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    emails = {"worker": "priya.sharma", "restaurant": "arvind.rao", "darkstore": "kiran.hegde", "ngo": "lakshmi.nair"}

    def headers(account: str) -> dict:
        r = client.post("/api/v1/auth/login", json={"email": f"{emails[account]}@foodforall.in", "password": "foodforall2026"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}
    return headers
