import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from studio import analyze, genjutsu as G
from studio.ledger import CapExceeded, Ledger
from studio.providers import Job, Outcome, ProviderError, Quote, Submission, SubmitRefused


def make_clip(path, seconds=2.0, fps=24, size="96x170", audio=False):
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=duration={seconds}", "-c:a", "aac"]
    subprocess.run(args + ["-pix_fmt", "yuv420p", str(path)], check=True)
    return path


SHOTS = {
    "duration_s": 2,
    "subject": {"name": "FOX", "description": "a green fox."},
    "source_subject": "woman",
    "setting": "a plaza.",
    "keep": ["The scooter is red."],
    "shots": [{"start": 0, "end": 1.33, "framing": "Wide.", "camera": "pushes in.", "action": "sits."},
              {"start": 1.33, "end": 2, "framing": "Close.", "camera": "still.", "action": "drinks."}],
}


def tag(kind, n=1):
    return f"@{kind}{n}"


class FakeProvider:
    route, currency, idempotent = "fake.route", "USD", True

    def __init__(self, outcomes=None, charged=Decimal("0.49"), clip=None, submit_error=None, ceiling="0.50", download_error=None):
        self.outcomes = list(outcomes or [Outcome("succeeded", charged=charged)])
        self.clip, self.submit_error, self.ceiling, self.download_error = clip, submit_error, ceiling, download_error
        self.submits, self.polls, self.keys = 0, 0, []

    def tag(self, kind, n=1):
        return tag(kind, n)

    def quote(self, job):
        return Quote(self.route, "USD", Decimal("0.41"), Decimal("0.49"), Decimal(self.ceiling), "test")

    def preflight(self, quote):
        pass

    def submit(self, job, key):
        self.submits += 1
        self.keys.append(key)
        if self.submit_error:
            raise self.submit_error
        return Submission(self.route, "task-1", "call-1", Decimal("0.41"), {"id": "task-1"})

    def poll(self, sub):
        self.polls += 1
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]

    def download(self, sub, outcome, dest):
        if self.download_error:
            raise self.download_error
        Path(dest).write_bytes(Path(self.clip).read_bytes() if self.clip else b"video")


def job():
    return Job("p", "https://x/v", ("https://x/i",), 2, 2.0)


def exe(provider, led, tmp_path, **kw):
    return G.execute(provider, job(), led, tmp_path, sleep=lambda s: None, poll_interval=0, **kw)


# ---------------------------------------------------------------- execute: money behaviour

