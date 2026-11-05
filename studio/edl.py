"""Validated edit decision lists (EDLs) and their ffmpeg renderer.

`validate` turns EDL JSON into a Plan or reports every problem at once. `render` builds exact-length shot
segments, joins them, applies the grade and flashes, then draws text as a separate final pass and mixes
recorded audio. `check` inspects a rendered file for black, white, frozen and repeated frames, bars, wrong
length and audio faults. Format reference: references/edl.md in the remix skill.

    remix-studio edl validate edit.json
    remix-studio edl render edit.json --preview -o runs/x/preview.mp4
    remix-studio edl render edit.json -o runs/x/final.mp4
    remix-studio edl check runs/x/final.mp4 --edl edit.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from studio import analyze, fx

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
SR = 48000
MATTE_SOURCE = Path(__file__).resolve().with_name("matte.m")
TRUE_PEAK_LIMIT_DB = -1.0
THREADS = str(int(os.environ.get("STUDIO_THREADS", "4")))


class EDLError(Exception):
    def __init__(self, problems: list["Problem"]):
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems if p.level == "error"))


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class Problem:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Source:
    key: str
    path: Path
    is_image: bool
    w: int
    h: int
    fps: Fraction
    frames: int
    bad_ranges: list[tuple[int, int]]


@dataclass
class Shot:
    id: str
    source: str
    start: int
    frames: int
    src_in: int
    src_span: int
    speed: float
    repeat: bool
    flip: bool
    focus: tuple[float, float]
    slowmo: str
    camera: fx.Camera
    grade: dict | None
    whip_in: fx.Whip | None = None
    whip_out: fx.Whip | None = None
    glitch_in: fx.Glitch | None = None
    glitch_out: fx.Glitch | None = None


@dataclass
class HitCue:
    path: Path
    frame: int
    gain_db: float


@dataclass
class AudioPlan:
    bed: Path | None = None
    bed_in: float = 0.0
    bed_gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    hits: list[HitCue] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    none_reason: str | None = None


@dataclass
class Plan:
    base: Path
    w: int
    h: int
    fps: Fraction
    frames: int
    sources: dict[str, Source]
    shots: list[Shot]
    flashes: list[fx.Flash]
    texts: list[fx.Text]
    grade: dict | None
    audio: AudioPlan
    problems: list[Problem]
    matte: Path | None = None

    @property
    def duration(self) -> float:
        return float(self.frames / self.fps)

    def stats(self) -> dict:
        """Authored rates, directly comparable with analyze.energy()'s measured `rates`."""
        d = self.duration
        bursts = sum(len(s.camera.bursts) for s in self.shots)
        moving = sum(s.frames for s in self.shots if s.camera.active)
        return {
            "frames": self.frames, "duration": round(d, 4), "shots": len(self.shots),
            "cuts_per_s": round((len(self.shots) - 1) / d, 3),
            "flashes_per_s": round(len(self.flashes) / d, 3),
            "shake_bursts_per_s": round(bursts / d, 3),
            "camera_moved_fraction": round(moving / self.frames, 3),
            "hits_per_s": round(len(self.audio.hits) / d, 3),
            "texts": len(self.texts),
        }

    def uses(self) -> list[dict]:
        return [{"shot": s.id, "source": s.source, "out": [s.start, s.start + s.frames],
                 "src": [s.src_in, s.src_in + s.src_span], "speed": round(s.speed, 4), "repeat": s.repeat}
                for s in self.shots]

    def flash_frames(self) -> set[int]:
        return {f.at + k for f in self.flashes for k in range(f.frames)}

    def repeat_frames(self) -> set[int]:
        return {i for s in self.shots if s.repeat for i in range(s.start, s.start + s.frames)}


# ---------------------------------------------------------------- reading values

class _Reader:
    def __init__(self):
        self.problems: list[Problem] = []

    def error(self, where: str, msg: str) -> None:
        self.problems.append(Problem("error", where, msg))

    def warn(self, where: str, msg: str) -> None:
        self.problems.append(Problem("warning", where, msg))

    def keys(self, obj, allowed: set[str], where: str) -> bool:
        if not isinstance(obj, dict):
            self.error(where, "must be an object")
            return False
        for k in obj:
            if k not in allowed and not k.startswith("_"):
                self.error(where, f"unknown key {k!r}; allowed: {', '.join(sorted(allowed))}")
        return True

    def number(self, obj: dict, key: str, where: str, default=None, lo=None, hi=None):
        v = obj.get(key, default)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.error(f"{where}.{key}", f"must be a number, got {v!r}")
            return default
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.error(f"{where}.{key}", f"{v} is outside [{lo}, {hi}]")
            return default
        return v

    def frame(self, obj: dict, key: str, where: str, fps: Fraction, default=None) -> int | None:
        """Seconds as a number, or an exact frame as "f123"."""
        if key not in obj:
            return default
        v = obj[key]
        if isinstance(v, str) and re.fullmatch(r"f\d+", v):
            return int(v[1:])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or not math.isfinite(v):
            self.error(f"{where}.{key}", f"must be seconds or 'f<frame>', got {v!r}")
            return default if isinstance(default, int) else 0
        return round(Fraction(v).limit_denominator(100000) * fps)


def _fps(value) -> Fraction:
    if isinstance(value, str):
        return Fraction(value)
    return Fraction(value).limit_denominator(1001)


def _probe_source(key: str, path: Path, r: _Reader, where: str) -> Source | None:
    if not path.is_file():
        r.error(where, f"file not found: {path}")
        return None
    is_image = path.suffix.lower() in IMAGE_SUFFIXES
    try:
        info = analyze.probe(path)
    except (subprocess.CalledProcessError, ValueError, KeyError, ZeroDivisionError) as e:
        r.error(where, f"cannot probe {path.name}: {e}")
        return None
    if is_image:
        return Source(key, path, True, info["width"], info["height"], Fraction(1), 0, [])
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_packets,r_frame_rate,avg_frame_rate,color_transfer", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    avg, nominal = Fraction(st["avg_frame_rate"]), Fraction(st["r_frame_rate"])
    if avg and abs(float(avg - nominal)) / float(avg) > 0.005:
        r.error(where, f"{path.name} looks variable-rate (avg {float(avg):.3f}, nominal {float(nominal):.3f} fps), so "
                       "frame numbers would be wrong; conform it first: ffmpeg -i in -fps_mode cfr -r <fps> out")
    if st.get("color_transfer") in HDR_TRANSFERS:
        r.error(where, f"{path.name} is HDR ({st['color_transfer']}); tone-map it to SDR before using it")
    return Source(key, path, False, info["width"], info["height"], avg or nominal, int(st["nb_read_packets"]), [])


# ---------------------------------------------------------------- validation

def load(path: str | Path) -> tuple[dict, Path]:
    p = Path(path)
    try:
        return json.loads(p.read_text()), p.resolve().parent
    except json.JSONDecodeError as e:
        raise EDLError([Problem("error", str(p), f"invalid JSON: {e}")]) from e


