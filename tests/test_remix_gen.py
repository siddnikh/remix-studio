import json
import subprocess
from pathlib import Path

import pytest
from decimal import Decimal

from studio import gen, providers

ROOT = Path(__file__).resolve().parents[1]

VIDEO = "reapi.video-gen.seedance-2-5"
IMAGE = "reapi.image-gen.gpt-image-2-5"


class FakeTreg:
    """Counts submits, so a test can prove nothing was paid twice. Never touches a network."""

    def __init__(self, polls=("completed",), fail_submit=None):
        self.submits = []
        self.polls = list(polls)
        self.fail_submit = fail_submit

    def call(self, tool, body=None, query=None, idempotency_key=None):
        if body is not None:
            if self.fail_submit:
                err, self.fail_submit = self.fail_submit, None
                if isinstance(err, Exception):
                    raise err
                return {"error": err}, {"http_status": 400}
            self.submits.append((tool, body, idempotency_key))
            return {"id": f"task{len(self.submits)}"}, {"http_status": 200, "call_id": "c"}
        status = self.polls.pop(0) if self.polls else "completed"
        doc = {"status": status, "usage": {"credits": 500}, "output": {"video_urls": ["https://x/a.mp4"], "image_urls": ["https://x/a.png"]}}
        if status == "failed":
            doc["error"] = "moderated"
        return doc, {"http_status": 200}


