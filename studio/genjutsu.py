"""Genjutsu-equivalent pipeline: source video + subject image(s) + shot list -> subject-swapped video.

Steps: normalise the source, extract frames and cuts, compose a shot-by-shot prompt, submit, download,
enforce duration, score. `prepare` spends nothing and produces the contact sheet that a prompt author
reads; `run` spends and is resumable, so a re-run polls the existing task and never pays twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol

import cv2
import numpy as np

from . import analyze, score
from .ledger import Ledger
from .providers import HiggsfieldHeadless, Job, Provider, ProviderError, Submission, SubmitRefused, Treg, get_route

FPS = 24
CUT_TOL_S = 0.2
MAX_STRETCH = 0.10
FRAME_S = 1 / FPS


class PipelineError(Exception):
    pass


class DurationError(PipelineError):
    pass


class StillRunning(PipelineError):
    """The poll window ended with the task unfinished. The hold stays open; re-run to resume."""


# ---------------------------------------------------------------- normalise

def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise PipelineError(f"ffmpeg failed: {r.stderr.strip()[:500]}")


def _is_normal(info: dict, seconds: int) -> bool:
    return info["fps_rational"] == f"{FPS}/1" and info["frames"] == seconds * FPS \
        and info["width"] % 2 == 0 and info["height"] % 2 == 0


def _codec(path: Path) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()


def normalise(source: str | Path, out: str | Path, seconds: int | None = None) -> dict:
    """Trim to whole seconds at exactly 24 fps. Rounds down: padding would invent frames that were never shot."""
    source, out = Path(source), Path(out)
    info = analyze.probe(source)
    seconds = seconds or int(info["frames"] / info["fps"] + 1e-6)
    if seconds < 1:
        raise PipelineError(f"{source} is shorter than one second")
    if seconds > info["duration"] + FRAME_S:
        raise PipelineError(f"{source} is {info['duration']} s; cannot normalise up to {seconds} s")
    out.parent.mkdir(parents=True, exist_ok=True)
    if _is_normal(info, seconds) and _codec(source) == "h264":
        shutil.copyfile(source, out)
        method = "copied"
    else:
        audio = ["-c:a", "aac", "-b:a", "160k", "-t", str(seconds)] if info["has_audio"] else ["-an"]
        _ffmpeg(["-i", str(source), "-vf", f"fps={FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-frames:v", str(seconds * FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                 "-movflags", "+faststart", *audio, str(out)])
        method = "reencoded"
    final = analyze.probe(out)
    if final["frames"] != seconds * FPS:
        raise PipelineError(f"normalised clip has {final['frames']} frames, expected {seconds * FPS}")
    return {"path": str(out), "seconds": seconds, "frames": final["frames"], "method": method,
            "width": final["width"], "height": final["height"], "has_audio": final["has_audio"]}


# ---------------------------------------------------------------- frames and cuts

def extract_frames(video: str | Path, out_dir: str | Path, fps: float = 2.0, width: int = 540) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, frame in enumerate(analyze.iter_frames(video, width, fps)):
        p = out_dir / f"t{k / fps:07.3f}.jpg"
        cv2.imwrite(str(p), frame)
        paths.append(str(p))
    return paths


def analyse(video: str | Path, run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    energy = analyze.energy(video)
    sheet_fps = min(6.0, 30.0 / max(energy["info"]["duration"], 0.1))
    sheet = analyze.sheet(video, run_dir / "source_sheet.png", fps=sheet_fps, tile_width=200, cols=8)
    frames = extract_frames(video, run_dir / "frames")
    res = {"info": energy["info"], "cuts": energy["events"]["cuts"],
           "camera_moves": energy["events"]["camera_moves"], "sheet": sheet["path"], "frames": frames}
    (run_dir / "analysis.json").write_text(json.dumps(res, indent=1))
    return res


# ---------------------------------------------------------------- shot list and prompt

@dataclass(frozen=True)
class Shot:
    start: float
    end: float
    framing: str
    camera: str
    action: str


@dataclass(frozen=True)
class ShotList:
    duration_s: int
    subject_name: str
    subject_description: str
    source_subject: str
    setting: str
    keep: tuple[str, ...]
    shots: tuple[Shot, ...]

    @property
    def cuts(self) -> list[float]:
        return [s.start for s in self.shots[1:]]


def parse_shot_list(doc: dict) -> ShotList:
    try:
        shots = tuple(Shot(float(s["start"]), float(s["end"]), str(s["framing"]), str(s["camera"]), str(s["action"]))
                      for s in doc["shots"])
        sl = ShotList(int(doc["duration_s"]), str(doc["subject"]["name"]), str(doc["subject"]["description"]),
                      str(doc["source_subject"]), str(doc["setting"]), tuple(str(k) for k in doc.get("keep", [])), shots)
    except (KeyError, TypeError, ValueError) as e:
        raise PipelineError(f"bad shot list: {e!r}") from e
    if not shots:
        raise PipelineError("shot list has no shots")
    if abs(shots[0].start) > 1e-6:
        raise PipelineError(f"first shot starts at {shots[0].start}, expected 0")
    if abs(shots[-1].end - sl.duration_s) > 1e-6:
        raise PipelineError(f"last shot ends at {shots[-1].end}, expected {sl.duration_s}")
    for a, b in zip(shots, shots[1:]):
        if abs(a.end - b.start) > 1e-6:
            raise PipelineError(f"shots are not contiguous at {a.end} / {b.start}")
    for s in shots:
        if s.end <= s.start:
            raise PipelineError(f"shot ending at {s.end} does not advance")
    return sl


def cut_mismatches(sl: ShotList, measured: list[dict], tol: float = CUT_TOL_S) -> list[str]:
    """Where the authored shot boundaries and the measured cuts disagree. Advisory: detectors miss some cuts."""
    pairs = score.match_times(sl.cuts, sorted(c["t"] for c in measured), tol)
    ok_a, ok_m = {i for i, _ in pairs}, {j for _, j in pairs}
    times = sorted(c["t"] for c in measured)
    return ([f"shot boundary at {t} s has no measured cut" for i, t in enumerate(sl.cuts) if i not in ok_a]
            + [f"measured cut at {t} s has no shot boundary" for j, t in enumerate(times) if j not in ok_m])


class Composer(Protocol):
    def shot_list(self, analysis: dict) -> ShotList: ...


class ShotListFile:
    """A shot list someone wrote after viewing the contact sheet. This is how the Claude Code skill drives it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def shot_list(self, analysis: dict) -> ShotList:
        if not self.path.exists():
            raise PipelineError(f"no shot list at {self.path}; view {analysis.get('sheet')} and write one")
        try:
            return parse_shot_list(json.loads(self.path.read_text()))
        except json.JSONDecodeError as e:
            raise PipelineError(f"{self.path} is not JSON: {e}") from e


