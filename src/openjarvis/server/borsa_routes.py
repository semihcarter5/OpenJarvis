"""FastAPI routes for borsa quotes, analysis, level alarms and the paper
portfolio.

Every route is read-only EXCEPT the /portfoy/al, /portfoy/sat and
/portfoy/sifirla writes, which only ever touch a local, purely virtual
paper-trading ledger (openjarvis.borsa.portfolio) - no order placement, no
write access to a real brokerage, ever. Intended for the Midas side-panel
browser extension and the desktop UI's polling feed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SYMBOL_QUERY = Query(..., min_length=1, max_length=20, alias="sembol")


class _BuyRequest(BaseModel):
    sembol: str = Field(min_length=1, max_length=20)
    tutar: float = Field(gt=0)


class _SellRequest(BaseModel):
    sembol: str = Field(min_length=1, max_length=20)
    miktar: float | None = Field(default=None, gt=0)


class _ResetRequest(BaseModel):
    bakiye: float = Field(default=100_000.0, gt=0)


class _SymbolRequest(BaseModel):
    sembol: str = Field(min_length=1, max_length=20)


def create_borsa_router(store=None, watchlist=None) -> APIRouter:  # noqa: ANN001
    """Quote/analysis/alarm/paper-portfolio endpoints for the Midas side panel."""
    from openjarvis.borsa import analysis, data, portfolio, signals
    from openjarvis.borsa.alarms import AlarmStore
    from openjarvis.borsa.watchlist import WatchlistStore

    router = APIRouter(prefix="/api/borsa", tags=["borsa"])
    store = store or AlarmStore()
    watchlist = watchlist or WatchlistStore()
    paper = portfolio.PaperPortfolio()

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
        """Watchlist: one quote per distinct symbol (alarms + manual takip)."""
        alarm_symbols = {a.symbol for a in store.load()}
        takip_symbols = set(watchlist.list())
        symbols = sorted(alarm_symbols | takip_symbols)
        items = []
        for symbol in symbols:
            kaynak = "alarm+takip" if symbol in takip_symbols and symbol in alarm_symbols \
                else "takip" if symbol in takip_symbols else "alarm"
            try:
                asset = data.resolve(symbol)
                items.append(
                    {"symbol": asset.display, "kind": asset.kind, "kaynak": kaynak,
                     **data.quote(asset)}
                )
            except data.BorsaError as exc:
                items.append({"symbol": symbol, "kaynak": kaynak, "error": str(exc)})
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

    @router.get("/portfoy")
    async def portfoy_goster():
        """SANAL (virtual) portfolio summary: cash, positions, P&L."""
        return paper.summary()

    @router.post("/portfoy/al")
    async def portfoy_al(body: _BuyRequest):
        """Buy with virtual TRY at the current (delayed) price. No real order."""
        try:
            return asdict(paper.buy(body.sembol, body.tutar))
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/portfoy/sat")
    async def portfoy_sat(body: _SellRequest):
        """Sell a virtual position (or part of it). No real order."""
        try:
            return asdict(paper.sell(body.sembol, body.miktar))
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/portfoy/sifirla")
    async def portfoy_sifirla(body: _ResetRequest = _ResetRequest()):
        """Reset the virtual portfolio to a fresh starting balance."""
        try:
            paper.reset(body.bakiye)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return paper.summary()

    @router.get("/takip")
    async def takip_liste():
        """Manually tracked symbols (independent of alarm thresholds)."""
        return {"symbols": watchlist.list()}

    @router.post("/takip/ekle")
    async def takip_ekle(body: _SymbolRequest):
        try:
            symbol = watchlist.add(body.sembol)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"symbol": symbol}

    @router.post("/takip/sil")
    async def takip_sil(body: _SymbolRequest):
        try:
            removed = watchlist.remove(body.sembol)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail=f"{body.sembol} takipte değil.")
        return {"ok": True}

    @router.get("/sinyal")
    async def sinyal(sembol: str = _SYMBOL_QUERY):
        """Short/medium/long-term buy-sell-hold verdict, analyst view, news."""
        try:
            return signals.signal(sembol)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/tara")
    async def tara(
        tavsiye: str | None = Query("AL", pattern="^(AL|SAT|TUT)$"),
        sablon: str | None = Query(None, alias="sablon"),
        limit: int = Query(20, ge=1, le=100),
    ):
        """Whole-BIST screener (borsapy.screen_stocks); default: AL candidates."""
        try:
            return signals.scan(recommendation=tavsiye, template=sablon, limit=limit)
        except data.BorsaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
