"""'Şu tarihte alsaydım bugün ne kazanırdım' hesaplayıcısı.

Purely a hindsight calculation over real historical closes - it never
places an order (real or virtual) and never predicts the future. The
paper portfolio (openjarvis.borsa.portfolio) is the place for forward-
looking simulated trades; this module only looks backward.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjarvis.borsa import data

DISCLAIMER = (
    "Geçmiş veriye dayalı varsayımsal bir hesaplamadır, gerçekleşmiş bir "
    "işlem değildir ve gelecekteki performansın göstergesi değildir. "
    "Yatırım tavsiyesi değildir."
)


def what_if(symbol: str, since: str, amount: float, period: str = "5y") -> dict[str, Any]:
    """If you'd put ``amount`` TRY into ``symbol`` on/after date ``since``."""
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise data.BorsaError("Tutar pozitif bir sayı olmalı.")
    try:
        target = datetime.strptime(since, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise data.BorsaError(f"Tarih YYYY-AA-GG biçiminde olmalı: {since!r}") from exc

    asset = data.resolve(symbol)
    df = data.history(asset, period)
    dates = [ts.date() for ts in df.index]

    if target < dates[0]:
        raise data.BorsaError(
            f"{asset.display} verisi en erken {dates[0]}'e kadar gidiyor "
            f"({period} dönem içinde). Daha uzun bir dönem gerekir."
        )
    try:
        i = next(idx for idx, d in enumerate(dates) if d >= target)
    except StopIteration as exc:
        raise data.BorsaError(f"{since} tarihinden sonra veri yok.") from exc

    buy_date, buy_price = dates[i], float(df["Close"].iloc[i])
    last_date, last_price = dates[-1], float(df["Close"].iloc[-1])
    if buy_price <= 0:
        raise data.BorsaError(f"{asset.display} için geçersiz geçmiş fiyat.")

    quantity = amount / buy_price
    current_value = quantity * last_price
    profit = current_value - amount

    return {
        "symbol": asset.display,
        "requested_date": since,
        "buy_date": str(buy_date),
        "buy_price": round(buy_price, 4),
        "last_date": str(last_date),
        "last_price": round(last_price, 4),
        "amount": round(amount, 2),
        "quantity": round(quantity, 6),
        "current_value": round(current_value, 2),
        "profit": round(profit, 2),
        "profit_pct": round((last_price / buy_price - 1) * 100, 2),
        "days": (last_date - buy_date).days,
        "disclaimer": DISCLAIMER,
    }


def format_what_if(result: dict[str, Any]) -> str:
    """Plain-text Turkish report for CLI output."""
    sign = "+" if result["profit"] >= 0 else ""
    return (
        f"{result['symbol']}: {result['buy_date']} tarihinde {result['amount']:,.2f} TL "
        f"({result['buy_price']:g} TL'den {result['quantity']:.4f} adet) alınsaydı,\n"
        f"{result['last_date']} itibarıyla ({result['last_price']:g} TL) değeri "
        f"{result['current_value']:,.2f} TL olurdu.\n"
        f"Kâr/zarar: {sign}{result['profit']:,.2f} TL ({sign}{result['profit_pct']:.2f}%, "
        f"{result['days']} gün)\n\n{result['disclaimer']}"
    )


__all__ = ["DISCLAIMER", "format_what_if", "what_if"]