def validate(edl: dict, base: Path, strict: bool = True) -> Plan:
    """Build the render plan. Reports every problem together; raises EDLError unless strict is off."""
    r = _Reader()
    r.keys(edl, {"version", "canvas", "sources", "shots", "flashes", "text", "grade", "audio", "matte"}, "edl")
    if edl.get("version") != 1:
        r.error("edl.version", "must be 1")
    canvas = edl.get("canvas") if isinstance(edl.get("canvas"), dict) else {}
    r.keys(canvas, {"w", "h", "fps", "frames", "duration"}, "canvas")
    w, h = canvas.get("w", 0), canvas.get("h", 0)
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0 and w % 2 == 0 and h % 2 == 0):
        r.error("canvas", f"w and h must be positive even integers, got {w}x{h}")
        w, h = w if isinstance(w, int) and w > 0 else 2, h if isinstance(h, int) and h > 0 else 2
    try:
        fps = _fps(canvas.get("fps", 24))
        if fps <= 0:
            raise ValueError
    except (ValueError, ZeroDivisionError, TypeError):
        r.error("canvas.fps", f"invalid frame rate {canvas.get('fps')!r}")
        fps = Fraction(24)
    frames = canvas.get("frames")
    if "duration" in canvas:
        from_d = r.frame(canvas, "duration", "canvas", fps)
        if frames is not None and frames != from_d:
            r.error("canvas", f"frames {frames} disagrees with duration {canvas['duration']} ({from_d} frames)")
        frames = from_d
    if not isinstance(frames, int) or frames <= 0:
        r.error("canvas", "needs a positive frames or duration")
        frames = 0

    sources = _read_sources(edl.get("sources"), base, r)
    shots = _read_shots(edl.get("shots"), sources, w, h, fps, r)
    _check_timeline(shots, frames, fps, r)
    _check_placement(shots, sources, fps, r)
    flashes = _read_flashes(edl, shots, frames, fps, r)
    texts = _read_texts(edl.get("text", []), base, w, h, frames, fps, r)
    grade = _read_grade(edl.get("grade"), base, "grade", r)
    audio = _read_audio(edl.get("audio"), base, frames, fps, r)
    if not any(p.level == "error" for p in r.problems):
        _warn_framing(shots, sources, w, h, r)
    matte = _read_matte(edl.get("matte"), texts, base, frames, r)
    plan = Plan(base, w, h, fps, frames, sources, shots, flashes, texts, grade, audio, r.problems, matte)
    if strict and any(p.level == "error" for p in r.problems):
        raise EDLError(r.problems)
    return plan


def _read_matte(raw, texts: list[fx.Text], base: Path, frames: int, r: _Reader) -> Path | None:
    if not any(t.behind for t in texts):
        return (base / raw).resolve() if raw else None
    if not raw:
        r.error("edl.matte", 'text with "behind" needs a matte file: set "matte", then run `edl matte`')
        return None
    path = (base / raw).resolve()
    if not path.is_file():
        r.error("edl.matte", f"{raw} does not exist yet: run `edl matte`")
        return None
    got = _count_frames(path)
    if got != frames:
        r.error("edl.matte", f"{raw} has {got} frames, the edit has {frames}: run `edl matte` again")
    return path


def _read_sources(raw, base: Path, r: _Reader) -> dict[str, Source]:
    out: dict[str, Source] = {}
    if not isinstance(raw, dict) or not raw:
        r.error("sources", "must be a non-empty object")
        return out
    for key, spec in raw.items():
        where = f"sources.{key}"
        if isinstance(spec, str):
            spec = {"path": spec}
        if not r.keys(spec, {"path", "bad_ranges"}, where) or "path" not in spec:
            r.error(where, "needs a path")
            continue
        src = _probe_source(key, (base / spec["path"]).resolve(), r, where)
        if src is None:
            continue
        for i, pair in enumerate(spec.get("bad_ranges", [])):
            if not (isinstance(pair, list) and len(pair) == 2):
                r.error(f"{where}.bad_ranges[{i}]", "must be [start, end]")
                continue
            a = r.frame({"v": pair[0]}, "v", f"{where}.bad_ranges[{i}]", src.fps or Fraction(1))
            b = r.frame({"v": pair[1]}, "v", f"{where}.bad_ranges[{i}]", src.fps or Fraction(1))
            src.bad_ranges.append((a, b))
        out[key] = src
    return out


def _read_camera(raw, shot_id: str, frames: int, fps: Fraction, seed: int, r: _Reader, where: str) -> fx.Camera:
    if raw is None:
        return fx.Camera(seed=seed)
    if not r.keys(raw, {"push", "shake", "punch"}, where):
        return fx.Camera(seed=seed)
    kw = {}
    push = raw.get("push")
    if push is not None and r.keys(push, {"from", "to", "x", "y", "to_x", "to_y", "ease"}, f"{where}.push"):
        p = f"{where}.push"
        z0 = r.number(push, "from", p, 1.0, 1.0, 4.0)
        z1 = r.number(push, "to", p, z0, 1.0, 4.0)
        x0, y0 = r.number(push, "x", p, 0.5, 0, 1), r.number(push, "y", p, 0.5, 0, 1)
        x1, y1 = r.number(push, "to_x", p, x0, 0, 1), r.number(push, "to_y", p, y0, 0, 1)
        ease = push.get("ease", "linear")
        if ease not in fx.EASES:
            r.error(f"{p}.ease", f"must be one of {', '.join(fx.EASES)}")
            ease = "linear"
        kw.update(z0=z0, z1=z1, x0=x0, y0=y0, x1=x1, y1=y1, ease=ease)
    bursts = []
    for i, b in enumerate(raw.get("shake", [])):
        p = f"{where}.shake[{i}]"
        if not r.keys(b, {"at", "frames", "amp", "hz", "rot", "decay"}, p):
            continue
        at = r.frame(b, "at", p, fps, 0)
        n = r.frame(b, "frames", p, fps) if isinstance(b.get("frames"), str) else b.get("frames", frames - at)
        if not isinstance(n, int) or n < 1 or at + n > frames:
            r.error(p, f"burst at {at} for {n} frames does not fit the {frames}-frame shot")
            continue
        amp = r.number(b, "amp", p, 0.012, 0.0005, 0.08)
        bursts.append(fx.Burst(at, n, amp, r.number(b, "hz", p, 9.0, 0.5, 24.0), r.number(b, "rot", p, 0.0, 0, 8.0),
                               bool(b.get("decay", True))))
    punches = []
    for i, b in enumerate(raw.get("punch", [])):
        p = f"{where}.punch[{i}]"
        if not r.keys(b, {"at", "frames", "strength"}, p):
            continue
        at = r.frame(b, "at", p, fps, 0)
        if at >= frames:
            r.error(p, f"at frame {at} is outside the {frames}-frame shot")
            continue
        punches.append(fx.Punch(at, int(r.number(b, "frames", p, 6, 1, 96)), r.number(b, "strength", p, 0.08, 0.005, 0.6)))
    return fx.Camera(bursts=tuple(bursts), punches=tuple(punches), seed=seed, **kw)


