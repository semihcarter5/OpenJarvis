"""Market data access for BIST stocks, FX and gold via ``borsapy``.

borsapy data is for personal use and is delayed (~15 minutes for BIST).
The library is imported lazily so the rest of OpenJarvis loads without it.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

# User-facing aliases -> borsapy FX symbols.
FX_ALIASES: dict[str, str] = {
    "USD": "USD",
    "DOLAR": "USD",
    "EUR": "EUR",
    "EURO": "EUR",
    "GBP": "GBP",
    "STERLIN": "GBP",
    "ALTIN": "gram-altin",
    "GRAM-ALTIN": "gram-altin",
    "GRAMALTIN": "gram-altin",
    "ONS": "ons-altin",
    "ONS-ALTIN": "ons-altin",
    "CEYREK": "ceyrek-altin",
    "CEYREK-ALTIN": "ceyrek-altin",
}

VALID_PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")
_TR_UPPER = str.maketrans("çğıöşüi", "ÇĞIÖŞÜİ")
_TR_ASCII = str.maketrans("ÇĞİÖŞÜ", "CGIOSU")
_CACHE_TTL_SECONDS = 300.0


class BorsaError(Exception):
    """Raised for invalid input or data-provider failures."""


@dataclass(frozen=True, slots=True)
class Asset:
    symbol: str  # symbol as borsapy expects it
    kind: str  # "stock" | "fx"
    display: str


def resolve(symbol: str) -> Asset:
    """Normalise user input (``thyao``, ``dolar``, ``altın``) to an Asset."""
    if not isinstance(symbol, str):
        raise BorsaError("Sembol metin olmalı.")
    raw = symbol.strip().translate(_TR_UPPER).upper().translate(_TR_ASCII)
    raw = raw.removesuffix(".IS").replace(" ", "-")
    if not raw or not _SYMBOL_RE.fullmatch(raw):
        raise BorsaError(f"Geçersiz sembol: {symbol!r}")
    if raw in FX_ALIASES:
        fx = FX_ALIASES[raw]
        return Asset(symbol=fx, kind="fx", display=fx)
    return Asset(symbol=raw, kind="stock", display=raw)


def _bp() -> Any:
    try:
        import borsapy
    except ImportError as exc:  # pragma: no cover - depends on env
        raise BorsaError(
            "borsapy kurulu değil: uv pip install --python .venv borsapy"
        ) from exc
    return borsapy


class _TTLCache:
    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: tuple, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        value = factory()
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _TTLCache(_CACHE_TTL_SECONDS)


def history(asset: Asset, period: str = "6mo") -> pd.DataFrame:
    """Daily OHLC(V) frame, oldest first. FX frames have no Volume column."""
    if period not in VALID_PERIODS:
        raise BorsaError(f"Dönem şunlardan biri olmalı: {', '.join(VALID_PERIODS)}")

    def fetch() -> pd.DataFrame:
        bp = _bp()
        obj = bp.Ticker(asset.symbol) if asset.kind == "stock" else bp.FX(asset.symbol)
        try:
            df = obj.history(period=period)
        except Exception as exc:
            raise BorsaError(f"{asset.display} verisi alınamadı: {exc}") from exc
        if df is None or df.empty:
            raise BorsaError(f"{asset.display} için veri yok (sembol doğru mu?).")
        return df.sort_index()

    return _cache.get_or_set(("history", asset, period), fetch).copy()


def quote(asset: Asset) -> dict[str, Any]:
    """Latest quote: ``last`` plus change info when the provider has it."""

    def fetch() -> dict[str, Any]:
        bp = _bp()
        try:
            if asset.kind == "stock":
                info = bp.Ticker(asset.symbol).info
            else:
                info = bp.FX(asset.symbol).current
        except Exception as exc:
            raise BorsaError(f"{asset.display} fiyatı alınamadı: {exc}") from exc
        if not info or info.get("last") is None:
            raise BorsaError(f"{asset.display} için fiyat yok (sembol doğru mu?).")
        keys = ("last", "open", "high", "low", "change", "change_percent",
                "volume", "update_time")
        out = {k: info.get(k) for k in keys if info.get(k) is not None}
        if "update_time" in out:
            out["update_time"] = str(out["update_time"])
        return out

    return dict(_cache.get_or_set(("quote", asset), fetch))


def clear_cache() -> None:
    _cache.clear()


__all__ = [
    "Asset",
    "BorsaError",
    "FX_ALIASES",
    "VALID_PERIODS",
    "clear_cache",
    "history",
    "quote",
    "resolve",
]
