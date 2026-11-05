#!/usr/bin/env python
"""Run paid stills and clips from a finite plan, under the spend ledger.

    remix-studio gen quote  run/plan/gen_plan.json              total the plan against its cap; spends nothing
    remix-studio gen estimate run/plan/gen_plan.json --line c1  free price check, on services that offer one
    remix-studio gen run    run/plan/gen_plan.json --line kf1   reserve, submit, poll, download, settle
    remix-studio gen run    ... --line c2 --reroll "identity"   one bounded retry with a named reason
    remix-studio gen pass-canary run/plan/gen_plan.json --family fox --note "identity holds, hands fine"
    remix-studio gen status run/plan/gen_plan.json

State lives beside the plan, so a re-run after a crash polls the task it already paid for. Line format:
references/generation.md in the remix skill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import analyze, providers, services
from .ledger import CapExceeded, Ledger

MAX_ATTEMPTS = {"still": 3, "plate": 3, "clip": 2}
DONE, FAILED, REFUSED, OPEN = "done", "failed", "refused", "open"
HTTP_PREFIX = "http:"


class GenError(Exception):
    pass


class Run:
    def __init__(self, plan_path: str | Path, treg=None, sleep=time.sleep, fetch=None):
        self.plan_path = Path(plan_path)
        self.dir = self.plan_path.parent
        self.plan = json.loads(self.plan_path.read_text())
        self.lines = {ln["id"]: ln for ln in self.plan["lines"]}
        if len(self.lines) != len(self.plan["lines"]):
            raise GenError("duplicate line ids")
        for ln in self.plan["lines"]:
            for key in ("id", "kind", "family", "route", "worst_case", "body", "out"):
                if key not in ln:
                    raise GenError(f"line {ln.get('id')}: missing {key}")
            if ln["kind"] not in MAX_ATTEMPTS:
                raise GenError(f"line {ln['id']}: kind must be one of {sorted(MAX_ATTEMPTS)}")
        self.state_path = self.dir / "gen_state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"lines": {}, "canaries": {}}
        self._treg = treg
        self._services: dict[str, services.HttpService] = {}
        self.sleep = sleep
        self.fetch = fetch or _fetch
        self.ledger = Ledger(self.dir / "ledger.jsonl", Decimal(str(self.plan["cap"])), self.plan.get("currency", "USD"))

    @property
    def treg(self):
        if self._treg is None:
            self._treg = providers.Treg()
        return self._treg

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.state_path)

    def _attempts(self, line_id: str) -> list[dict]:
        return self.state["lines"].setdefault(line_id, [])

    def quote(self) -> dict:
        lines = self.plan["lines"]
        planned = sum(Decimal(str(ln["worst_case"])) for ln in lines)
        with_retries = planned + sum(Decimal(str(ln["worst_case"])) * (min(ln.get("max_attempts", MAX_ATTEMPTS[ln["kind"]]), MAX_ATTEMPTS[ln["kind"]]) - 1)
                                     for ln in lines)
        cap = Decimal(str(self.plan["cap"]))
        return {"lines": len(lines), "planned": str(planned), "with_all_retries": str(with_retries), "cap": str(cap),
                "fits_cap": planned <= cap, "retries_fit_cap": with_retries <= cap, "ledger": self.ledger.summary()["available"]}

    def resolve(self, value, service=None):
        """Replace "<result of LINE>" with that line's delivered URL, and "<host path>" or "<upload path>" with a file's URL."""
        if isinstance(value, dict):
            return {k: self.resolve(v, service) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, service) for v in value]
        if isinstance(value, str):
            m = re.fullmatch(r"<result of (.+)>", value)
            if m:
                done = [a for a in self._attempts(m.group(1)) if a["status"] == DONE and a.get("accepted", True)]
                if not done:
                    raise GenError(f"waiting on line {m.group(1)}: it has no accepted result yet")
                return done[-1]["url"]
            m = re.fullmatch(r"<upload (.+)>", value)
            if m:
                if not self.plan.get("hosting_consent"):
                    raise GenError("this plan uploads a local file to a URL anyone with the link can open; set hosting_consent "
                                   "only after the user agreed for this file")
                if not service:
                    raise GenError(f"<upload {m.group(1)}> needs an http: route with an upload block")
                return service.upload(self._plan_file(m.group(1)), self.dir / "uploads.json")
            m = re.fullmatch(r"<inline (.+)>", value)
            if m:
                return services.data_uri(self._plan_file(m.group(1)))
            m = re.fullmatch(r"<host (.+)>", value)
            if m:
                hosted = self.state.setdefault("hosted", {})
                if m.group(1) not in hosted:
                    if not self.plan.get("hosting_consent"):
                        raise GenError("this plan uploads a local file to a public URL for 7 days; set hosting_consent "
                                       "only after the user agreed for this file")
                    info = self.treg.host(self._plan_file(m.group(1)))
                    hosted[m.group(1)] = info.get("url") or info["public_url"]
                    self._save()
                return hosted[m.group(1)]
        return value

    def _plan_file(self, rel: str) -> Path:
        path = (self.dir / rel).resolve()
        if not path.is_file():
            raise GenError(f"waiting on {rel}: the file does not exist yet")
        return path

    def _service(self, ln: dict) -> services.HttpService | None:
        route = ln["route"]
        if not route.startswith(HTTP_PREFIX):
            return None
        if route not in self._services:
            self._services[route] = services.get(route.removeprefix(HTTP_PREFIX))
        return self._services[route]

    def run_line(self, line_id: str, reroll: str | None = None) -> dict:
        if line_id not in self.lines:
            raise GenError(f"no line {line_id!r}")
        ln = self.lines[line_id]
        attempts = self._attempts(line_id)
        if attempts and attempts[-1]["status"] == OPEN:
            return self._follow(ln, attempts[-1])
        if attempts and attempts[-1]["status"] == DONE and not reroll:
            return attempts[-1]
        if attempts and not reroll:
            raise GenError(f"line {line_id} last ended {attempts[-1]['status']}: {attempts[-1].get('error')}. "
                           "Diagnose it; a retry needs --reroll with the reason. Never resubmit a refusal unchanged")
        limit = min(ln.get("max_attempts", MAX_ATTEMPTS[ln["kind"]]), MAX_ATTEMPTS[ln["kind"]])
        if len(attempts) >= limit:
            raise GenError(f"line {line_id} has used its {limit} attempts: re-cast from existing footage or report the gap")
        service = self._service(ln)
        if service:
            service.check_ready()
        family = ln["family"]
        if not ln.get("canary") and not self.state["canaries"].get(family):
            raise GenError(f"family {family!r} has no passed canary; run its canary line, look at it, then pass-canary")
        if attempts:
            attempts[-1]["accepted"] = False
        body = self.resolve(ln["body"], service)
        n = len(attempts) + 1
        idem = hashlib.sha256(json.dumps([line_id, n, body], sort_keys=True).encode()).hexdigest()[:32]
        try:
            hold = self.ledger.reserve(Decimal(str(ln["worst_case"])), line_id, route=ln["route"], attempt=n, reroll=reroll)
        except CapExceeded as e:
            raise GenError(str(e)) from e
        attempt = {"n": n, "status": OPEN, "hold": hold, "idempotency_key": idem, "task_id": None, "body": body, "reroll": reroll}
        attempts.append(attempt)
        self._save()
        return self._follow(ln, attempt)

    def estimate(self, line_id: str) -> dict:
        ln = self.lines[line_id]
        service = self._service(ln)
        if not service or not service.can_estimate:
            raise GenError(f"line {line_id}: {ln['route']} has no free estimate; read the price from the provider")
        price = service.estimate(self.resolve(ln["body"], service))
        return {"line": line_id, "usd": str(price), "worst_case": str(ln["worst_case"]), "fits": price <= Decimal(str(ln["worst_case"]))}

    def _submit_http(self, ln: dict, attempt: dict, service: services.HttpService) -> None:
        if service.can_estimate and "estimate" not in attempt:
            try:
                price = service.estimate(attempt["body"])
                if price > Decimal(str(ln["worst_case"])):
                    raise providers.SubmitRefused(f"estimate ${price} is above this line's worst_case {ln['worst_case']}")
            except providers.ProviderError as e:
                # Nothing was submitted, so the hold can go whatever the estimate's problem was.
                self.ledger.release(attempt["hold"], f"not submitted: {str(e)[:300]}")
                attempt.update(status=REFUSED, error=str(e)[:600])
                self._save()
                return
            attempt["estimate"] = str(price)
            self._save()
        if attempt.get("submit_started") and not service.idempotent:
            raise GenError(f"line {ln['id']}: an earlier submit to {ln['route']} may have created a job and this service has "
                           "no idempotency_header, so sending it again could pay twice. Check the service by hand; the hold stays open")
        attempt["submit_started"] = True
        self._save()
        try:
            sub = service.submit_body(attempt["body"], attempt["idempotency_key"])
        except providers.SubmitRefused as e:
            self.ledger.release(attempt["hold"], f"refused: {str(e)[:300]}")
            attempt.update(status=REFUSED, error=str(e)[:600])
            self._save()
            return
        attempt.update(task_id=sub.task_id, submit_answer=sub.raw)
        self._save()

    def _submit(self, ln: dict, attempt: dict) -> None:
        service = self._service(ln)
        if service:
            return self._submit_http(ln, attempt, service)
        result, meta = self.treg.call(ln["route"], body=attempt["body"], idempotency_key=attempt["idempotency_key"])
        status = meta.get("http_status")
        if status is None or not 200 <= int(status) < 300:
            self.ledger.release(attempt["hold"], f"refused HTTP {status}")
            attempt.update(status=REFUSED, error=f"HTTP {status}: {json.dumps(result)[:500]}")
            self._save()
            return
        attempt["call_id"] = meta.get("call_id")
        url = providers.find_url(result)
        if url:
            attempt.update(immediate_url=url)
        attempt["task_id"] = (result or {}).get("id") or (result or {}).get("task_id")
        if not attempt["task_id"] and not url:
            attempt.update(error=f"no task id or URL in answer: {json.dumps(result)[:300]}")
        self._save()

    def _follow(self, ln: dict, attempt: dict) -> dict:
        if not attempt.get("task_id") and not attempt.get("immediate_url"):
            try:
                self._submit(ln, attempt)
            except (providers.ProviderError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                # A timeout does not prove no job exists: keep the hold; a re-run reuses the same idempotency key.
                attempt["error"] = f"submit unconfirmed: {e}"
                self._save()
                raise GenError(f"line {ln['id']}: {attempt['error']}. Re-run to reconcile; the hold stays open") from e
            if attempt["status"] != OPEN:
                return attempt
        url, charged = attempt.get("immediate_url"), None
        known_price = Decimal(attempt["estimate"]) if attempt.get("estimate") else None
        deadline = time.monotonic() + ln.get("timeout_s", 1500)
        while not url:
            outcome = self._poll(ln, attempt)
            if outcome.status == "succeeded":
                url, charged = outcome.url, outcome.charged
                if not url:
                    url = providers.find_url(outcome.raw)
                if not url:
                    raise GenError(f"line {ln['id']}: finished with no output URL: {json.dumps(outcome.raw)[:300]}")
            elif outcome.status == "failed":
                self.ledger.release(attempt["hold"], f"task failed: {outcome.error}", task=attempt["task_id"])
                attempt.update(status=FAILED, error=outcome.error,
                               note="A failed job is not proof of a refund: compare treg balance before reporting spend")
                self._save()
                return attempt
            else:
                if time.monotonic() > deadline:
                    raise GenError(f"line {ln['id']}: still pending after {ln.get('timeout_s', 1500)} s ({outcome.error or 'no error reported'}); re-run to keep polling")
                self.sleep(ln.get("poll_s", 15))
        return self._deliver(ln, attempt, url, charged if charged is not None else known_price)

    def _deliver(self, ln: dict, attempt: dict, url: str, charged: Decimal | None) -> dict:
        service = self._service(ln)
        fetch = service.fetch if service else self.fetch
        dest = (self.dir.parent / ln["out"]).resolve() if not Path(ln["out"]).is_absolute() else Path(ln["out"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        fetch(url, dest)
        estimated = charged is None
        amount = Decimal(str(ln["worst_case"])) if estimated else Decimal(str(charged))
        self.ledger.settle(attempt["hold"], amount, task=attempt.get("task_id"), estimated=estimated)
        attempt.update(status=DONE, url=url, path=str(dest), charged=str(amount), charge_estimated=estimated)
        if ln["kind"] == "clip":
            info = analyze.probe(dest)
            attempt.update(frames=info["frames"], duration=info["duration"], fps=info["fps_rational"],
                           note="Place by these frames, not by what the prompt promised")
        self._save()
        return attempt

    def _poll(self, ln: dict, attempt: dict) -> providers.Outcome:
        service = self._service(ln)
        if service:
            return service.poll(providers.Submission(ln["route"], attempt["task_id"], raw=attempt.get("submit_answer") or {}))
        tool, param = ln.get("poll_tool"), ln.get("poll_param", "id")
        parse = _parse_generic
        if ln["route"] in providers.ROUTES:
            route = providers.ROUTES[ln["route"]](self.treg)
            tool, param, parse = tool or route.poll_tool, route.poll_param, route.parse_status
        if not tool:
            raise GenError(f"line {ln['id']}: set poll_tool from `treg catalog get {ln['route']}`")
        result, meta = self.treg.call(tool, query={param: attempt["task_id"]})
        status = meta.get("http_status")
        if status is None or not 200 <= int(status) < 300:
            return providers.Outcome("pending", error=f"poll HTTP {status}")
        return parse(result)

    def pass_canary(self, family: str, note: str) -> None:
        if not any(ln["family"] == family for ln in self.plan["lines"]):
            raise GenError(f"no family {family!r}")
        done = [ln["id"] for ln in self.plan["lines"] if ln["family"] == family and ln.get("canary")
                and any(a["status"] == DONE for a in self._attempts(ln["id"]))]
        if not done:
            raise GenError(f"family {family!r}: run its canary line first")
        self.state["canaries"][family] = {"lines": done, "note": note}
        self._save()

    def status(self) -> dict:
        lines = {}
        for line_id in self.lines:
            a = self._attempts(line_id)
            lines[line_id] = {"attempts": len(a), "status": a[-1]["status"] if a else "not started",
                              **({k: a[-1][k] for k in ("frames", "duration", "charged") if k in a[-1]} if a else {})}
        s = self.ledger.summary()
        return {"lines": lines, "canaries": self.state["canaries"], "charged": s["charged"], "held": s["held"], "available": s["available"]}


def _parse_generic(r: dict) -> providers.Outcome:
    status = (r or {}).get("status")
    if status in ("completed", "succeeded", "success"):
        credits = (r.get("usage") or {}).get("credits")
        return providers.Outcome("succeeded", providers.find_url(r), None if credits is None else Decimal(str(credits)) / 1000, raw=r)
    if status in ("failed", "error", "cancelled"):
        return providers.Outcome("failed", error=str(r.get("error") or status)[:500], raw=r)
    return providers.Outcome("pending", raw=r)


def _fetch(url: str, dest: Path) -> None:
    subprocess.run(["curl", "-sSfL", "--max-time", "300", "-o", str(dest), url], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remix-studio gen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("quote", "status"):
        sub.add_parser(name).add_argument("plan")
    p = sub.add_parser("estimate", help="free price check for one line, on services that offer it")
    p.add_argument("plan")
    p.add_argument("--line", required=True)
    p = sub.add_parser("run")
    p.add_argument("plan")
    p.add_argument("--line", required=True)
    p.add_argument("--reroll", help="the fail-list reason for this retry")
    p = sub.add_parser("pass-canary")
    p.add_argument("plan")
    p.add_argument("--family", required=True)
    p.add_argument("--note", required=True)
    a = ap.parse_args(argv)
    try:
        r = Run(a.plan)
        if a.cmd == "quote":
            q = r.quote()
            print(json.dumps(q, indent=1))
            return 0 if q["fits_cap"] else 1
        if a.cmd == "status":
            print(json.dumps(r.status(), indent=1))
            return 0
        if a.cmd == "estimate":
            print(json.dumps(r.estimate(a.line)))
            return 0
        if a.cmd == "pass-canary":
            r.pass_canary(a.family, a.note)
            print(json.dumps({"family": a.family, "passed": True}))
            return 0
        res = r.run_line(a.line, a.reroll)
        print(json.dumps({k: v for k, v in res.items() if k not in ("body", "idempotency_key", "hold")}, indent=1))
        return 0 if res["status"] == DONE else 1
    except (GenError, providers.ProviderError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
