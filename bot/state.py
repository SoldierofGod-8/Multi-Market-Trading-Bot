"""Persistent bot state (positions, cash, halts) so restarts resume cleanly."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Position:
    symbol: str
    market: str
    side: int            # +1 long, -1 short
    qty: float
    entry: float
    sl: float
    tp: float
    opened_at: str
    entry_fee: float = 0.0

    def pnl(self, price: float) -> float:
        return (price - self.entry) * self.side * self.qty


@dataclass
class BotState:
    cash: float
    peak_equity: float
    day: str = ""
    day_start_equity: float = 0.0
    halted_day: Optional[str] = None
    halted_permanent: bool = False
    halt_reason: str = ""
    positions: Dict[str, Position] = field(default_factory=dict)
    last_bar: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path, starting_equity: float) -> "BotState":
        p = Path(path)
        if not p.exists():
            return cls(cash=starting_equity, peak_equity=starting_equity)
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["positions"] = {k: Position(**v) for k, v in raw.get("positions", {}).items()}
        return cls(**raw)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)  # atomic on same filesystem