def compose_prompt(sl: ShotList, tag: Callable[..., str], task: str = "reference") -> str:
    """`reference` must not read as an edit: Seedance classifies the prompt itself and fails a fixed-duration
    job it classifies as an edit, so that wording is only safe for `task="edit"`."""
    video, image, name = tag("video"), tag("image"), sl.subject_name
    cut_times = ", ".join(f"{t:.2f} s" for t in sl.cuts) or "none"
    if task == "edit":
        head = [f"{image} shows {name}: {sl.subject_description}",
                f"In {video}, change the {sl.source_subject} into {name} throughout. All other footage stays "
                "as filmed: the shots, cuts, camera path, framing, setting, light and timing."]
    elif task == "reference":
        head = [f"{image} shows {name}: {sl.subject_description}",
                f"{video} is the blueprint for this {sl.duration_s}-second film. Follow its shots, camera path, "
                f"framing, blocking and timing exactly. {name} is the only performer and takes the on-screen "
                f"position, pose and size of the {sl.source_subject} in {video}, who does not appear."]
    else:
        raise PipelineError(f"unknown task {task!r}")
    lines = head + [f"Setting from {video}: {sl.setting}",
                    f"Timeline, {sl.duration_s}.00 s in {len(sl.shots)} shots, hard cuts at {cut_times}:"]
    for n, s in enumerate(sl.shots, 1):
        lines.append(f"Shot {n}, {s.start:.2f}-{s.end:.2f} s. {s.framing} Camera: {s.camera} Action: {s.action}")
    lines.append(f"{name} looks the same in every shot, matching {image}. "
                 + (" ".join(sl.keep) + " " if sl.keep else "")
                 + "No extra cuts, shots, titles or captions.")
    return "\n".join(lines)


