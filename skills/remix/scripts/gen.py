#!/usr/bin/env python
"""Run paid stills and clips from a finite plan, under the spend ledger.

    gen.py quote  run/plan/gen_plan.json              total the plan against its cap; spends nothing
    gen.py run    run/plan/gen_plan.json --line kf1   reserve, submit, poll, download, settle
    gen.py run    ... --line c2 --reroll "identity"   one bounded retry with a named reason
    gen.py pass-canary run/plan/gen_plan.json --family fox --note "identity holds, hands fine"
    gen.py status run/plan/gen_plan.json
    gen.py quote-live run/plan/gen_plan.json --line c1   free get_cost for a higgsfield.* line

State lives beside the plan, so a re-run after a crash polls the task it already paid for. Line format:
skills/remix/references/generation.md.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from studio import analyze, providers  # noqa: E402
from studio.ledger import CapExceeded, Ledger  # noqa: E402

MAX_ATTEMPTS = {"still": 3, "plate": 3, "clip": 2}
DONE, FAILED, REFUSED, OPEN = "done", "failed", "refused", "open"
HF_PREFIX = "higgsfield."


class GenError(Exception):
    pass


class Run:
    def __init__(self, plan_path: str | Path, treg=None, sleep=time.sleep, fetch=None, higgsfield=None):
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
        hf = self.plan.get("higgsfield") or {}
        self.higgsfield = higgsfield or (providers.HiggsfieldJobs(hf["cwd"], model=hf.get("model", "haiku")) if hf.get("cwd") else None)
        self.hf_logs = self.dir / "hf_logs"
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

    def resolve(self, value):
        """Replace "<result of LINE>" with that line's delivered URL, and "<host path>" with a hosted one."""
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, str):
            m = re.fullmatch(r"<result of (.+)>", value)
            if m:
                done = [a for a in self._attempts(m.group(1)) if a["status"] == DONE and a.get("accepted", True)]
                if not done:
                    raise GenError(f"waiting on line {m.group(1)}: it has no accepted result yet")
                return done[-1]["url"]
            m = re.fullmatch(r"<media (.+)>", value)
            if m:
                return self._media_id(m.group(1))
            m = re.fullmatch(r"<host (.+)>", value)
            if m:
                hosted = self.state.setdefault("hosted", {})
                if m.group(1) not in hosted:
                    if not self.plan.get("hosting_consent"):
                        raise GenError("this plan uploads a local file to a public URL for 7 days; set hosting_consent "
                                       "only after the user agreed for this file")
                    info = self.treg.host((self.dir / m.group(1)).resolve())
                    hosted[m.group(1)] = info.get("url") or info["public_url"]
                    self._save()
                return hosted[m.group(1)]
        return value

    def _media_id(self, rel: str) -> str:
        path = (self.dir / rel).resolve()
        if not path.exists():
            raise GenError(f"waiting on {rel}: the file does not exist yet")
        media = self.state.setdefault("media", {})
        key = hashlib.sha256(path.read_bytes()).hexdigest()
        if key not in media:
            log = self._log(f"upload_{path.stem}_{key[:8]}")
            media[key] = {"id": self._hf().upload(path, log), "path": rel}
            self._note_session(f"upload {rel}", log)
        return media[key]["id"]

    def _hf(self) -> providers.HiggsfieldJobs:
        if not self.higgsfield:
            raise GenError('higgsfield.* lines need plan "higgsfield": {"cwd": "<folder with the MCP connected>"}')
        return self.higgsfield

    def _log(self, name: str) -> Path:
        return self.hf_logs / f"{name}.jsonl"

    def _note_session(self, label: str, log: Path) -> None:
        final = self._hf().audit(log)["session"] or {}
        self.state.setdefault("sessions", []).append({"label": label, "usd": final.get("total_cost_usd"), "turns": final.get("num_turns")})
        self._save()

    def quote_live(self, line_id: str) -> dict:
        ln = self.lines[line_id]
        if not ln["route"].startswith(HF_PREFIX):
            raise GenError(f"line {line_id} is not a Higgsfield line")
        fallback = (self.plan.get("higgsfield") or {}).get("quote_media")
        params = self._resolve_for_quote(ln["body"]["params"], fallback)
        log = self._log(f"quote_{line_id}_{len(self.state.setdefault('sessions', []))}")
        credits = self._hf().quote(ln["body"]["tool"], params, log)
        self._note_session(f"quote {line_id}", log)
        self.state.setdefault("live_quotes", {})[line_id] = str(credits)
        self._save()
        return {"line": line_id, "credits": str(credits), "worst_case": ln["worst_case"], "fits": credits <= Decimal(str(ln["worst_case"]))}

    def _resolve_for_quote(self, value, fallback):
        """Prices a clip before its stills exist, by referencing a stand-in image of the same kind."""
        if isinstance(value, dict):
            return {k: self._resolve_for_quote(v, fallback) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_for_quote(v, fallback) for v in value]
        m = re.fullmatch(r"<media (.+)>", value) if isinstance(value, str) else None
        if m and fallback and not (self.dir / m.group(1)).resolve().exists():
            value = f"<media {fallback}>"
        return self.resolve(value)

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
        family = ln["family"]
        if not ln.get("canary") and not self.state["canaries"].get(family):
            raise GenError(f"family {family!r} has no passed canary; run its canary line, look at it, then pass-canary")
        if attempts:
            attempts[-1]["accepted"] = False
        body = self.resolve(ln["body"])
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

    def _follow_higgsfield(self, ln: dict, attempt: dict) -> dict:
        """Never submits twice: a log that exists is read first, and a known job is only ever waited on."""
        hf = self._hf()
        log = self.dir / attempt["log"] if attempt.get("log") else None
        if log and log.exists():
            res = hf.audit(log)
        else:
            attempt["log"] = f"hf_logs/{ln['id']}_{attempt['n']}.jsonl"
            self._save()
            log = self.dir / attempt["log"]
            body = attempt["body"]
            res = hf.run_job(body["tool"], body["params"], Decimal(str(ln["worst_case"])), log)
            self._note_session(f"job {ln['id']} #{attempt['n']}", log)
        if res["job_id"] and res["status"] not in ("completed", *hf.FAILED):
            log = self.dir / f"hf_logs/{ln['id']}_{attempt['n']}_follow.jsonl"
            followed = hf.follow(res["job_id"], log)
            self._note_session(f"follow {ln['id']} #{attempt['n']}", log)
            res = {**res, "status": followed["status"], "url": followed["url"]}
        attempt.update(task_id=res["job_id"], quote=res["quote"], balances=res["balances"], submissions=res["submissions"])
        self._save()
        if res["submissions"] > 1:
            raise GenError(f"line {ln['id']}: {res['submissions']} paid submissions in one session. The hold stays open; settle by hand")
        if not res["submissions"]:
            if not res["session"]:
                raise GenError(f"line {ln['id']}: the session ended without a result and may have submitted. The hold stays open; "
                               "check show_generations before re-running")
            self.ledger.release(attempt["hold"], f"nothing submitted, quote {res['quote']}")
            attempt.update(status=REFUSED, error=f"session submitted nothing; quote {res['quote']}, limit {ln['worst_case']}")
            self._save()
            return attempt
        if not res["job_id"]:
            raise GenError(f"line {ln['id']}: submitted, but no job id recorded: {res['error']}. The hold stays open")
        if res["status"] in hf.FAILED:
            self.ledger.release(attempt["hold"], f"job {res['status']}", task=res["job_id"])
            attempt.update(status=FAILED, error=f"job {res['status']}", note="A failed job is not proof of a refund: compare balances")
            self._save()
            return attempt
        if res["status"] != "completed" or not res["url"]:
            raise GenError(f"line {ln['id']}: job {res['job_id']} is {res['status']} with url {bool(res['url'])}; re-run to keep following")
        return self._deliver(ln, attempt, res["url"], None if res["quote"] is None else Decimal(res["quote"]))

    def _submit(self, ln: dict, attempt: dict) -> None:
        result, meta = self.treg.call(ln["route"], body=attempt["body"], idempotency_key=attempt["idempotency_key"])
        status = meta.get("http_status")
        if status is None or not 200 <= int(status) < 300:
            self.ledger.release(attempt["hold"], f"refused HTTP {status}")
            attempt.update(status=REFUSED, error=f"HTTP {status}: {json.dumps(result)[:500]}")
            self._save()
            return
        attempt["call_id"] = meta.get("call_id")
        url = _find_url(result)
        if url:
            attempt.update(immediate_url=url)
        attempt["task_id"] = (result or {}).get("id") or (result or {}).get("task_id")
        if not attempt["task_id"] and not url:
            attempt.update(error=f"no task id or URL in answer: {json.dumps(result)[:300]}")
        self._save()

    def _follow(self, ln: dict, attempt: dict) -> dict:
        if ln["route"].startswith(HF_PREFIX):
            return self._follow_higgsfield(ln, attempt)
        if not attempt.get("task_id") and not attempt.get("immediate_url"):
            try:
                self._submit(ln, attempt)
            except (providers.ProviderError, subprocess.TimeoutExpired) as e:
                # A timeout does not prove no job exists: keep the hold; a re-run reuses the same idempotency key.
                attempt["error"] = f"submit unconfirmed: {e}"
                self._save()
                raise GenError(f"line {ln['id']}: {attempt['error']}. Re-run to reconcile; the hold stays open") from e
            if attempt["status"] != OPEN:
                return attempt
        url, charged = attempt.get("immediate_url"), None
        deadline = time.monotonic() + ln.get("timeout_s", 1500)
        while not url:
            outcome = self._poll(ln, attempt)
            if outcome.status == "succeeded":
                url, charged = outcome.url, outcome.charged
                if not url:
                    url = _find_url(outcome.raw)
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
                    raise GenError(f"line {ln['id']}: still pending after {ln.get('timeout_s', 1500)} s; re-run to keep polling")
                self.sleep(ln.get("poll_s", 15))
        return self._deliver(ln, attempt, url, charged)

    def _deliver(self, ln: dict, attempt: dict, url: str, charged: Decimal | None) -> dict:
        dest = (self.dir.parent / ln["out"]).resolve() if not Path(ln["out"]).is_absolute() else Path(ln["out"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.fetch(url, dest)
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
        sessions = self.state.get("sessions", [])
        return {"lines": lines, "canaries": self.state["canaries"], "charged": s["charged"], "held": s["held"], "available": s["available"],
                "headless_usd": round(sum(x["usd"] or 0 for x in sessions), 4), "headless_sessions": len(sessions)}


def _find_url(doc) -> str | None:
    if isinstance(doc, str):
        return doc if re.match(r"https?://", doc) else None
    if isinstance(doc, list):
        for v in doc:
            if (u := _find_url(v)):
                return u
    if isinstance(doc, dict):
        for k, v in doc.items():
            if "url" in k.lower() and (u := _find_url(v)):
                return u
        for k in ("output", "data", "images", "result"):
            if k in doc and (u := _find_url(doc[k])):
                return u
    return None


def _parse_generic(r: dict) -> providers.Outcome:
    status = (r or {}).get("status")
    if status in ("completed", "succeeded", "success"):
        credits = (r.get("usage") or {}).get("credits")
        return providers.Outcome("succeeded", _find_url(r), None if credits is None else Decimal(str(credits)) / 1000, raw=r)
    if status in ("failed", "error", "cancelled"):
        return providers.Outcome("failed", error=str(r.get("error") or status)[:500], raw=r)
    return providers.Outcome("pending", raw=r)


def _fetch(url: str, dest: Path) -> None:
    subprocess.run(["curl", "-sSfL", "--max-time", "300", "-o", str(dest), url], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("quote", "status"):
        sub.add_parser(name).add_argument("plan")
    p = sub.add_parser("quote-live")
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
        if a.cmd == "quote-live":
            print(json.dumps(r.quote_live(a.line)))
            return 0
        if a.cmd == "pass-canary":
            r.pass_canary(a.family, a.note)
            print(json.dumps({"family": a.family, "passed": True}))
            return 0
        res = r.run_line(a.line, a.reroll)
        print(json.dumps({k: v for k, v in res.items() if k not in ("body", "idempotency_key", "hold")}, indent=1))
        return 0 if res["status"] == DONE else 1
    except (GenError, providers.ProviderError) as e:
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
