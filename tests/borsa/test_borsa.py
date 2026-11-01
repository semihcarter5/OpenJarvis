"""Offline tests for openjarvis.borsa (no network; borsapy is stubbed)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from openjarvis.borsa import analysis, data, indicators


def _frame(close: np.ndarray, volume: bool = True) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(close), freq="B")
    df = pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close},
        index=idx,
    )
    if volume:
        df["Volume"] = 1_000_000.0
    return df


@pytest.fixture(autouse=True)
def _clear_cache():
    data.clear_cache()
    yield
    data.clear_cache()


# -- resolve ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,symbol,kind",
    [
        ("thyao", "THYAO", "stock"),
        (" ASELS.IS ", "ASELS", "stock"),
        ("dolar", "USD", "fx"),
        ("Euro", "EUR", "fx"),
        ("altın", "gram-altin", "fx"),
        ("gram altin", "gram-altin", "fx"),
        ("çeyrek", "ceyrek-altin", "fx"),
    ],
)
def test_resolve(raw, symbol, kind):
    asset = data.resolve(raw)
    assert (asset.symbol, asset.kind) == (symbol, kind)


@pytest.mark.parametrize("bad", ["", "   ", "THY;rm", "A" * 30, None, 42])
def test_resolve_rejects_bad_input(bad):
    with pytest.raises(data.BorsaError):
        data.resolve(bad)


# -- indicators ------------------------------------------------------------


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = up[::-1].reset_index(drop=True)
    assert indicators.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert indicators.rsi(down).iloc[-1] == pytest.approx(0.0)
    assert indicators.rsi(up).iloc[:13].isna().all()


def test_sma_and_bollinger():
    s = pd.Series(np.arange(1, 41, dtype=float))
    assert indicators.sma(s, 20).iloc[-1] == pytest.approx(30.5)
    bb = indicators.bollinger(s).iloc[-1]
    assert bb["lower"] < bb["mid"] < bb["upper"]


def test_macd_positive_in_uptrend():
    s = pd.Series(np.linspace(100, 200, 80))
    assert indicators.macd(s)["macd"].iloc[-1] > 0


def test_support_resistance_sides():
    t = np.arange(150)
    close = 100 + 10 * np.sin(t / 8)
    df = _frame(close)
    lv = indicators.support_resistance(df)
    last = df["Close"].iloc[-1]
    assert all(v < last for v in lv["support"])
    assert all(v > last for v in lv["resistance"])
    assert lv["support"] == sorted(lv["support"], reverse=True)


# -- analysis --------------------------------------------------------------


def test_analyze_frame_uptrend_is_positive():
    rng = np.random.default_rng(0)
    close = np.linspace(100, 180, 260) + rng.normal(0, 0.5, 260)
    r = analysis.analyze_frame(_frame(close))
    assert r["score"] > 15
    assert "pozitif" in r["bias"]
    assert r["indicators"]["sma200"] is not None
    json.dumps(r)  # must be JSON-serialisable


def test_analyze_frame_downtrend_is_negative_fx_without_volume():
    close = np.linspace(200, 120, 120)
    r = analysis.analyze_frame(_frame(close, volume=False))
    assert r["score"] < -15
    assert r["indicators"]["sma200"] is None


def test_analyze_frame_too_short():
    with pytest.raises(data.BorsaError):
        analysis.analyze_frame(_frame(np.linspace(1, 2, 10)))


def test_format_report_contains_disclaimer():
    r = analysis.analyze_frame(_frame(np.linspace(100, 120, 60)))
    r["symbol"] = "TEST"
    text = analysis.format_report(r)
    assert "TEST" in text and "yatırım tavsiyesi değildir" in text


# -- data layer with stubbed borsapy ---------------------------------------


class _FakeBP:
    calls = 0

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            _FakeBP.calls += 1
            if self.symbol == "YOK":
                return pd.DataFrame()
            return _frame(np.linspace(10, 20, 250))[::-1]  # newest first

        @property
        def info(self):
            return {"last": 12.5, "change_percent": 1.2, "close": None}

    class FX(Ticker):
        @property
        def current(self):
            return {"last": 49.9, "update_time": pd.Timestamp("2026-10-30")}


@pytest.fixture
def fake_bp(monkeypatch):
    _FakeBP.calls = 0
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    return _FakeBP


def test_history_sorted_and_cached(fake_bp):
    a = data.resolve("THYAO")
    df1 = data.history(a, "1y")
    df2 = data.history(a, "1y")
    assert df1.index.is_monotonic_increasing
    assert fake_bp.calls == 1
    df2.iloc[0, 0] = -1  # returned copies must not poison the cache
    assert data.history(a, "1y").iloc[0, 0] != -1


def test_history_errors(fake_bp):
    with pytest.raises(data.BorsaError):
        data.history(data.resolve("YOK"), "1y")
    with pytest.raises(data.BorsaError):
        data.history(data.resolve("THYAO"), "10y")


def test_quote_drops_none_and_stringifies_time(fake_bp):
    assert data.quote(data.resolve("THYAO")) == {"last": 12.5, "change_percent": 1.2}
    q = data.quote(data.resolve("dolar"))
    assert q["last"] == 49.9 and isinstance(q["update_time"], str)


def test_tools_registered_and_execute(fake_bp):
    import importlib

    import openjarvis.tools.borsa_tool as borsa_tool
    from openjarvis.core.registry import ToolRegistry

    # conftest clears registries per test; re-run the decorators.
    importlib.reload(borsa_tool)

    quote_tool = ToolRegistry.get("borsa_fiyat")()
    res = quote_tool.execute(symbol="altın")
    assert res.success and json.loads(res.content)["symbol"] == "gram-altin"

    an_tool = ToolRegistry.get("borsa_analiz")()
    res = an_tool.execute(symbol="THYAO", period="1y")
    body = json.loads(res.content)
    assert res.success and body["symbol"] == "THYAO" and "disclaimer" in body

    bad = an_tool.execute(symbol="YOK")
    assert not bad.success


def test_cli(fake_bp):
    from click.testing import CliRunner

    from openjarvis.cli.borsa_cmd import borsa

    out = CliRunner().invoke(borsa, ["fiyat", "THYAO", "dolar"])
    assert out.exit_code == 0, out.output
    assert "THYAO" in out.output and "USD" in out.output
    out = CliRunner().invoke(borsa, ["analiz", "THYAO", "--json"])
    assert out.exit_code == 0 and json.loads(out.output)["symbol"] == "THYAO"
