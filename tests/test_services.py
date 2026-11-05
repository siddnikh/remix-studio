"""A real HTTP server on localhost stands in for the provider, so the requests, headers and failures are the real thing."""
import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from studio import gen, services, swap
from studio.ledger import Ledger
from studio.providers import Job, ProviderError, SubmitRefused, UnsupportedJob, get_route

SECRET = "sk-test-do-not-persist"
CLIP = (Path(__file__).parent / "test_services.py").read_bytes()


class Api:
    def __init__(self):
        self.submits, self.polls = [], 0
        self.submit_answer = (200, {"id": "t1"})
        self.poll_answers = [(200, {"status": "running"}), (200, {"status": "completed", "cost": 0.4, "url": None})]
        self.result_answer = (200, {"video": {"url": None}})
        self.base = ""


@pytest.fixture
def api():
    state = Api()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, doc):
            raw = json.dumps(doc).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.submits.append({"body": body, "auth": self.headers.get("Authorization"), "idem": self.headers.get("X-Idem")})
            self._send(*state.submit_answer)

        def do_GET(self):
            if self.path == "/file.mp4":
                self.send_response(200)
                self.send_header("Content-Length", str(len(CLIP)))
                self.end_headers()
                self.wfile.write(CLIP)
            elif self.path.startswith("/result/"):
                code, doc = state.result_answer
                self._send(code, {"video": {"url": state.base + "/file.mp4"}} if code == 200 else doc)
            else:
                state.polls += 1
                code, doc = state.poll_answers.pop(0) if len(state.poll_answers) > 1 else state.poll_answers[0]
                self._send(code, {**doc, "url": state.base + "/file.mp4"} if doc.get("status") == "completed" else doc)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIX_STUDIO_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("TEST_API_KEY", SECRET)
    (tmp_path / "cfg").mkdir()
    return tmp_path / "cfg"


def config(api, **over):
    auth = {"Authorization": "Bearer ${TEST_API_KEY}"}
    return {"submit": {"url": api.base + "/v1/jobs", "headers": auth}, "poll": {"url": api.base + "/v1/jobs/{task_id}", "headers": auth},
            "status_path": "status", "result_url_path": "url", "charge_path": "cost", "idempotency_header": "X-Idem",
            "job_body": {"text": "{prompt}", "seconds": "{duration_s}", "refs": "{image_urls}", "video": "{video_url}", "note": "at {resolution}"},
            "tags": {"video": "<<v{n}>>", "image": "<<i{n}>>"},
            "price": {"currency": "USD", "per_second": {"480p": 0.1}, "bill_reference_seconds": True}, **over}


def write_service(home, cfg, name="vid"):
    (home / "services.json").write_text(json.dumps({"_note": "ignored", name: cfg}))


def clip_plan(tmp_path, route="http:vid", cap=5, **line):
    plan = tmp_path / "run" / "plan" / "gen_plan.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({"cap": cap, "lines": [{"id": "c1", "kind": "still", "family": "f", "canary": True, "route": route, "worst_case": 1,
                                                       "body": {"prompt": "hi"}, "out": "stills/c1.bin", "poll_s": 0, **line}]}))
    return plan


def run(plan):
    return gen.Run(plan, treg=object(), sleep=lambda s: None)


def test_plan_line_submits_polls_downloads_and_settles_the_reported_charge(api, home, tmp_path):
    write_service(home, config(api))
    r = run(clip_plan(tmp_path))
    res = r.run_line("c1")
    assert res["status"] == "done" and res["charged"] == "0.4"
    assert Path(res["path"]).read_bytes() == CLIP
    assert api.submits[0]["auth"] == f"Bearer {SECRET}" and api.submits[0]["idem"]
    assert api.polls == 2
    assert Decimal(r.ledger.summary()["charged"]) == Decimal("0.4")


def test_a_key_never_reaches_state_ledger_or_plan(api, home, tmp_path):
    write_service(home, config(api))
    plan = clip_plan(tmp_path)
    run(plan).run_line("c1")
    for path in plan.parent.iterdir():
        assert SECRET not in path.read_text(), path


def test_rerun_after_delivery_does_not_pay_twice(api, home, tmp_path):
    write_service(home, config(api))
    plan = clip_plan(tmp_path)
    run(plan).run_line("c1")
    run(plan).run_line("c1")
    assert len(api.submits) == 1


