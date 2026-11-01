"""Paper-trading (virtual) portfolio: tracks simulated buys/sells against
real (delayed) market prices so a strategy can be sanity-checked without
risking real money. This NEVER places a real order anywhere - OpenJarvis
has no brokerage integration and places none, real or virtual-on-exchange.
Everything here is a local JSON ledger.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjarvis.borsa import data

DEFAULT_BALANCE = 100_000.0
_MAX_TRADES = 200

DISCLAIMER = (
    "Bu tamamen SANAL bir portföydür; gerçek para veya gerçek emir içermez, "
    "hiçbir yere gönderilmez. Yatırım tavsiyesi değildir."
)


@dataclass(slots=True)
class Trade:
    id: str
    symbol: str
    side: str  # "al" | "sat"
    quantity: float
    price: float
    amount: float
    at: str
    realized_pnl: float | None = None


def _fresh_state(balance: float) -> dict[str, Any]:
    return {
        "cash": balance,
        "starting_balance": balance,
        "positions": {},  # symbol -> {"quantity": float, "avg_cost": float}
        "trades": [],
        "realized_pnl": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class PaperPortfolio:
    """JSON-file-backed virtual portfolio, one position table + trade log."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from openjarvis.core.paths import get_data_dir

            path = get_data_dir() / "borsa_portfolio.json"
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _fresh_state(DEFAULT_BALANCE)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _fresh_state(DEFAULT_BALANCE)

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def reset(self, balance: float = DEFAULT_BALANCE) -> None:
        if balance <= 0:
            raise data.BorsaError("Başlangıç bakiyesi pozitif olmalı.")
        with self._lock:
            self._save(_fresh_state(balance))

    def buy(self, symbol: str, amount: float) -> Trade:
        if not isinstance(amount, (int, float)) or amount <= 0:
            raise data.BorsaError("Tutar pozitif bir sayı olmalı.")
        asset = data.resolve(symbol)
        price = float(data.quote(asset)["last"])
        if price <= 0:
            raise data.BorsaError(f"{asset.display} için geçerli fiyat alınamadı.")
        with self._lock:
            state = self._load()
            if amount > state["cash"] + 1e-9:
                raise data.BorsaError(
                    f"Yetersiz sanal bakiye: {state['cash']:.2f} TL var, "
                    f"{amount:.2f} TL isteniyor."
                )
            qty = amount / price
            pos = state["positions"].get(asset.display)
            if pos:
                total_qty = pos["quantity"] + qty
                pos["avg_cost"] = (
                    pos["avg_cost"] * pos["quantity"] + amount
                ) / total_qty
                pos["quantity"] = total_qty
            else:
                state["positions"][asset.display] = {
                    "quantity": qty,
                    "avg_cost": price,
                }
            state["cash"] -= amount
            trade = Trade(
                id=uuid.uuid4().hex[:8],
                symbol=asset.display,
                side="al",
                quantity=round(qty, 6),
                price=round(price, 4),
                amount=round(amount, 2),
                at=datetime.now(timezone.utc).isoformat(),
            )
            state["trades"] = (state["trades"] + [asdict(trade)])[-_MAX_TRADES:]
            self._save(state)
            return trade

    def sell(self, symbol: str, quantity: float | None = None) -> Trade:
        asset = data.resolve(symbol)
        if quantity is not None and (
            not isinstance(quantity, (int, float)) or quantity <= 0
        ):
            raise data.BorsaError("Miktar pozitif bir sayı olmalı.")
        with self._lock:
            state = self._load()
            pos = state["positions"].get(asset.display)
            if not pos or pos["quantity"] <= 0:
                raise data.BorsaError(f"{asset.display} için açık sanal pozisyon yok.")
            qty = pos["quantity"] if quantity is None else min(quantity, pos["quantity"])
            price = float(data.quote(asset)["last"])
            proceeds = qty * price
            cost_basis = qty * pos["avg_cost"]
            pnl = proceeds - cost_basis
            pos["quantity"] -= qty
            if pos["quantity"] <= 1e-9:
                del state["positions"][asset.display]
            state["cash"] += proceeds
            state["realized_pnl"] = state.get("realized_pnl", 0.0) + pnl
            trade = Trade(
                id=uuid.uuid4().hex[:8],
                symbol=asset.display,
                side="sat",
                quantity=round(qty, 6),
                price=round(price, 4),
                amount=round(proceeds, 2),
                at=datetime.now(timezone.utc).isoformat(),
                realized_pnl=round(pnl, 2),
            )
            state["trades"] = (state["trades"] + [asdict(trade)])[-_MAX_TRADES:]
            self._save(state)
            return trade

    def summary(self) -> dict[str, Any]:
        with self._lock:
            state = self._load()
        positions_out: list[dict[str, Any]] = []
        market_value_total = 0.0
        unrealized_total = 0.0
        for symbol, pos in state["positions"].items():
            try:
                asset = data.resolve(symbol)
                price = float(data.quote(asset)["last"])
                market_value = pos["quantity"] * price
                unrealized = market_value - pos["quantity"] * pos["avg_cost"]
                unrealized_pct = (
                    (price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] else None
                )
                positions_out.append(
                    {
                        "symbol": symbol,
                        "quantity": round(pos["quantity"], 6),
                        "avg_cost": round(pos["avg_cost"], 4),
                        "last": price,
                        "market_value": round(market_value, 2),
                        "unrealized_pnl": round(unrealized, 2),
                        "unrealized_pnl_pct": (
                            round(unrealized_pct, 2) if unrealized_pct is not None else None
                        ),
                    }
                )
                market_value_total += market_value
                unrealized_total += unrealized
            except data.BorsaError as exc:
                positions_out.append(
                    {"symbol": symbol, "quantity": pos["quantity"], "error": str(exc)}
                )
        net_worth = state["cash"] + market_value_total
        starting = state["starting_balance"]
        total_return_pct = (net_worth / starting - 1) * 100 if starting else None
        return {
            "cash": round(state["cash"], 2),
            "starting_balance": starting,
            "positions": sorted(positions_out, key=lambda p: p["symbol"]),
            "market_value": round(market_value_total, 2),
            "net_worth": round(net_worth, 2),
            "realized_pnl": round(state.get("realized_pnl", 0.0), 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
            "trades": list(reversed(state["trades"]))[:20],
            "disclaimer": DISCLAIMER,
        }


def format_summary(result: dict[str, Any]) -> str:
    """Plain-text Turkish report for CLI output."""
    lines = [
        "SANAL portföy",
        f"  Nakit: {result['cash']:,.2f} TL",
        f"  Piyasa değeri: {result['market_value']:,.2f} TL",
        f"  Net değer: {result['net_worth']:,.2f} TL "
        f"(başlangıç {result['starting_balance']:,.2f} TL)",
        f"  Gerçekleşen K/Z: {result['realized_pnl']:+,.2f} TL | "
        f"Gerçekleşmemiş K/Z: {result['unrealized_pnl']:+,.2f} TL",
    ]
    if result["total_return_pct"] is not None:
        lines.append(f"  Toplam getiri: {result['total_return_pct']:+.2f}%")
    if result["positions"]:
        lines.append("\nPozisyonlar:")
        for p in result["positions"]:
            if "error" in p:
                lines.append(f"  {p['symbol']}: HATA - {p['error']}")
                continue
            lines.append(
                f"  {p['symbol']:<12} {p['quantity']:.4f} adet @ ort {p['avg_cost']:g} "
                f"| son {p['last']:g} | K/Z {p['unrealized_pnl']:+,.2f} TL "
                f"({p['unrealized_pnl_pct']:+.2f}%)"
            )
    else:
        lines.append("\nAçık pozisyon yok.")
    lines.append(f"\n{result['disclaimer']}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_BALANCE",
    "DISCLAIMER",
    "PaperPortfolio",
    "Trade",
    "format_summary",
]