def _read_shots(raw, sources: dict[str, Source], w: int, h: int, fps: Fraction, r: _Reader) -> list[Shot]:
    out: list[Shot] = []
    if not isinstance(raw, list) or not raw:
        r.error("shots", "must be a non-empty list")
        return out
    allowed = {"id", "source", "in", "out", "frames", "speed", "repeat", "flip", "focus", "slowmo", "camera", "grade",
               "transition_in", "flashes"}
    seen = set()
    start = 0
    for i, spec in enumerate(raw):
        where = f"shots[{i}]"
        if not r.keys(spec, allowed, where):
            continue
        sid = spec.get("id", f"s{i}")
        if sid in seen:
            r.error(where, f"duplicate id {sid!r}")
        seen.add(sid)
        where = f"shots[{i}:{sid}]"
        src = sources.get(spec.get("source"))
        if src is None:
            r.error(f"{where}.source", f"unknown source {spec.get('source')!r}")
            continue
        speed = r.number(spec, "speed", where, None, 0.05, 16)
        n = spec.get("frames")
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
            r.error(f"{where}.frames", f"must be a positive integer, got {n!r}")
            continue
        if src.is_image:
            if n is None:
                r.error(where, "a still needs frames")
                continue
            src_in, span, sp = 0, 0, 1.0
        else:
            src_in = r.frame(spec, "in", where, src.fps, 0)
            src_out = r.frame(spec, "out", where, src.fps) if "out" in spec else None
            if src_out is not None and src_out <= src_in:
                r.error(where, f"out (frame {src_out}) must come after in (frame {src_in})")
                continue
            if src_out is None and n is None:
                r.error(where, "needs out or frames")
                continue
            ratio = src.fps / fps
            if src_out is not None and n is not None:
                sp = float(Fraction(src_out - src_in, n) / ratio)
                if speed is not None and abs(sp - speed) > 0.02:
                    r.error(where, f"in/out/frames imply speed {sp:.3f} but speed is {speed}")
            elif src_out is not None:
                sp = speed or 1.0
                n = round(Fraction(src_out - src_in) / ratio / Fraction(sp).limit_denominator(10000))
                if n < 1:
                    r.error(where, "out is so close to in that no output frame results")
                    continue
            else:
                sp = speed or 1.0
            span = math.ceil(n * sp * float(ratio) - 1e-6)
        focus = spec.get("focus", [0.5, 0.5])
        if not (isinstance(focus, list) and len(focus) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in focus)):
            r.error(f"{where}.focus", f"must be [x, y] in 0..1, got {focus!r}")
            focus = [0.5, 0.5]
        slowmo = spec.get("slowmo", "blend" if 0.3 <= sp < 1 else "nearest")
        if slowmo not in ("blend", "nearest"):
            r.error(f"{where}.slowmo", "must be blend or nearest")
            slowmo = "nearest"
        seed = int(hashlib.sha1(str(sid).encode()).hexdigest()[:8], 16)
        camera = _read_camera(spec.get("camera"), sid, n, fps, seed, r, f"{where}.camera")
        grade = _read_grade(spec.get("grade"), Path("."), f"{where}.grade", r, check_lut=False)
        out.append(Shot(sid, src.key, start, n, src_in, span, sp, bool(spec.get("repeat", False)),
                        bool(spec.get("flip", False)), tuple(focus), slowmo, camera, grade))
        start += n
    _apply_transitions(raw, out, r)
    return out


def _apply_transitions(raw: list, shots: list[Shot], r: _Reader) -> None:
    by_id = {s.id: k for k, s in enumerate(shots)}
    for i, spec in enumerate(raw):
        if not isinstance(spec, dict) or "transition_in" not in spec:
            continue
        sid = spec.get("id", f"s{i}")
        where = f"shots[{i}:{sid}].transition_in"
        tr = spec["transition_in"]
        if not r.keys(tr, {"type", "frames", "dir", "strength"}, where) or sid not in by_id:
            continue
        k = by_id[sid]
        if k == 0:
            r.error(where, "the first shot has nothing to transition from")
            continue
        n = r.number(tr, "frames", where, 6, 2, 24)
        n = int(n)
        out_part, in_part = n // 2, n - n // 2
        cur, prev = shots[k], shots[k - 1]
        if in_part > cur.frames or out_part > prev.frames:
            r.error(where, f"{n} frames do not fit shots of {prev.frames} and {cur.frames} frames")
            continue
        kind = tr.get("type")
        if kind == "whip":
            d = tr.get("dir", "left")
            if d not in fx.WHIP_DIRS:
                r.error(f"{where}.dir", f"must be one of {', '.join(fx.WHIP_DIRS)}")
                continue
            axis = lambda s: s in ("left", "right")
            if prev.whip_in and axis(prev.whip_in.direction) != axis(d):
                r.error(where, f"shot {prev.id} would whip on both axes; keep one axis per shot")
                continue
            cur.whip_in, prev.whip_out = fx.Whip(in_part, d), fx.Whip(out_part, d)
        elif kind == "glitch":
            strength = r.number(tr, "strength", where, 1.0, 0.1, 3.0)
            cur.glitch_in = fx.Glitch(in_part, strength, cur.camera.seed)
            prev.glitch_out = fx.Glitch(out_part, strength, cur.camera.seed + 1)
        else:
            r.error(f"{where}.type", f"must be whip or glitch, got {kind!r}")


def _check_timeline(shots: list[Shot], frames: int, fps: Fraction, r: _Reader) -> None:
    if not shots or not frames:
        return
    total = sum(s.frames for s in shots)
    if total != frames:
        diff = total - frames
        r.error("shots", f"timeline is {total} frames but the target is {frames}: "
                         f"{'over' if diff > 0 else 'short'} by {abs(diff)} ({abs(diff) / float(fps):.3f} s)")


def _check_placement(shots: list[Shot], sources: dict[str, Source], fps: Fraction, r: _Reader) -> None:
    placed: dict[str, list[Shot]] = {}
    for s in shots:
        src = sources[s.source]
        if src.is_image:
            continue
        last = s.src_in + s.src_span
        if last > src.frames:
            r.error(f"shot {s.id}", f"needs source frames {s.src_in}..{last - 1} but {src.path.name} has only "
                                    f"{src.frames} frames (ends at {src.frames / float(src.fps):.3f} s). Generated clips "
                                    "often return fewer frames than asked: read the real count")
        for a, b in src.bad_ranges:
            if s.src_in < b and a < last:
                r.error(f"shot {s.id}", f"overlaps the rejected range {a}..{b} of {src.path.name}")
        for other in placed.setdefault(s.source, []):
            if s.src_in < other.src_in + other.src_span and other.src_in < last and not (s.repeat or other.repeat):
                a, b = max(s.src_in, other.src_in), min(last, other.src_in + other.src_span)
                r.error(f"shot {s.id}", f"places source frames {a}..{b - 1} of {src.path.name} already used by shot "
                                        f"{other.id}; pick another in-point or mark repeat if the reference repeats here")
        placed[s.source].append(s)


