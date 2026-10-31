"""BIST / FX / gold quote and technical-analysis tools (read-only)."""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_SYMBOL_DESC = (
    "BIST hisse kodu (THYAO, ASELS) veya döviz/altın: USD, EUR, GBP, "
    "gram-altin, ons-altin, ceyrek-altin. 'dolar', 'euro', 'altın' da olur."
)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@ToolRegistry.register("borsa_fiyat")
class BorsaQuoteTool(BaseTool):
    """Latest (delayed) price for a BIST stock, currency or gold."""

    tool_id = "borsa_fiyat"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="borsa_fiyat",
            description=(
                "BIST hissesi, döviz (USD/EUR) veya altının son fiyatını ve "
                "günlük değişimini getirir. Veri ~15 dk gecikmelidir."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": _SYMBOL_DESC},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            category="finance",
            latency_estimate=3.0,
            timeout_seconds=60.0,
            required_capabilities=["network:fetch"],
            metadata={"provider": "borsapy"},
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.borsa import data

        try:
            asset = data.resolve(params.get("symbol", ""))
            result = {"symbol": asset.display, "kind": asset.kind, **data.quote(asset)}
        except data.BorsaError as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        return ToolResult(
            tool_name=self.tool_id,
            content=_dump(result),
            success=True,
            metadata={"symbol": asset.display},
        )


@ToolRegistry.register("borsa_analiz")
class BorsaAnalysisTool(BaseTool):
    """Technical summary (trend, RSI, MACD, Bollinger, support/resistance)."""

    tool_id = "borsa_analiz"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        from openjarvis.borsa.data import VALID_PERIODS

        return ToolSpec(
            name="borsa_analiz",
            description=(
                "BIST hissesi, döviz veya altın için teknik analiz özeti: "
                "SMA20/50/200, RSI, MACD, Bollinger, ATR, destek/direnç ve "
                "-100..100 eğilim skoru. Yatırım tavsiyesi değildir; "
                "yanıtta bunu belirt."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": _SYMBOL_DESC},
                    "period": {
                        "type": "string",
                        "enum": list(VALID_PERIODS),
                        "description": "Geçmiş veri dönemi. Varsayılan 1y.",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            category="finance",
            latency_estimate=5.0,
            timeout_seconds=90.0,
            required_capabilities=["network:fetch"],
            metadata={"provider": "borsapy"},
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.borsa import analysis, data

        try:
            result = analysis.analyze(
                params.get("symbol", ""), params.get("period", "1y")
            )
        except data.BorsaError as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        return ToolResult(
            tool_name=self.tool_id,
            content=_dump(result),
            success=True,
            metadata={"symbol": result["symbol"], "score": result["score"]},
        )


__all__ = ["BorsaAnalysisTool", "BorsaQuoteTool"]
