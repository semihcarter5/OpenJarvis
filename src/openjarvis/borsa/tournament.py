"""Weekly strategy tournament: backtests a few simple rule-based strategies
on the watched symbols and ranks them by realized return.

This is a descriptive backtest over historical data, not live trading advice
and not an order-routing feature - OpenJarvis places no orders anywhere.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from openjarvis.borsa import alarms, data, indicators

logger = logging.getLogger(__name__)

_MIN_BARS = 60
_PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y")
BENCHMARK = "al_tut"

DISCLAIMER = (
    "Geçmiş veriye dayalı geriye dönük testtir (backtest); gelecekteki "
    "performansın garantisi değildir. Yatırım tavsiyesi değildir ve hiçbir "
    "şekilde otomatik emir vermez."
)

STRATEGY_LABELS: dict[str, str] = {
    "sma_cross": "SMA20/50 kesişimi",
    "rsi_reversion": "RSI aşırı alım/satım",
    "macd_trend": "MACD histogram yönü",
    "bollinger_breakout": "Bollinger kırılımı",
    BENCHMARK: "Al-tut (karşılaştırma)",
}


def _carry_forward(raw: pd.Series) -> pd.Series:
    """Hold the last non-NaN signal until a new one arrives; 0 before the first."""
    return raw.ffill().fillna(0.0)


def _sig_sma_cross(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].astype(float)
    fast, slow = indicators.sma(close, 20), indicators.sma(close, 50)
    pos = (fast > slow).astype(float)
    return pos.where(fast.notna() & slow.notna())


def _sig_rsi_reversion(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].astype(float)
    r = indicators.rsi(close)
    raw = pd.Series(index=close.index, dtype=float)
    raw[r <= 30] = 1.0
    raw[r >= 70] = 0.0
    return _carry_forward(raw.where(r.notna()))


def _sig_macd_trend(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].astype(float)
    hist = indicators.macd(close)["hist"]
    return (hist > 0).astype(float).where(hist.notna())


def _sig_bollinger_breakout(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].astype(float)
    bb = indicators.bollinger(close)
    raw = pd.Series(index=close.index, dtype=float)
    raw[close >= bb["upper"]] = 1.0
    raw[close <= bb["lower"]] = 0.0
    return _carry_forward(raw.where(bb["upper"].notna()))


def _sig_buy_hold(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].astype(float)
    return pd.Series(1.0, index=close.index)


STRATEGIES: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "sma_cross": _sig_sma_cross,
    "rsi_reversion": _sig_rsi_reversion,
    "macd_trend": _sig_macd_trend,
    "bollinger_breakout": _sig_bollinger_breakout,
    BENCHMARK: _sig_buy_hold,
}


@dataclass(slots=True)
class StrategyResult:
    strategy: str
    label: str
    total_return_pct: float
    trades: int
    win_rate_pct: float | None
    max_drawdown_pct: float


def backtest_symbol(symbol: str, period: str = "6mo") -> list[StrategyResult]:
    """Backtest every strategy in :data:`STRATEGIES` for one symbol."""
    if period not in _PERIODS:
        raise data.BorsaError(f"Dönem şunlardan biri olmalı: {', '.join(_PERIODS)}")
    asset = data.resolve(symbol)
    df = data.history(asset, period)
    if len(df) < _MIN_BARS:
        raise data.BorsaError(
            f"Turnuva için en az {_MIN_BARS} işlem günü gerekli "
            f"(gelen: {len(df)}). Daha uzun dönem seçin."
        )
    close = df["Close"].astype(float)
    returns = close.pct_change()

    results: list[StrategyResult] = []
    for name, signal_fn in STRATEGIES.items():
        position = signal_fn(df).clip(0.0, 1.0).fillna(0.0)
        # Shift one bar: a signal computed from today's close can only be
        # acted on from tomorrow, avoiding look-ahead bias.
        held = position.shift(1).fillna(0.0)
        strat_returns = (held * returns).fillna(0.0)
        equity = (1.0 + strat_returns).cumprod()
        total_return = float((equity.iloc[-1] - 1.0) * 100)
        drawdown = float(((equity / equity.cummax()) - 1.0).min() * 100)
        trades = int(position.diff().fillna(0.0).ne(0).sum())
        active = strat_returns[held > 0]
        win_rate = float((active > 0).mean() * 100) if len(active) else None
        results.append(
            StrategyResult(
                strategy=name,
                label=STRATEGY_LABELS.get(name, name),
                total_return_pct=round(total_return, 2),
                trades=trades,
                win_rate_pct=round(win_rate, 1) if win_rate is not None else None,
                max_drawdown_pct=round(drawdown, 2),
            )
        )
    return results


def default_symbols() -> list[str]:
    """Distinct symbols from saved alarms - the panel's watchlist, reused here
    so the tournament has something to run on without asking the user twice."""
    return sorted({a.symbol for a in alarms.AlarmStore().load()})


def run_tournament(symbols: list[str], period: str = "6mo") -> dict[str, Any]:
    """Backtest every strategy across ``symbols`` and rank by average return."""
    per_symbol: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    totals: dict[str, list[float]] = {name: [] for name in STRATEGIES}

    for symbol in symbols:
        try:
            results = backtest_symbol(symbol, period)
        except data.BorsaError as exc:
            errors[symbol] = str(exc)
            continue
        per_symbol[symbol] = [asdict(r) for r in results]
        for r in results:
            totals[r.strategy].append(r.total_return_pct)

    leaderboard = []
    for name, values in totals.items():
        avg = round(sum(values) / len(values), 2) if values else None
        leaderboard.append(
            {
                "strategy": name,
                "label": STRATEGY_LABELS.get(name, name),
                "avg_return_pct": avg,
                "symbols": len(values),
            }
        )
    leaderboard.sort(key=lambda r: (r["avg_return_pct"] is None, -(r["avg_return_pct"] or 0)))
    champion = next(
        (r["strategy"] for r in leaderboard
         if r["strategy"] != BENCHMARK and r["avg_return_pct"] is not None),
        None,
    )

    return {
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": sorted(per_symbol),
        "per_symbol": per_symbol,
        "leaderboard": leaderboard,
        "champion": champion,
        "errors": errors,
        "disclaimer": DISCLAIMER,
    }


def format_report(result: dict[str, Any]) -> str:
    """Plain-text Turkish report for CLI / Telegram output."""
    lines = [f"Strateji turnuvası ({result['period']}, {len(result['symbols'])} sembol)"]
    for row in result["leaderboard"]:
        avg = row["avg_return_pct"]
        avg_s = "veri yok" if avg is None else f"{avg:+.2f}%"
        tag = " [karşılaştırma]" if row["strategy"] == BENCHMARK else ""
        lines.append(f"  {row['label']:<28} {avg_s:>10} ({row['symbols']} sembol){tag}")
    if result["champion"]:
        champ_label = STRATEGY_LABELS.get(result["champion"], result["champion"])
        lines.append(f"\nBu haftanın lideri: {champ_label}")
    if result["errors"]:
        lines.append("\nHatalar:")
        for symbol, err in result["errors"].items():
            lines.append(f"  {symbol}: {err}")
    lines.append(f"\n{result['disclaimer']}")
    return "\n".join(lines)


def send_telegram_report(result: dict[str, Any]) -> bool | None:
    """Send the leaderboard to Telegram. ``None`` = not configured."""
    token, chat = alarms.telegram_target()
    if not token or not chat:
        return None
    from openjarvis.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token=token, parse_mode="")
    return channel.send(chat, format_report(result))


Notifier = Callable[[dict[str, Any]], Any]


class TournamentScheduler:
    """Background thread that runs the tournament once per ISO week.

    Mirrors :class:`openjarvis.borsa.alarms.AlarmWatcher`'s polling style
    rather than the generic LLM-prompt scheduler (see that class's docstring
    for why).
    """

    def __init__(
        self,
        *,
        symbols_provider: Callable[[], list[str]] | None = None,
        period: str = "6mo",
        weekday: int = 0,  # Monday
        hour: int = 9,
        check_interval: int = 3600,
        notifiers: list[Notifier] | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._symbols_provider = symbols_provider or default_symbols
        self._period = period
        self._weekday = weekday
        self._hour = hour
        self._check_interval = max(60, int(check_interval))
        self._notifiers = notifiers
        if state_path is None:
            from openjarvis.core.paths import get_data_dir

            state_path = get_data_dir() / "borsa_tournament_state.json"
        self._state_path = Path(state_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _last_run_week(self) -> str | None:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))["last_run_week"]
        except Exception:
            return None

    def _mark_run(self, week_key: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"last_run_week": week_key}), encoding="utf-8"
        )

    @staticmethod
    def _week_key(local: datetime) -> str:
        iso = local.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _due(self, now: datetime) -> bool:
        local = now.astimezone(alarms.TR_TZ)
        if self._week_key(local) == self._last_run_week():
            return False
        return local.weekday() == self._weekday and local.hour >= self._hour

    def run_once(self, *, force: bool = False) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        if not force and not self._due(now):
            return None
        symbols = self._symbols_provider()
        if not symbols:
            return None
        result = run_tournament(symbols, period=self._period)
        for notify in (self._notifiers if self._notifiers is not None else (send_telegram_report,)):
            try:
                notify(result)
            except Exception:
                logger.exception("Turnuva bildirimi gönderilemedi")
        self._mark_run(self._week_key(now.astimezone(alarms.TR_TZ)))
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Haftalık strateji turnuvası başarısız")
            self._stop.wait(self._check_interval)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="jarvis-borsa-tournament"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


__all__ = [
    "BENCHMARK",
    "STRATEGIES",
    "STRATEGY_LABELS",
    "StrategyResult",
    "TournamentScheduler",
    "backtest_symbol",
    "default_symbols",
    "format_report",
    "run_tournament",
    "send_telegram_report",
]
