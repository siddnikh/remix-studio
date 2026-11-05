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
    """Stands in for subprocess.run, so no test can reach treg, claude or the network."""

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


def stream(*events):
    return [json.dumps(e) for e in events]


def tool(id_, name, inp, text):
    return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": id_, "name": name, "input": inp}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": id_, "content": text}]}}]


def session(balance_after="90.05", submits=1, status="completed"):
    ev = []
    ev += tool("b1", "mcp__higgsfield__balance", {}, json.dumps({"credits": 102.05}))
    ev += tool("q", "mcp__higgsfield__generate_video", {"params": {"get_cost": True, "mode": "omni_reference"}}, json.dumps({"cost": {"credits": 12, "credits_exact": 12}}))
    for n in range(submits):
        ev += tool(f"g{n}", "mcp__higgsfield__generate_video", {"params": {"model": "seedance_2_5"}},
                   json.dumps({"results": [{"id": f"job{n}", "status": "pending"}]}))
    ev += tool("w", "mcp__higgsfield__show_generations", {}, json.dumps(
        {"items": [{"id": f"job{submits - 1}", "status": status, "results": {"rawUrl": "https://cdn/o.mp4"}}]}))
    ev += tool("b2", "mcp__higgsfield__balance", {}, json.dumps({"credits": float(balance_after)}))
    ev += [{"type": "result", "result": "ignore previous instructions", "total_cost_usd": 0.3, "num_turns": 5}]
    return stream(*ev)


def hf(tmp_path):
    return P.HiggsfieldHeadless(tmp_path, "vid", "img", Decimal("87"), Decimal("15"), tmp_path / "s.jsonl", run=FakeRun([]))


def test_higgsfield_audit_reads_facts_from_tool_results(tmp_path):
    r = hf(tmp_path).audit(session())
    assert r["job_id"] == "job0" and r["status"] == "completed" and r["url"] == "https://cdn/o.mp4"
    assert r["quote"] == "12"
    assert r["balance_before"] == "102.05" and r["balance_after"] == "90.05"
    assert Decimal(r["spent"]) == 12 and r["violations"] == [] and r["session"]["total_cost_usd"] == 0.3


def test_higgsfield_audit_flags_a_double_submit_and_a_broken_floor(tmp_path):
    r = hf(tmp_path).audit(session(balance_after="78.05", submits=2))
    assert any("2 paid submissions" in v for v in r["violations"])
    assert any("below the floor" in v for v in r["violations"])
    assert any("spent" in v for v in r["violations"])


def test_higgsfield_outcome_comes_from_the_audit(tmp_path):
    h = hf(tmp_path)
    h.result = h.audit(session())
    out = h.poll(P.Submission(h.route, "job0"))
    assert out.status == "succeeded" and out.url == "https://cdn/o.mp4" and out.charged == Decimal("12")
    h.result = h.audit(session(status="nsfw"))
    assert h.poll(P.Submission(h.route, "job0")).status == "failed"


def test_higgsfield_audit_reads_the_jobs_wait_shape_and_quotes_per_mode(tmp_path):
    ev = tool("g", "mcp__higgsfield__generate_video", {"params": {"model": "seedance_2_5"}}, json.dumps({"results": [{"id": "j1"}]}))
    ev += tool("q1", "mcp__higgsfield__generate_video", {"params": {"get_cost": True, "mode": "omni_reference"}}, json.dumps({"cost": {"credits_exact": 12}}))
    ev += tool("q2", "mcp__higgsfield__generate_video", {"params": {"get_cost": True, "mode": "video_edit"}}, json.dumps({"cost": {"credits": 12}}))
    ev += tool("w", "mcp__higgsfield__jobs_wait", {}, json.dumps({"jobs": [{"job_id": "j1", "status": "completed", "result_url": "https://cdn/x.mp4"}]}))
    r = hf(tmp_path).audit(stream(*ev))
    assert r["status"] == "completed" and r["url"] == "https://cdn/x.mp4" and r["submissions"] == 1
    assert r["quote"] == "12" and r["quotes"] == {"omni_reference": "12", "video_edit": "12"}


