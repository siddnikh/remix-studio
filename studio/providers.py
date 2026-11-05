"""Model adapters and call execution.

Each adapter owns its route's request body, price arithmetic and response parsing. `quote` makes no
network call. `ceiling` in a Quote is what the ledger must hold: on usage-settled routes the provider's own
reservation can be far below what the call finally charges.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol

Runner = Callable[..., subprocess.CompletedProcess]


class ProviderError(Exception):
    pass


class UnsupportedJob(ProviderError):
    """The route cannot express this job at all. Nothing was submitted."""


class Unaffordable(ProviderError):
    """The account cannot cover the provider's hold or the expected charge. Nothing was submitted."""


class SubmitRefused(ProviderError):
    """The provider or gateway answered without creating a task, so nothing was charged."""


@dataclass(frozen=True)
class Job:
    prompt: str
    video_url: str
    image_urls: tuple[str, ...]
    duration_s: int
    reference_s: float
    resolution: str = "480p"
    aspect_ratio: str = "9:16"
    generate_audio: bool = False
    task: str = "reference"  # "reference": fixed output length. "edit": output follows the source clip.


@dataclass(frozen=True)
class Quote:
    route: str
    currency: str
    provider_hold: Decimal
    expected: Decimal
    ceiling: Decimal
    basis: str


@dataclass(frozen=True)
class Submission:
    route: str
    task_id: str
    call_id: str | None = None
    provider_hold: Decimal | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    status: str  # "pending" | "succeeded" | "failed"
    url: str | None = None
    charged: Decimal | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


class Provider(Protocol):
    route: str
    currency: str
    idempotent: bool

    def tag(self, kind: str, n: int = 1) -> str: ...
    def quote(self, job: Job) -> Quote: ...
    def preflight(self, quote: Quote) -> None: ...
    def submit(self, job: Job, idempotency_key: str) -> Submission: ...
    def poll(self, sub: Submission) -> Outcome: ...
    def download(self, sub: Submission, outcome: Outcome, dest: Path) -> None: ...


def _dec(x) -> Decimal:
    return Decimal(str(x))


def find_url(doc) -> str | None:
    if isinstance(doc, str):
        return doc if re.match(r"https?://", doc) else None
    if isinstance(doc, list):
        for v in doc:
            if (u := find_url(v)):
                return u
    if isinstance(doc, dict):
        for k, v in doc.items():
            if "url" in k.lower() and (u := find_url(v)):
                return u
        for k in ("output", "data", "images", "result", "video"):
            if k in doc and (u := find_url(doc[k])):
                return u
    return None


