"""Offline tests for openjarvis.borsa.signals and watchlist (borsapy stubbed)."""

from __future__ import annotations

import pandas as pd
import pytest

from openjarvis.borsa import data, signals, watchlist


def _tf(recommendation: str) -> dict:
    return {"summary": {"recommendation": recommendation, "buy": 0, "sell": 0, "neutral": 0}}


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.recommendations_summary = {
            "strongBuy": 1, "buy": 2, "hold": 3, "sell": 0, "strongSell": 0,
        }
        self.news = pd.DataFrame(
            [{"Date": "01.01.2026 10:00:00", "Title": "Test haberi", "URL": "https://x/1"}]
        )

    def ta_signals_all_timeframes(self):
        return {
            "15m": _tf("BUY"), "1h": _tf("BUY"), "4h": _tf("STRONG_BUY"),
            "1d": _tf("SELL"), "1W": _tf("SELL"),
            "1M": _tf("NEUTRAL"),
        }


class _FakeBP:
    Ticker = _FakeTicker

    @staticmethod
    def screen_stocks(template=None, recommendation=None):
        rows = [
            {"symbol": "THYAO", "name": "Turk Hava Yollari"},
            {"symbol": "ASELS", "name": "Aselsan"},
        ]
        return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def fake_bp(monkeypatch, tmp_path):
    data.clear_cache()
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    yield _FakeBP
    data.clear_cache()


# -- signal --------------------------------------------------------------


def test_signal_groups_timeframes_into_horizons(fake_bp):
    result = signals.signal("THYAO")
    assert result["symbol"] == "THYAO"
    # kisa_vade = 15m/1h/4h -> BUY, BUY, STRONG_BUY -> avg score 1.33 -> round to 1
    assert result["horizons"]["kisa_vade"]["verdict"] == "AL (zayıf)"
    # orta_vade = 1d/1W -> SELL, SELL -> score -1
    assert result["horizons"]["orta_vade"]["verdict"] == "SAT (zayıf)"
    # uzun_vade = 1M -> NEUTRAL
    assert result["horizons"]["uzun_vade"]["verdict"] == "NÖTR/BEKLE"


def test_signal_includes_analyst_view_and_news(fake_bp):
    result = signals.signal("THYAO")
    assert result["analyst_recommendation"]["buy"] == 2
    assert result["news"][0]["title"] == "Test haberi"
    assert result["disclaimer"] == signals.DISCLAIMER


def test_signal_rejects_fx_symbols(fake_bp):
    with pytest.raises(data.BorsaError, match="yalnızca BIST"):
        signals.signal("dolar")


def test_signal_rejects_bad_symbol(fake_bp):
    with pytest.raises(data.BorsaError):
        signals.signal("THY;rm")


def test_signal_wraps_provider_failure(fake_bp, monkeypatch):
    def boom(self):
        raise RuntimeError("network down")

    monkeypatch.setattr(_FakeTicker, "ta_signals_all_timeframes", boom)
    with pytest.raises(data.BorsaError, match="alınamadı"):
        signals.signal("THYAO")


# -- scan ------------------------------------------------------------------


def test_scan_returns_items_and_respects_limit(fake_bp):
    result = signals.scan(recommendation="AL", limit=1)
    assert result["count"] == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["symbol"] == "THYAO"
    assert result["disclaimer"] == signals.DISCLAIMER


def test_scan_rejects_bad_recommendation(fake_bp):
    with pytest.raises(data.BorsaError):
        signals.scan(recommendation="YOK")


def test_scan_wraps_provider_failure(fake_bp, monkeypatch):
    def boom(template=None, recommendation=None):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(fake_bp, "screen_stocks", boom)
    with pytest.raises(data.BorsaError, match="Tarama başarısız"):
        signals.scan()


# -- watchlist ---------------------------------------------------------------


@pytest.fixture
def wl(tmp_path):
    return watchlist.WatchlistStore(tmp_path / "watchlist.json")


def test_watchlist_add_list_remove(wl):
    assert wl.add("thyao") == "THYAO"
    assert wl.list() == ["THYAO"]
    assert wl.remove("THYAO") is True
    assert wl.list() == []


def test_watchlist_add_is_idempotent(wl):
    wl.add("THYAO")
    wl.add("THYAO")
    assert wl.list() == ["THYAO"]


def test_watchlist_remove_missing_returns_false(wl):
    assert wl.remove("THYAO") is False


def test_watchlist_rejects_bad_symbol(wl):
    with pytest.raises(data.BorsaError):
        wl.add("THY;rm")


# -- HTTP routes ---------------------------------------------------------


def _client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.borsa.alarms import AlarmStore
    from openjarvis.server.borsa_routes import create_borsa_router

    app = FastAPI()
    app.include_router(
        create_borsa_router(
            AlarmStore(tmp_path / "alarms.json"),
            watchlist.WatchlistStore(tmp_path / "watchlist.json"),
        )
    )
    return TestClient(app)


def test_http_sinyal(tmp_path, fake_bp):
    client = _client(tmp_path)
    res = client.get("/api/borsa/sinyal", params={"sembol": "THYAO"})
    assert res.status_code == 200
    assert res.json()["symbol"] == "THYAO"


def test_http_sinyal_rejects_fx(tmp_path, fake_bp):
    client = _client(tmp_path)
    res = client.get("/api/borsa/sinyal", params={"sembol": "dolar"})
    assert res.status_code == 400


def test_http_tara(tmp_path, fake_bp):
    client = _client(tmp_path)
    res = client.get("/api/borsa/tara", params={"tavsiye": "AL", "limit": 1})
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1


def test_http_takip_ekle_liste_sil(tmp_path, fake_bp):
    client = _client(tmp_path)
    assert client.get("/api/borsa/takip").json()["symbols"] == []

    add = client.post("/api/borsa/takip/ekle", json={"sembol": "thyao"})
    assert add.status_code == 200 and add.json()["symbol"] == "THYAO"
    assert client.get("/api/borsa/takip").json()["symbols"] == ["THYAO"]

    # the manually tracked symbol now shows up in /izleme, tagged "takip"
    izleme = client.get("/api/borsa/izleme").json()["items"]
    assert izleme[0]["symbol"] == "THYAO" and izleme[0]["kaynak"] == "takip"

    sil = client.post("/api/borsa/takip/sil", json={"sembol": "THYAO"})
    assert sil.status_code == 200
    assert client.get("/api/borsa/takip").json()["symbols"] == []


def test_http_takip_sil_missing_is_404(tmp_path, fake_bp):
    client = _client(tmp_path)
    res = client.post("/api/borsa/takip/sil", json={"sembol": "THYAO"})
    assert res.status_code == 404
