"""Offline tests for openjarvis.borsa.alarms (borsapy stubbed, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from openjarvis.borsa import alarms, data

# Wednesday 2026-10-28 12:00 Istanbul (09:00 UTC) -> session open.
IN_SESSION = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
# Saturday -> closed.
WEEKEND = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


class _FakeBP:
    prices = {"THYAO": 225.0, "USD": 50.5}
    calls = 0

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            idx = pd.date_range("2025-01-01", periods=120, freq="B")
            close = np.linspace(200, 100, 120)  # steady fall -> RSI ~0
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close},
                index=idx,
            )

        @property
        def info(self):
            _FakeBP.calls += 1
            return {"last": _FakeBP.prices[self.symbol]}

    class FX(Ticker):
        @property
        def current(self):
            _FakeBP.calls += 1
            return {"last": _FakeBP.prices[self.symbol]}


@pytest.fixture(autouse=True)
def fake_bp(monkeypatch, tmp_path):
    data.clear_cache()
    _FakeBP.prices = {"THYAO": 225.0, "USD": 50.5}
    _FakeBP.calls = 0
    monkeypatch.setattr(data, "_bp", lambda: _FakeBP)
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    yield _FakeBP
    data.clear_cache()


@pytest.fixture
def store(tmp_path):
    return alarms.AlarmStore(tmp_path / "alarms.json")


# -- parsing / session ------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("THYAO 230 altına inerse", ("THYAO", "fiyat", "alti", 230.0)),
        ("USD 50 üstüne çıkarsa", ("USD", "fiyat", "ustu", 50.0)),
        ("dolar 49,75 üzerine", ("dolar", "fiyat", "ustu", 49.75)),
        ("ASELS RSI 30 altı", ("ASELS", "rsi", "alti", 30.0)),
        ("gram altın 3000 >", ("gram altin", "fiyat", "ustu", 3000.0)),
    ],
)
def test_parse_rule(text, expected):
    r = alarms.parse_rule(text)
    assert (r["symbol"], r["metric"], r["op"], r["threshold"]) == expected


@pytest.mark.parametrize("bad", ["", "THYAO", "THYAO düşerse", None])
def test_parse_rule_rejects(bad):
    with pytest.raises(data.BorsaError):
        alarms.parse_rule(bad)


def test_bist_session():
    assert alarms.is_bist_session(IN_SESSION)
    assert not alarms.is_bist_session(WEEKEND)
    # 18:30 Istanbul on a weekday -> closed; 09:59 -> closed.
    utc = timezone.utc
    assert not alarms.is_bist_session(datetime(2026, 10, 28, 15, 30, tzinfo=utc))
    assert not alarms.is_bist_session(datetime(2026, 10, 28, 6, 59, tzinfo=utc))


# -- store --------------------------------------------------------------------


def test_store_add_list_remove(store):
    a = store.add("thyao", "fiyat", "alti", 230)
    b = store.add("dolar", "fiyat", "ustu", 50, mode="tekrar")
    assert [x.symbol for x in store.load()] == ["THYAO", "USD"]
    assert store.remove(a.id) and not store.remove(a.id)
    assert [x.id for x in store.load()] == [b.id]


@pytest.mark.parametrize(
    "args",
    [
        ("THY;rm", "fiyat", "alti", 1),
        ("THYAO", "hacim", "alti", 1),
        ("THYAO", "fiyat", "esit", 1),
        ("THYAO", "fiyat", "alti", -5),
        ("THYAO", "rsi", "alti", 150),
    ],
)
def test_store_validates(store, args):
    with pytest.raises(data.BorsaError):
        store.add(*args)


def test_corrupt_store_is_backed_up(store):
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == []
    assert list(store.path.parent.glob("alarms.json.bozuk-*"))


# -- checking -----------------------------------------------------------------


def test_one_shot_fires_once_and_logs_event(store):
    a = store.add("THYAO", "fiyat", "alti", 230)
    r = alarms.check_alarms(store, now=IN_SESSION)
    assert [e.alarm_id for e in r.triggered] == [a.id]
    assert "THYAO" in r.triggered[0].message and "225" in r.triggered[0].message
    assert store.load()[0].active is False
    # second check: no repeat
    assert alarms.check_alarms(store, now=IN_SESSION).triggered == []
    events = store.events()
    assert len(events) == 1 and store.events(after=events[0]["id"]) == []


def test_not_hit_keeps_alarm_open(store):
    store.add("THYAO", "fiyat", "alti", 200)
    r = alarms.check_alarms(store, now=IN_SESSION)
    assert r.triggered == [] and r.checked == 1
    saved = store.load()[0]
    assert saved.active and saved.last_value == 225.0


def test_repeat_respects_cooldown(store):
    store.add("USD", "fiyat", "ustu", 50, mode="tekrar", cooldown_min=30)
    assert len(alarms.check_alarms(store, now=IN_SESSION).triggered) == 1
    later = IN_SESSION + timedelta(minutes=10)
    r = alarms.check_alarms(store, now=later)
    assert r.triggered == [] and len(r.skipped) == 1
    data.clear_cache()
    much_later = IN_SESSION + timedelta(minutes=31)
    assert len(alarms.check_alarms(store, now=much_later).triggered) == 1
    assert store.load()[0].active


def test_stock_skipped_outside_session_fx_still_checked(store, fake_bp):
    stock = store.add("THYAO", "fiyat", "alti", 230)
    store.add("USD", "fiyat", "ustu", 50)
    r = alarms.check_alarms(store, now=WEEKEND)
    assert r.skipped == [stock.id]
    assert [e.symbol for e in r.triggered] == ["USD"]
    forced = alarms.check_alarms(store, now=WEEKEND, ignore_session=True)
    assert [e.symbol for e in forced.triggered] == ["THYAO"]


def test_rsi_alarm(store):
    store.add("ASELS", "rsi", "alti", 30)
    r = alarms.check_alarms(store, now=IN_SESSION)
    assert len(r.triggered) == 1 and r.triggered[0].value < 30


def test_provider_error_is_reported_not_raised(store, fake_bp):
    a = store.add("YOKSUZ", "fiyat", "alti", 10)
    r = alarms.check_alarms(store, now=IN_SESSION)
    assert a.id in r.errors and r.triggered == []
    assert store.load()[0].active


def test_quotes_shared_across_alarms(store, fake_bp):
    store.add("THYAO", "fiyat", "alti", 200)
    store.add("THYAO", "fiyat", "ustu", 300)
    alarms.check_alarms(store, now=IN_SESSION)
    assert fake_bp.calls == 1


# -- notification -------------------------------------------------------------


def test_run_check_calls_notifiers(store, monkeypatch):
    monkeypatch.setattr(alarms, "is_bist_session", lambda now=None: True)
    store.add("THYAO", "fiyat", "alti", 230)
    got = []
    alarms.run_check(store, notifiers=[got.append])
    assert len(got) == 1 and got[0][0].symbol == "THYAO"


def test_send_telegram_uses_channel(monkeypatch):
    sent = {}

    class FakeChannel:
        def __init__(self, bot_token, parse_mode):
            sent["token"] = bot_token

        def send(self, chat, text):
            sent.update(chat=chat, text=text)
            return True

    import openjarvis.channels.telegram as tg

    monkeypatch.setattr(tg, "TelegramChannel", FakeChannel)
    monkeypatch.setattr(alarms, "telegram_target", lambda: ("tok", "123"))
    ev = alarms.AlarmEvent("1", "a", "THYAO", "🔔 test", 1.0, "now")
    assert alarms.send_telegram([ev]) is True
    assert sent["chat"] == "123" and "🔔 test" in sent["text"]

    monkeypatch.setattr(alarms, "telegram_target", lambda: (None, None))
    assert alarms.send_telegram([ev]) is None


def test_watcher_noop_without_alarms(store):
    assert alarms.AlarmWatcher(store).run_once() is None


# -- CLI + HTTP -----------------------------------------------------------------


def test_cli_flow(monkeypatch):
    from click.testing import CliRunner

    from openjarvis.cli.borsa_cmd import borsa

    monkeypatch.setattr(alarms, "send_telegram", lambda events: None)
    run = CliRunner().invoke
    out = run(borsa, ["alarm", "ekle", "THYAO", "230", "altına", "inerse"])
    assert out.exit_code == 0, out.output
    alarm_id = out.output.split("[")[1].split("]")[0]
    out = run(borsa, ["alarm", "ekle", "USD", "60", "üstüne", "--tekrar"])
    assert out.exit_code == 0, out.output

    out = run(borsa, ["alarm", "liste"])
    assert alarm_id in out.output and "USD" in out.output

    out = run(borsa, ["alarm", "kontrol", "--seans-disi"])
    assert out.exit_code == 0, out.output
    assert "THYAO" in out.output and "tetiklenen: 1" in out.output
    assert "Telegram ayarlı değil" in out.output

    out = run(borsa, ["alarm", "liste"])
    assert alarm_id not in out.output  # one-shot closed
    assert alarm_id in run(borsa, ["alarm", "liste", "--hepsi"]).output

    out = run(borsa, ["alarm", "sil", alarm_id])
    assert out.exit_code == 0 and "Silindi" in out.output
    out = run(borsa, ["alarm", "sil", alarm_id])
    assert out.exit_code != 0

    out = run(borsa, ["alarm", "ekle", "THYAO", "düşerse"])
    assert out.exit_code != 0


def test_http_event_feed(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.borsa_routes import create_borsa_router

    store.add("THYAO", "fiyat", "alti", 230)
    alarms.check_alarms(store, now=IN_SESSION)
    app = FastAPI()
    app.include_router(create_borsa_router(store))
    client = TestClient(app)
    events = client.get("/api/borsa/alarm/olaylar").json()["events"]
    assert len(events) == 1 and events[0]["symbol"] == "THYAO"
    after = client.get("/api/borsa/alarm/olaylar", params={"after": events[0]["id"]})
    assert after.json()["events"] == []
    assert len(client.get("/api/borsa/alarm/liste").json()["alarms"]) == 1


def test_http_fiyat(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.borsa_routes import create_borsa_router

    app = FastAPI()
    app.include_router(create_borsa_router(store))
    client = TestClient(app)

    res = client.get("/api/borsa/fiyat", params={"sembol": "thyao"})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "THYAO" and body["last"] == 225.0

    bad = client.get("/api/borsa/fiyat", params={"sembol": "THY;rm"})
    assert bad.status_code == 400


def test_http_analiz(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.borsa_routes import create_borsa_router

    app = FastAPI()
    app.include_router(create_borsa_router(store))
    client = TestClient(app)

    res = client.get("/api/borsa/analiz", params={"sembol": "THYAO", "period": "6mo"})
    assert res.status_code == 200
    assert res.json()["symbol"] == "THYAO"

    bad = client.get("/api/borsa/analiz", params={"sembol": "THYAO", "period": "10y"})
    assert bad.status_code == 400  # not a valid VALID_PERIODS value


def test_http_izleme(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.borsa_routes import create_borsa_router

    store.add("THYAO", "fiyat", "alti", 230)
    store.add("USD", "fiyat", "alti", 60)
    app = FastAPI()
    app.include_router(create_borsa_router(store))
    client = TestClient(app)

    res = client.get("/api/borsa/izleme")
    assert res.status_code == 200
    items = {i["symbol"]: i for i in res.json()["items"]}
    assert items["THYAO"]["last"] == 225.0
    assert items["USD"]["last"] == 50.5


def test_http_izleme_reports_provider_error_without_failing(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.borsa_routes import create_borsa_router

    store.add("THYAO", "fiyat", "alti", 230)
    store.add("ASELS", "fiyat", "alti", 230)  # resolves fine, no price in fake_bp
    app = FastAPI()
    app.include_router(create_borsa_router(store))
    client = TestClient(app)

    items = {i["symbol"]: i for i in client.get("/api/borsa/izleme").json()["items"]}
    assert items["THYAO"]["last"] == 225.0
    assert "error" in items["ASELS"]