def _read_flashes(edl: dict, shots: list[Shot], frames: int, fps: Fraction, r: _Reader) -> list[fx.Flash]:
    out = []

    def one(spec, where, offset, limit):
        if not r.keys(spec, {"at", "frames", "color", "strength"}, where):
            return
        at = r.frame(spec, "at", where, fps, 0) + offset
        n = int(r.number(spec, "frames", where, 2, 1, 24))
        color = spec.get("color", "white")
        if not isinstance(color, str) or not re.fullmatch(r"[a-zA-Z]+|#[0-9a-fA-F]{6}", color):
            r.error(f"{where}.color", f"must be a colour name or #rrggbb, got {color!r}")
            return
        if at + n > limit:
            r.error(where, f"flash at frame {at} for {n} frames runs past frame {limit}")
            return
        out.append(fx.Flash(at, n, color, r.number(spec, "strength", where, 1.0, 0.05, 1.0)))

    for i, spec in enumerate(edl.get("flashes", [])):
        one(spec, f"flashes[{i}]", 0, frames)
    raw_shots = edl.get("shots") if isinstance(edl.get("shots"), list) else []
    by_id = {s.id: s for s in shots}
    for i, spec in enumerate(raw_shots):
        s = by_id.get(spec.get("id", f"s{i}")) if isinstance(spec, dict) else None
        if s is None:
            continue
        for j, fl in enumerate(spec.get("flashes", [])):
            one(fl, f"shots[{i}:{s.id}].flashes[{j}]", s.start, frames)
    out.sort(key=lambda f: f.at)
    for a, b in zip(out, out[1:]):
        if a.at + a.frames > b.at:
            r.error("flashes", f"flashes at frames {a.at} and {b.at} overlap")
    return out


def _read_grade(raw, base: Path, where: str, r: _Reader, check_lut: bool = True) -> dict | None:
    if raw is None:
        return None
    if not r.keys(raw, {"contrast", "brightness", "saturation", "gamma", "temperature", "lut", "sharpen", "vignette", "grain"}, where):
        return None
    limits = {"contrast": (0.3, 2.5), "brightness": (-0.5, 0.5), "saturation": (0, 3), "gamma": (0.3, 3),
              "temperature": (1000, 40000), "sharpen": (0, 3), "vignette": (0.05, 1.57), "grain": (0, 40)}
    for k, (lo, hi) in limits.items():
        if k in raw:
            r.number(raw, k, where, None, lo, hi)
    if check_lut and "lut" in raw and not (base / raw["lut"]).is_file():
        r.error(f"{where}.lut", f"file not found: {raw['lut']}")
    return raw


def _read_texts(raw, base: Path, w: int, h: int, frames: int, fps: Fraction, r: _Reader) -> list[fx.Text]:
    out: list[fx.Text] = []
    fonts: dict[Path, fx.Font] = {}
    allowed = {"text", "font", "start", "end", "x", "y", "size", "color", "border", "border_color", "shadow", "box", "anim", "fade_out", "behind"}
    for i, spec in enumerate(raw if isinstance(raw, list) else []):
        where = f"text[{i}]"
        if not r.keys(spec, allowed, where):
            continue
        words = spec.get("text")
        if not isinstance(words, str) or not words.strip():
            r.error(f"{where}.text", "must be non-empty")
            continue
        if "\n" in words:
            r.error(f"{where}.text", "one line per event; use a second event for a second line")
            continue
        if "font" not in spec:
            r.error(f"{where}.font", "a font file is required: no system fallback is ever used")
            continue
        path = (base / spec["font"]).resolve()
        if not path.is_file():
            r.error(f"{where}.font", f"font not found: {spec['font']}")
            continue
        try:
            font = fonts.setdefault(path, fx.Font(path))
        except (fx.FontError, struct.error) as e:
            r.error(f"{where}.font", f"unreadable font: {e}")
            continue
        missing = font.missing(words)
        if missing:
            r.error(f"{where}.text", f"{path.name} has no glyph for {' '.join(missing)}")
            continue
        start, end = r.frame(spec, "start", where, fps, 0), r.frame(spec, "end", where, fps, frames)
        if not 0 <= start < end <= frames:
            r.error(where, f"frames {start}..{end} are outside the {frames}-frame edit")
            continue
        anim = spec.get("anim", "none")
        if anim not in fx.TEXT_ANIMS:
            r.error(f"{where}.anim", f"must be one of {', '.join(fx.TEXT_ANIMS)}")
            continue
        size = r.number(spec, "size", where, 0.08, 0.005, 0.8)
        x, y = r.number(spec, "x", where, 0.5, 0, 1), r.number(spec, "y", where, 0.5, 0, 1)
        em = font.width(words)
        width_frac = em * size * h / w
        left, right = x - width_frac / 2, x + width_frac / 2
        if left < 0 or right > 1:
            r.error(where, f"{words!r} is {width_frac:.0%} of the frame width at x={x}, so it would be clipped; reduce size")
        elif left < 0.03 or right > 0.97:
            r.warn(where, f"{words!r} sits within 3% of the frame edge")
        if y - size / 2 < 0.03 or y + size / 2 > 0.97:
            r.warn(where, f"{words!r} sits within 3% of the top or bottom edge")
        for key in ("border", "shadow"):
            r.number(spec, key, where, 0.0, 0, 0.3)
        out.append(fx.Text(words, str(path), start, end, x, y, size, spec.get("color", "white"), spec.get("border", 0.0),
                           spec.get("border_color", "black"), spec.get("shadow", 0.0), spec.get("box"), anim,
                           int(r.number(spec, "fade_out", where, 0, 0, 48)), i, em, bool(spec.get("behind", False))))
    return out


def _audio_duration(path: Path) -> float | None:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if p.returncode:
        return None
    info = json.loads(p.stdout)
    streams = info.get("streams", [])
    if not streams:
        return None
    return float(streams[0].get("duration") or info.get("format", {}).get("duration") or 0)


def _read_audio(raw, base: Path, frames: int, fps: Fraction, r: _Reader) -> AudioPlan:
    plan = AudioPlan()
    if raw is None:
        r.error("audio", "required: give a recorded bed or hits, or none_reason saying why the edit is silent")
        return plan
    if not r.keys(raw, {"assets", "bed", "hits", "none_reason"}, "audio"):
        return plan
    plan.none_reason = raw.get("none_reason")
    if plan.none_reason is not None and (raw.get("bed") or raw.get("hits")):
        r.error("audio", "none_reason cannot sit beside a bed or hits")
    if plan.none_reason is None and not (raw.get("bed") or raw.get("hits")):
        r.error("audio", "give a recorded bed or hits, or none_reason saying why the edit is silent")
    assets: dict[str, Path] = {}
    for key, spec in (raw.get("assets") or {}).items():
        where = f"audio.assets.{key}"
        if not r.keys(spec, {"path", "provenance"}, where):
            continue
        if not (isinstance(spec.get("provenance"), str) and spec["provenance"].strip()):
            r.error(where, "needs provenance: where the recording came from and its licence")
            continue
        path = (base / str(spec.get("path"))).resolve()
        if not path.is_file():
            r.error(where, f"file not found: {spec.get('path')}")
        elif _audio_duration(path) is None:
            r.error(where, f"{path.name} has no audio stream")
        else:
            assets[key] = path
            plan.provenance[key] = spec["provenance"]
    bed = raw.get("bed")
    if bed is not None and r.keys(bed, {"asset", "in", "gain_db", "fade_in", "fade_out"}, "audio.bed"):
        path = assets.get(bed.get("asset"))
        if path is None:
            r.error("audio.bed.asset", f"unknown asset {bed.get('asset')!r}")
        else:
            plan.bed, plan.bed_in = path, r.number(bed, "in", "audio.bed", 0.0, 0)
            plan.bed_gain_db = r.number(bed, "gain_db", "audio.bed", 0.0, -60, 12)
            plan.fade_in = r.number(bed, "fade_in", "audio.bed", 0.0, 0, 30)
            plan.fade_out = r.number(bed, "fade_out", "audio.bed", 0.0, 0, 30)
            need = plan.bed_in + float(frames / fps)
            have = _audio_duration(path)
            if have is not None and have + 1e-3 < need:
                r.error("audio.bed", f"{path.name} is {have:.3f} s but the edit needs it to run to {need:.3f} s")
    for i, hit in enumerate(raw.get("hits") or []):
        where = f"audio.hits[{i}]"
        if not r.keys(hit, {"asset", "at", "gain_db"}, where):
            continue
        path = assets.get(hit.get("asset"))
        if path is None:
            r.error(f"{where}.asset", f"unknown asset {hit.get('asset')!r}")
            continue
        at = r.frame(hit, "at", where, fps, 0)
        if at >= frames:
            r.error(where, f"hit at frame {at} is past the end ({frames})")
            continue
        plan.hits.append(HitCue(path, at, r.number(hit, "gain_db", where, -6.0, -60, 12)))
    return plan


