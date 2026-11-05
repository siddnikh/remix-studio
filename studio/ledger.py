"""Append-only spend ledger with a finite cap.

Amounts are integer micro-units of one currency (USD or provider credits). A reservation must fit under
the cap before money can be committed; a provider's settlement is always recorded, even past the cap,
because that money is already gone. Once over the cap every further reservation is refused.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

MICRO = 1_000_000


class LedgerError(Exception):
    pass


class CapExceeded(LedgerError):
    def __init__(self, requested: int, available: int, cap: int, currency: str, label: str):
        self.requested, self.available, self.cap, self.currency, self.label = requested, available, cap, currency, label
        super().__init__(
            f"refused {label!r}: needs {fmt(requested)} {currency}, only {fmt(available)} of the "
            f"{fmt(cap)} {currency} cap is available")


class LedgerCorrupt(LedgerError):
    pass


def to_micro(amount) -> int:
    """Exact conversion from str, int or Decimal; floats are refused because they cannot be exact."""
    if isinstance(amount, (float, bool)):
        raise TypeError("pass money as str, int or Decimal, not float or bool")
    try:
        d = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError) as e:
        raise ValueError(f"not an amount: {amount!r}") from e
    if not d.is_finite():
        raise ValueError(f"amount must be finite: {amount!r}")
    return math.ceil(d * MICRO)


def fmt(micro: int) -> str:
    return f"{Decimal(micro) / MICRO:f}"


@dataclass
class _State:
    cap: int | None = None
    currency: str | None = None
    holds: dict[str, dict] = field(default_factory=dict)
    charged: int = 0
    events: list[dict] = field(default_factory=list)

    @property
    def held(self) -> int:
        return sum(h["amount_micro"] for h in self.holds.values())

    @property
    def available(self) -> int:
        return self.cap - self.charged - self.held


class Ledger:
    def __init__(self, path: str | Path, cap, currency: str = "USD"):
        self.path = Path(path)
        self.cap = to_micro(cap)
        self.currency = currency
        if self.cap <= 0:
            raise ValueError("cap must be positive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked() as st:
            if st.cap is None:
                self._append({"event": "open", "cap_micro": self.cap, "currency": currency})
            elif st.cap != self.cap or st.currency != currency:
                raise LedgerError(
                    f"{self.path} was opened with cap {fmt(st.cap)} {st.currency}; refusing to change it to "
                    f"{fmt(self.cap)} {currency}. Start a new ledger file to set a new cap.")

    @contextmanager
    def _locked(self):
        with open(self.path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self._replay()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _replay(self) -> _State:
        st = _State()
        if not self.path.exists():
            return st
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                kind = ev["event"]
                if kind == "open":
                    st.cap, st.currency = int(ev["cap_micro"]), ev["currency"]
                elif kind == "reserve":
                    st.holds[ev["hold"]] = {**ev, "amount_micro": int(ev["amount_micro"])}
                elif kind == "settle":
                    del st.holds[ev["hold"]]
                    st.charged += int(ev["amount_micro"])
                elif kind == "release":
                    del st.holds[ev["hold"]]
                else:
                    raise ValueError(f"unknown event {kind!r}")
            except (ValueError, KeyError, TypeError) as e:
                # An unreadable ledger cannot prove how much was spent, so spending stops.
                raise LedgerCorrupt(f"{self.path}:{n}: {e}") from e
            st.events.append(ev)
        return st

    def _append(self, ev: dict) -> dict:
        ev = {"ts": round(time.time(), 3), **ev}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return ev

    def reserve(self, amount, label: str, **meta) -> str:
        """Hold `amount` before committing money. Pass the worst case the route can settle at, not its hold."""
        micro = to_micro(amount)
        if micro <= 0:
            raise ValueError("a reservation must be positive")
        with self._locked() as st:
            if micro > st.available:
                raise CapExceeded(micro, st.available, st.cap, st.currency, label)
            hold = uuid.uuid4().hex[:12]
            self._append({"event": "reserve", "hold": hold, "amount_micro": micro, "label": label, **meta})
            return hold

    def settle(self, hold: str, amount, **meta) -> dict:
        """Record what the provider actually charged. Never refused: an over-cap charge has already happened."""
        micro = to_micro(amount)
        if micro < 0:
            raise ValueError("a charge cannot be negative")
        with self._locked() as st:
            h = self._open_hold(st, hold)
            over = st.charged + micro > st.cap
            return self._append({"event": "settle", "hold": hold, "amount_micro": micro, "label": h["label"],
                                 "reserved_micro": h["amount_micro"], "over_cap": over, **meta})

    def release(self, hold: str, reason: str, **meta) -> dict:
        with self._locked() as st:
            h = self._open_hold(st, hold)
            return self._append({"event": "release", "hold": hold, "label": h["label"],
                                 "amount_micro": h["amount_micro"], "reason": reason, **meta})

    def _open_hold(self, st: _State, hold: str) -> dict:
        if hold not in st.holds:
            raise LedgerError(f"no open hold {hold!r} in {self.path}")
        return st.holds[hold]

    def open_holds(self) -> dict[str, dict]:
        with self._locked() as st:
            return dict(st.holds)

    def summary(self) -> dict:
        with self._locked() as st:
            return {"currency": st.currency, "cap": fmt(st.cap), "charged": fmt(st.charged), "held": fmt(st.held),
                    "available": fmt(max(st.available, 0)), "over_cap": st.charged > st.cap,
                    "charges": [{"label": e["label"], "amount": fmt(int(e["amount_micro"])), "ts": e["ts"]}
                                for e in st.events if e["event"] == "settle"],
                    "open_holds": [{"hold": k, "label": v["label"], "amount": fmt(v["amount_micro"])}
                                   for k, v in st.holds.items()]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ledger", description=__doc__)
    ap.add_argument("path")
    a = ap.parse_args(argv)
    p = Path(a.path)
    if not p.exists():
        print(f"no ledger at {p}", file=sys.stderr)
        return 1
    first = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    print(json.dumps(Ledger(p, Decimal(first["cap_micro"]) / MICRO, first["currency"]).summary(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
