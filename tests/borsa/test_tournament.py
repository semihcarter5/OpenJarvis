"""Offline tests for openjarvis.borsa.tournament (borsapy stubbed, no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from openjarvis.borsa import data, tournament


def _frame(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(close), freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close},
        index=idx,
    )


# Trending up with a dip-and-recover shape so RSI/MACD/Bollinger strategies
# actually produce mixed signals instead of a flat line.
_UPTREND = np.concatenate(
    [np.linspace(100, 70, 40), np.linspace(70, 160, 110)]
)
_DOWNTREND = np.linspace(100, 40, 150)


class _FakeBP:
    series: dict[str, np.ndarray] = {"THYAO": _UPTREND, "ASELS": _DOWNTREND}
    calls = 0

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            _FakeBP.calls += 1
            if self.symbol == "YOK":
                return pd.DataFrame()
            if self.symbol == "KISA":
                return _frame(np.linspace(100, 110, 10))
            return _frame(_FakeBP.series[self.symbol])

    class FX(Ticker):
        pass


@pytest.fixture(autouse=True)
def fake_bp(monkeypatch, tmp_path):
    data.clear_cache()
    _FakeBP.calls = 0
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    yield _FakeBP
    data.clear_cache()


# -- backtest_symbol ---------------------------------------------------------


def test_backtest_symbol_covers_every_strategy(fake_bp):
    results = tournament.backtest_symbol("THYAO", "6mo")
    names = {r.strategy for r in results}
    assert names == set(tournament.STRATEGIES)


def test_backtest_symbol_buy_hold_matches_total_move(fake_bp):
    results = tournament.backtest_symbol("THYAO", "6mo")
    benchmark = next(r for r in results if r.strategy == tournament.BENCHMARK)
    start, end = _UPTREND[0], _UPTREND[-1]
    expected = (end / start - 1) * 100
    assert benchmark.total_return_pct == pytest.approx(expected, abs=0.5)
    assert benchmark.trades == 0  # always-long: position never changes


def test_backtest_symbol_rejects_short_history(fake_bp):
    with pytest.raises(data.BorsaError, match="en az"):
        tournament.backtest_symbol("KISA", "6mo")


def test_backtest_symbol_rejects_bad_period(fake_bp):
    with pytest.raises(data.BorsaError):
        tournament.backtest_symbol("THYAO", "10y")


# -- run_tournament -----------------------------------------------------------


def test_run_tournament_ranks_and_picks_a_non_benchmark_champion(fake_bp):
    result = tournament.run_tournament(["THYAO", "ASELS"], "6mo")
    assert result["symbols"] == ["ASELS", "THYAO"]
    assert not result["errors"]
    returns = [row["avg_return_pct"] for row in result["leaderboard"]]
    assert returns == sorted(returns, reverse=True)
    assert result["champion"] is not None
    assert result["champion"] != tournament.BENCHMARK


def test_run_tournament_reports_bad_symbol_without_failing(fake_bp):
    result = tournament.run_tournament(["THYAO", "YOK"], "6mo")
    assert "THYAO" in result["symbols"]
    assert "YOK" in result["errors"]
    # the failed symbol contributes no return samples
    for row in result["leaderboard"]:
        assert row["symbols"] <= 1


def test_run_tournament_all_symbols_fail(fake_bp):
    result = tournament.run_tournament(["YOK"], "6mo")
    assert result["champion"] is None
    assert all(row["avg_return_pct"] is None for row in result["leaderboard"])


# -- default_symbols ----------------------------------------------------------


class _FakeAlarm:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _FakeStore:
    def __init__(self, items: list[_FakeAlarm]) -> None:
        self._items = items

    def load(self) -> list[_FakeAlarm]:
        return self._items


def test_default_symbols_dedupes_and_sorts(monkeypatch):
    items = [_FakeAlarm("THYAO"), _FakeAlarm("USD"), _FakeAlarm("THYAO")]
    monkeypatch.setattr(tournament.alarms, "AlarmStore", lambda: _FakeStore(items))
    assert tournament.default_symbols() == ["THYAO", "USD"]


# -- format_report -------------------------------------------------------------


def test_format_report_lists_strategies_and_disclaimer(fake_bp):
    result = tournament.run_tournament(["THYAO"], "6mo")
    text = tournament.format_report(result)
    assert "Strateji turnuvası" in text
    assert "Al-tut (karşılaştırma)" in text
    assert tournament.DISCLAIMER in text


def test_format_report_lists_errors(fake_bp):
    result = tournament.run_tournament(["YOK"], "6mo")
    text = tournament.format_report(result)
    assert "YOK" in text


# -- Telegram ------------------------------------------------------------------


def test_send_telegram_report_uses_channel(monkeypatch, fake_bp):
    sent = {}

    class FakeChannel:
        def __init__(self, bot_token, parse_mode):
            sent["token"] = bot_token

        def send(self, chat, text):
            sent.update(chat=chat, text=text)
            return True

    import openjarvis.channels.telegram as tg

    monkeypatch.setattr(tg, "TelegramChannel", FakeChannel)
    monkeypatch.setattr(tournament.alarms, "telegram_target", lambda: ("tok", "123"))
    result = tournament.run_tournament(["THYAO"], "6mo")
    assert tournament.send_telegram_report(result) is True
    assert sent["chat"] == "123" and "Strateji turnuvası" in sent["text"]

    monkeypatch.setattr(tournament.alarms, "telegram_target", lambda: (None, None))
    assert tournament.send_telegram_report(result) is None


# -- TournamentScheduler --------------------------------------------------------


# 2026-11-02 is a Monday.
_MONDAY_10_TR = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)  # 10:00 Europe/Istanbul
_MONDAY_08_TR = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)  # 08:00 Europe/Istanbul
_TUESDAY_10_TR = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)


def _scheduler(tmp_path, **kwargs):
    return tournament.TournamentScheduler(
        state_path=tmp_path / "tournament_state.json",
        weekday=0,
        hour=9,
        **kwargs,
    )


def test_due_true_on_scheduled_day_and_hour(tmp_path):
    sched = _scheduler(tmp_path)
    assert sched._due(_MONDAY_10_TR) is True


def test_due_false_before_the_hour(tmp_path):
    sched = _scheduler(tmp_path)
    assert sched._due(_MONDAY_08_TR) is False


def test_due_false_on_wrong_weekday(tmp_path):
    sched = _scheduler(tmp_path)
    assert sched._due(_TUESDAY_10_TR) is False


def test_due_false_once_already_run_this_week(tmp_path):
    sched = _scheduler(tmp_path)
    sched._mark_run(sched._week_key(_MONDAY_10_TR.astimezone(tournament.alarms.TR_TZ)))
    assert sched._due(_MONDAY_10_TR) is False
    # a later week is due again
    assert sched._due(_TUESDAY_10_TR.replace(day=9)) is True  # 2026-11-09, next Monday...


def test_run_once_skips_without_symbols(tmp_path, fake_bp):
    sched = _scheduler(tmp_path, symbols_provider=lambda: [])
    assert sched.run_once(force=True) is None
    assert not (tmp_path / "tournament_state.json").exists()


def test_run_once_skips_when_not_due(tmp_path, fake_bp):
    notified = []
    sched = tournament.TournamentScheduler(
        state_path=tmp_path / "tournament_state.json",
        symbols_provider=lambda: ["THYAO"],
        notifiers=[notified.append],
    )
    sched._due = lambda now: False  # type: ignore[method-assign]
    assert sched.run_once() is None
    assert notified == []


def test_run_once_force_runs_notifies_and_marks_week(tmp_path, fake_bp):
    notified = []
    sched = tournament.TournamentScheduler(
        state_path=tmp_path / "tournament_state.json",
        symbols_provider=lambda: ["THYAO"],
        notifiers=[notified.append],
    )
    result = sched.run_once(force=True)
    assert result is not None and result["symbols"] == ["THYAO"]
    assert len(notified) == 1 and notified[0] is result
    now_tr = datetime.now(timezone.utc).astimezone(tournament.alarms.TR_TZ)
    assert sched._last_run_week() == sched._week_key(now_tr)
