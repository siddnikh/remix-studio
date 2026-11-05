"""ffmpeg filter builders for the EDL renderer. Pure string construction plus a small font reader.

All sizes are fractions of the canvas so a preview at another scale looks the same. Frame numbers are
integers on the shot's own timeline (camera, whip, glitch) or the edit's timeline (flash, text).
"""
from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

EASES = {
    "linear": "({u})",
    "in": "pow({u},2)",
    "out": "(1-pow(1-{u},2))",
    "snap": "(1-pow(1-{u},4))",
    "inout": "({u}*{u}*(3-2*{u}))",
}
TEXT_ANIMS = ("none", "pop", "slam", "slide_up", "flicker", "typewriter")
WHIP_DIRS = ("left", "right", "up", "down")


def _num(x: float) -> str:
    return f"{x:.6g}"


def escape_value(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# ---------------------------------------------------------------- grade

def grade_filters(grade: dict | None, base_dir: Path) -> list[str]:
    if not grade:
        return []
    out = []
    eq = {k: grade[k] for k in ("contrast", "brightness", "saturation", "gamma") if k in grade}
    if eq:
        out.append("eq=" + ":".join(f"{k}={_num(v)}" for k, v in eq.items()))
    if "temperature" in grade:
        out.append(f"colortemperature=temperature={_num(grade['temperature'])}")
    if "lut" in grade:
        out.append(f"lut3d=file='{escape_value(str((base_dir / grade['lut']).resolve()))}':interp=tetrahedral")
    if "sharpen" in grade:
        out.append(f"unsharp=5:5:{_num(grade['sharpen'])}:5:5:0")
    if "vignette" in grade:
        out.append(f"vignette=angle={_num(grade['vignette'])}")
    if "grain" in grade:
        out.append(f"noise=alls={int(grade['grain'])}:allf=t")
    return out


# ---------------------------------------------------------------- camera

@dataclass(frozen=True)
class Burst:
    """A shake: starts hard at `at` and rings out over `frames`."""
    at: int
    frames: int
    amp: float
    hz: float = 9.0
    rot: float = 0.0
    decay: bool = True


@dataclass(frozen=True)
class Punch:
    """A snap zoom that lands at `at` and relaxes over `frames`."""
    at: int
    frames: int
    strength: float


@dataclass(frozen=True)
class Camera:
    z0: float = 1.0
    z1: float = 1.0
    x0: float = 0.5
    y0: float = 0.5
    x1: float = 0.5
    y1: float = 0.5
    ease: str = "linear"
    bursts: tuple[Burst, ...] = ()
    punches: tuple[Punch, ...] = ()
    seed: int = 0

    @property
    def active(self) -> bool:
        return bool(self.bursts or self.punches or self.z0 != 1 or self.z1 != 1)


def shake_margin(cam: Camera, w: int, h: int) -> float:
    """Largest fraction of the frame the window can drift past its nominal rectangle."""
    margin = 0.0
    for b in cam.bursts:
        rot = math.radians(b.rot)
        margin = max(margin, b.amp * h / w + rot * h / w / 2, b.amp + rot * w / h / 2)
    return margin


def camera_headroom(cam: Camera, w: int, h: int) -> float:
    """Zoom multiplier that keeps shake inside the picture, so no edge is ever smeared into frame."""
    margin = shake_margin(cam, w, h)
    if margin <= 0:
        return 1.0
    return max(1.0, (1 / (1 - 2 * margin)) / min(cam.z0, cam.z1))


def camera_filter(cam: Camera, frames: int, fps: float, w: int, h: int, interpolation: str = "cubic") -> str | None:
    if not cam.active:
        return None
    rng = random.Random(cam.seed)
    n = "(on-1)"  # perspective numbers frames from 1
    u = f"min(1,{n}/{max(frames - 1, 1)})"
    e = EASES[cam.ease].format(u=u)
    head = camera_headroom(cam, w, h)
    zoom = f"({_num(cam.z0)}+{_num(cam.z1 - cam.z0)}*{e})*{_num(head)}"
    for p in cam.punches:
        tau = max(p.frames / 2, 0.5)
        zoom += f"*(1+{_num(p.strength)}*gte({n},{p.at})*exp(-({n}-{p.at})/{_num(tau)}))"
    margin = shake_margin(cam, w, h)
    cx = f"({_num(cam.x0)}+{_num(cam.x1 - cam.x0)}*{e})"
    cy = f"({_num(cam.y0)}+{_num(cam.y1 - cam.y0)}*{e})"
    sx, sy, rot = ["0"], ["0"], ["0"]
    for b in cam.bursts:
        t = f"({n}/{_num(fps)})"
        env = f"between({n},{b.at},{b.at + b.frames - 1})"
        if b.decay:
            env += f"*exp(-2.5*({n}-{b.at})/{b.frames})"
        p = [rng.uniform(0, math.tau) for _ in range(5)]
        wave = "(sin({w}*" + t + "+{a})+0.5*sin({w}*1.73*" + t + "+{b}))/1.5"
        om = math.tau * b.hz
        sx.append(f"{env}*{_num(b.amp)}*H*" + wave.format(w=_num(om), a=_num(p[0]), b=_num(p[1])))
        sy.append(f"{env}*{_num(b.amp)}*H*" + wave.format(w=_num(om * 0.9), a=_num(p[2]), b=_num(p[3])))
        if b.rot:
            rot.append(f"{env}*{_num(math.radians(b.rot))}*sin({_num(om * 0.8)}*{t}+{_num(p[4])})")
    setup = (f"st(0,{zoom});"
             f"st(1,max(0.5/ld(0)+{_num(margin)},min(1-0.5/ld(0)-{_num(margin)},{cx})));"
             f"st(2,max(0.5/ld(0)+{_num(margin)},min(1-0.5/ld(0)-{_num(margin)},{cy})));"
             f"st(3,{'+'.join(rot)});")
    corners = {"0": (-1, -1), "1": (1, -1), "2": (-1, 1), "3": (1, 1)}
    parts = []
    for name, (dx, dy) in corners.items():
        hw, hh = f"({dx}*W/2/ld(0))", f"({dy}*H/2/ld(0))"
        x = f"{setup}ld(1)*W+{hw}*cos(ld(3))-{hh}*sin(ld(3))+{'+'.join(sx)}"
        y = f"{setup}ld(2)*H+{hw}*sin(ld(3))+{hh}*cos(ld(3))+{'+'.join(sy)}"
        parts += [f"x{name}='{x}'", f"y{name}='{y}'"]
    return "perspective=" + ":".join(parts) + f":interpolation={interpolation}:sense=source:eval=frame"


# ---------------------------------------------------------------- whip and glitch

@dataclass(frozen=True)
class Whip:
    frames: int
    direction: str


@dataclass(frozen=True)
class Glitch:
    frames: int
    strength: float = 1.0
    seed: int = 0


def _whip_offset(i: Whip | None, o: Whip | None, total: int, span: int) -> str:
    terms = []
    travel = 0.92 * span
    if i:
        sign = -1 if i.direction in ("left", "up") else 1
        terms.append(f"lt(n,{i.frames})*{_num(sign * travel)}*pow(1-(n+1)/{i.frames + 1},2)")
    if o:
        sign = 1 if o.direction in ("left", "up") else -1
        start = total - o.frames
        terms.append(f"gte(n,{start})*{_num(sign * travel)}*pow((n-{start}+1)/{o.frames + 1},2)")
    return "+".join(terms) or "0"


def _whip_blurs(i: Whip | None, o: Whip | None, total: int, h_axis: bool, span: int) -> list[str]:
    """Blur each frame in proportion to how far the picture moved, heavily enough to hide the mirror seam."""
    out = []
    travel = 0.92 * span

    def blur(velocity: float, frame: int) -> str | None:
        sigma = 0.5 * velocity
        if sigma < 0.6:
            return None
        sx, sy = (sigma, 0) if h_axis else (0, sigma)
        return f"gblur=sigma={_num(sx)}:sigmaV={_num(sy)}:steps=2:enable='eq(n,{frame})'"

    if i:
        at = [travel * (1 - (k + 1) / (i.frames + 1)) ** 2 for k in range(i.frames)] + [0.0]
        out += [blur(at[k] - at[k + 1], k) for k in range(i.frames)]
    if o:
        start = total - o.frames
        at = [0.0] + [travel * ((k + 1) / (o.frames + 1)) ** 2 for k in range(o.frames)]
        out += [blur(at[k + 1] - at[k], start + k) for k in range(o.frames)]
    return [b for b in out if b]


def whip_graph(cur: str, nxt: str, whip_in: Whip | None, whip_out: Whip | None, total: int, w: int, h: int) -> str:
    """Mirror-tile the plate so the camera can leave the frame without showing an edge, then fling the window."""
    axes = {d.direction in ("left", "right") for d in (whip_in, whip_out) if d}
    if len(axes) > 1:
        raise ValueError("a shot's whip in and out must share one axis")
    horizontal = axes.pop()
    span = w if horizontal else h
    flip, stack = ("hflip", "hstack") if horizontal else ("vflip", "vstack")
    off = _whip_offset(whip_in, whip_out, total, span)
    crop = f"crop={w}:{h}:'{w}+({off})':0" if horizontal else f"crop={w}:{h}:0:'{h}+({off})'"
    blurs = _whip_blurs(whip_in, whip_out, total, horizontal, span)
    chain = ",".join([crop, *blurs])
    return (f"[{cur}]split=3[{nxt}a][{nxt}b][{nxt}c];[{nxt}a]{flip}[{nxt}l];[{nxt}c]{flip}[{nxt}r];"
            f"[{nxt}l][{nxt}b][{nxt}r]{stack}=inputs=3,{chain}[{nxt}]")


def glitch_graph(cur: str, nxt: str, frames: list[int], g: Glitch, w: int, h: int, fps: float) -> str:
    """Per-frame chroma tear plus displaced horizontal strips. Whole-plate only: no region follows a face.

    Strips are addressed by timestamp: crop and overlay count frames differently, and any disagreement
    pastes a strip onto the wrong row.
    """
    rng = random.Random(g.seed)
    chain = []
    for f in frames:
        c = max(2, int(w * 0.022 * g.strength)) // 2 * 2
        sign = rng.choice((-1, 1))
        chain.append(f"chromashift=cbh={sign * c}:crh={-sign * c}:cbv={rng.randint(-1, 1) * c // 2}:edge=wrap:enable='eq(n,{f})'")
        chain.append(f"noise=alls={int(14 * g.strength)}:allf=t:enable='eq(n,{f})'")
    parts = [f"[{cur}]" + ",".join(chain) + f"[{nxt}g]"]
    last = f"{nxt}g"
    at = lambda f: f"lt(abs(t-{f / fps:.6f}),{0.5 / fps:.6f})"
    enable = "+".join(at(f) for f in frames)
    for s in range(3):
        strip_h = max(2, int(h * rng.uniform(0.03, 0.11)) // 2 * 2)
        y_expr, x_expr = "0", "0"
        for f in frames:
            y = int(rng.uniform(0, h - strip_h)) // 2 * 2
            x = int(rng.choice((-1, 1)) * rng.uniform(0.03, 0.14) * w * g.strength) // 2 * 2
            y_expr, x_expr = f"if({at(f)},{y},{y_expr})", f"if({at(f)},{x},{x_expr})"
        parts.append(f"[{last}]split[{nxt}p{s}][{nxt}q{s}]")
        parts.append(f"[{nxt}q{s}]crop={w}:{strip_h}:0:'{y_expr}'[{nxt}s{s}]")
        parts.append(f"[{nxt}p{s}][{nxt}s{s}]overlay=x='{x_expr}':y='{y_expr}':enable='{enable}':eval=frame[{nxt}o{s}]")
        last = f"{nxt}o{s}"
    parts.append(f"[{last}]null[{nxt}]")
    return ";".join(parts)


# ---------------------------------------------------------------- flash

@dataclass(frozen=True)
class Flash:
    at: int
    frames: int = 2
    color: str = "white"
    strength: float = 1.0

    def levels(self) -> list[float]:
        return [self.strength * (1 - k / self.frames) ** 1.5 for k in range(self.frames)]


def flash_filters(flashes: list[Flash]) -> list[str]:
    """White flashes add exposure and bleach colour, so highlights blow out first like real light."""
    white, out = [], []
    for fl in flashes:
        for k, level in enumerate(fl.levels()):
            frame = fl.at + k
            if fl.color == "white":
                white.append(f"eq(n,{frame})*{_num(level)}")
            else:
                out.append(f"drawbox=x=0:y=0:w=iw:h=ih:t=fill:color={fl.color}@{level:.3f}:enable='eq(n,{frame})'")
    if white:
        s = "+".join(white)
        out.insert(0, f"eq=brightness='min(1,{s})':saturation='max(0,1-({s}))':eval=frame")
    return out


# ---------------------------------------------------------------- fonts

class FontError(ValueError):
    pass


class Font:
    """Reads a font file's character map and advance widths, enough to prove coverage and measure a line."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = self.path.read_bytes()
        if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            raise FontError(f"{self.path.name}: not a single-face TrueType or OpenType file")
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, off, length = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            tables[tag.decode("latin1")] = (off, length)
        for need in ("cmap", "head", "hhea", "hmtx"):
            if need not in tables:
                raise FontError(f"{self.path.name}: missing {need} table")
        self.units = struct.unpack(">H", data[tables["head"][0] + 18:tables["head"][0] + 20])[0]
        self.cmap = self._read_cmap(data, tables["cmap"][0])
        n_metrics = struct.unpack(">H", data[tables["hhea"][0] + 34:tables["hhea"][0] + 36])[0]
        off = tables["hmtx"][0]
        self.advances = [struct.unpack(">H", data[off + 4 * i:off + 4 * i + 2])[0] for i in range(n_metrics)]

    @staticmethod
    def _read_cmap(data: bytes, base: int) -> dict[int, int]:
        cmap: dict[int, int] = {}
        count = struct.unpack(">H", data[base + 2:base + 4])[0]
        subtables = []
        for i in range(count):
            pid, eid, off = struct.unpack(">HHI", data[base + 4 + 8 * i:base + 12 + 8 * i])
            if (pid, eid) in ((3, 1), (3, 10), (0, 3), (0, 4), (0, 6), (0, 0)):
                subtables.append(base + off)
        for sub in subtables:
            fmt = struct.unpack(">H", data[sub:sub + 2])[0]
            if fmt == 4:
                seg = struct.unpack(">H", data[sub + 6:sub + 8])[0] // 2
                ends = struct.unpack(f">{seg}H", data[sub + 14:sub + 14 + 2 * seg])
                starts = struct.unpack(f">{seg}H", data[sub + 16 + 2 * seg:sub + 16 + 4 * seg])
                deltas = struct.unpack(f">{seg}H", data[sub + 16 + 4 * seg:sub + 16 + 6 * seg])
                ro_at = sub + 16 + 6 * seg
                ros = struct.unpack(f">{seg}H", data[ro_at:ro_at + 2 * seg])
                for i in range(seg):
                    for c in range(starts[i], ends[i] + 1):
                        if c == 0xFFFF:
                            continue
                        if ros[i] == 0:
                            g = (c + deltas[i]) & 0xFFFF
                        else:
                            at = ro_at + 2 * i + ros[i] + 2 * (c - starts[i])
                            g = struct.unpack(">H", data[at:at + 2])[0]
                            g = (g + deltas[i]) & 0xFFFF if g else 0
                        if g:
                            cmap.setdefault(c, g)
            elif fmt == 12:
                groups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
                for i in range(groups):
                    s, e, g = struct.unpack(">III", data[sub + 16 + 12 * i:sub + 28 + 12 * i])
                    for c in range(s, e + 1):
                        cmap.setdefault(c, g + c - s)
        return cmap

    def missing(self, text: str) -> list[str]:
        return sorted({ch for ch in text if ord(ch) not in self.cmap and not ch.isspace()})

    def width(self, text: str) -> float:
        """Line width in em units, ignoring kerning."""
        total = 0
        for ch in text:
            g = self.cmap.get(ord(ch), 0)
            total += self.advances[min(g, len(self.advances) - 1)]
        return total / self.units


# ---------------------------------------------------------------- text

@dataclass(frozen=True)
class Text:
    text: str
    font: str
    start: int
    end: int
    x: float = 0.5
    y: float = 0.5
    size: float = 0.08
    color: str = "white"
    border: float = 0.0
    border_color: str = "black"
    shadow: float = 0.0
    box: str | None = None
    anim: str = "none"
    fade_out: int = 0
    seed: int = 0
    measured_width: float = field(default=0.0, compare=False)
    behind: bool = False


def _text_exprs(t: Text) -> tuple[str, str, str]:
    d = f"(n-{t.start})"
    scale, alpha, dy = "1", "1", "0"
    if t.anim == "pop":
        scale = f"(1-0.35*exp(-0.8*{d})*cos(1.1*{d}))"
        alpha = f"min(1,({d}+1)/2)"
    elif t.anim == "slam":
        scale = f"(1+0.9*exp(-1.1*{d}))"
        alpha = f"min(1,0.5+0.5*{d})"
    elif t.anim == "slide_up":
        dy = f"h*0.05*exp(-0.6*{d})"
        alpha = f"min(1,({d}+1)/4)"
    elif t.anim == "flicker":
        rng = random.Random(t.seed)
        on = [f for f in range(8) if rng.random() < 0.55 or f == 0]
        alpha = "+".join([f"eq({d},{f})" for f in on] + [f"gte({d},8)"])
    if t.fade_out:
        alpha = f"({alpha})*min(1,({t.end}-n)/{t.fade_out})"
    return scale, alpha, dy


def _drawtext(t: Text, textfile: Path, start: int, end: int, x_expr: str, y_expr: str, size_expr: str, alpha: str) -> str:
    opts = [
        f"fontfile='{escape_value(str(Path(t.font).resolve()))}'",
        f"textfile='{escape_value(str(textfile.resolve()))}'",
        "expansion=none",
        f"fontcolor={t.color}",
        f"fontsize='{size_expr}'",
        f"alpha='{alpha}'",
        f"x='{x_expr}'",
        f"y='{y_expr}'",
        f"enable='between(n,{start},{end - 1})'",
    ]
    if t.border:
        opts += ["borderw={BORDER}", f"bordercolor={t.border_color}"]
    if t.shadow:
        opts += ["shadowx={SHADOW}", "shadowy={SHADOW}", "shadowcolor=black@0.7"]
    if t.box:
        opts += ["box=1", f"boxcolor={t.box}", "boxborderw={BOXPAD}"]
    return "drawtext=" + ":".join(opts)


def text_filters(t: Text, workdir: Path, index: int, canvas_h: int) -> list[str]:
    """One drawtext per event, or per prefix for a typewriter. The words come from a file: no escaping to get wrong."""
    workdir.mkdir(parents=True, exist_ok=True)
    scale, alpha, dy = _text_exprs(t)
    size_expr = f"h*{_num(t.size)}*{scale}"
    px = t.size * canvas_h
    filters = []
    if t.anim == "typewriter":
        chars = [i for i in range(1, len(t.text) + 1) if t.text[i - 1] != " "]
        step = max(1, min(3, (t.end - t.start) // (2 * max(len(chars), 1))))
        left = f"w*{_num(t.x)}-{_num(t.measured_width * px / 2)}*h/{canvas_h}"
        for k, upto in enumerate(chars):
            f = workdir / f"text{index}_{k}.txt"
            f.write_text(t.text[:upto], encoding="utf-8")
            s = t.start + k * step
            e = t.start + (k + 1) * step if k + 1 < len(chars) else t.end
            if s >= t.end:
                break
            filters.append(_drawtext(t, f, s, min(e, t.end), left, f"h*{_num(t.y)}-text_h/2", size_expr, alpha))
    else:
        f = workdir / f"text{index}.txt"
        f.write_text(t.text, encoding="utf-8")
        x = f"w*{_num(t.x)}-text_w/2"
        y = f"h*{_num(t.y)}-text_h/2+{dy}"
        filters.append(_drawtext(t, f, t.start, t.end, x, y, size_expr, alpha))
    subs = {"{BORDER}": str(max(1, round(px * t.border))), "{SHADOW}": str(max(1, round(px * t.shadow))),
            "{BOXPAD}": str(max(1, round(px * 0.18)))}
    done = []
    for flt in filters:
        for k, v in subs.items():
            flt = flt.replace(k, v)
        done.append(flt)
    return done