def _warn_framing(shots: list[Shot], sources: dict[str, Source], w: int, h: int, r: _Reader) -> None:
    target = w / h
    seen = set()
    for s in shots:
        src = sources[s.source]
        if s.focus == (0.5, 0.5) and s.source not in seen and abs(src.w / src.h - target) > 0.02 * target:
            seen.add(s.source)
            r.warn(f"shot {s.id}", f"{src.path.name} is {src.w}x{src.h} on a {w}x{h} canvas and is centre-cropped; "
                                   "set focus after looking at what the crop removes")
        scale = max(w / src.w, h / src.h)
        if scale > 1.5:
            r.warn(f"shot {s.id}", f"{src.path.name} is upscaled {scale:.1f}x, so it will look soft next to sharper shots")


# ---------------------------------------------------------------- ffmpeg

def _run(args: list[str], what: str) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RenderError(f"{what} failed: {p.stderr.strip()[-1500:]}")
    return p


def _count_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return int(out.strip().split(",")[0])


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{path}:{st.st_size}:{st.st_mtime_ns}"


def _hash(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Quality:
    scale: float
    preset: str
    crf_working: int
    crf_final: int
    interpolation: str

    @staticmethod
    def preview(scale: float = 0.5) -> "Quality":
        return Quality(scale, "veryfast", 20, 22, "linear")

    @staticmethod
    def final(crf: int = 17) -> "Quality":
        return Quality(1.0, "medium", 14, crf, "cubic")


COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def _encode_args(crf: int, preset: str) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-bf", "0", "-pix_fmt", "yuv420p", *COLOR_TAGS]


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def shot_graph(plan: Plan, shot: Shot, w: int, h: int, q: Quality) -> str:
    """The full per-shot filter graph, from decoded source to a canvas-sized plate."""
    fps = plan.fps
    src = plan.sources[shot.source]
    fx_expr = f"{fps.numerator}/{fps.denominator}"
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos:out_color_matrix=bt709:out_range=tv,"
           f"crop={w}:{h}:(iw-{w})*{fx._num(shot.focus[0])}:(ih-{h})*{fx._num(shot.focus[1])},setsar=1,format=yuv420p")
    chain = []
    if src.is_image:
        chain.append(fit)
    else:
        chain.append("setpts=PTS-STARTPTS")
        chain.append(fit)
        ratio = fps / src.fps
        if shot.speed == 1.0 and ratio == 1:
            chain.append(f"fps={fx_expr}")
        elif shot.slowmo == "blend" and shot.speed < 1:
            chain.append(f"setpts=PTS/{fx._num(shot.speed)},framerate=fps={fx_expr}:interp_start=0:interp_end=255")
        else:
            chain.append(f"setpts=PTS/{fx._num(shot.speed)},fps={fx_expr}")
    if shot.flip:
        chain.append("hflip")
    cam = fx.camera_filter(shot.camera, shot.frames, float(fps), w, h, q.interpolation)
    if cam:
        chain.append(cam)
    chain += fx.grade_filters(shot.grade, plan.base)
    parts = [f"[0:v]{','.join(chain)}[plate]"]
    cur = "plate"
    if shot.whip_in or shot.whip_out:
        parts.append(fx.whip_graph(cur, "whip", shot.whip_in, shot.whip_out, shot.frames, w, h))
        cur = "whip"
    if shot.glitch_in:
        frames = list(range(shot.glitch_in.frames))
        parts.append(fx.glitch_graph(cur, "gi", frames, shot.glitch_in, w, h, float(fps)))
        cur = "gi"
    if shot.glitch_out:
        frames = list(range(shot.frames - shot.glitch_out.frames, shot.frames))
        parts.append(fx.glitch_graph(cur, "go", frames, shot.glitch_out, w, h, float(fps)))
        cur = "go"
    parts.append(f"[{cur}]null[out]")
    return ";".join(parts)


def _render_segment(plan: Plan, shot: Shot, w: int, h: int, q: Quality, work: Path) -> Path:
    src = plan.sources[shot.source]
    graph_text = shot_graph(plan, shot, w, h, q)
    # The graph text is in the key so a change to any effect's code invalidates the cached pixels.
    key = _hash(_fingerprint(src.path), graph_text, shot.src_in, shot.frames, str(plan.fps), q.preset, q.crf_working)
    out = work / f"seg_{key}.mp4"
    if out.exists():
        return out
    graph = work / f"seg_{key}.graph"
    graph.write_text(graph_text)
    if src.is_image:
        rate = f"{plan.fps.numerator}/{plan.fps.denominator}"
        seconds = f"{float(shot.frames / plan.fps) + 1:.3f}"
        inp = ["-loop", "1", "-framerate", rate, "-t", seconds, "-i", str(src.path)]
    else:
        # Half a frame early: accurate seek then keeps exactly the in-point frame despite float timestamps.
        seek = max(0.0, (shot.src_in - 0.5) / float(src.fps)) if shot.src_in else 0.0
        inp = (["-ss", f"{seek:.6f}"] if seek else []) + ["-i", str(src.path)]
    tmp = out.with_suffix(".part.mp4")
    _run(["-threads", THREADS, *inp, "-/filter_complex", str(graph), "-map", "[out]", "-an", "-frames:v", str(shot.frames),
          "-fps_mode", "cfr", "-r", f"{plan.fps.numerator}/{plan.fps.denominator}",
          *_encode_args(q.crf_working, q.preset), str(tmp)], f"shot {shot.id}")
    got = _count_frames(tmp)
    if got != shot.frames:
        raise RenderError(f"shot {shot.id} produced {got} frames, wanted {shot.frames}")
    tmp.rename(out)
    return out


def _concat(segments: list[Path], work: Path) -> Path:
    key = _hash([p.name for p in segments])
    out = work / f"concat_{key}.mp4"
    if out.exists():
        return out
    lst = work / f"concat_{key}.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], "concat")
    return out


