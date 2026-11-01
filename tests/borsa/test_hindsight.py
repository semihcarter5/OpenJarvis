"""Offline tests for openjarvis.borsa.hindsight (borsapy stubbed, no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from openjarvis.borsa import data, hindsight

# 100 business days, close rising linearly 100 -> 199.
_IDX = pd.date_range("2024-01-02", periods=100, freq="B")
_CLOSE = np.linspace(100, 199, 100)


class _FakeBP:
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol == "YOK":
                return pd.DataFrame()
            return pd.DataFrame(
                {"Open": _CLOSE, "High": _CLOSE, "Low": _CLOSE, "Close": _CLOSE},
                index=_IDX,
            )

    FX = Ticker


@pytest.fixture(autouse=True)
def fake_bp(monkeypatch, tmp_path):
    data.clear_cache()
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    yield _FakeBP
    data.clear_cache()


def test_what_if_computes_profit_from_exact_date(fake_bp):
    # index[0] = 2024-01-02, close 100.0; last close = 199.0
    r = hindsight.what_if("THYAO", "2024-01-02", 1000.0)
    assert r["buy_date"] == "2024-01-02"
    assert r["buy_price"] == pytest.approx(100.0)
    assert r["quantity"] == pytest.approx(10.0)
    assert r["last_price"] == pytest.approx(199.0)
    assert r["current_value"] == pytest.approx(1990.0)
    assert r["profit"] == pytest.approx(990.0)
    assert r["profit_pct"] == pytest.approx(99.0)
    assert r["days"] > 0


def test_what_if_snaps_to_next_trading_day_for_weekend(fake_bp):
    # 2024-01-06 is a Saturday; first business day on/after it is 2024-01-08.
    r = hindsight.what_if("THYAO", "2024-01-06", 1000.0)
    assert r["buy_date"] == "2024-01-08"


def test_what_if_rejects_date_before_available_history(fake_bp):
    with pytest.raises(data.BorsaError, match="en erken"):
        hindsight.what_if("THYAO", "2020-01-01", 1000.0)


def test_what_if_rejects_date_after_available_history(fake_bp):
    with pytest.raises(data.BorsaError, match="sonra veri yok"):
        hindsight.what_if("THYAO", "2030-01-01", 1000.0)


def test_what_if_rejects_bad_date_format(fake_bp):
    with pytest.raises(data.BorsaError, match="YYYY-AA-GG"):
        hindsight.what_if("THYAO", "02/01/2024", 1000.0)


@pytest.mark.parametrize("amount", [0, -5])
def test_what_if_rejects_non_positive_amount(fake_bp, amount):
    with pytest.raises(data.BorsaError):
        hindsight.what_if("THYAO", "2024-01-02", amount)


def test_what_if_rejects_bad_symbol(fake_bp):
    with pytest.raises(data.BorsaError):
        hindsight.what_if("THY;rm", "2024-01-02", 1000.0)


def test_what_if_rejects_empty_history(fake_bp):
    with pytest.raises(data.BorsaError):
        hindsight.what_if("YOK", "2024-01-02", 1000.0)


def test_format_what_if_includes_key_figures(fake_bp):
    r = hindsight.what_if("THYAO", "2024-01-02", 1000.0)
    text = hindsight.format_what_if(r)
    assert "THYAO" in text and "990" in text and hindsight.DISCLAIMER in text


# -- HTTP route ------------------------------------------------------------


def test_http_gecmis(tmp_path, fake_bp):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.borsa.alarms import AlarmStore
    from openjarvis.borsa.watchlist import WatchlistStore
    from openjarvis.server.borsa_routes import create_borsa_router

    app = FastAPI()
    app.include_router(
        create_borsa_router(
            AlarmStore(tmp_path / "alarms.json"), WatchlistStore(tmp_path / "wl.json")
        )
    )
    client = TestClient(app)

    res = client.get(
        "/api/borsa/gecmis",
        params={"sembol": "THYAO", "tarih": "2024-01-02", "tutar": 1000},
    )
    assert res.status_code == 200
    assert res.json()["profit"] == pytest.approx(990.0)


def test_http_gecmis_bad_date_is_400(tmp_path, fake_bp):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.borsa.alarms import AlarmStore
    from openjarvis.borsa.watchlist import WatchlistStore
    from openjarvis.server.borsa_routes import create_borsa_router

    app = FastAPI()
    app.include_router(
        create_borsa_router(
            AlarmStore(tmp_path / "alarms.json"), WatchlistStore(tmp_path / "wl.json")
        )
    )
    client = TestClient(app)

    res = client.get(
        "/api/borsa/gecmis",
        params={"sembol": "THYAO", "tarih": "not-a-date", "tutar": 1000},
    )
    assert res.status_code == 400
