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