def _finish(plan: Plan, concat: Path, q: Quality, work: Path) -> Path:
    """Grade, flashes, frame-exact. Kept apart from text so a wording fix never re-renders picture."""
    filters = fx.grade_filters(plan.grade, plan.base) + fx.flash_filters(plan.flashes)
    key = _hash(concat.name, filters, q.preset, q.crf_working)
    out = work / f"picture_{key}.mp4"
    if out.exists():
        return out
    args = ["-threads", THREADS, "-i", str(concat)]
    if filters:
        graph = work / f"picture_{key}.graph"
        graph.write_text(",".join(filters))
        args += ["-/filter:v", str(graph)]
    _run([*args, "-an", "-frames:v", str(plan.frames), "-fps_mode", "passthrough", *_encode_args(q.crf_working, q.preset), str(out)], "picture pass")
    return out


def _text_pass(plan: Plan, picture: Path, w: int, h: int, q: Quality, work: Path) -> Path:
    text_dir = work / f"text_{_hash(picture.name, [asdict(t) for t in plan.texts])}"
    filters, behind = [], []
    for i, t in enumerate(plan.texts):
        (behind if t.behind else filters).extend(fx.text_filters(t, text_dir, i, h))
    key = _hash(picture.name, behind, filters, q.preset, q.crf_final, _fingerprint(plan.matte) if behind else None)
    out = work / f"titled_{key}.mp4"
    if out.exists():
        return out
    args = ["-threads", THREADS, "-i", str(picture)]
    if behind:
        graph = work / f"titled_{key}.graph"
        graph.write_text(_behind_graph(behind, filters, w, h))
        args += ["-i", str(plan.matte), "-/filter_complex", str(graph), "-map", "[out]"]
    elif filters:
        graph = work / f"titled_{key}.graph"
        graph.write_text(",".join(filters))
        args += ["-/filter:v", str(graph)]
    _run([*args, "-an", "-frames:v", str(plan.frames), "-fps_mode", "passthrough", *_encode_args(q.crf_final, q.preset), str(out)], "text pass")
    return out


def _behind_graph(behind: list[str], front: list[str], w: int, h: int) -> str:
    """Draw type on the picture, then lay the picture's own subject pixels back over it through the matte."""
    return (f"[0:v]split[bg][fg];[bg]{','.join(behind)}[typed];"
            f"[1:v]scale={w}:{h},format=gray[m];[fg]format=yuva420p[fa];[fa][m]alphamerge[subject];"
            f"[typed][subject]overlay=format=yuv420[over];[over]{','.join([*front, 'format=yuv420p'])}[out]")


def _picture_key(edl: dict, plan: Plan) -> str:
    return _hash({k: edl.get(k) for k in ("canvas", "sources", "shots", "flashes", "grade")},
                 [_fingerprint(s.path) for s in plan.sources.values()])


def make_matte(edl_path: str | Path, crf: int = 17) -> dict:
    """Mask the finished picture on the frames that have type behind the subject, and write the EDL's matte file."""
    edl, base = load(edl_path)
    if not edl.get("matte"):
        raise RenderError('this EDL has no "matte" path')
    spec = {**edl, "text": [{k: v for k, v in t.items() if k != "behind"} for t in edl.get("text", [])]}
    spec.pop("matte")
    plan = validate(spec, base)
    wanted = sorted({n for t, raw in zip(plan.texts, edl.get("text", [])) if raw.get("behind") for n in range(t.start, t.end)})
    if not wanted:
        raise RenderError('no text has "behind": there is nothing to mask')
    dest = (base / edl["matte"]).resolve()
    work = dest.parent / ".edl-cache"
    work.mkdir(parents=True, exist_ok=True)
    q = Quality.final(crf)
    picture = _finish(plan, _concat([_render_segment(plan, s, plan.w, plan.h, q, work) for s in plan.shots], work), q, work)
    tool = work / f"matte_{_hash(_fingerprint(MATTE_SOURCE))}"
    if not tool.exists():
        _build_tool(tool)
    frames_in, masks = work / "matte_in", work / "matte_out"
    for d in (frames_in, masks):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    select = "+".join(f"between(n,{a},{b})" for a, b in _ranges(wanted))
    _run(["-i", str(picture), "-vf", f"select='{select}'", "-fps_mode", "passthrough", str(frames_in / "%06d.png")], "matte frames")
    done = subprocess.run([str(tool), str(frames_in), str(masks)], capture_output=True, text=True)
    if done.returncode:
        raise RenderError(f"matte tool failed: {done.stderr[-300:]}")
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                            "-s", f"{plan.w}x{plan.h}", "-r", str(plan.fps), "-i", "-", "-c:v", "ffv1", str(dest)], stdin=subprocess.PIPE)
    blank = bytes(plan.w * plan.h)
    files = dict(zip(wanted, sorted(masks.glob("*.png"))))
    if len(files) != len(wanted):
        enc.kill()
        raise RenderError(f"{len(wanted)} frames were wanted, {len(files)} masks came back")
    for n in range(plan.frames):
        mask = cv2.imread(str(files[n]), cv2.IMREAD_GRAYSCALE) if n in files else None
        enc.stdin.write(blank if mask is None else cv2.resize(mask, (plan.w, plan.h), interpolation=cv2.INTER_LINEAR).tobytes())
    enc.stdin.close()
    if enc.wait():
        raise RenderError("could not encode the matte")
    shutil.rmtree(frames_in)
    shutil.rmtree(masks)
    dest.with_suffix(".json").write_text(json.dumps({"picture": _picture_key(spec, plan)}))
    return {"ok": True, "matte": str(dest), "masked_frames": len(wanted), "tool": json.loads(done.stdout)}


