"""Level alarms for BIST stocks, FX and gold (price / RSI thresholds).

Alarms live in ``<openjarvis root>/borsa_alarms.json``; every trigger is also
appended to ``borsa_alarm_events.jsonl`` so the web UI can raise a browser
notification. Checks are deterministic (no LLM) and run either on demand
(``jarvis borsa alarm kontrol``) or periodically via :class:`AlarmWatcher`.

Stock alarms are skipped outside the BIST session; FX/gold run any time.
One-shot alarms deactivate after firing; repeating alarms honour a cooldown.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from openjarvis.borsa import analysis, data

logger = logging.getLogger(__name__)

# Turkey has used fixed UTC+3 since 2016; avoids a tzdata dependency on Windows.
TR_TZ = timezone(timedelta(hours=3), "Europe/Istanbul")
SESSION_OPEN = (10, 0)
SESSION_CLOSE = (18, 10)  # continuous session + closing auction

METRICS = ("fiyat", "rsi")
OPS = {"alti": "<", "ustu": ">"}
MODES = ("tek", "tekrar")
DEFAULT_COOLDOWN_MIN = 60
_MAX_EVENTS = 200

_RULE_RE = re.compile(
    r"^\s*(?P<sym>[^\s]+(?:\s+altin)?)\s+(?:(?P<rsi>rsi)\s+)?"
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<op>alt\w*|ust\w*|uzer\w*|<|>)",
    re.IGNORECASE,
)
_TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


@dataclass(slots=True)
class Alarm:
    id: str
    symbol: str  # user-facing symbol, resolved again at check time
    metric: str  # "fiyat" | "rsi"
    op: str  # "alti" | "ustu"
    threshold: float
    mode: str = "tek"
    cooldown_min: int = DEFAULT_COOLDOWN_MIN
    active: bool = True
    created_at: str = ""
    last_triggered: str | None = None
    last_value: float | None = None
    note: str = ""

    def describe(self) -> str:
        what = "RSI14" if self.metric == "rsi" else "fiyat"
        side = "altına" if self.op == "alti" else "üstüne"
        return f"{self.symbol} {what} {self.threshold:g} {side}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Alarm:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(slots=True)
class AlarmEvent:
    id: str
    alarm_id: str
    symbol: str
    message: str
    value: float
    at: str
    meta: dict[str, Any] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_rule(text: str) -> dict[str, Any]:
    """Parse rules like ``"THYAO 230 altına inerse"`` or ``"ASELS rsi 30 altı"``."""
    if not isinstance(text, str):
        raise data.BorsaError("Kural metin olmalı.")
    m = _RULE_RE.match(text.translate(_TR_ASCII))
    if not m:
        raise data.BorsaError(
            f"Kural anlaşılamadı: {text!r}. Örnek: 'THYAO 230 altına', "
            "'USD 50 üstüne', 'ASELS rsi 30 altı'"
        )
    op_raw = m["op"].lower()
    op = "alti" if op_raw.startswith(("alt", "<")) else "ustu"
    return {
        "symbol": m["sym"],
        "metric": "rsi" if m["rsi"] else "fiyat",
        "op": op,
        "threshold": float(m["num"].replace(",", ".")),
    }


def is_bist_session(now: datetime | None = None) -> bool:
    """True during BIST weekday trading hours (holidays are not modelled)."""
    local = (now or _now()).astimezone(TR_TZ)
    if local.weekday() >= 5:
        return False
    hm = (local.hour, local.minute)
    return SESSION_OPEN <= hm < SESSION_CLOSE


class AlarmStore:
    """JSON-file persistence for alarms plus a capped JSONL event log."""

    def __init__(self, path: Path | None = None, events_path: Path | None = None):
        if path is None:
            from openjarvis.core.paths import get_data_dir

            path = get_data_dir() / "borsa_alarms.json"
        self.path = Path(path)
        self.events_path = (
            Path(events_path) if events_path
            else self.path.with_name("borsa_alarm_events.jsonl")
        )
        self._lock = threading.RLock()

    # -- alarms ---------------------------------------------------------------

    def load(self) -> list[Alarm]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            except json.JSONDecodeError:
                backup = self.path.with_name(f"{self.path.name}.bozuk-{time.time_ns()}")
                self.path.replace(backup)
                logger.warning("Bozuk alarm dosyası %s olarak saklandı", backup)
                return []
            return [Alarm.from_dict(d) for d in raw if isinstance(d, dict)]

    def save(self, alarms: Iterable[Alarm]) -> None:
        payload = json.dumps([asdict(a) for a in alarms], ensure_ascii=False, indent=2)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)

    def add(
        self,
        symbol: str,
        metric: str,
        op: str,
        threshold: float,
        *,
        mode: str = "tek",
        cooldown_min: int = DEFAULT_COOLDOWN_MIN,
        note: str = "",
    ) -> Alarm:
        asset = data.resolve(symbol)  # validates the symbol
        if metric not in METRICS:
            raise data.BorsaError(f"Metrik şunlardan biri olmalı: {', '.join(METRICS)}")
        if op not in OPS:
            raise data.BorsaError("Yön 'alti' ya da 'ustu' olmalı.")
        if mode not in MODES:
            raise data.BorsaError("Mod 'tek' ya da 'tekrar' olmalı.")
        threshold = float(threshold)
        if not threshold > 0 or (metric == "rsi" and threshold >= 100):
            raise data.BorsaError("Eşik pozitif olmalı (RSI için 0-100 arası).")
        if cooldown_min < 1:
            raise data.BorsaError("Bekleme süresi en az 1 dakika olmalı.")
        alarm = Alarm(
            id=uuid.uuid4().hex[:8],
            symbol=asset.display,
            metric=metric,
            op=op,
            threshold=threshold,
            mode=mode,
            cooldown_min=int(cooldown_min),
            created_at=_now().isoformat(),
            note=note[:200],
        )
        with self._lock:
            alarms = self.load()
            alarms.append(alarm)
            self.save(alarms)
        return alarm

    def remove(self, alarm_id: str) -> bool:
        with self._lock:
            alarms = self.load()
            kept = [a for a in alarms if a.id != alarm_id]
            if len(kept) == len(alarms):
                return False
            self.save(kept)
            return True

    # -- events ---------------------------------------------------------------

    def append_events(self, events: Iterable[AlarmEvent]) -> None:
        new = [json.dumps(asdict(e), ensure_ascii=False) for e in events]
        if not new:
            return
        with self._lock:
            lines: list[str] = []
            if self.events_path.exists():
                lines = self.events_path.read_text(encoding="utf-8").splitlines()
            lines = (lines + new)[-_MAX_EVENTS:]
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def events(self, after: str = "") -> list[dict[str, Any]]:
        """Events newer than event id ``after`` (ids sort chronologically)."""
        with self._lock:
            if not self.events_path.exists():
                return []
            out = []
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("id", "") > after:
                    out.append(ev)
            return out


# -- evaluation ---------------------------------------------------------------


def _metric_value(alarm: Alarm, cache: dict[tuple[str, str], float]) -> float:
    key = (alarm.symbol, alarm.metric)
    if key not in cache:
        if alarm.metric == "rsi":
            rsi = analysis.analyze(alarm.symbol, "6mo")["indicators"]["rsi14"]
            if rsi is None:
                raise data.BorsaError(f"{alarm.symbol} için RSI hesaplanamadı.")
            cache[key] = float(rsi)
        else:
            cache[key] = float(data.quote(data.resolve(alarm.symbol))["last"])
    return cache[key]


def _in_cooldown(alarm: Alarm, now: datetime) -> bool:
    if not alarm.last_triggered:
        return False
    last = datetime.fromisoformat(alarm.last_triggered)
    return now - last < timedelta(minutes=alarm.cooldown_min)


@dataclass(slots=True)
class CheckResult:
    triggered: list[AlarmEvent] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # alarm ids
    errors: dict[str, str] = field(default_factory=dict)  # alarm id -> message
    checked: int = 0


def check_alarms(
    store: AlarmStore,
    *,
    now: datetime | None = None,
    ignore_session: bool = False,
) -> CheckResult:
    """Evaluate active alarms, persist state and log any triggers."""
    now = now or _now()
    session_open = ignore_session or is_bist_session(now)
    result = CheckResult()
    cache: dict[tuple[str, str], float] = {}

    with store._lock:
        alarms = store.load()
        for alarm in alarms:
            if not alarm.active:
                continue
            try:
                kind = data.resolve(alarm.symbol).kind
            except data.BorsaError as exc:
                result.errors[alarm.id] = str(exc)
                continue
            if kind == "stock" and not session_open:
                result.skipped.append(alarm.id)
                continue
            if _in_cooldown(alarm, now):
                result.skipped.append(alarm.id)
                continue
            try:
                value = _metric_value(alarm, cache)
            except data.BorsaError as exc:
                result.errors[alarm.id] = str(exc)
                continue
            result.checked += 1
            alarm.last_value = value
            if alarm.op == "alti":
                hit = value < alarm.threshold
            else:
                hit = value > alarm.threshold
            if not hit:
                continue
            alarm.last_triggered = now.isoformat()
            if alarm.mode == "tek":
                alarm.active = False
            msg = f"🔔 {alarm.describe()} — şu an {value:g}"
            if alarm.note:
                msg += f" ({alarm.note})"
            result.triggered.append(AlarmEvent(
                id=f"{time.time_ns():020d}-{alarm.id}",
                alarm_id=alarm.id,
                symbol=alarm.symbol,
                message=msg,
                value=value,
                at=now.isoformat(),
                meta={"metric": alarm.metric, "op": alarm.op,
                      "threshold": alarm.threshold, "mode": alarm.mode},
            ))
        store.save(alarms)
        store.append_events(result.triggered)
    return result


# -- notification -------------------------------------------------------------


def _vault_get(key: str) -> str | None:
    try:
        from openjarvis.cli.vault_cmd import _load_vault

        value = _load_vault().get(key)
    except Exception:
        return None
    return value or None


def telegram_target() -> tuple[str | None, str | None]:
    """(bot token, chat id) from credentials.toml / vault / env / config."""
    import os

    from openjarvis.core.credentials import get_tool_credential

    token = (get_tool_credential("telegram", "TELEGRAM_BOT_TOKEN")
             or _vault_get("TELEGRAM_BOT_TOKEN"))
    chat = os.environ.get("TELEGRAM_CHAT_ID") or _vault_get("TELEGRAM_CHAT_ID")
    if not chat:
        try:
            from openjarvis.core.config import load_config

            ids = load_config().channel.telegram.allowed_chat_ids
            chat = next((c.strip() for c in ids.split(",") if c.strip()), None)
        except Exception:
            chat = None
    return token, chat


def send_telegram(events: list[AlarmEvent]) -> bool | None:
    """Send triggers to Telegram. ``None`` = not configured, else success flag."""
    if not events:
        return True
    token, chat = telegram_target()
    if not token or not chat:
        return None
    from openjarvis.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token=token, parse_mode="")
    text = "\n".join(e.message for e in events) + "\n(Veri ~15 dk gecikmeli olabilir.)"
    return channel.send(chat, text)


Notifier = Callable[[list[AlarmEvent]], Any]


def run_check(
    store: AlarmStore | None = None,
    *,
    notifiers: Iterable[Notifier] | None = None,
    ignore_session: bool = False,
) -> CheckResult:
    """Check alarms and push triggers to every notifier (default: Telegram).

    Browser notifications need no notifier: the web UI polls the event log.
    """
    store = store or AlarmStore()
    result = check_alarms(store, ignore_session=ignore_session)
    if result.triggered:
        for notify in (notifiers if notifiers is not None else (send_telegram,)):
            try:
                notify(result.triggered)
            except Exception:
                logger.exception("Alarm bildirimi gönderilemedi")
    return result


class AlarmWatcher:
    """Background thread that calls :func:`run_check` every ``interval`` seconds.

    Mirrors :class:`openjarvis.scheduler.TaskScheduler`'s polling loop; the
    generic scheduler is not used because its tasks are LLM prompts.
    """

    def __init__(self, store: AlarmStore | None = None, *, interval: int = 300,
                 notifiers: Iterable[Notifier] | None = None) -> None:
        self._store = store or AlarmStore()
        self._interval = max(30, int(interval))
        self._notifiers = notifiers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> CheckResult | None:
        if not any(a.active for a in self._store.load()):
            return None
        return run_check(self._store, notifiers=self._notifiers)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Borsa alarm kontrolü başarısız")
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="jarvis-borsa-alarm")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


__all__ = [
    "Alarm",
    "AlarmEvent",
    "AlarmStore",
    "AlarmWatcher",
    "CheckResult",
    "check_alarms",
    "is_bist_session",
    "parse_rule",
    "run_check",
    "send_telegram",
    "telegram_target",
]