def test_a_refusal_releases_the_hold_and_will_not_retry_unchanged(api, home, tmp_path):
    write_service(home, config(api))
    api.submit_answer = (400, {"error": "real person detected"})
    r = run(clip_plan(tmp_path))
    res = r.run_line("c1")
    assert res["status"] == "refused" and "real person" in res["error"]
    assert Decimal(r.ledger.summary()["held"]) == 0
    with pytest.raises(gen.GenError, match="--reroll"):
        r.run_line("c1")


def test_a_500_at_submit_keeps_the_hold_because_a_job_may_exist(api, home, tmp_path):
    write_service(home, config(api))
    api.submit_answer = (502, {"error": "bad gateway"})
    r = run(clip_plan(tmp_path))
    with pytest.raises(gen.GenError, match="hold stays open"):
        r.run_line("c1")
    assert Decimal(r.ledger.summary()["held"]) == 1


def test_idempotent_service_retries_with_the_same_key_after_an_unconfirmed_submit(api, home, tmp_path):
    write_service(home, config(api))
    api.submit_answer = (502, {})
    plan = clip_plan(tmp_path)
    with pytest.raises(gen.GenError):
        run(plan).run_line("c1")
    api.submit_answer = (200, {"id": "t1"})
    assert run(plan).run_line("c1")["status"] == "done"
    assert len(api.submits) == 2 and api.submits[0]["idem"] == api.submits[1]["idem"]


def test_service_without_idempotency_refuses_to_resend_an_unconfirmed_submit(api, home, tmp_path):
    cfg = config(api)
    del cfg["idempotency_header"]
    write_service(home, cfg)
    api.submit_answer = (502, {})
    plan = clip_plan(tmp_path)
    with pytest.raises(gen.GenError):
        run(plan).run_line("c1")
    api.submit_answer = (200, {"id": "t1"})
    with pytest.raises(gen.GenError, match="pay twice"):
        run(plan).run_line("c1")
    assert len(api.submits) == 1


def test_missing_key_stops_before_any_hold_or_request(api, home, tmp_path, monkeypatch):
    write_service(home, config(api))
    monkeypatch.delenv("TEST_API_KEY")
    r = run(clip_plan(tmp_path))
    with pytest.raises(services.ServiceConfigError, match="TEST_API_KEY"):
        r.run_line("c1")
    assert api.submits == [] and Decimal(r.ledger.summary()["held"]) == 0
    assert "sk-" not in json.dumps(services.get("vid").missing_env())


def test_key_can_come_from_the_env_file(api, home, tmp_path, monkeypatch):
    write_service(home, config(api))
    monkeypatch.delenv("TEST_API_KEY")
    (home / "env").write_text('# my keys\nexport TEST_API_KEY="from-file"\n')
    run(clip_plan(tmp_path)).run_line("c1")
    assert api.submits[0]["auth"] == "Bearer from-file"


def test_failed_job_releases_the_hold(api, home, tmp_path):
    write_service(home, config(api))
    api.poll_answers = [(200, {"status": "FAILED", "error": "moderation"})]
    r = run(clip_plan(tmp_path))
    res = r.run_line("c1")
    assert res["status"] == "failed" and res["error"] == "moderation"
    assert Decimal(r.ledger.summary()["held"]) == 0


def test_poll_errors_stay_pending_rather_than_losing_the_job(api, home, tmp_path):
    write_service(home, config(api))
    api.poll_answers = [(503, {}), (200, {"status": "completed"})]
    assert run(clip_plan(tmp_path)).run_line("c1")["status"] == "done"


def test_separate_result_endpoint_is_read_when_configured(api, home, tmp_path):
    write_service(home, config(api, result={"url": api.base + "/result/{task_id}"}, result_url_path="video.url", charge_path=None))
    res = run(clip_plan(tmp_path)).run_line("c1")
    assert res["status"] == "done" and res["charge_estimated"] is True


def test_service_answering_directly_needs_no_poll(api, home, tmp_path):
    cfg = config(api)
    for key in ("poll", "status_path"):
        del cfg[key]
    write_service(home, cfg)
    api.submit_answer = (200, {"url": api.base + "/file.mp4", "cost": 0.2})
    res = run(clip_plan(tmp_path)).run_line("c1")
    assert res["status"] == "done"
    assert api.polls == 0


