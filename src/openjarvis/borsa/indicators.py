"""Pure-pandas technical indicators for OHLCV frames.

Every function takes a ``pandas.Series`` (or an OHLCV ``DataFrame`` with
``Open/High/Low/Close/Volume`` columns) and returns a Series/DataFrame with the
same index. No network access, so these are cheap to unit-test.
"""

from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI in the 0-100 range."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # No losses in the window -> RSI is 100 by definition.
    out = out.where(avg_loss != 0, 100.0)
    return out.where(avg_gain.notna())


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"lower": mid - k * std, "mid": mid, "upper": mid + k * std})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift()
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def support_resistance(
    df: pd.DataFrame, window: int = 5, lookback: int = 120, max_levels: int = 3
) -> dict[str, list[float]]:
    """Swing-high/low levels from the last ``lookback`` bars.

    A bar is a swing high (low) when it is the max (min) of the centred
    ``2*window+1`` neighbourhood. Levels are returned nearest-to-price first:
    supports below the last close, resistances above it.
    """
    recent = df.tail(lookback)
    span = 2 * window + 1
    highs = recent["High"]
    lows = recent["Low"]
    swing_hi = highs[highs == highs.rolling(span, center=True).max()]
    swing_lo = lows[lows == lows.rolling(span, center=True).min()]
    last = float(recent["Close"].iloc[-1])
    levels = sorted({round(float(v), 4) for v in pd.concat([swing_hi, swing_lo])})
    supports = sorted((v for v in levels if v < last), reverse=True)[:max_levels]
    resistances = sorted(v for v in levels if v > last)[:max_levels]
    return {"support": supports, "resistance": resistances}


__all__ = [
    "atr",
    "bollinger",
    "ema",
    "macd",
    "rsi",
    "sma",
    "support_resistance",
]
