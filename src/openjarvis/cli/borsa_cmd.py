"""``jarvis borsa`` — BIST / FX / gold quotes and technical analysis."""

from __future__ import annotations

import json

import click

_PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y")


@click.group()
def borsa() -> None:
    """BIST hisse, döviz ve altın: fiyat ve teknik analiz (~15 dk gecikmeli)."""


@borsa.command("fiyat")
@click.argument("symbols", nargs=-1, required=True)
def fiyat(symbols: tuple[str, ...]) -> None:
    """Son fiyat. Örnek: jarvis borsa fiyat THYAO dolar altin"""
    from openjarvis.borsa import data

    failed = False
    for sym in symbols:
        try:
            asset = data.resolve(sym)
            q = data.quote(asset)
        except data.BorsaError as exc:
            click.echo(f"{sym}: HATA - {exc}", err=True)
            failed = True
            continue
        chg = q.get("change_percent")
        chg_s = f"  ({chg:+.2f}%)" if isinstance(chg, (int, float)) else ""
        click.echo(f"{asset.display:<14} {q['last']}{chg_s}")
    if failed:
        raise SystemExit(1)


@borsa.command("analiz")
@click.argument("symbol")
@click.option("--donem", "period", type=click.Choice(_PERIODS), default="1y",
              show_default=True, help="Geçmiş veri dönemi.")
@click.option("--json", "as_json", is_flag=True, help="Ham JSON çıktısı.")
def analiz(symbol: str, period: str, as_json: bool) -> None:
    """Teknik analiz özeti. Örnek: jarvis borsa analiz ASELS --donem 6mo"""
    from openjarvis.borsa import analysis, data

    try:
        result = analysis.analyze(symbol, period)
    except data.BorsaError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo(analysis.format_report(result))


# -- alarms -------------------------------------------------------------------


@borsa.group("alarm")
def alarm() -> None:
    """Seviye alarmları: fiyat / RSI eşikleri (Telegram + tarayıcı bildirimi)."""


@alarm.command("ekle")
@click.argument("kural", nargs=-1, required=True)
@click.option("--tekrar", is_flag=True,
              help="Tetiklenince kapanmasın, bekleme süresi sonunda yine bildirsin.")
@click.option("--bekleme", "cooldown", type=int, default=60, show_default=True,
              help="Tekrarlı alarmda iki bildirim arası dakika.")
@click.option("--not", "note", default="", help="Bildirime eklenecek kısa not.")
def alarm_ekle(kural: tuple[str, ...], tekrar: bool, cooldown: int, note: str) -> None:
    """Alarm ekle. Örnekler:

    \b
      jarvis borsa alarm ekle THYAO 230 altına
      jarvis borsa alarm ekle dolar 50 üstüne --tekrar
      jarvis borsa alarm ekle ASELS rsi 30 altı
    """
    from openjarvis.borsa import alarms, data

    try:
        rule = alarms.parse_rule(" ".join(kural))
        a = alarms.AlarmStore().add(
            **rule, mode="tekrar" if tekrar else "tek",
            cooldown_min=cooldown, note=note,
        )
    except data.BorsaError as exc:
        raise click.ClickException(str(exc)) from exc
    mode = ("tek seferlik" if a.mode == "tek"
            else f"tekrarlı, {a.cooldown_min} dk bekleme")
    click.echo(f"Eklendi [{a.id}] {a.describe()} ({mode})")


@alarm.command("liste")
@click.option("--hepsi", is_flag=True, help="Tetiklenip kapanmış alarmları da göster.")
def alarm_liste(hepsi: bool) -> None:
    """Alarmları listele."""
    from openjarvis.borsa import alarms

    items = [a for a in alarms.AlarmStore().load() if hepsi or a.active]
    if not items:
        click.echo("Alarm yok.")
        return
    for a in items:
        state = "açık" if a.active else "kapandı"
        extra = f" | son değer {a.last_value:g}" if a.last_value is not None else ""
        if a.last_triggered:
            extra += f" | son tetik {a.last_triggered[:16]}"
        click.echo(f"[{a.id}] {a.describe():<32} {a.mode:<6} {state}{extra}")


@alarm.command("sil")
@click.argument("alarm_ids", nargs=-1, required=True)
def alarm_sil(alarm_ids: tuple[str, ...]) -> None:
    """Alarm sil (kimlik 'liste' çıktısında)."""
    from openjarvis.borsa import alarms

    store = alarms.AlarmStore()
    missing = [i for i in alarm_ids if not store.remove(i)]
    for i in alarm_ids:
        if i not in missing:
            click.echo(f"Silindi: {i}")
    if missing:
        raise click.ClickException(f"Bulunamadı: {', '.join(missing)}")


@alarm.command("kontrol")
@click.option("--seans-disi", "ignore_session", is_flag=True,
              help="BIST seans saati dışında da hisse alarmlarını kontrol et.")
