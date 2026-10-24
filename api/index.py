"""Vercel entry point: exposes the FastAPI ASGI app as a single Python function."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app as _app  # noqa: E402

_log = logging.getLogger("foodforall.vercel")


async def app(scope, receive, send):
    if scope["type"] == "http":
        _log.warning("ASGI scope path=%r root_path=%r raw_path=%r headers=%r", scope.get("path"), scope.get("root_path"),
                     scope.get("raw_path"), [(k, v) for k, v in scope.get("headers", []) if k.startswith(b"x-")][:12])
    await _app(scope, receive, send)