def fetch_clip(url, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x64:r=24", "-frames:v", "20", str(dest)] if dest.suffix == ".mp4"
                   else ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x64:r=24", "-frames:v", "1", str(dest)], check=True)


def write_plan(tmp: Path, lines, cap=5.0, **extra) -> Path:
    d = tmp / "plan"
    d.mkdir(exist_ok=True)
    p = d / "gen_plan.json"
    p.write_text(json.dumps({"cap": cap, "currency": "USD", "lines": lines, **extra}))
    return p


def clip_line(id="c1", canary=True, family="fox", worst=1.0, body=None, **kw):
    return {"id": id, "kind": "clip", "family": family, "canary": canary, "route": VIDEO, "worst_case": worst,
            "body": body or {"prompt": "p"}, "out": f"clips/{id}.mp4", **kw}


def make(tmp, lines, treg=None, **extra):
    return gen.Run(write_plan(tmp, lines, **extra), treg=treg or FakeTreg(), sleep=lambda s: None, fetch=fetch_clip)


def test_clip_runs_settles_the_real_charge_and_reports_real_frames(tmp_path):
    r = make(tmp_path, [clip_line()])
    res = r.run_line("c1")
    assert res["status"] == "done" and res["frames"] == 20
    assert res["charged"] == "0.5" and not res["charge_estimated"]
    s = r.ledger.summary()
    assert Path(res["path"]).exists() and s["charges"][0]["label"] == "c1" and s["open_holds"] == []


def test_cap_refuses_before_anything_is_submitted(tmp_path):
    t = FakeTreg()
    r = make(tmp_path, [clip_line(worst=9.0)], treg=t)
    with pytest.raises(gen.GenError, match="cap"):
        r.run_line("c1")
    assert t.submits == []


def test_batch_line_is_refused_until_its_canary_passes(tmp_path):
    t = FakeTreg()
    r = make(tmp_path, [clip_line("c1"), clip_line("c2", canary=False)], treg=t)
    with pytest.raises(gen.GenError, match="no passed canary"):
        r.run_line("c2")
    with pytest.raises(gen.GenError, match="run its canary"):
        r.pass_canary("fox", "too early")
    r.run_line("c1")
    r.pass_canary("fox", "identity holds")
    assert r.run_line("c2")["status"] == "done"
    assert len(t.submits) == 2


def test_finished_line_is_not_paid_for_twice(tmp_path):
    t = FakeTreg()
    r = make(tmp_path, [clip_line()], treg=t)
    r.run_line("c1")
    r2 = gen.Run(r.plan_path, treg=t, sleep=lambda s: None, fetch=fetch_clip)
    assert r2.run_line("c1")["status"] == "done"
    assert len(t.submits) == 1


def test_crash_during_polling_resumes_the_same_task(tmp_path):
    class Boom(Exception):
        pass

    t = FakeTreg(polls=["processing", "completed"])
    naps = []

    def sleep(s):
        naps.append(s)
        raise Boom

    r = gen.Run(write_plan(tmp_path, [clip_line()]), treg=t, sleep=sleep, fetch=fetch_clip)
    with pytest.raises(Boom):
        r.run_line("c1")
    r2 = gen.Run(r.plan_path, treg=t, sleep=lambda s: None, fetch=fetch_clip)
    assert r2.run_line("c1")["status"] == "done"
    assert len(t.submits) == 1


def test_submit_timeout_keeps_the_hold_and_retries_with_the_same_idempotency_key(tmp_path):
    t = FakeTreg(fail_submit=subprocess.TimeoutExpired("treg", 180))
    r = make(tmp_path, [clip_line()], treg=t)
    with pytest.raises(gen.GenError, match="unconfirmed"):
        r.run_line("c1")
    assert len(r.ledger.summary()["open_holds"]) == 1
    first_key = r.state["lines"]["c1"][0]["idempotency_key"]
    assert r.run_line("c1")["status"] == "done"
    assert t.submits[0][2] == first_key and len(t.submits) == 1


def test_refusal_releases_the_hold_and_is_not_auto_retried(tmp_path):
    t = FakeTreg(fail_submit="moderated")
    r = make(tmp_path, [clip_line()], treg=t)
    res = r.run_line("c1")
    assert res["status"] == "refused" and r.ledger.summary()["charged"] == "0"
    with pytest.raises(gen.GenError, match="Never resubmit a refusal unchanged"):
        r.run_line("c1")


def test_failed_task_releases_hold_and_warns_about_refunds(tmp_path):
    r = make(tmp_path, [clip_line()], treg=FakeTreg(polls=["failed"]))
    res = r.run_line("c1")
    assert res["status"] == "failed" and "not proof of a refund" in res["note"]
    assert r.ledger.summary()["open_holds"] == []


def test_clip_gets_one_retry_only_and_it_needs_a_reason(tmp_path):
    t = FakeTreg()
    r = make(tmp_path, [clip_line()], treg=t)
    r.run_line("c1")
    assert r.run_line("c1", reroll="identity")["n"] == 2
    with pytest.raises(gen.GenError, match="used its 2 attempts"):
        r.run_line("c1", reroll="still bad")
    assert len(t.submits) == 2 and t.submits[0][2] != t.submits[1][2]


def test_a_plan_cannot_raise_its_own_attempt_limit(tmp_path):
    r = make(tmp_path, [clip_line(max_attempts=99)])
    r.run_line("c1")
    r.run_line("c1", reroll="a")
    with pytest.raises(gen.GenError, match="used its 2 attempts"):
        r.run_line("c1", reroll="b")


def test_dependent_line_waits_for_its_upstream_result_and_binds_it(tmp_path):
    t = FakeTreg()
    still = {"id": "kf", "kind": "still", "family": "s", "canary": True, "route": IMAGE, "worst_case": 0.02, "poll_tool": "reapi.tasks.get",
             "body": {"prompt": "x"}, "out": "stills/kf.png"}
    clip = clip_line(body={"prompt": "p", "image_urls": ["<result of kf>"]})
    r = make(tmp_path, [still, clip], treg=t)
    with pytest.raises(gen.GenError, match="waiting on line kf"):
        r.run_line("c1")
    r.run_line("kf")
    r.run_line("c1")
    assert t.submits[1][1]["image_urls"] == ["https://x/a.mp4"]


def test_hosting_a_local_file_needs_recorded_consent(tmp_path):
    class Host(FakeTreg):
        hosted = 0

        def host(self, p):
            Host.hosted += 1
            return {"url": "https://host/x.png"}

    t = Host()
    line = clip_line(body={"prompt": "p", "image_urls": ["<host ../subject.png>"]})
    with pytest.raises(gen.GenError, match="hosting_consent"):
        make(tmp_path, [line], treg=t).run_line("c1")
    assert Host.hosted == 0 and t.submits == []
    (tmp_path / "subject.png").write_bytes(b"x")
    assert make(tmp_path, [line], treg=t, hosting_consent=True).run_line("c1")["status"] == "done"
    assert t.submits[0][1]["image_urls"] == ["https://host/x.png"]


def test_quote_totals_the_plan_and_its_retry_reserve(tmp_path):
    r = make(tmp_path, [clip_line("c1", worst=1.0), clip_line("c2", worst=2.0)], cap=4.0)
    q = r.quote()
    assert q["planned"] == "3.0" and q["with_all_retries"] == "6.0"
    assert q["fits_cap"] and not q["retries_fit_cap"]


def test_cap_cannot_be_raised_by_editing_the_plan(tmp_path):
    from studio.ledger import LedgerError

    r = make(tmp_path, [clip_line()])
    r.plan["cap"] = 50
    r.plan_path.write_text(json.dumps(r.plan))
    with pytest.raises(LedgerError, match="refusing to change"):
        gen.Run(r.plan_path, treg=FakeTreg())


def test_documented_plan_is_valid_and_gates_as_described(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIX_STUDIO_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("SUME_API_KEY", "test")
    doc = (ROOT / "skills" / "remix" / "references" / "generation.md").read_text()
    plan = json.loads(doc.split("```json")[1].split("```")[0])
    d = tmp_path / "plan"
    d.mkdir()
    (d / "gen_plan.json").write_text(json.dumps(plan))
    r = gen.Run(d / "gen_plan.json", treg=FakeTreg(), sleep=lambda s: None, fetch=fetch_clip)
    assert r.quote()["fits_cap"]
    with pytest.raises(gen.GenError, match="hosting_consent"):
        r.run_line("kf1")
    with pytest.raises(gen.GenError, match="no passed canary"):
        r.run_line("plate1")
    with pytest.raises(gen.GenError, match="waiting on line kf1"):
        r.run_line("c1")


def test_find_url_reads_common_shapes():
    assert providers.find_url({"output": {"image_urls": ["https://a/b.png"]}}) == "https://a/b.png"
    assert providers.find_url({"data": [{"url": "https://a/c.png"}]}) == "https://a/c.png"
    assert providers.find_url({"status": "processing"}) is None