def test_inline_placeholder_sends_the_file_inside_the_request(api, home, tmp_path):
    write_service(home, config(api))
    plan = clip_plan(tmp_path, body={"image": "<inline ref.png>"})
    (plan.parent / "ref.png").write_bytes(b"\x89PNG")
    run(plan).run_line("c1")
    assert api.submits[0]["body"]["image"] == "data:image/png;base64,iVBORw=="


def test_inline_placeholder_waits_for_a_missing_file(api, home, tmp_path):
    write_service(home, config(api))
    r = run(clip_plan(tmp_path, body={"image": "<inline nope.png>"}))
    with pytest.raises(gen.GenError, match="does not exist"):
        r.run_line("c1")
    assert Decimal(r.ledger.summary()["held"]) == 0


def job(**kw):
    return Job(**{**dict(prompt="p", video_url="v", image_urls=("a", "b"), duration_s=4, reference_s=4.0), **kw})


def test_swap_job_body_keeps_value_types(api, home):
    write_service(home, config(api))
    svc = get_route("http:vid")
    svc.submit(job(), "key")
    assert api.submits[0]["body"] == {"text": "p", "seconds": 4, "refs": ["a", "b"], "video": "v", "note": "at 480p"}
    assert svc.tag("image", 2) == "<<i2>>"


def test_swap_quote_bills_reference_seconds_and_refuses_what_it_cannot_price(api, home):
    write_service(home, config(api))
    svc = get_route("http:vid")
    q = svc.quote(job())
    assert q.expected == Decimal("0.8") and q.ceiling > q.expected and q.currency == "USD"
    with pytest.raises(UnsupportedJob, match="1080p"):
        svc.quote(job(resolution="1080p"))
    with pytest.raises(UnsupportedJob):
        svc.quote(job(task="edit"))
    write_service(home, config(api, price=None))
    with pytest.raises(UnsupportedJob, match="price"):
        get_route("http:vid").quote(job())


def test_swap_pipeline_runs_end_to_end_through_a_service(api, home, tmp_path):
    write_service(home, config(api))
    ledger = Ledger(tmp_path / "ledger.jsonl", "5", "USD")
    task = swap.execute(get_route("http:vid"), job(), ledger, tmp_path, poll_interval=0, sleep=lambda s: None)
    assert task["state"] == "done" and (tmp_path / "raw.mp4").read_bytes() == CLIP
    assert Decimal(ledger.summary()["charged"]) == Decimal("0.4")
    swap.execute(get_route("http:vid"), job(), ledger, tmp_path, poll_interval=0, sleep=lambda s: None)
    assert len(api.submits) == 1


def test_swap_refusal_raises_and_releases(api, home, tmp_path):
    write_service(home, config(api))
    api.submit_answer = (403, {"error": "no"})
    ledger = Ledger(tmp_path / "ledger.jsonl", "5", "USD")
    with pytest.raises(SubmitRefused):
        swap.execute(get_route("http:vid"), job(), ledger, tmp_path, poll_interval=0, sleep=lambda s: None)
    assert Decimal(ledger.summary()["held"]) == 0


def test_bad_configuration_names_the_problem(home):
    (home / "services.json").write_text(json.dumps({"a": {"submit": {}}, "b": {"submit": {"url": "x"}, "pol": {}}, "c": {"submit": {"url": "x"}, "poll": {"url": "y"}}}))
    with pytest.raises(ProviderError, match="submit.url"):
        services.get("a")
    with pytest.raises(ProviderError, match="unknown keys"):
        services.get("b")
    with pytest.raises(ProviderError, match="status_path"):
        services.get("c")
    with pytest.raises(ProviderError, match="no service"):
        services.get("nope")
    (home / "services.json").write_text("{not json")
    with pytest.raises(ProviderError, match="not JSON"):
        services.load_all()


def test_read_path():
    doc = {"a": [{"b": 1}], "c": "x"}
    assert services.read_path(doc, "a.0.b") == 1
    assert services.read_path(doc, "a.5.b") is None
    assert services.read_path(doc, "c.d") is None


def test_bundled_example_is_valid_configuration(monkeypatch, tmp_path):
    example = Path(__file__).parents[1] / "examples" / "services.example.json"
    monkeypatch.setenv("REMIX_STUDIO_HOME", str(tmp_path))
    (tmp_path / "services.json").write_text(example.read_text())
    names = services.load_all()
    assert set(names) == {"replicate", "fal"}
    for name in names:
        assert services.get(name).required_env()
