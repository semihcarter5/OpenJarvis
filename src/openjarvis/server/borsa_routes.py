"""FastAPI routes for borsa quotes, analysis and level alarms.

Every route here is read-only: no order placement, no write access to a
brokerage. Intended for the Midas side-panel browser extension and the
desktop UI's polling feed.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, FastAPI, HTTPException, Query

logger = logging.getLogger(__name__)

_SYMBOL_QUERY = Query(..., min_length=1, max_length=20, alias="sembol")


def create_borsa_router(store=None) -> APIRouter:  # noqa: ANN001
    """Read-only quote/analysis/alarm endpoints for the Midas side panel."""
    from openjarvis.borsa import analysis, data
    from openjarvis.borsa.alarms import AlarmStore

    router = APIRouter(prefix="/api/borsa", tags=["borsa"])
    store = store or AlarmStore()

    @router.get("/fiyat")
    async def fiyat(sembol: str = _SYMBOL_QUERY):
        """Latest (delayed) price for one BIST stock, currency or gold."""
        try:
            asset = data.resolve(sembol)
            return {"symbol": asset.display, "kind": asset.kind, **data.quote(asset)}
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/analiz")
    async def analiz(
        sembol: str = _SYMBOL_QUERY,
        period: str = Query("1y", enum=list(data.VALID_PERIODS)),
    ):
        """Technical summary (trend score, RSI, MACD, support/resistance)."""
        try:
            return analysis.analyze(sembol, period)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/izleme")
    async def izleme():
        """Watchlist: one quote per distinct symbol across saved alarms."""
        symbols = sorted({a.symbol for a in store.load()})
        items = []
        for symbol in symbols:
            try:
                asset = data.resolve(symbol)
                items.append(
                    {"symbol": asset.display, "kind": asset.kind, **data.quote(asset)}
                )
            except data.BorsaError as exc:
                items.append({"symbol": symbol, "error": str(exc)})
        return {"items": items}

    @router.get("/turnuva")
    async def turnuva(
        sembol: list[str] = Query([], alias="sembol"),
        period: str = Query("6mo", enum=list(data.VALID_PERIODS)),
    ):
        """Weekly strategy backtest leaderboard. No symbols = alarm watchlist."""
        from openjarvis.borsa import tournament

        symbols = sembol or sorted({a.symbol for a in store.load()})
        if not symbols:
            raise HTTPException(
                status_code=400,
                detail="Sembol yok: ?sembol= ile verin ya da önce alarm ekleyin.",
            )
        return tournament.run_tournament(symbols, period=period)

    @router.get("/alarm/olaylar")
    async def events(after: str = Query("", max_length=64)):
        """Triggered alarm events newer than event id ``after``."""
        return {"events": store.events(after)}

    @router.get("/alarm/liste")
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


def install_borsa_tournament(app: FastAPI) -> None:
    """Run the weekly strategy-tournament scheduler while the server is up.

    Set ``OPENJARVIS_BORSA_TOURNAMENT_WATCH=0`` to disable it.
    """
    if os.environ.get("OPENJARVIS_BORSA_TOURNAMENT_WATCH", "1") == "0":
        return
    from openjarvis.borsa.tournament import TournamentScheduler

    scheduler = TournamentScheduler()

    @app.on_event("startup")
    async def _start_borsa_tournament() -> None:
        scheduler.start()
        logger.info("Borsa strateji turnuvası zamanlayıcısı başladı")

    @app.on_event("shutdown")
    async def _stop_borsa_tournament() -> None:
        scheduler.stop()