def _download_url(url: str, dest: Path, run: Runner = subprocess.run) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run(["curl", "-sS", "-f", "-L", "-m", "300", "-o", str(tmp), url], capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise ProviderError(f"download failed: {r.stderr.strip()[:300]}")
    tmp.replace(dest)


# ---------------------------------------------------------------- treg

class Treg:
    """The `treg` CLI. Credentials stay on treg's server, so nothing here ever holds a key."""

    def __init__(self, run: Runner = subprocess.run, binary: str = "treg"):
        self._run, self._bin = run, binary

    def balance_micro(self) -> int:
        r = self._run([self._bin, "--json", "balance"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise ProviderError(f"treg balance failed: {r.stderr.strip()[:300]}")
        return int(json.loads(r.stdout)["balance_micro"])

    def call(self, tool: str, body: dict | None = None, query: dict | None = None,
             idempotency_key: str | None = None) -> tuple[dict, dict]:
        """Returns (provider body, treg metadata). A non-2xx is data for the adapter, not an exception."""
        cmd = [self._bin, "--json", "call", tool]
        for k, v in (query or {}).items():
            cmd += ["-p", f"{k}={v}"]
        if idempotency_key:
            cmd += ["-H", f"Idempotency-Key: {idempotency_key}"]
        stdin = None
        if body is not None:
            cmd += ["--method", "POST", "--data", "@-"]
            stdin = json.dumps(body)
        r = self._run(cmd, input=stdin, capture_output=True, text=True, timeout=180)
        try:
            doc = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise ProviderError(f"unparseable answer from {tool} (exit {r.returncode}): {r.stdout[:200]!r}") from e
        return doc.get("result"), doc.get("_treg") or {}

    def fetch_binary(self, tool: str, query: dict, dest: Path) -> None:
        tmp = dest.with_suffix(dest.suffix + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self._bin, "call", tool] + [a for k, v in query.items() for a in ("-p", f"{k}={v}")]
        with open(tmp, "wb") as f:
            r = self._run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=300)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise ProviderError(f"{tool} failed: {(r.stderr or b'')[:300]!r}")
        tmp.replace(dest)

    def host(self, path: str | Path) -> dict:
        """Makes the file publicly readable for 7 days. Callers must have explicit consent for this file."""
        r = self._run([self._bin, "host", str(path), "--json"], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise ProviderError(f"treg host failed: {r.stderr.strip()[:300]}")
        return json.loads(r.stdout)


class TregRoute:
    route = ""
    currency = "USD"
    idempotent = True
    poll_tool = ""
    poll_param = "id"

    def __init__(self, treg: Treg | None = None):
        self.treg = treg or Treg()

    def tag(self, kind: str, n: int = 1) -> str:
        return f"@{kind}{n}"

    def preflight(self, quote: Quote) -> None:
        balance = _dec(self.treg.balance_micro()) / 1_000_000
        if quote.provider_hold > balance:
            raise Unaffordable(f"{self.route}: provider hold ${quote.provider_hold} exceeds balance ${balance}")
        # A hold that fits is not enough: this route can settle above its hold.
        if quote.ceiling > balance:
            raise Unaffordable(f"{self.route}: could settle at ${quote.ceiling}, above balance ${balance}")

    def submit(self, job: Job, idempotency_key: str) -> Submission:
        result, meta = self.treg.call(self.route, body=self.body(job), idempotency_key=idempotency_key)
        status = meta.get("http_status")
        if status is None or not 200 <= int(status) < 300:
            raise SubmitRefused(f"{self.route}: HTTP {status}: {json.dumps(result)[:600]}")
        task_id = self.task_id(result)
        if not task_id:
            raise SubmitRefused(f"{self.route}: no task id in answer: {json.dumps(result)[:600]}")
        reserved = meta.get("reserved_micro", meta.get("charged_micro"))
        return Submission(self.route, task_id, meta.get("call_id"),
                          None if reserved is None else _dec(reserved) / 1_000_000, result)

    def poll(self, sub: Submission) -> Outcome:
        result, meta = self.treg.call(self.poll_tool, query={self.poll_param: sub.task_id})
        status = meta.get("http_status")
        if status is None or not 200 <= int(status) < 300:
            return Outcome("pending", error=f"poll HTTP {status}", raw=result if isinstance(result, dict) else {})
        return self.parse_status(result)

    def download(self, sub: Submission, outcome: Outcome, dest: Path) -> None:
        if not outcome.url:
            raise ProviderError(f"{self.route}: finished task has no output URL")
        _download_url(outcome.url, dest)

    def body(self, job: Job) -> dict:
        raise NotImplementedError

    def task_id(self, result: dict) -> str | None:
        return (result or {}).get("id")

    def parse_status(self, result: dict) -> Outcome:
        raise NotImplementedError


# Live 2026-09-14 on this route a 4 s 480p clip settled 1.04% above the token arithmetic.
SETTLE_MARGIN = Decimal("1.02")
OPENROUTER_SIZES = {("480p", "9:16"): (480, 854), ("480p", "16:9"): (854, 480),
                    ("720p", "9:16"): (720, 1280), ("720p", "16:9"): (1280, 720)}


class OpenRouterSeedance25(TregRoute):
    """openrouter.video-gen.seedance-2-5.

    The catalog lists only frame_images, but its price note acknowledges a reference-video input and
    OpenRouter's own schema takes `input_references` of type video_url. Its supported durations stop at 4
    with no auto value, so it cannot run an edit job, whose length must follow the source.
    """
    route = "openrouter.video-gen.seedance-2-5"
    poll_tool = "openrouter.video-gen.task.status"
    TOKEN_RATE = Decimal("0.0000107")
    TOKEN_RATE_VIDEO = Decimal("0.0000064")
    HOLD_PER_S = {"480p": Decimal("0.1028"), "720p": Decimal("0.2312")}

    def _check(self, job: Job) -> tuple[int, int]:
        if job.task != "reference":
            raise UnsupportedJob(f"{self.route} has no edit task: fixed durations only (4-30 s)")
        if not 4 <= job.duration_s <= 30:
            raise UnsupportedJob(f"{self.route} takes 4-30 s, not {job.duration_s}")
        size = OPENROUTER_SIZES.get((job.resolution, job.aspect_ratio))
        if size is None:
            raise UnsupportedJob(f"{self.route}: no size known for {job.resolution} {job.aspect_ratio}")
        return size

    def quote(self, job: Job) -> Quote:
        w, h = self._check(job)
        billable = math.ceil(job.reference_s) + job.duration_s
        tokens = Decimal(w * h * 24 * billable) / 1024
        expected = (tokens * self.TOKEN_RATE_VIDEO).quantize(Decimal("0.000001"))
        return Quote(self.route, self.currency, self.HOLD_PER_S[job.resolution] * job.duration_s, expected,
                     (expected * SETTLE_MARGIN).quantize(Decimal("0.000001")),
                     f"{tokens:.0f} video tokens ({billable} s input+output, {w}x{h}, 24 fps) at "
                     f"${self.TOKEN_RATE_VIDEO}/token with video input; usage.cost settles")

    def body(self, job: Job) -> dict:
        self._check(job)
        refs = [{"type": "video_url", "video_url": {"url": job.video_url}}]
        refs += [{"type": "image_url", "image_url": {"url": u}} for u in job.image_urls]
        return {"model": "bytedance/seedance-2.5", "prompt": job.prompt, "duration": job.duration_s,
                "resolution": job.resolution, "aspect_ratio": job.aspect_ratio,
                "generate_audio": job.generate_audio, "input_references": refs}

    def parse_status(self, r: dict) -> Outcome:
        status = r.get("status")
        cost = (r.get("usage") or {}).get("cost")
        charged = None if cost is None else _dec(cost)
        if status == "completed":
            return Outcome("succeeded", charged=charged, raw=r)
        if status in ("failed", "cancelled", "expired"):
            return Outcome("failed", error=str(r.get("error") or status)[:600], raw=r)
        return Outcome("pending", raw=r)

    def download(self, sub: Submission, outcome: Outcome, dest: Path) -> None:
        self.treg.fetch_binary("openrouter.video-gen.result.retrieve", {"id": sub.task_id}, dest)


class ReapiSeedance25(TregRoute):
    """reapi.video-gen.seedance-2-5. The only callable route with an edit task, which holds 30 s at submit."""
    route = "reapi.video-gen.seedance-2-5"
    poll_tool = "reapi.tasks.get"
    PER_S = {"480p": Decimal("0.1186"), "720p": Decimal("0.26683"), "1080p": Decimal("0.462")}
    extra: dict = {}

    def tag(self, kind: str, n: int = 1) -> str:
        return f"@{kind.capitalize()} {n}"

    def quote(self, job: Job) -> Quote:
        rate = self.PER_S[job.resolution]
        duration = math.ceil(job.reference_s) if job.task == "edit" else job.duration_s
        billable = max(math.ceil(job.reference_s) + duration, math.ceil(Decimal(5 * duration) / 3))
        expected = rate * billable
        hold = rate * 30 if job.task == "edit" else rate * job.duration_s
        return Quote(self.route, self.currency, hold, expected, expected * SETTLE_MARGIN,
                     f"{billable} billable s (reference plus output, with the video-input floor) at ${rate}/s")

    def body(self, job: Job) -> dict:
        body = {"model": "doubao-seedance-2.5-face", "prompt": job.prompt, "resolution": job.resolution,
                "generate_audio": job.generate_audio, "video_urls": [job.video_url],
                "image_urls": list(job.image_urls), **self.extra}
        if job.task == "edit":
            body.update(duration=-1, omni_reference_task_type="edit")
        else:
            body.update(duration=job.duration_s, size=job.aspect_ratio)
        return body

    def parse_status(self, r: dict) -> Outcome:
        status = r.get("status")
        if status == "completed":
            urls = (r.get("output") or {}).get("video_urls") or []
            credits = (r.get("usage") or {}).get("credits")
            return Outcome("succeeded", urls[0] if urls else None,
                           None if credits is None else _dec(credits) / 1000, raw=r)
        if status == "failed":
            return Outcome("failed", error=str(r.get("error") or status)[:600], raw=r)
        return Outcome("pending", raw=r)


class ReapiSeedance25Unrestricted(ReapiSeedance25):
    route = "reapi.video-gen.seedance-2-5.unrestricted"
    extra = {"content_filter": False}


class PiapiSeedance25(TregRoute):
    """piapi.video-gen.seedance-2-5. Fixed price table; shortest output is 5 s."""
    route = "piapi.video-gen.seedance-2-5"
    poll_tool = "piapi.task.get"
    poll_param = "task_id"
    task_type = "seedance-2.5"
    DURATIONS = (5, 8, 10, 15, 20, 25, 30)
    PER_S = {"480p": Decimal("0.15"), "720p": Decimal("0.35"), "1080p": Decimal("0.8")}

    def quote(self, job: Job) -> Quote:
        if job.task != "reference":
            raise UnsupportedJob(f"{self.route}: the catalog lists no edit task")
        if job.duration_s not in self.DURATIONS:
            raise UnsupportedJob(f"{self.route} takes {self.DURATIONS} s, not {job.duration_s}")
        price = self.PER_S[job.resolution] * job.duration_s
        return Quote(self.route, self.currency, price, price, price, "price table, settled as reserved")

    def body(self, job: Job) -> dict:
        return {"model": "seedance", "task_type": self.task_type,
                "input": {"prompt": job.prompt, "duration": job.duration_s, "resolution": job.resolution,
                          "aspect_ratio": job.aspect_ratio, "image_urls": list(job.image_urls),
                          "video_urls": [job.video_url]}}

    def task_id(self, result: dict) -> str | None:
        return ((result or {}).get("data") or {}).get("task_id")

    def parse_status(self, r: dict) -> Outcome:
        d = r.get("data") or {}
        status = str(d.get("status", "")).lower()
        if status == "completed":
            return Outcome("succeeded", (d.get("output") or {}).get("video"), raw=r)
        if status == "failed":
            return Outcome("failed", error=str(d.get("error") or status)[:600], raw=r)
        return Outcome("pending", raw=r)


ROUTES: dict[str, Callable[[], TregRoute]] = {
    OpenRouterSeedance25.route: OpenRouterSeedance25,
    ReapiSeedance25.route: ReapiSeedance25,
    ReapiSeedance25Unrestricted.route: ReapiSeedance25Unrestricted,
    PiapiSeedance25.route: PiapiSeedance25,
}


def get_route(route: str) -> Provider:
    if route.startswith("http:"):
        from . import services
        return services.get(route.removeprefix("http:"))
    try:
        return ROUTES[route]()
    except KeyError:
        raise ProviderError(f"no adapter for {route!r}; known: {sorted(ROUTES)}, or http:<name> from services.json") from None