# ---------------------------------------------------------------- submit, download

def aspect_ratio(width: int, height: int) -> str:
    options = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "3:4": 3 / 4, "4:3": 4 / 3, "21:9": 21 / 9}
    return min(options, key=lambda k: abs(options[k] - width / height))


def _job_hash(job: Job) -> str:
    return hashlib.sha256(json.dumps(asdict(job), sort_keys=True).encode()).hexdigest()[:24]


def _save(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str))
    tmp.replace(path)


def execute(provider: Provider, job: Job, ledger: Ledger, run_dir: str | Path, *, poll_interval: float = 30.0,
            timeout: float = 1200.0, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.monotonic) -> dict:
    """Quote, reserve, submit, poll, settle, download. Returns the task record; raises before any spend if refused."""
    run_dir = Path(run_dir)
    task_path, raw_path = run_dir / "task.json", run_dir / "raw.mp4"
    key = _job_hash(job)
    quote = provider.quote(job)
    task = json.loads(task_path.read_text()) if task_path.exists() else None
    if task and task["job_hash"] != key:
        raise PipelineError(f"{task_path} belongs to a different job. Use a new run directory.")
    if task and task["state"] == "failed":
        raise PipelineError(f"this run already failed: {task.get('error')}. Use a new run directory.")
    if task and task["state"] == "done":
        return task

    if not task:
        provider.preflight(quote)
        hold = ledger.reserve(quote.ceiling, f"{provider.route} {job.duration_s}s {job.resolution}",
                              route=provider.route, expected=str(quote.expected), basis=quote.basis)
        task = {"route": provider.route, "state": "submitting", "job_hash": key, "hold": hold, "quote": asdict(quote),
                "currency": quote.currency, "submitted_at": time.time()}
        _save(task_path, task)

    if task["state"] == "submitting":
        if task.get("attempted") and not provider.idempotent:
            raise PipelineError(f"a submit may have happened without an idempotency key; check {provider.route} by hand")
        task["attempted"] = True
        _save(task_path, task)
        try:
            sub = provider.submit(job, f"remix-{key}")
        except SubmitRefused as e:
            ledger.release(task["hold"], f"refused at submit: {str(e)[:300]}")
            task_path.unlink()
            raise
        task.update(state="running", task_id=sub.task_id, call_id=sub.call_id, provider_hold=sub.provider_hold,
                    submit_answer=sub.raw)
        _save(task_path, task)

    sub = Submission(task["route"], task["task_id"], task.get("call_id"), raw=task.get("submit_answer") or {})
    deadline = clock() + timeout
    while True:
        out = provider.poll(sub)
        if out.status != "pending":
            break
        if clock() >= deadline:
            raise StillRunning(f"{provider.route} task {sub.task_id} still running after {timeout} s; "
                               f"hold {task['hold']} stays open. Re-run to resume.")
        sleep(poll_interval)

    task["finished_at"] = time.time()
    task["final_status"] = out.raw
    if out.status == "failed":
        ledger.release(task["hold"], f"task failed: {str(out.error)[:300]}", task_id=sub.task_id)
        task.update(state="failed", error=out.error)
        _save(task_path, task)
        raise PipelineError(f"{provider.route} task {sub.task_id} failed, hold released: {out.error}")

    # A re-run reaches here after a failed download, or a crash after settling but before saving the task.
    if task["hold"] in ledger.open_holds():
        charged = out.charged if out.charged is not None else quote.expected
        source = "provider" if out.charged is not None else "quote"
        ledger.settle(task["hold"], charged, task_id=sub.task_id, call_id=sub.call_id, charge_source=source)
        task.update(state="settled", charged=charged, charge_source=source)
        _save(task_path, task)
    provider.download(sub, out, raw_path)
    task.update(state="done", raw=str(raw_path))
    _save(task_path, task)
    return task


