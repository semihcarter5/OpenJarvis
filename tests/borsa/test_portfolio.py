"""Offline tests for openjarvis.borsa.portfolio (borsapy stubbed, no network)."""

from __future__ import annotations

import pytest

from openjarvis.borsa import data, portfolio


class _FakeBP:
    prices = {"THYAO": 100.0, "ASELS": 50.0}
    calls = 0

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            _FakeBP.calls += 1
            if self.symbol not in _FakeBP.prices:
                return {"last": None}
            return {"last": _FakeBP.prices[self.symbol]}

    class FX(Ticker):
        @property
        def current(self):
            return self.info


@pytest.fixture(autouse=True)
def fake_bp(monkeypatch, tmp_path):
    data.clear_cache()
    _FakeBP.prices = {"THYAO": 100.0, "ASELS": 50.0}
    _FakeBP.calls = 0
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    yield _FakeBP
    data.clear_cache()


@pytest.fixture
def paper(tmp_path):
    return portfolio.PaperPortfolio(tmp_path / "portfolio.json")


# -- buy -----------------------------------------------------------------


def test_buy_opens_a_position_and_deducts_cash(paper):
    trade = paper.buy("THYAO", 1000.0)
    assert trade.symbol == "THYAO" and trade.side == "al"
    assert trade.quantity == pytest.approx(10.0)
    summary = paper.summary()
    assert summary["cash"] == pytest.approx(portfolio.DEFAULT_BALANCE - 1000.0)
    assert summary["positions"][0]["quantity"] == pytest.approx(10.0)
    assert summary["positions"][0]["avg_cost"] == pytest.approx(100.0)


def test_buy_averages_cost_on_repeat_purchase(paper, fake_bp):
    paper.buy("THYAO", 1000.0)  # 10 @ 100
    fake_bp.prices["THYAO"] = 200.0
    data.clear_cache()
    paper.buy("THYAO", 1000.0)  # 5 @ 200
    pos = paper.summary()["positions"][0]
    assert pos["quantity"] == pytest.approx(15.0)
    # (10*100 + 1000) / 15 = 133.33
    assert pos["avg_cost"] == pytest.approx(133.33, abs=0.01)


def test_buy_rejects_insufficient_balance(paper):
    with pytest.raises(data.BorsaError, match="Yetersiz"):
        paper.buy("THYAO", portfolio.DEFAULT_BALANCE + 1)


@pytest.mark.parametrize("amount", [0, -5])
def test_buy_rejects_non_positive_amount(paper, amount):
    with pytest.raises(data.BorsaError):
        paper.buy("THYAO", amount)


def test_buy_rejects_bad_symbol(paper):
    with pytest.raises(data.BorsaError):
        paper.buy("THY;rm", 100)


# -- sell ------------------------------------------------------------------


def test_sell_full_position_realizes_pnl(paper, fake_bp):
    paper.buy("THYAO", 1000.0)  # 10 @ 100
    fake_bp.prices["THYAO"] = 150.0
    data.clear_cache()
    trade = paper.sell("THYAO")
    assert trade.side == "sat" and trade.quantity == pytest.approx(10.0)
    assert trade.realized_pnl == pytest.approx(500.0)  # 10 * (150-100)
    summary = paper.summary()
    assert summary["positions"] == []
    assert summary["realized_pnl"] == pytest.approx(500.0)
    assert summary["cash"] == pytest.approx(portfolio.DEFAULT_BALANCE - 1000.0 + 1500.0)


def test_sell_partial_quantity_keeps_remainder(paper):
    paper.buy("THYAO", 1000.0)  # 10 @ 100
    paper.sell("THYAO", 4.0)
    pos = paper.summary()["positions"][0]
    assert pos["quantity"] == pytest.approx(6.0)


def test_sell_more_than_held_clamps_to_full_position(paper):
    paper.buy("THYAO", 1000.0)  # 10 @ 100
    trade = paper.sell("THYAO", 999.0)
    assert trade.quantity == pytest.approx(10.0)
    assert paper.summary()["positions"] == []


def test_sell_without_position_raises(paper):
    with pytest.raises(data.BorsaError, match="açık sanal pozisyon yok"):
        paper.sell("THYAO")


# -- summary -----------------------------------------------------------------


def test_summary_computes_unrealized_pnl_and_total_return(paper, fake_bp):
    paper.buy("THYAO", 2000.0)  # 20 @ 100
    fake_bp.prices["THYAO"] = 120.0
    data.clear_cache()
    summary = paper.summary()
    pos = summary["positions"][0]
    assert pos["unrealized_pnl"] == pytest.approx(400.0)  # 20 * (120-100)
    assert pos["unrealized_pnl_pct"] == pytest.approx(20.0)
    assert summary["net_worth"] == pytest.approx(portfolio.DEFAULT_BALANCE + 400.0)
    expected_return = (summary["net_worth"] / portfolio.DEFAULT_BALANCE - 1) * 100
    assert summary["total_return_pct"] == pytest.approx(expected_return)


def test_summary_reports_provider_error_for_a_position_without_failing(paper, fake_bp):
    paper.buy("THYAO", 1000.0)
    del fake_bp.prices["THYAO"]  # provider can no longer price it
    data.clear_cache()
    summary = paper.summary()
    assert "error" in summary["positions"][0]


def test_summary_empty_portfolio(paper):
    summary = paper.summary()
    assert summary["positions"] == []
    assert summary["cash"] == portfolio.DEFAULT_BALANCE
    assert summary["net_worth"] == portfolio.DEFAULT_BALANCE
    assert summary["total_return_pct"] == 0.0


# -- reset ---------------------------------------------------------------


def test_reset_clears_positions_and_sets_new_balance(paper):
    paper.buy("THYAO", 1000.0)
    paper.reset(50_000.0)
    summary = paper.summary()
    assert summary["positions"] == []
    assert summary["cash"] == 50_000.0
    assert summary["starting_balance"] == 50_000.0


def test_reset_rejects_non_positive_balance(paper):
    with pytest.raises(data.BorsaError):
        paper.reset(0)


# -- format_summary ---------------------------------------------------------


def test_format_summary_includes_positions_and_disclaimer(paper):
    paper.buy("THYAO", 1000.0)
    text = portfolio.format_summary(paper.summary())
    assert "THYAO" in text
    assert portfolio.DISCLAIMER in text


def test_format_summary_no_positions(paper):
    text = portfolio.format_summary(paper.summary())
    assert "Açık pozisyon yok" in text
