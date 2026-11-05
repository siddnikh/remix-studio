import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from studio import providers as P


def job(**kw):
    base = dict(prompt="p", video_url="https://x/v.mp4", image_urls=("https://x/i.png",), duration_s=4, reference_s=4.0)
    return P.Job(**{**base, **kw})


class FakeRun:
    """Stands in for subprocess.run, so no test can reach treg or the network."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        out = self.answers.pop(0)
        if isinstance(out, dict):
            out = json.dumps(out)
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def treg_with(*answers):
    run = FakeRun(answers)
    return P.Treg(run=run), run


def test_openrouter_sends_the_video_as_an_input_reference():
    b = P.OpenRouterSeedance25().body(job())
    assert {"type": "video_url", "video_url": {"url": "https://x/v.mp4"}} in b["input_references"]
    assert {"type": "image_url", "image_url": {"url": "https://x/i.png"}} in b["input_references"]
    assert b["duration"] == 4 and b["resolution"] == "480p" and b["generate_audio"] is False
    assert "frame_images" not in b


def test_openrouter_quote_bills_input_and_output_seconds_at_the_video_rate():
    q = P.OpenRouterSeedance25().quote(job())
    assert q.provider_hold == Decimal("0.4112")
    assert q.expected == Decimal("0.491904")
    assert q.provider_hold < q.expected < q.ceiling


def test_openrouter_cannot_express_an_edit_job():
    with pytest.raises(P.UnsupportedJob):
        P.OpenRouterSeedance25().quote(job(task="edit"))


def test_reapi_hold_fits_but_settlement_does_not_is_refused_before_submit():
    treg, run = treg_with({"balance_micro": 508330})
    route = P.ReapiSeedance25(treg)
    q = route.quote(job())
    assert q.provider_hold == Decimal("0.4744") and q.expected == Decimal("0.9488")
    with pytest.raises(P.Unaffordable, match="could settle"):
        route.preflight(q)
    assert len(run.calls) == 1


def test_reapi_edit_declares_the_task_and_holds_thirty_seconds():
    route = P.ReapiSeedance25(P.Treg(run=FakeRun([])))
    b = route.body(job(task="edit"))
    assert b["duration"] == -1 and b["omni_reference_task_type"] == "edit" and "size" not in b
    q = route.quote(job(task="edit"))
    assert q.provider_hold == Decimal("3.558") and q.expected == Decimal("0.9488")


def test_reapi_applies_the_video_input_floor_for_a_short_reference():
    q = P.ReapiSeedance25(P.Treg(run=FakeRun([]))).quote(job(duration_s=5, reference_s=2.0))
    assert q.expected == Decimal("0.1186") * 9


def test_unrestricted_route_turns_the_filter_off():
    assert P.ReapiSeedance25Unrestricted(P.Treg(run=FakeRun([]))).body(job())["content_filter"] is False


def test_piapi_has_no_four_second_output():
    with pytest.raises(P.UnsupportedJob):
        P.PiapiSeedance25().quote(job())
    assert P.PiapiSeedance25().quote(job(duration_s=5)).expected == Decimal("0.75")


def test_submit_passes_body_on_stdin_and_idempotency_key_and_reads_the_hold():
    treg, run = treg_with({"result": {"id": "t1", "status": "pending"},
                           "_treg": {"http_status": 202, "call_id": "c1", "reserved_micro": 411200}})
    sub = P.OpenRouterSeedance25(treg).submit(job(), "key-1")
    assert sub.task_id == "t1" and sub.call_id == "c1" and sub.provider_hold == Decimal("0.4112")
    call = run.calls[0]
    assert "Idempotency-Key: key-1" in call["cmd"] and json.loads(call["input"])["model"] == "bytedance/seedance-2.5"


def test_a_400_or_402_at_submit_is_refused_not_a_task():
    treg, _ = treg_with({"result": {"error": "bad"}, "_treg": {"http_status": 400, "reserved_micro": 0}})
    with pytest.raises(P.SubmitRefused):
        P.OpenRouterSeedance25(treg).submit(job(), "k")


def test_poll_parses_terminal_states():
    route = P.OpenRouterSeedance25(P.Treg(run=FakeRun([])))
    done = route.parse_status({"status": "completed", "usage": {"cost": 0.4970}})
    assert done.status == "succeeded" and done.charged == Decimal("0.497")
    assert route.parse_status({"status": "failed", "error": "moderated"}).status == "failed"
    assert route.parse_status({"status": "in_progress"}).status == "pending"


def test_reapi_status_converts_credits_to_dollars():
    out = P.ReapiSeedance25(P.Treg(run=FakeRun([]))).parse_status(
        {"status": "completed", "output": {"video_urls": ["https://x/o.mp4"]}, "usage": {"credits": 949}})
    assert out.url == "https://x/o.mp4" and out.charged == Decimal("0.949")


def test_poll_error_stays_pending_rather_than_losing_the_task():
    treg, _ = treg_with({"result": {"detail": "busy"}, "_treg": {"http_status": 503}})
    out = P.OpenRouterSeedance25(treg).poll(P.Submission("r", "t1"))
    assert out.status == "pending"


def test_tags_match_each_route():
    assert P.OpenRouterSeedance25().tag("video") == "@video1"
    assert P.ReapiSeedance25(P.Treg(run=FakeRun([]))).tag("image") == "@Image 1"


def test_download_leaves_no_partial_file_on_failure(tmp_path):
    def fail(cmd, **kw):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 22, stdout="", stderr="404")
    dest = tmp_path / "o.mp4"
    with pytest.raises(P.ProviderError):
        P._download_url("https://x", dest, run=fail)
    assert list(tmp_path.iterdir()) == []