@click.option("--telegramsiz", "no_telegram", is_flag=True,
              help="Telegram'a gönderme, sadece ekrana yaz.")
def alarm_kontrol(ignore_session: bool, no_telegram: bool) -> None:
    """Alarmları şimdi bir kez kontrol et ve tetiklenenleri bildir."""
    from openjarvis.borsa import alarms

    sent: list = []

    def telegram(events):  # noqa: ANN001, ANN202
        sent.append(alarms.send_telegram(events))

    result = alarms.run_check(
        notifiers=() if no_telegram else (telegram,),
        ignore_session=ignore_session,
    )
    for ev in result.triggered:
        click.echo(ev.message)
    for alarm_id, err in result.errors.items():
        click.echo(f"[{alarm_id}] HATA - {err}", err=True)
    click.echo(
        f"Kontrol: {result.checked}, tetiklenen: {len(result.triggered)}, "
        f"atlanan: {len(result.skipped)} (seans dışı / bekleme)"
    )
    if sent and sent[0] is None:
        click.echo("Telegram ayarlı değil (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); "
                   "bildirim yalnızca tarayıcıya düştü.", err=True)
    elif sent and sent[0] is False:
        click.echo("Telegram gönderimi başarısız.", err=True)


@alarm.command("izle")
@click.option("--aralik", "interval", type=int, default=300, show_default=True,
              help="Kontrol aralığı (saniye, en az 30).")
def alarm_izle(interval: int) -> None:
    """Ön planda periyodik kontrol (Ctrl+C durdurur; 'jarvis serve' açıksa gerekmez)."""
    import time

    from openjarvis.borsa import alarms

    watcher = alarms.AlarmWatcher(interval=interval)
    click.echo(f"Alarm izleniyor ({max(30, interval)} sn aralık). Durdurmak: Ctrl+C.")
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()


# -- strategy tournament --------------------------------------------------


_WEEKDAYS = {
    "pazartesi": 0, "sali": 1, "çarşamba": 2, "carsamba": 2, "perşembe": 3,
    "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}


@borsa.group("turnuva")
def turnuva() -> None:
    """Haftalık strateji turnuvası: basit kuralları geriye dönük test edip
    sıralar. Emir vermez, geçmiş veriye dayalı backtest'tir."""


@turnuva.command("calistir")
@click.argument("symbols", nargs=-1)
@click.option("--donem", "period", type=click.Choice(_PERIODS), default="6mo",
              show_default=True, help="Geriye dönük test dönemi.")
@click.option("--json", "as_json", is_flag=True, help="Ham JSON çıktısı.")
@click.option("--telegram", "send_telegram_flag", is_flag=True,
              help="Sonucu Telegram'a da gönder.")
def turnuva_calistir(
    symbols: tuple[str, ...], period: str, as_json: bool, send_telegram_flag: bool
) -> None:
    """Turnuvayı şimdi bir kez çalıştır. Sembol verilmezse alarm listesindeki
    semboller kullanılır. Örnek: jarvis borsa turnuva calistir THYAO ASELS
    """
    from openjarvis.borsa import tournament

    syms = list(symbols) or tournament.default_symbols()
    if not syms:
        raise click.ClickException(
            "Sembol yok: argüman verin ya da önce 'jarvis borsa alarm ekle' ile "
            "izleme listesine sembol ekleyin."
        )
    result = tournament.run_tournament(syms, period=period)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo(tournament.format_report(result))
    if send_telegram_flag:
        sent = tournament.send_telegram_report(result)
        if sent is None:
            click.echo(
                "Telegram ayarlı değil (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).",
                err=True,
            )
        elif sent is False:
            click.echo("Telegram gönderimi başarısız.", err=True)


@turnuva.command("izle")
@click.option("--gun", "weekday", type=click.Choice(sorted(_WEEKDAYS)),
              default="pazartesi", show_default=True, help="Haftalık çalışma günü.")
@click.option("--saat", "hour", type=click.IntRange(0, 23), default=9,
              show_default=True, help="Çalışma saati (yerel, 24 saat).")
@click.option("--donem", "period", type=click.Choice(_PERIODS), default="6mo",
              show_default=True)
def turnuva_izle(weekday: str, hour: int, period: str) -> None:
    """Ön planda haftalık zamanlayıcı (Ctrl+C durdurur; 'jarvis serve' açıksa
    gerekmez). Seçilen gün/saatte turnuvayı çalıştırıp Telegram'a gönderir."""
    import time

    from openjarvis.borsa import tournament

    watcher = tournament.TournamentScheduler(
        weekday=_WEEKDAYS[weekday], hour=hour, period=period
    )
    click.echo(f"Turnuva izleniyor (her {weekday} saat {hour:02d}:00). Durdurmak: Ctrl+C.")
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