def _ranges(frames: list[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for n in frames:
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [(a, b) for a, b in out]


def _build_tool(dest: Path) -> None:
    done = subprocess.run(["clang", "-fobjc-arc", "-O2", "-framework", "Foundation", "-framework", "Vision", "-framework", "CoreImage",
                           "-framework", "CoreVideo", "-framework", "CoreGraphics", str(MATTE_SOURCE), "-o", str(dest)],
                          capture_output=True, text=True)
    if done.returncode:
        raise RenderError(f"cannot build the matte tool (it needs macOS 14 and clang): {done.stderr[-300:]}")


# ---------------------------------------------------------------- audio

def onset_seconds(path: Path) -> float:
    """Where the sound actually starts, so its attack rather than its leading silence lands on the cue."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    x = np.frombuffer(p.stdout, dtype="<f4")
    if not len(x):
        return 0.0
    peak = float(np.abs(x).max())
    if peak <= 0:
        return 0.0
    return float(np.argmax(np.abs(x) >= peak * 0.1)) / SR


def _measure_audio(path: Path) -> dict:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    summary = p.stderr[p.stderr.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    return {"lufs": float(lufs.group(1)) if lufs else None,
            "true_peak_db": float(peak.group(1)) if peak and peak.group(1) != "-inf" else None}


def _mix_audio(plan: Plan, work: Path) -> tuple[Path | None, dict]:
    a = plan.audio
    if a.bed is None and not a.hits:
        return None, {"none_reason": a.none_reason}
    dur = plan.duration
    samples = round(dur * SR)
    inputs, parts, labels = [], [], []
    fmt = f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}"
    if a.bed is not None:
        inputs += ["-i", str(a.bed)]
        chain = [f"atrim=start={a.bed_in:.6f}:duration={dur + 0.2:.6f}", "asetpts=PTS-STARTPTS", fmt, f"volume={a.bed_gain_db}dB"]
        if a.fade_in:
            chain.append(f"afade=t=in:d={a.fade_in}")
        if a.fade_out:
            chain.append(f"afade=t=out:st={max(0.0, dur - a.fade_out):.6f}:d={a.fade_out}")
        parts.append(f"[0:a]{','.join(chain)}[bed]")
        labels.append("[bed]")
    placed = []
    for hit in a.hits:
        idx = len(inputs) // 2
        inputs += ["-i", str(hit.path)]
        onset = onset_seconds(hit.path)
        cue = float(hit.frame / plan.fps)
        delay = cue - onset
        chain = [fmt, f"volume={hit.gain_db}dB"]
        if delay >= 0:
            chain.append(f"adelay={delay * 1000:.3f}:all=1")
        else:
            chain += [f"atrim=start={-delay:.6f}", "asetpts=PTS-STARTPTS"]
        parts.append(f"[{idx}:a]{','.join(chain)}[h{idx}]")
        labels.append(f"[h{idx}]")
        placed.append({"file": hit.path.name, "frame": hit.frame, "onset_s": round(onset, 4)})
    encode = ["-map_chapters", "-1", "-map_metadata", "-1", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR)]
    key = _hash([_fingerprint(Path(inputs[i + 1])) for i in range(0, len(inputs), 2)], parts, samples, encode)
    report: dict = {"hits": placed, "provenance": a.provenance}
    limit = 0.84
    for attempt in range(4):
        out = work / f"mix_{key}_{attempt}.m4a"
        mix = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0"
        graph = ";".join(parts) + (f";{mix}[m]" if len(labels) > 1 else f";{labels[0]}anull[m]")
        graph += f";[m]apad=whole_len={samples},atrim=end_sample={samples},alimiter=limit={limit}:attack=1:release=40:level=disabled[a]"
        if not out.exists():
            _run([*inputs, "-filter_complex", graph, "-map", "[a]", *encode, str(out)], "audio mix")
        m = _measure_audio(out)
        report.update(m, limiter=limit)
        if m["true_peak_db"] is None or m["true_peak_db"] <= TRUE_PEAK_LIMIT_DB:
            break
        limit = round(limit * 10 ** ((TRUE_PEAK_LIMIT_DB - m["true_peak_db"] - 0.2) / 20), 3)
    return out, report


# ---------------------------------------------------------------- render

def render(edl_path: str | Path, out: str | Path, preview: bool = False, scale: float = 0.5, crf: int = 17,
           work: str | Path | None = None) -> dict:
    """Render an EDL and check the result. Raises EDLError before any work if the EDL is invalid."""
    edl, base = load(edl_path)
    plan = validate(edl, base)
    if any(t.behind for t in plan.texts):
        sidecar = plan.matte.with_suffix(".json")
        made_for = json.loads(sidecar.read_text()).get("picture") if sidecar.exists() else None
        if made_for != _picture_key(edl, plan):
            raise RenderError(f"{plan.matte.name} was made for a different picture, so the mask would miss the subject: run `edl matte` again")
    q = Quality.preview(scale) if preview else Quality.final(crf)
    w, h = _even(plan.w * q.scale), _even(plan.h * q.scale)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work) if work else out.parent / ".edl-cache"
    work_dir.mkdir(parents=True, exist_ok=True)

    segments = [_render_segment(plan, s, w, h, q, work_dir) for s in plan.shots]
    concat = _concat(segments, work_dir)
    picture = _finish(plan, concat, q, work_dir)
    titled = _text_pass(plan, picture, w, h, q, work_dir)
    audio, audio_report = _mix_audio(plan, work_dir)
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    if audio:
        _run(["-i", str(titled), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(tmp)], "mux")
    else:
        _run(["-i", str(titled), "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(tmp)], "mux")
    tmp.replace(out)

    report = check(out, plan, width=w, height=h)
    report.update(mode="preview" if preview else "final", size=[w, h], audio=audio_report, stats=plan.stats(), uses=plan.uses(),
                  warnings=[str(p) for p in plan.problems if p.level == "warning"] + report["warnings"])
    report["text_checks"] = _verify_text(plan, picture, titled, w, h)
    report["errors"] += [f"text {c['text']!r} did not visibly change frame {c['frame']}" for c in report["text_checks"] if not c["drawn"]]
    report["ok"] = not report["errors"]
    out.with_suffix(".report.json").write_text(json.dumps(report, indent=1))
    return report


def _gray_frames(path: Path, w: int, h: int) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
                        "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode:
        raise RenderError(f"cannot decode {path}: {p.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w)


def _verify_text(plan: Plan, picture: Path, titled: Path, w: int, h: int) -> list[dict]:
    if not plan.texts:
        return []
    tw, th = 135, _even(135 * h / w)
    a, b = _gray_frames(picture, tw, th), _gray_frames(titled, tw, th)
    out = []
    for t in plan.texts:
        frame = t.end - max(1, (t.end - t.start) // 4)
        delta = np.abs(a[frame].astype(int) - b[frame].astype(int))
        out.append({"text": t.text, "frame": frame, "drawn": bool(delta.max() > 25), "max_delta": int(delta.max())})
    return out


# ---------------------------------------------------------------- post-render checks

def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def frame_faults(gray: np.ndarray, fps: float, flash_frames: set[int] = frozenset(), repeat_frames: set[int] = frozenset(),
                 shot_of: list[int] | None = None) -> dict:
    """Pixel-level faults over decoded grayscale frames; takes arrays so it can be tested without video."""
    n = len(gray)
    means = gray.reshape(n, -1).mean(axis=1)
    p98 = np.percentile(gray.reshape(n, -1), 98, axis=1)
    p2 = np.percentile(gray.reshape(n, -1), 2, axis=1)
    exempt = np.isin(np.arange(n), sorted(flash_frames))
    black = (p98 < 28) & (means < 10) & ~exempt
    white = (p2 > 235) & (means > 245) & ~exempt
    diffs = np.abs(np.diff(gray.astype(np.int16), axis=0)).reshape(n - 1, -1).mean(axis=1) if n > 1 else np.zeros(0)
    still = np.concatenate([[False], diffs < 0.12]) if n > 1 else np.zeros(n, bool)
    still[exempt] = False
    freeze_min = max(3, math.ceil(fps / 4))
    frozen = _runs(still, freeze_min)
    stutter = [r for r in _runs(diffs == 0, 2) if not any(a <= r[0] < b for a, b in frozen)]

    small = gray[:, ::2, ::2].astype(np.int16)
    repeats = []
    skip = np.isin(np.arange(n), sorted(set(flash_frames) | set(repeat_frames)))
    textured = gray.reshape(n, -1).std(axis=1) > 3
    usable = ~skip & textured
    min_gap = int(fps * 2)
    found: dict[int, set[int]] = {}
    for i in np.nonzero(usable)[0]:
        later = np.nonzero(usable[i + min_gap:])[0] + i + min_gap
        if shot_of is not None:
            later = later[[shot_of[j] != shot_of[i] for j in later]]
        if len(later):
            close = np.abs(small[later] - small[i]).mean(axis=(1, 2)) < 0.6
            if close.any():
                found[int(i)] = {int(j) for j in later[close]}
    for i, partners in sorted(found.items()):
        for j in sorted(partners):
            if j - 1 in found.get(i - 1, ()):
                continue
            k = 1
            while i + k in found and j + k in found[i + k]:
                k += 1
            if k >= 3:
                repeats.append({"frames_a": [i, i + k], "frames_b": [j, j + k]})
    h, w = gray.shape[1:]
    bars = []
    thick_y, thick_x = max(1, h // 33), max(1, w // 33)
    edges = {"top": gray[:, :thick_y], "bottom": gray[:, -thick_y:], "left": gray[:, :, :thick_x], "right": gray[:, :, -thick_x:]}
    centre = means > 30
    for name, region in edges.items():
        dark = region.reshape(n, -1).mean(axis=1) < 6
        if centre.sum() and (dark & centre).sum() / centre.sum() > 0.9:
            bars.append(name)
    return {"black": _runs(black, 1), "white": _runs(white, 1), "frozen": frozen, "stutter": stutter, "repeats": repeats, "bars": bars}


def check(path: str | Path, plan: Plan | None = None, width: int | None = None, height: int | None = None) -> dict:
    """Decode the file and report what is wrong with it. Uses the plan's targets and exemptions when given."""
    path = Path(path)
    errors, warnings, m = [], [], {}
    streams = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate,duration,pix_fmt:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in streams["streams"] if s["codec_type"] == "video")
    a = next((s for s in streams["streams"] if s["codec_type"] == "audio"), None)
    extra = [s["codec_type"] for s in streams["streams"] if s["codec_type"] not in ("video", "audio")]
    if extra:
        errors.append(f"unexpected {', '.join(extra)} stream in the file")
    sw, sh = int(v["width"]), int(v["height"])
    fps = Fraction(v["avg_frame_rate"])
    tw, th = 54, _even(54 * sh / sw)
    gray = _gray_frames(path, tw, th)
    n = len(gray)
    m.update(frames=n, video_duration=round(float(v.get("duration", 0)), 4), container_duration=round(float(streams["format"]["duration"]), 4),
             size=[sw, sh], fps=f"{fps.numerator}/{fps.denominator}", pix_fmt=v.get("pix_fmt"))
    flash, repeat, shot_of = frozenset(), frozenset(), None
    if plan is not None:
        flash, repeat = plan.flash_frames(), plan.repeat_frames()
        shot_of = [k for k, s in enumerate(plan.shots) for _ in range(s.frames)]
        if n != plan.frames:
            errors.append(f"decoded {n} frames, target {plan.frames}")
            shot_of = None
        if fps != plan.fps:
            errors.append(f"frame rate {float(fps):.3f}, target {float(plan.fps):.3f}")
        if abs(float(v.get("duration", 0)) - plan.duration) > 0.001:
            errors.append(f"video stream lasts {v.get('duration')} s, target {plan.duration:.4f} s")
        if width and (sw, sh) != (width, height):
            errors.append(f"size {sw}x{sh}, target {width}x{height}")
    faults = frame_faults(gray, float(fps), flash, repeat, shot_of)
    m["faults"] = {k: v2 for k, v2 in faults.items() if v2}
    for key, label in (("black", "black"), ("white", "white"), ("frozen", "frozen")):
        for s, e in faults[key]:
            errors.append(f"{label} frames {s}..{e - 1} ({s / float(fps):.2f} s)")
    for s, e in faults["stutter"]:
        warnings.append(f"identical consecutive frames {s}..{e} ({s / float(fps):.2f} s): judder unless intended")
    for rep in faults["repeats"]:
        errors.append(f"frames {rep['frames_a']} look identical to frames {rep['frames_b']}: footage placed twice")
    if faults["bars"]:
        errors.append(f"dark bars at {', '.join(faults['bars'])}")
    if a is None:
        if plan is None or plan.audio.none_reason is None:
            errors.append("no audio stream")
        else:
            warnings.append(f"no audio, as declared: {plan.audio.none_reason}")
    else:
        ad = float(a.get("duration", 0))
        m["audio_duration"] = round(ad, 4)
        m.update(_measure_audio(path))
        vd = float(v.get("duration", 0))
        if abs(ad - vd) > 0.05:
            errors.append(f"audio lasts {ad:.3f} s, video {vd:.3f} s")
        if m.get("true_peak_db") is not None and m["true_peak_db"] > TRUE_PEAK_LIMIT_DB:
            errors.append(f"true peak {m['true_peak_db']} dBTP is above {TRUE_PEAK_LIMIT_DB}")
        if m.get("lufs") is None or m["lufs"] < -40:
            errors.append(f"audio is effectively silent ({m.get('lufs')} LUFS)")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "measured": m}


