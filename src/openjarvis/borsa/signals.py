"""Multi-timeframe buy/sell/hold signals, analyst recommendations, news and
a market-wide screener - all sourced from borsapy's live BIST data.

Everything here is descriptive, automated technical/analyst output, not
personalized investment advice, and a horizon label ("kısa vade" etc.) is
a grouping of indicator timeframes, never a promised wait time or a
profit guarantee. OpenJarvis places no orders, real or simulated-on-a-
real-exchange.
"""

from __future__ import annotations

from typing import Any

from openjarvis.borsa import data

DISCLAIMER = (
    "Teknik gösterge ve analist görüşlerinin otomatik özetidir; yatırım "
    "tavsiyesi değildir, kâr garantisi vermez. Veri gecikmeli olabilir."
)

# Timeframe -> horizon bucket shown to the user.
_HORIZONS: dict[str, list[str]] = {
    "kisa_vade": ["15m", "1h", "4h"],
    "orta_vade": ["1d", "1W"],
    "uzun_vade": ["1M"],
}
_HORIZON_LABELS = {
    "kisa_vade": "Kısa vade (gün içi/birkaç gün)",
    "orta_vade": "Orta vade (haftalar)",
    "uzun_vade": "Uzun vade (aylar)",
}

_SCORES = {"STRONG_BUY": 2, "BUY": 1, "NEUTRAL": 0, "SELL": -1, "STRONG_SELL": -2}
_LABELS = {2: "AL", 1: "AL (zayıf)", 0: "NÖTR/BEKLE", -1: "SAT (zayıf)", -2: "SAT"}


def _bucket_verdict(frames: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    scores = [
        _SCORES.get(frames[k]["summary"]["recommendation"], 0)
        for k in keys
        if k in frames
    ]
    if not scores:
        return {"verdict": "NÖTR/BEKLE", "score": 0}
    avg = sum(scores) / len(scores)
    rounded = max(-2, min(2, round(avg)))
    return {"verdict": _LABELS[rounded], "score": round(avg, 2)}


def signal(symbol: str) -> dict[str, Any]:
    """Short/medium/long-term buy/sell/hold verdict + analyst view + news."""
    asset = data.resolve(symbol)
    if asset.kind != "stock":
        raise data.BorsaError(
            "Sinyal/haber yalnızca BIST hisseleri için var (döviz/altın için yok)."
        )
    bp = data._bp()
    ticker = bp.Ticker(asset.symbol)
    try:
        frames = ticker.ta_signals_all_timeframes()
    except Exception as exc:  # pragma: no cover - network/provider failure
        raise data.BorsaError(f"{asset.display} sinyalleri alınamadı: {exc}") from exc

    horizons = {
        key: {"label": _HORIZON_LABELS[key], **_bucket_verdict(frames, tfs)}
        for key, tfs in _HORIZONS.items()
    }

    try:
        analyst = ticker.recommendations_summary
    except Exception:  # pragma: no cover
        analyst = None

    try:
        news_df = ticker.news
        news = (
            [
                {"date": str(r["Date"]), "title": str(r["Title"]), "url": str(r["URL"])}
                for _, r in news_df.head(5).iterrows()
            ]
            if news_df is not None and len(news_df)
            else []
        )
    except Exception:  # pragma: no cover
        news = []

    return {
        "symbol": asset.display,
        "horizons": horizons,
        "analyst_recommendation": analyst,
        "news": news,
        "disclaimer": DISCLAIMER,
    }


def scan(
    recommendation: str | None = "AL",
    template: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Market-wide screener over every BIST company (borsapy.screen_stocks)."""
    bp = data._bp()
    valid = {"AL", "SAT", "TUT"}
    if recommendation is not None and recommendation not in valid:
        raise data.BorsaError(f"recommendation şunlardan biri olmalı: {', '.join(valid)}")
    try:
        df = bp.screen_stocks(template=template, recommendation=recommendation)
    except Exception as exc:  # pragma: no cover - network/provider failure
        raise data.BorsaError(f"Tarama başarısız: {exc}") from exc
    items = [
        {"symbol": row.get("symbol"), "name": row.get("name")}
        for row in df.head(max(1, limit)).to_dict(orient="records")
    ]
    return {
        "recommendation": recommendation,
        "template": template,
        "count": len(df),
        "items": items,
        "disclaimer": DISCLAIMER,
    }


__all__ = ["DISCLAIMER", "scan", "signal"]
