import json
from decimal import Decimal

import pytest

from studio.ledger import CapExceeded, Ledger, LedgerCorrupt, LedgerError, to_micro


@pytest.fixture
def path(tmp_path):
    return tmp_path / "ledger.jsonl"


@pytest.mark.parametrize("cap", ["inf", "NaN", "-1", "0"])
def test_cap_must_be_finite_and_positive(path, cap):
    with pytest.raises(ValueError):
        Ledger(path, cap)


def test_refuses_a_reservation_past_the_cap_and_records_nothing(path):
    led = Ledger(path, "0.50")
    led.reserve("0.30", "first")
    before = path.read_text()
    with pytest.raises(CapExceeded):
        led.reserve("0.21", "second")
    assert path.read_text() == before
    assert led.summary()["available"] == "0.2"


def test_reservation_exactly_at_cap_is_allowed(path):
    Ledger(path, "0.50").reserve("0.50", "all of it")


def test_settle_records_the_actual_charge_and_frees_the_difference(path):
    led = Ledger(path, "1")
    hold = led.reserve("0.60", "job")
    led.settle(hold, "0.49", task_id="t1")
    s = led.summary()
    assert s["charged"] == "0.49" and s["held"] == "0" and s["available"] == "0.51"
    assert s["charges"][0]["label"] == "job" and s["charges"][0]["amount"] == "0.49"


def test_settle_past_the_cap_is_recorded_and_then_blocks_spending(path):
    led = Ledger(path, "0.50")
    hold = led.reserve("0.41", "under-reserved")
    ev = led.settle(hold, "0.95")
    assert ev["over_cap"] is True
    s = led.summary()
    assert s["charged"] == "0.95" and s["over_cap"] is True and s["available"] == "0"
    with pytest.raises(CapExceeded):
        led.reserve("0.01", "next")


def test_release_returns_the_hold(path):
    led = Ledger(path, "1")
    hold = led.reserve("1", "job")
    led.release(hold, "task failed")
    assert led.summary()["available"] == "1" and led.summary()["charged"] == "0"


def test_a_hold_can_only_close_once(path):
    led = Ledger(path, "1")
    hold = led.reserve("0.5", "job")
    led.settle(hold, "0.5")
    with pytest.raises(LedgerError):
        led.settle(hold, "0.5")
    with pytest.raises(LedgerError):
        led.release(hold, "again")


def test_state_survives_reopening_and_open_holds_still_count(path):
    Ledger(path, "1").reserve("0.7", "crashed before settling")
    again = Ledger(path, "1")
    assert list(v["label"] for v in again.open_holds().values()) == ["crashed before settling"]
    with pytest.raises(CapExceeded):
        again.reserve("0.4", "next")


def test_cap_cannot_be_changed_by_reopening(path):
    Ledger(path, "1")
    with pytest.raises(LedgerError, match="refusing to change"):
        Ledger(path, "100")
    with pytest.raises(LedgerError, match="refusing to change"):
        Ledger(path, "1", currency="credits")


def test_corrupt_ledger_refuses_to_spend(path):
    led = Ledger(path, "1")
    with open(path, "a") as f:
        f.write("{not json\n")
    with pytest.raises(LedgerCorrupt):
        led.reserve("0.1", "job")


def test_close_of_unknown_hold_in_file_is_corruption(path):
    Ledger(path, "1")
    with open(path, "a") as f:
        f.write(json.dumps({"event": "settle", "hold": "nope", "amount_micro": 1}) + "\n")
    with pytest.raises(LedgerCorrupt):
        Ledger(path, "1")


def test_float_money_is_refused_and_rounding_is_up():
    with pytest.raises(TypeError):
        to_micro(0.1)
    assert to_micro("0.0000001") == 1
    assert to_micro(Decimal("1.5")) == 1_500_000


def test_every_charge_is_on_disk(path):
    led = Ledger(path, "2")
    for n in range(3):
        led.settle(led.reserve("0.5", f"job{n}"), "0.25")
    events = [json.loads(x) for x in path.read_text().splitlines()]
    assert [e["amount_micro"] for e in events if e["event"] == "settle"] == [250000] * 3
