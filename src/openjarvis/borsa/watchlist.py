"""User-managed watchlist: symbols tracked for price/signal/news display,
independent of alarms (which exist to notify on a threshold). A symbol can
be on the watchlist with no alarm, or have an alarm with no watchlist entry.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjarvis.borsa import data


class WatchlistStore:
    """JSON-file-backed list of distinct, resolved symbols."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from openjarvis.core.paths import get_data_dir

            path = get_data_dir() / "borsa_watchlist.json"
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return raw if isinstance(raw, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list(self) -> list[str]:
        with self._lock:
            return [i["symbol"] for i in self._load()]

    def add(self, symbol: str) -> str:
        asset = data.resolve(symbol)
        with self._lock:
            items = self._load()
            if not any(i["symbol"] == asset.display for i in items):
                items.append(
                    {
                        "symbol": asset.display,
                        "kind": asset.kind,
                        "added_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                self._save(items)
        return asset.display

    def remove(self, symbol: str) -> bool:
        asset = data.resolve(symbol)
        with self._lock:
            items = self._load()
            kept = [i for i in items if i["symbol"] != asset.display]
            if len(kept) == len(items):
                return False
            self._save(kept)
            return True


__all__ = ["WatchlistStore"]
