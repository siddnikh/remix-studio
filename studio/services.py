"""Any HTTP generation API, described in services.json instead of code.

A plan line or a swap run uses one with the route `http:<name>`. Keys stay in the environment or the env file:
`${NAME}` is expanded only when a request is sent, so a key never reaches a plan, a state file or a log.
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

from .providers import Job, Outcome, ProviderError, Quote, Submission, SubmitRefused, UnsupportedJob, find_url

ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
JOB_FIELD = re.compile(r"\{(prompt|video_url|image_urls|duration_s|resolution|aspect_ratio|generate_audio)\}")
KNOWN_KEYS = {"_comment", "submit", "idempotency_header", "task_id_path", "poll", "status_path", "succeeded", "failed", "error_path",
              "result", "result_url_path", "charge_path", "charge_scale", "download_headers", "job_body", "tags", "price"}
PENDING_ON_POLL = "poll"
TIMEOUT_S = 180


class ServiceConfigError(ProviderError):
    """The service is not usable as configured. Raised before any hold or request."""


def home() -> Path:
    return Path(os.environ.get("REMIX_STUDIO_HOME") or Path.home() / ".config" / "remix-studio")


def services_path() -> Path:
    return home() / "services.json"


def env_path() -> Path:
    return home() / "env"


def _env_file() -> dict[str, str]:
    """KEY=value lines. Agents often start without the user's shell profile, so a key has to live somewhere they can all read."""
    path = env_path()
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def _lookup_env(name: str) -> str | None:
    return os.environ.get(name) or _env_file().get(name)


def load_all() -> dict[str, dict]:
    path = services_path()
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ServiceConfigError(f"{path} is not JSON: {e}") from e
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def get(name: str) -> "HttpService":
    services = load_all()
    if name not in services:
        raise ServiceConfigError(f"no service {name!r} in {services_path()}; known: {sorted(services)}")
    return HttpService(name, services[name])