# ---------------------------------------------------------------- duration

def enforce_duration(raw: str | Path, out: str | Path, seconds: int, max_stretch: float = MAX_STRETCH) -> dict:
    """Make the output exactly `seconds` long at 24 fps: trim a long one, retime a slightly short one, else refuse."""
    raw, out = Path(raw), Path(out)
    info = analyze.probe(raw)
    n = seconds * FPS
    dur = info["frames"] / info["fps"] if info["fps"] else info["duration"]
    res = {"raw_s": round(dur, 4), "raw_frames": info["frames"], "target_s": seconds, "stretch": 0.0}
    out.parent.mkdir(parents=True, exist_ok=True)
    if info["fps_rational"] == f"{FPS}/1" and info["frames"] == n:
        shutil.copyfile(raw, out)
        res["method"] = "copied"
    elif dur >= seconds:
        audio = ["-c:a", "aac", "-t", str(seconds)] if info["has_audio"] else ["-an"]
        _ffmpeg(["-i", str(raw), "-vf", f"fps={FPS}", "-frames:v", str(n), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "16", *audio, str(out)])
        res.update(method="trim", trimmed_s=round(dur - seconds, 4))
    else:
        stretch = seconds / dur - 1
        if stretch > max_stretch:
            raise DurationError(f"{raw} is {dur:.3f} s; reaching {seconds} s needs a {stretch:.1%} retime, "
                                f"above the {max_stretch:.0%} limit")
        # tpad covers the frame or two that rounding can lose at the end of a retime.
        vf = f"setpts=PTS*{seconds / dur:.6f},fps={FPS},tpad=stop_mode=clone:stop=2"
        audio = ["-af", f"atempo={dur / seconds:.6f}", "-c:a", "aac", "-t", str(seconds)] if info["has_audio"] else ["-an"]
        _ffmpeg(["-i", str(raw), "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "16", *audio, str(out)])
        res.update(method="retime", stretch=round(stretch, 4))
    final = analyze.probe(out)
    if final["frames"] != n:
        raise DurationError(f"{out} has {final['frames']} frames after enforcement, expected {n}")
    res.update(final_s=final["duration"], final_frames=final["frames"], path=str(out))
    return res


# ---------------------------------------------------------------- score and compare

def load_baselines(path: str | Path) -> dict[str, dict]:
    """Baselines come from one file, scored by one scorer version, so a gate never compares across versions."""
    return {k: v for k, v in json.loads(Path(path).read_text()).items() if not k.startswith("_")}


def score_output(source: str | Path, output: str | Path, baselines: dict[str, dict] | None = None) -> dict:
    res = score.tracking(str(source), str(output))
    res["gates"] = {name: score.gate(res, b) for name, b in (baselines or {}).items()}
    return res


def compare_sheet(videos: dict[str, str | Path], out_png: str | Path, fps: float = 2.0, tile_width: int = 180) -> dict:
    """One row per video at the same timestamps, so drift is visible by reading down a column."""
    rows = {label: list(analyze.iter_frames(p, tile_width, fps)) for label, p in videos.items()}
    if not any(rows.values()):
        raise PipelineError("no frames decoded")
    cols = max(len(r) for r in rows.values())
    tile_h = max(r[0].shape[0] for r in rows.values() if r)
    canvas = np.zeros((tile_h * len(rows), tile_width * cols, 3), np.uint8)
    for r, (label, frames) in enumerate(rows.items()):
        for c, frame in enumerate(frames):
            t = frame.copy()
            cv2.rectangle(t, (0, 0), (tile_width, 16), (0, 0, 0), -1)
            cv2.putText(t, f"{label} {c / fps:.1f}s", (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
            canvas[r * tile_h:r * tile_h + t.shape[0], c * tile_width:(c + 1) * tile_width] = t[:tile_h]
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), canvas)
    return {"path": str(out_png), "rows": list(rows), "cols": cols}


# ---------------------------------------------------------------- pipeline

@dataclass
class RunConfig:
    source: Path
    run_dir: Path
    video_url: str
    image_urls: tuple[str, ...]
    resolution: str = "480p"
    aspect_ratio: str | None = None
    task: str = "reference"
    baselines: dict[str, dict] = field(default_factory=dict)
    poll_interval: float = 30.0
    timeout: float = 1200.0


def prepare(source: str | Path, run_dir: str | Path, seconds: int | None = None) -> dict:
    run_dir = Path(run_dir)
    norm = normalise(source, run_dir / "source.mp4", seconds)
    ana = analyse(norm["path"], run_dir)
    return {"normalised": norm, "analysis": {k: ana[k] for k in ("info", "cuts", "camera_moves", "sheet")}}


def build_job(cfg: RunConfig, composer: Composer, provider: Provider, seconds: int | None = None) -> tuple[Job, ShotList, list[str]]:
    ana = json.loads((cfg.run_dir / "analysis.json").read_text())
    info = ana["info"]
    sl = composer.shot_list(ana)
    if sl.duration_s != round(info["duration"]):
        raise PipelineError(f"shot list is {sl.duration_s} s, source is {info['duration']} s")
    warnings = cut_mismatches(sl, ana["cuts"])
    prompt = compose_prompt(sl, provider.tag, cfg.task)
    (cfg.run_dir / "prompt.txt").write_text(prompt)
    job = Job(prompt, cfg.video_url, cfg.image_urls, seconds or sl.duration_s, info["duration"], cfg.resolution,
              cfg.aspect_ratio or aspect_ratio(info["width"], info["height"]), False, cfg.task)
    return job, sl, warnings


def run(cfg: RunConfig, composer: Composer, provider: Provider, ledger: Ledger) -> dict:
    job, sl, warnings = build_job(cfg, composer, provider)
    task = execute(provider, job, ledger, cfg.run_dir, poll_interval=cfg.poll_interval, timeout=cfg.timeout)
    source = cfg.run_dir / "source.mp4"
    report = {"route": provider.route, "task": task, "cut_warnings": warnings, "ledger": ledger.summary()}
    try:
        report["duration"] = enforce_duration(cfg.run_dir / "raw.mp4", cfg.run_dir / "output.mp4", sl.duration_s)
    except DurationError as e:
        report["duration"] = {"error": str(e)}
        _save(cfg.run_dir / "report.json", report)
        raise
    report["score_raw"] = score_output(source, cfg.run_dir / "raw.mp4", cfg.baselines)
    report["score"] = score_output(source, cfg.run_dir / "output.mp4", cfg.baselines)
    _save(cfg.run_dir / "report.json", report)
    return report


# ---------------------------------------------------------------- CLI

def _provider(a) -> Provider:
    if a.route == "higgsfield":
        return HiggsfieldHeadless(a.hf_cwd, a.hf_video_media, a.hf_image_media, Decimal(a.hf_floor), Decimal(a.hf_max),
                                  Path(a.run_dir) / "session.jsonl", mode=a.hf_mode,
                                  also_quote=tuple(a.hf_also_quote))
    return get_route(a.route)


def _resolve_urls(a) -> tuple[str, tuple[str, ...]]:
    if a.route == "higgsfield":
        return a.hf_video_media, (a.hf_image_media,)
    if a.host_public:
        treg = Treg()
        return treg.host(Path(a.run_dir) / "source.mp4")["url"], tuple(treg.host(p)["url"] for p in a.subject)
    if not (a.video_url and a.image_url):
        raise PipelineError("pass --video-url and --image-url, or --host-public --subject FILE to make the "
                            "source and subject publicly readable for 7 days")
    return a.video_url, tuple(a.image_url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="genjutsu", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="normalise and analyse; spends nothing")
    p.add_argument("source"); p.add_argument("--run-dir", required=True); p.add_argument("--seconds", type=int)

    def job_args(p):
        p.add_argument("--run-dir", required=True); p.add_argument("--shot-list", required=True)
        p.add_argument("--route", required=True); p.add_argument("--video-url"); p.add_argument("--image-url", action="append")
        p.add_argument("--host-public", action="store_true"); p.add_argument("--subject", action="append", default=[])
        p.add_argument("--resolution", default="480p"); p.add_argument("--aspect-ratio"); p.add_argument("--task", default="reference")
        p.add_argument("--hf-cwd"); p.add_argument("--hf-video-media"); p.add_argument("--hf-image-media")
        p.add_argument("--hf-floor", default="87"); p.add_argument("--hf-max", default="15"); p.add_argument("--hf-mode", default="omni_reference")
        p.add_argument("--hf-also-quote", action="append", default=[], metavar="MODE")

    p = sub.add_parser("quote", help="show what a run would cost; spends nothing"); job_args(p)
    p = sub.add_parser("run", help="submit, download, enforce duration, score; spends"); job_args(p)
    p.add_argument("--cap", required=True, help="spend cap for this ledger, in the route's currency")
    p.add_argument("--ledger"); p.add_argument("--baselines", help="baselines JSON, as in docs/baselines.json")
    p.add_argument("--poll-interval", type=float, default=30.0); p.add_argument("--timeout", type=float, default=1200.0)
    p = sub.add_parser("compare", help="side-by-side sheet: one row per video"); p.add_argument("out_png")
    p.add_argument("videos", nargs="+", metavar="LABEL=PATH"); p.add_argument("--fps", type=float, default=2.0)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "prepare":
            print(json.dumps(prepare(a.source, a.run_dir, a.seconds), indent=1))
        elif a.cmd == "compare":
            print(json.dumps(compare_sheet(dict(v.split("=", 1) for v in a.videos), a.out_png, a.fps)))
        else:
            prov = _provider(a)
            video_url, image_urls = _resolve_urls(a)
            cfg = RunConfig(Path(a.run_dir) / "source.mp4", Path(a.run_dir), video_url, image_urls, a.resolution,
                            a.aspect_ratio, a.task)
            composer = ShotListFile(a.shot_list)
            if a.cmd == "quote":
                job, _, warnings = build_job(cfg, composer, prov)
                q = prov.quote(job)
                print(json.dumps({**asdict(q), "cut_warnings": warnings, "prompt": job.prompt}, indent=1, default=str))
            else:
                cfg.baselines = load_baselines(a.baselines) if a.baselines else {}
                cfg.poll_interval, cfg.timeout = a.poll_interval, a.timeout
                ledger = Ledger(a.ledger or Path(a.run_dir) / "ledger.jsonl", a.cap, prov.currency)
                rep = run(cfg, composer, prov, ledger)
                print(json.dumps({"tracking": rep["score"]["tracking"], "gates": rep["score"]["gates"],
                                  "duration": rep["duration"], "ledger": rep["ledger"], "cut_warnings": rep["cut_warnings"]},
                                 indent=1, default=str))
                print(score.table(rep["score"]), file=sys.stderr)
    except (PipelineError, ProviderError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