def test_success_settles_the_actual_charge(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    task = exe(FakeProvider(), led, tmp_path)
    assert task["state"] == "done" and task["charge_source"] == "provider"
    assert led.summary()["charged"] == "0.49" and led.summary()["held"] == "0"
    assert (tmp_path / "raw.mp4").exists()


def test_cap_refusal_means_nothing_is_submitted(tmp_path):
    prov = FakeProvider(ceiling="0.50")
    with pytest.raises(CapExceeded):
        exe(prov, Ledger(tmp_path / "l", "0.49"), tmp_path)
    assert prov.submits == 0 and not (tmp_path / "task.json").exists()


def test_ledger_holds_the_ceiling_not_the_provider_hold(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(outcomes=[Outcome("pending")])
    with pytest.raises(G.StillRunning):
        exe(prov, led, tmp_path, timeout=0)
    assert led.summary()["held"] == "0.5"


def test_failed_task_releases_the_hold_and_cannot_be_rerun_silently(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(outcomes=[Outcome("failed", error="moderated")])
    with pytest.raises(G.PipelineError, match="moderated"):
        exe(prov, led, tmp_path)
    assert led.summary()["charged"] == "0" and led.summary()["held"] == "0"
    with pytest.raises(G.PipelineError, match="already failed"):
        exe(prov, led, tmp_path)
    assert prov.submits == 1


def test_refused_submit_releases_the_hold_and_leaves_no_task(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    with pytest.raises(SubmitRefused):
        exe(FakeProvider(submit_error=SubmitRefused("402")), led, tmp_path)
    assert led.summary()["held"] == "0" and not (tmp_path / "task.json").exists()


def test_unknown_submit_error_keeps_the_hold_because_money_may_have_moved(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    with pytest.raises(ProviderError):
        exe(FakeProvider(submit_error=ProviderError("connection dropped")), led, tmp_path)
    assert led.summary()["held"] == "0.5"


def test_resubmit_after_a_lost_answer_uses_the_same_idempotency_key(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(submit_error=ProviderError("connection dropped"))
    with pytest.raises(ProviderError):
        exe(prov, led, tmp_path)
    prov.submit_error = None
    assert exe(prov, led, tmp_path)["state"] == "done"
    assert prov.submits == 2 and prov.keys[0] == prov.keys[1]
    assert led.summary()["charged"] == "0.49" and len(led.summary()["charges"]) == 1


def test_a_provider_without_idempotency_is_never_blindly_resubmitted(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(submit_error=ProviderError("session died"))
    prov.idempotent = False
    with pytest.raises(ProviderError):
        exe(prov, led, tmp_path)
    prov.submit_error = None
    with pytest.raises(G.PipelineError, match="check .* by hand"):
        exe(prov, led, tmp_path)
    assert prov.submits == 1


def test_rerun_resumes_the_running_task_without_paying_again(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(outcomes=[Outcome("pending")])
    with pytest.raises(G.StillRunning):
        exe(prov, led, tmp_path, timeout=0)
    prov.outcomes = [Outcome("succeeded", charged=Decimal("0.49"))]
    assert exe(prov, led, tmp_path)["state"] == "done"
    assert prov.submits == 1 and len(led.summary()["charges"]) == 1


def test_download_failure_keeps_the_settled_charge_and_rerun_only_redownloads(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    prov = FakeProvider(download_error=ProviderError("expired"))
    with pytest.raises(ProviderError):
        exe(prov, led, tmp_path)
    assert led.summary()["charged"] == "0.49"
    prov.download_error = None
    assert exe(prov, led, tmp_path)["state"] == "done"
    assert prov.submits == 1 and len(led.summary()["charges"]) == 1


def test_a_run_directory_belongs_to_one_job(tmp_path):
    led = Ledger(tmp_path / "l", "2")
    exe(FakeProvider(), led, tmp_path)
    with pytest.raises(G.PipelineError, match="different job"):
        G.execute(FakeProvider(), Job("other", "https://x/v", (), 2, 2.0), led, tmp_path)


def test_missing_provider_charge_falls_back_to_the_quote_and_says_so(tmp_path):
    led = Ledger(tmp_path / "l", "1")
    task = exe(FakeProvider(outcomes=[Outcome("succeeded")]), led, tmp_path)
    assert task["charge_source"] == "quote" and led.summary()["charged"] == "0.49"


# ---------------------------------------------------------------- normalise and duration

def test_normalise_reencodes_to_whole_seconds_at_24_fps(tmp_path):
    src = make_clip(tmp_path / "a.mp4", seconds=2.5, fps=30)
    n = G.normalise(src, tmp_path / "n.mp4")
    info = analyze.probe(n["path"])
    assert n["seconds"] == 2 and n["method"] == "reencoded"
    assert info["fps_rational"] == "24/1" and info["frames"] == 48 and info["duration"] == 2.0


def test_normalise_keeps_a_conforming_clip_byte_identical(tmp_path):
    src = make_clip(tmp_path / "a.mp4", seconds=2)
    n = G.normalise(src, tmp_path / "n.mp4")
    assert n["method"] == "copied" and Path(n["path"]).read_bytes() == src.read_bytes()


def test_normalise_never_pads_a_short_clip(tmp_path):
    src = make_clip(tmp_path / "a.mp4", seconds=0.5)
    with pytest.raises(G.PipelineError):
        G.normalise(src, tmp_path / "n.mp4")
    with pytest.raises(G.PipelineError):
        G.normalise(make_clip(tmp_path / "b.mp4", seconds=2), tmp_path / "n.mp4", seconds=4)


def test_normalise_keeps_audio_trimmed_to_length(tmp_path):
    n = G.normalise(make_clip(tmp_path / "a.mp4", seconds=2.5, fps=30, audio=True), tmp_path / "n.mp4")
    assert n["has_audio"] is True and abs(analyze.probe(n["path"])["duration"] - 2.0) < 0.05


def test_enforce_trims_an_extra_frame(tmp_path):
    raw = make_clip(tmp_path / "r.mp4", seconds=97 / 24)
    res = G.enforce_duration(raw, tmp_path / "o.mp4", 4)
    assert res["method"] == "trim" and res["final_frames"] == 96 and res["stretch"] == 0.0


def test_enforce_retimes_the_genjutsu_replace_shortfall(tmp_path):
    raw = make_clip(tmp_path / "r.mp4", seconds=89 / 24)
    res = G.enforce_duration(raw, tmp_path / "o.mp4", 4)
    assert res["method"] == "retime" and res["final_frames"] == 96 and 0.07 < res["stretch"] < 0.08
    assert analyze.probe(res["path"])["duration"] == 4.0


def test_enforce_refuses_a_retime_that_would_be_visible(tmp_path):
    raw = make_clip(tmp_path / "r.mp4", seconds=3.0)
    with pytest.raises(G.DurationError):
        G.enforce_duration(raw, tmp_path / "o.mp4", 4)
    assert not (tmp_path / "o.mp4").exists()


def test_enforce_copies_when_already_exact(tmp_path):
    raw = make_clip(tmp_path / "r.mp4", seconds=4)
    assert G.enforce_duration(raw, tmp_path / "o.mp4", 4)["method"] == "copied"


# ---------------------------------------------------------------- shot list and prompt

def test_parse_shot_list_accepts_a_contiguous_list():
    sl = G.parse_shot_list(SHOTS)
    assert sl.cuts == [1.33] and sl.subject_name == "FOX"


@pytest.mark.parametrize("change", [
    {"shots": []},
    {"duration_s": 3},
    {"shots": [dict(SHOTS["shots"][0], end=1.0), SHOTS["shots"][1]]},
    {"shots": [dict(SHOTS["shots"][0], start=0.5), SHOTS["shots"][1]]},
    {"shots": [{"start": 0, "end": 2}]},
])
def test_parse_shot_list_rejects_bad_lists(change):
    with pytest.raises(G.PipelineError):
        G.parse_shot_list({**SHOTS, **change})


def test_cut_mismatches_reports_both_directions():
    sl = G.parse_shot_list(SHOTS)
    assert G.cut_mismatches(sl, [{"t": 1.333}]) == []
    assert G.cut_mismatches(sl, []) == ["shot boundary at 1.33 s has no measured cut"]
    assert G.cut_mismatches(sl, [{"t": 1.333}, {"t": 1.8}]) == ["measured cut at 1.8 s has no shot boundary"]


def test_prompt_carries_every_shot_and_cut_in_the_routes_tag_style():
    p = G.compose_prompt(G.parse_shot_list(SHOTS), tag)
    for needle in ("@image1", "@video1", "0.00-1.33 s", "1.33-2.00 s", "hard cuts at 1.33 s", "Wide.", "pushes in.", "drinks.", "The scooter is red."):
        assert needle in p


def test_reference_prompt_avoids_wording_the_model_would_classify_as_an_edit():
    p = G.compose_prompt(G.parse_shot_list(SHOTS), tag, "reference").lower()
    for word in ("edit", "replace", "swap", "change the", "modify"):
        assert word not in p
    assert "change the woman" in G.compose_prompt(G.parse_shot_list(SHOTS), tag, "edit")


def test_missing_shot_list_stops_before_any_spend_and_points_at_the_sheet(tmp_path):
    with pytest.raises(G.PipelineError, match="sheet.png"):
        G.ShotListFile(tmp_path / "none.json").shot_list({"sheet": "sheet.png"})


@pytest.mark.parametrize("w,h,want", [(540, 960, "9:16"), (1920, 1080, "16:9"), (480, 854, "9:16"), (500, 500, "1:1")])
def test_aspect_ratio(w, h, want):
    assert G.aspect_ratio(w, h) == want


# ---------------------------------------------------------------- whole pipeline, offline

def test_pipeline_end_to_end_with_a_fake_provider(tmp_path):
    src = make_clip(tmp_path / "in.mp4", seconds=2.2, fps=30)
    out_clip = make_clip(tmp_path / "gen.mp4", seconds=49 / 24)
    rd = tmp_path / "run"
    prep = G.prepare(src, rd)
    assert prep["normalised"]["frames"] == 48 and Path(prep["analysis"]["sheet"]).exists()
    assert len(list((rd / "frames").glob("*.jpg"))) == 4

    sl_path = tmp_path / "shots.json"
    sl_path.write_text(json.dumps(SHOTS))
    cfg = G.RunConfig(rd / "source.mp4", rd, "https://x/v", ("https://x/i",))
    led = Ledger(rd / "ledger.jsonl", "1")
    prov = FakeProvider(clip=out_clip)
    rep = G.run(cfg, G.ShotListFile(sl_path), prov, led)

    assert rep["duration"]["method"] == "trim" and analyze.probe(rd / "output.mp4")["frames"] == 48
    assert rep["score"]["G4_duration"]["pass"] is True and 0 <= rep["score"]["tracking"] <= 1
    assert rep["ledger"]["charged"] == "0.49"
    assert (rd / "prompt.txt").read_text().count("Shot ") == 2
    assert json.loads((rd / "report.json").read_text())["route"] == "fake.route"


def test_score_output_gates_against_every_named_baseline_and_skips_metadata(tmp_path):
    src = make_clip(tmp_path / "s.mp4", seconds=2)
    (tmp_path / "b.json").write_text(json.dumps({"A": G.score_output(src, src), "_meta": {"note": "x"}}))
    res = G.score_output(src, src, G.load_baselines(tmp_path / "b.json"))
    assert list(res["gates"]) == ["A"] and res["gates"]["A"]["G1"] is True and res["gates"]["A"]["G4"] is True


def test_compare_sheet_pads_a_short_row_and_has_one_row_per_video(tmp_path):
    a, b = make_clip(tmp_path / "a.mp4", seconds=2), make_clip(tmp_path / "b.mp4", seconds=1)
    res = G.compare_sheet({"src": a, "out": b}, tmp_path / "c.png", fps=2)
    assert res["rows"] == ["src", "out"] and res["cols"] == 4 and (tmp_path / "c.png").exists()


def test_cli_refuses_to_host_publicly_without_being_told(tmp_path, capsys):
    rd = tmp_path / "run"
    G.prepare(make_clip(tmp_path / "in.mp4", seconds=2), rd)
    (tmp_path / "s.json").write_text(json.dumps(SHOTS))
    rc = G.main(["quote", "--run-dir", str(rd), "--shot-list", str(tmp_path / "s.json"),
                 "--route", "openrouter.video-gen.seedance-2-5"])
    assert rc == 1 and "--host-public" in capsys.readouterr().err


def test_cli_run_requires_a_cap(tmp_path):
    with pytest.raises(SystemExit):
        G.main(["run", "--run-dir", str(tmp_path), "--shot-list", "s", "--route", "r"])