def data_uri(path: str | Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def read_path(doc: Any, path: str) -> Any:
    """`output.0.url` walks dicts by key and lists by index. Anything missing is None."""
    for part in path.split("."):
        if isinstance(doc, dict):
            doc = doc.get(part)
        elif isinstance(doc, list) and part.lstrip("-").isdigit() and -len(doc) <= int(part) < len(doc):
            doc = doc[int(part)]
        else:
            return None
    return doc


def _fill(template: Any, values: dict) -> Any:
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    if isinstance(template, str):
        whole = JOB_FIELD.fullmatch(template)
        if whole:
            return values[whole.group(1)]
        return JOB_FIELD.sub(lambda m: str(values[m.group(1)]), template)
    return template


def _parse_body(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_unparsed": raw[:600].decode("utf-8", "replace")}


class HttpService:
    def __init__(self, name: str, cfg: dict):
        unknown = set(cfg) - KNOWN_KEYS
        if unknown:
            raise ServiceConfigError(f"service {name}: unknown keys {sorted(unknown)}")
        if not (cfg.get("submit") or {}).get("url"):
            raise ServiceConfigError(f"service {name}: submit.url is required")
        if cfg.get("poll") and not cfg.get("status_path"):
            raise ServiceConfigError(f"service {name}: poll needs status_path")
        self.name, self.cfg = name, cfg
        self.route = f"http:{name}"
        self.currency = (cfg.get("price") or {}).get("currency", "USD")
        self.idempotent = bool(cfg.get("idempotency_header"))
        self.succeeded = {s.lower() for s in cfg.get("succeeded", ["completed", "succeeded", "success"])}
        self.failed = {s.lower() for s in cfg.get("failed", ["failed", "error", "canceled", "cancelled", "expired"])}

    # ------------------------------------------------------------ readiness

    def required_env(self) -> list[str]:
        specs = [self.cfg["submit"], self.cfg.get("poll") or {}, self.cfg.get("result") or {}, {"headers": self.cfg.get("download_headers") or {}}]
        found: set[str] = set()
        for spec in specs:
            found.update(ENV_REF.findall(spec.get("url", "")))
            for value in (spec.get("headers") or {}).values():
                found.update(ENV_REF.findall(value))
        return sorted(found)

    def missing_env(self) -> list[str]:
        return [n for n in self.required_env() if not _lookup_env(n)]

    def check_ready(self) -> None:
        missing = self.missing_env()
        if missing:
            raise ServiceConfigError(f"{self.route}: set {', '.join(missing)} in the environment or in {env_path()}")

    def _expand(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            value = _lookup_env(m.group(1))
            if not value:
                raise ServiceConfigError(f"{self.route}: {m.group(1)} is not set")
            return value
        return ENV_REF.sub(sub, text)

    # ------------------------------------------------------------ HTTP

    def _send(self, spec: dict, body: Any = None, task_id: str | None = None, extra_headers: dict | None = None) -> tuple[int, Any]:
        url = self._expand(spec["url"])
        if task_id is not None:
            url = url.replace("{task_id}", urllib.parse.quote(str(task_id), safe=""))
        headers = {k: self._expand(v) for k, v in (spec.get("headers") or {}).items()}
        headers.update(extra_headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=headers, method=spec.get("method", "POST" if data else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=spec.get("timeout_s", TIMEOUT_S)) as r:
                return r.status, _parse_body(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _parse_body(e.read())

    def submit_body(self, body: Any, idempotency_key: str) -> Submission:
        """SubmitRefused means no job exists, so the hold can go. Any other error leaves that unknown, so the hold must stay."""
        self.check_ready()
        extra = {self.cfg["idempotency_header"]: idempotency_key} if self.idempotent else None
        try:
            status, doc = self._send(self.cfg["submit"], body, extra_headers=extra)
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError(f"{self.route}: submit got no answer: {e}") from e
        if status == 408 or status >= 500:
            raise ProviderError(f"{self.route}: submit answered HTTP {status}, which does not prove no job exists: {json.dumps(doc)[:400]}")
        if not 200 <= status < 300:
            raise SubmitRefused(f"{self.route}: HTTP {status}: {json.dumps(doc)[:600]}")
        task_id = read_path(doc, self.cfg.get("task_id_path", "id"))
        if not task_id and not self.cfg.get("poll"):
            task_id = "immediate"
        if not task_id:
            raise ProviderError(f"{self.route}: accepted, but no task id at {self.cfg.get('task_id_path', 'id')}: {json.dumps(doc)[:400]}")
        return Submission(self.route, str(task_id), raw=doc if isinstance(doc, dict) else {"answer": doc})

    def poll(self, sub: Submission) -> Outcome:
        if not self.cfg.get("poll"):
            return self._finished(sub.raw, sub.raw)
        try:
            status, doc = self._send(self.cfg["poll"], task_id=sub.task_id)
        except (urllib.error.URLError, OSError) as e:
            return Outcome("pending", error=f"poll got no answer: {e}")
        if not 200 <= status < 300:
            return Outcome("pending", error=f"poll HTTP {status}", raw=doc if isinstance(doc, dict) else {})
        state = str(read_path(doc, self.cfg["status_path"])).lower()
        if state in self.failed:
            error = read_path(doc, self.cfg.get("error_path", "error")) or state
            return Outcome("failed", error=str(error)[:600], raw=doc)
        if state not in self.succeeded:
            return Outcome("pending", raw=doc)
        if not self.cfg.get("result"):
            return self._finished(doc, doc)
        try:
            status, result = self._send(self.cfg["result"], task_id=sub.task_id)
        except (urllib.error.URLError, OSError) as e:
            return Outcome("pending", error=f"result got no answer: {e}")
        if not 200 <= status < 300:
            return Outcome("pending", error=f"result HTTP {status}", raw=doc)
        return self._finished(result, doc)

    def _finished(self, result_doc: Any, status_doc: Any) -> Outcome:
        path = self.cfg.get("result_url_path")
        value = read_path(result_doc, path) if path else None
        url = find_url(value) if value is not None else find_url(result_doc)
        charged = None
        if self.cfg.get("charge_path"):
            raw = read_path(result_doc, self.cfg["charge_path"])
            raw = read_path(status_doc, self.cfg["charge_path"]) if raw is None else raw
            if raw is not None:
                charged = Decimal(str(raw)) * Decimal(str(self.cfg.get("charge_scale", 1)))
        raw_doc = result_doc if isinstance(result_doc, dict) else {"answer": result_doc}
        if not url:
            return Outcome("failed", error=f"finished with no output URL: {json.dumps(result_doc)[:400]}", charged=charged, raw=raw_doc)
        return Outcome("succeeded", url, charged, raw=raw_doc)

    def fetch(self, url: str, dest: Path) -> None:
        """Result URLs can be signed and short-lived, and some need the service's own headers."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        headers = {k: self._expand(v) for k, v in (self.cfg.get("download_headers") or {}).items()}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except (urllib.error.URLError, OSError) as e:
            tmp.unlink(missing_ok=True)
            raise ProviderError(f"{self.route}: download failed: {e}") from e
        tmp.replace(dest)

    # ------------------------------------------------------------ the Provider protocol, for subject swap

    def tag(self, kind: str, n: int = 1) -> str:
        fmt = (self.cfg.get("tags") or {}).get(kind, "@" + kind + "{n}")
        return fmt.replace("{n}", str(n))

    def quote(self, job: Job) -> Quote:
        price = self.cfg.get("price")
        if not price:
            raise UnsupportedJob(f"{self.route}: add a price block, because the ledger needs a number to hold before it spends")
        rate = (price.get("per_second") or {}).get(job.resolution)
        if rate is None:
            raise UnsupportedJob(f"{self.route}: no price for {job.resolution}")
        allowed = price.get("durations")
        if allowed and job.duration_s not in allowed:
            raise UnsupportedJob(f"{self.route} takes {allowed} s, not {job.duration_s}")
        if job.task != "reference":
            raise UnsupportedJob(f"{self.route}: job_body describes a fixed-length job, not {job.task!r}")
        billable = job.duration_s + (math.ceil(job.reference_s) if price.get("bill_reference_seconds") else 0)
        expected = Decimal(str(rate)) * billable
        ceiling = expected * Decimal(str(price.get("margin", "1.02")))
        return Quote(self.route, self.currency, ceiling, expected, ceiling, f"{billable} billable s at {rate}/s, from services.json")

    def preflight(self, quote: Quote) -> None:
        self.check_ready()

    def submit(self, job: Job, idempotency_key: str) -> Submission:
        if not self.cfg.get("job_body"):
            raise UnsupportedJob(f"{self.route}: subject swap needs a job_body template")
        values = {"prompt": job.prompt, "video_url": job.video_url, "image_urls": list(job.image_urls), "duration_s": job.duration_s,
                  "resolution": job.resolution, "aspect_ratio": job.aspect_ratio, "generate_audio": job.generate_audio}
        return self.submit_body(_fill(self.cfg["job_body"], values), idempotency_key)

    def download(self, sub: Submission, outcome: Outcome, dest: Path) -> None:
        if not outcome.url:
            raise ProviderError(f"{self.route}: finished task has no output URL")
        self.fetch(outcome.url, dest)
