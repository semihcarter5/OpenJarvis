"""Rule-based technical summary built on :mod:`openjarvis.borsa.indicators`.

The output is descriptive, not investment advice: it lists what each
indicator currently shows plus a coarse -100..100 bias score.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from openjarvis.borsa import data, indicators

_MIN_BARS = 30
DISCLAIMER = (
    "Teknik göstergelerin otomatik özetidir, yatırım tavsiyesi değildir. "
    "Veri ~15 dk gecikmeli olabilir."
)


def _r(value: Any, nd: int = 2) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else round(f, nd)


def _pct(a: float, b: float) -> float | None:
    return None if not b else round((a / b - 1) * 100, 2)


def analyze_frame(df: pd.DataFrame) -> dict[str, Any]:
    """Compute indicators + signals for an OHLC(V) frame (oldest first)."""
    if df is None or len(df) < _MIN_BARS:
        raise data.BorsaError(
            f"Analiz için en az {_MIN_BARS} işlem günü gerekli "
            f"(gelen: {0 if df is None else len(df)}). Daha uzun dönem seçin."
        )
    close = df["Close"].astype(float)
    last = float(close.iloc[-1])
    n = len(close)

    sma20 = indicators.sma(close, 20).iloc[-1]
    sma50 = indicators.sma(close, 50).iloc[-1] if n >= 50 else float("nan")
    sma200 = indicators.sma(close, 200).iloc[-1] if n >= 200 else float("nan")
    rsi_s = indicators.rsi(close)
    rsi = rsi_s.iloc[-1]
    m = indicators.macd(close)
    bb = indicators.bollinger(close).iloc[-1]
    atr = indicators.atr(df).iloc[-1]
    levels = indicators.support_resistance(df)

    signals: list[str] = []
    score = 0

    # Trend vs moving averages.
    for name, ma, weight in (("SMA20", sma20, 10), ("SMA50", sma50, 15),
                             ("SMA200", sma200, 20)):
        if math.isnan(ma):
            continue
        if last > ma:
            score += weight
            signals.append(f"Fiyat {name} üzerinde ({ma:.2f})")
        else:
            score -= weight
            signals.append(f"Fiyat {name} altında ({ma:.2f})")
    if not math.isnan(sma50) and not math.isnan(sma200):
        prev50 = indicators.sma(close, 50).iloc[-2]
        prev200 = indicators.sma(close, 200).iloc[-2]
        if prev50 <= prev200 and sma50 > sma200:
            signals.append("Altın kesişim: SMA50, SMA200'ü yukarı kesti")
        elif prev50 >= prev200 and sma50 < sma200:
            signals.append("Ölüm kesişimi: SMA50, SMA200'ü aşağı kesti")

    # Momentum.
    if not math.isnan(rsi):
        if rsi >= 70:
            score -= 15
            signals.append(f"RSI {rsi:.1f}: aşırı alım bölgesi")
        elif rsi <= 30:
            score += 15
            signals.append(f"RSI {rsi:.1f}: aşırı satım bölgesi")
        else:
            score += 5 if rsi > 50 else -5
            signals.append(f"RSI {rsi:.1f}: nötr")

    hist = m["hist"]
    if hist.notna().sum() >= 2:
        h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
        if h_prev <= 0 < h_now:
            score += 15
            signals.append("MACD sinyal çizgisini yukarı kesti")
        elif h_prev >= 0 > h_now:
            score -= 15
            signals.append("MACD sinyal çizgisini aşağı kesti")
        else:
            score += 10 if h_now > 0 else -10
            signals.append(f"MACD histogramı {'pozitif' if h_now > 0 else 'negatif'}")

    if not math.isnan(bb["upper"]):
        if last >= bb["upper"]:
            signals.append("Fiyat üst Bollinger bandında/üzerinde")
        elif last <= bb["lower"]:
            signals.append("Fiyat alt Bollinger bandında/altında")

    score = max(-100, min(100, score))
    if score >= 40:
        bias = "güçlü pozitif"
    elif score >= 15:
        bias = "pozitif"
    elif score > -15:
        bias = "nötr"
    elif score > -40:
        bias = "negatif"
    else:
        bias = "güçlü negatif"

    def change(days: int) -> float | None:
        return _pct(last, float(close.iloc[-1 - days])) if n > days else None

    return {
        "last": _r(last, 4),
        "date": str(close.index[-1].date()) if hasattr(close.index[-1], "date")
        else str(close.index[-1]),
        "bars": n,
        "change_pct": {"1g": change(1), "5g": change(5), "20g": change(20)},
        "period_high": _r(df["High"].max(), 4),
        "period_low": _r(df["Low"].min(), 4),
        "indicators": {
            "sma20": _r(sma20),
            "sma50": _r(sma50),
            "sma200": _r(sma200),
            "rsi14": _r(rsi, 1),
            "macd": _r(m["macd"].iloc[-1], 4),
            "macd_signal": _r(m["signal"].iloc[-1], 4),
            "bb_lower": _r(bb["lower"]),
            "bb_upper": _r(bb["upper"]),
            "atr14": _r(atr, 4),
            "atr_pct": _r(atr / last * 100) if last else None,
        },
        "levels": levels,
        "signals": signals,
        "score": score,
        "bias": bias,
    }


def analyze(symbol: str, period: str = "1y") -> dict[str, Any]:
    """Fetch data for ``symbol`` and return the full analysis dict."""
    asset = data.resolve(symbol)
    df = data.history(asset, period)
    result = analyze_frame(df)
    result.update(
        symbol=asset.display,
        kind=asset.kind,
        period=period,
        disclaimer=DISCLAIMER,
    )
    return result


def format_report(result: dict[str, Any]) -> str:
    """Plain-text Turkish report for CLI / chat output."""
    ind = result["indicators"]
    ch = result["change_pct"]

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v:+.2f}%"

    lines = [
        f"{result.get('symbol', '?')}  {result['last']}  ({result['date']})",
        f"Değişim: 1g {pct(ch['1g'])} | 5g {pct(ch['5g'])} | 20g {pct(ch['20g'])}",
        f"Eğilim: {result['bias']} (skor {result['score']:+d})",
        f"RSI14 {ind['rsi14']} | SMA20 {ind['sma20']} | SMA50 {ind['sma50']} "
        f"| SMA200 {ind['sma200']} | ATR %{ind['atr_pct']}",
        "Destek: " + (", ".join(map(str, result["levels"]["support"])) or "-"),
        "Direnç: " + (", ".join(map(str, result["levels"]["resistance"])) or "-"),
        "Sinyaller:",
        *[f"  • {s}" for s in result["signals"]],
        result.get("disclaimer", DISCLAIMER),
    ]
    return "\n".join(lines)


__all__ = ["DISCLAIMER", "analyze", "analyze_frame", "format_report"]