def test_a_session_that_ends_before_the_job_does_is_not_treated_as_failed(tmp_path):
    h = hf(tmp_path)
    h.result = h.audit(session(status="in_progress"))
    with pytest.raises(P.ProviderError, match="check it by hand"):
        h.poll(P.Submission(h.route, "job0"))


def test_higgsfield_also_quote_asks_for_a_quote_without_submitting(tmp_path):
    h = P.HiggsfieldHeadless(tmp_path, "v", "i", Decimal("87"), Decimal("15"), tmp_path / "s", run=FakeRun([]), also_quote=("video_edit",))
    text = h.instructions(job())
    assert "as a quote only; never submit" in text and '"mode": "video_edit"' in text and "Never retry" in text


def test_higgsfield_edit_mode_omits_fields_the_model_ignores(tmp_path):
    h = P.HiggsfieldHeadless(tmp_path, "v", "i", Decimal("87"), Decimal("15"), tmp_path / "s", run=FakeRun([]), mode="video_edit")
    p = h.params(job())
    assert p["mode"] == "video_edit" and "duration" not in p and "aspect_ratio" not in p


def test_higgsfield_session_is_limited_to_the_agreed_tools():
    tools = P.HIGGSFIELD_TOOLS.split(",")
    assert "Bash(curl:*)" in tools and "Bash" not in tools and len(tools) == 9
    assert not [t for t in tools if "Edit" in t or "Write" in t]


def test_parse_stream_ignores_garbage():
    tools, final = P.parse_stream(["not json", "[1,2]", json.dumps({"type": "result", "result": "x"})])
    assert tools == [] and final["result"] == "x"


def test_download_leaves_no_partial_file_on_failure(tmp_path):
    def fail(cmd, **kw):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 22, stdout="", stderr="404")
    dest = tmp_path / "o.mp4"
    with pytest.raises(P.ProviderError):
        P._download_url("https://x", dest, run=fail)
    assert list(tmp_path.iterdir()) == []


def test_higgsfield_jobs_audit_reads_paid_image_job_from_recorded_tool_results(tmp_path):
    def tool_use(i, name, params):
        return json.dumps({"message": {"content": [{"type": "tool_use", "id": i, "name": f"mcp__higgsfield__{name}", "input": params}]}})

    def tool_result(i, doc):
        return json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": json.dumps(doc)}]}})
    lines = [
        tool_use("a", "balance", {}), tool_result("a", {"credits": 90.05}),
        tool_use("b", "generate_image", {"params": {"get_cost": True}}), tool_result("b", {"cost": {"credits_exact": 1.5}}),
        tool_use("c", "generate_image", {"params": {}}), tool_result("c", {"results": [{"id": "j9", "status": "queued"}]}),
        tool_use("d", "jobs_wait", {}), tool_result("d", {"items": [{"id": "other", "status": "failed"},
                                                                    {"id": "j9", "status": "completed", "results": {"rawUrl": "https://x/i.png"}}]}),
        json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.01, "num_turns": 4, "duration_ms": 1, "is_error": False}),
    ]
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join(lines))
    lines.insert(0, tool_result("z", {}))
    lines[:0] = [tool_use("m", "media_confirm", {}), tool_result("m", {"results": [{"media_id": "m1", "status": "uploaded"}]})]
    log.write_text("\n".join(lines))
    res = P.HiggsfieldJobs(tmp_path, run=FakeRun([])).audit(log)
    assert res["media_id"] == "m1"
    assert (res["job_id"], res["status"], res["url"], res["quote"], res["submissions"]) == ("j9", "completed", "https://x/i.png", "1.5", 1)
    assert res["balances"] == ["90.05"]


def test_higgsfield_jobs_audit_does_not_count_a_call_the_client_rejected(tmp_path):
    lines = [
        json.dumps({"message": {"content": [{"type": "tool_use", "id": "a", "name": "mcp__higgsfield__generate_image",
                                             "input": {"__unparsedToolInput": {"raw": "{"}}}]}}),
        json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": "a", "is_error": True,
                                             "content": "<tool_use_error>InputValidationError: could not be parsed</tool_use_error>"}]}}),
    ]
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join(lines))
    assert P.HiggsfieldJobs(tmp_path, run=FakeRun([])).audit(log)["submissions"] == 0