# ---------------------------------------------------------------- CLI

def _problem_lines(problems: list[Problem]) -> list[str]:
    return [str(p) for p in problems]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="edl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate", help="report every problem and the authored rates")
    p.add_argument("edl")
    p = sub.add_parser("render", help="render, then run all checks; exits 1 if any check fails")
    p.add_argument("edl")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--preview", action="store_true", help="half size, fast encode")
    p.add_argument("--scale", type=float, default=0.5, help="preview size factor")
    p.add_argument("--crf", type=int, default=17, help="final encode quality")
    p = sub.add_parser("matte", help="mask the subject for text marked behind (macOS 14+)")
    p.add_argument("edl")
    p = sub.add_parser("check", help="check an existing render")
    p.add_argument("video")
    p.add_argument("--edl", help="check against this EDL's targets and exemptions")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            edl, base = load(a.edl)
            plan = validate(edl, base, strict=False)
            ok = not any(q.level == "error" for q in plan.problems)
            print(json.dumps({"ok": ok, "problems": _problem_lines(plan.problems), "stats": plan.stats() if ok else None}, indent=1))
            return 0 if ok else 1
        if a.cmd == "render":
            res = render(a.edl, a.out, a.preview, a.scale, a.crf)
            brief = {k: res[k] for k in ("ok", "mode", "errors", "warnings", "stats", "text_checks")}
            brief["measured"] = {k: v for k, v in res["measured"].items() if k != "faults"}
            brief["audio"] = {k: res["audio"].get(k) for k in ("lufs", "true_peak_db", "limiter", "none_reason")}
            print(json.dumps(brief, indent=1))
            return 0 if res["ok"] else 1
        if a.cmd == "matte":
            print(json.dumps(make_matte(a.edl)))
            return 0
        plan = None
        if a.edl:
            edl, base = load(a.edl)
            plan = validate(edl, base)
        res = check(a.video, plan)
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    except (EDLError, RenderError) as e:
        print(json.dumps({"ok": False, "errors": str(e).splitlines()}, indent=1))
        return 1


if __name__ == "__main__":
    sys.exit(main())
