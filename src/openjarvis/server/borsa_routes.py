"""FastAPI routes for borsa level alarms (browser notification feed)."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, FastAPI, Query

logger = logging.getLogger(__name__)


def create_borsa_router(store=None) -> APIRouter:  # noqa: ANN001
    """Read-only alarm endpoints; the web UI polls ``/olaylar`` for triggers."""
    from openjarvis.borsa.alarms import AlarmStore

    router = APIRouter(prefix="/api/borsa/alarm", tags=["borsa"])
    store = store or AlarmStore()

    @router.get("/olaylar")
    async def events(after: str = Query("", max_length=64)):
        """Triggered alarm events newer than event id ``after``."""
        return {"events": store.events(after)}

    @router.get("/liste")
    async def list_alarms():
        from dataclasses import asdict

        return {"alarms": [asdict(a) for a in store.load()]}

    return router


def install_borsa_alarms(app: FastAPI) -> None:
    """Mount the router and run the alarm watcher while the server is up.

    Set ``OPENJARVIS_BORSA_ALARM_WATCH=0`` to disable the background checks.
    """
    app.include_router(create_borsa_router())
    if os.environ.get("OPENJARVIS_BORSA_ALARM_WATCH", "1") == "0":
        return
    from openjarvis.borsa.alarms import AlarmWatcher

    interval = int(os.environ.get("OPENJARVIS_BORSA_ALARM_INTERVAL", "300"))
    watcher = AlarmWatcher(interval=interval)

    @app.on_event("startup")
    async def _start_borsa_watcher() -> None:
        watcher.start()
        logger.info("Borsa alarm izleyici başladı (%ss)", interval)

    @app.on_event("shutdown")
    async def _stop_borsa_watcher() -> None:
        watcher.stop()
