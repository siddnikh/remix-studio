"""Free, deterministic measurement of videos: probe, cuts, energy map, contact sheets.

Everything is computed from ffmpeg-decoded frames downscaled to ~360 px wide and streamed, so memory
stays small. `extract` is the single pass that other modules (score.py) reuse.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

WIDTH = 360
GRID_LONG = 64  # stored low-res luma grid, long side
GRID_SHORT = 36


# ---------------------------------------------------------------- probe

def probe(path: str | Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise ValueError(f"no video stream in {path}")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = rot or int(v.get("tags", {}).get("rotate", 0) or 0)
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = Fraction(v.get("avg_frame_rate") or v["r_frame_rate"])
    if fps == 0:
        fps = Fraction(v["r_frame_rate"])
    duration = float(v.get("duration") or info["format"].get("duration") or 0)
    nb = v.get("nb_frames")
    frames = int(nb) if nb and nb != "N/A" else int(round(duration * float(fps)))
    return {
        "path": str(path),
        "width": w,
        "height": h,
        "fps": float(fps),
        "fps_rational": f"{fps.numerator}/{fps.denominator}",
        "frames": frames,
        "duration": round(duration, 4),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


# ---------------------------------------------------------------- decoding

def _scaled_size(info: dict, width: int) -> tuple[int, int]:
    h = int(round(width * info["height"] / info["width"] / 2)) * 2
    return width, max(h, 2)


def iter_frames(path, width: int = WIDTH, fps: float | None = None, info: dict | None = None):
    """Yield BGR uint8 frames scaled to `width`, optionally resampled to `fps`."""
    info = info or probe(path)
    w, h = _scaled_size(info, width)
    vf = ([f"fps={fps}"] if fps else []) + [f"scale={w}:{h}:flags=area"]
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", ",".join(vf),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = w * h * 3
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def _grid_shape(info: dict) -> tuple[int, int]:
    return (GRID_LONG, GRID_SHORT) if info["height"] >= info["width"] else (GRID_SHORT, GRID_LONG)


def extract(path, fps: float | None = None, width: int = WIDTH, grid_shape: tuple[int, int] | None = None) -> dict:
    """One streaming pass producing per-frame features.

    Arrays (length T = decoded frames; motion/diff arrays have T entries with index 0 = 0):
      luma, hist_d (histogram distance to previous), pix_d (thumbnail abs diff to previous),
      dx, dy, dlogs (dominant-motion affine at frame centre, in fractions of frame width / log-scale),
      inl (RANSAC inlier fraction), act (motion-compensated residual), grid (T,h,w,3) uint8 Lab (low-res colour layout),
      thumb_dist_skip (thumbnail diff between frame i and i-2).
    """
    info = probe(path)
    fps_eff = fps or info["fps"]
    gshape = grid_shape or _grid_shape(info)
    luma, hist_d, pix_d, skip_d = [], [], [], []
    dx, dy, dlogs, inl, act, grids = [], [], [], [], [], []
    prev_gray = prev_hist = prev_thumb = prev2_thumb = None
    for bgr in iter_frames(path, width, fps, info):
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hg = cv2.calcHist([gray], [0], None, [32], [0, 256]).ravel()
        hs = cv2.calcHist([hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).ravel()
        hist = np.concatenate([hg / hg.sum(), hs / hs.sum()])
        thumb = cv2.resize(gray, (48, 48 * gray.shape[0] // gray.shape[1]), interpolation=cv2.INTER_AREA).astype(np.float32)
        luma.append(float(gray.mean()))
        g = cv2.resize(bgr, (gshape[1], gshape[0]), interpolation=cv2.INTER_AREA)
        grids.append(cv2.cvtColor(g, cv2.COLOR_BGR2LAB))
        if prev_gray is None:
            hist_d.append(0.0); pix_d.append(0.0); skip_d.append(0.0)
            dx.append(0.0); dy.append(0.0); dlogs.append(0.0); inl.append(1.0); act.append(0.0)
        else:
            hist_d.append(float(0.25 * np.abs(hist - prev_hist).sum()))
            pix_d.append(float(np.abs(thumb - prev_thumb).mean() / 255.0))
            skip_d.append(float(np.abs(thumb - prev2_thumb).mean() / 255.0) if prev2_thumb is not None else 1.0)
            m, frac = _global_motion(prev_gray, gray)
            if m is None:
                dx.append(0.0); dy.append(0.0); dlogs.append(0.0); inl.append(0.0)
                act.append(float(np.abs(gray.astype(np.float32) - prev_gray).mean() / 255.0))
            else:
                h_, w_ = gray.shape
                c = np.array([w_ / 2, h_ / 2])
                moved = m[:, :2] @ c + m[:, 2]
                dx.append(float((moved[0] - c[0]) / w_)); dy.append(float((moved[1] - c[1]) / w_))
                dlogs.append(float(np.log(max(np.hypot(m[0, 0], m[1, 0]), 1e-3))))
                inl.append(frac)
                warped = cv2.warpAffine(prev_gray, m, (w_, h_), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                a = cv2.GaussianBlur(warped, (5, 5), 0).astype(np.float32)
                b = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
                act.append(float(np.abs(a - b)[8:-8, 8:-8].mean() / 255.0))
        prev2_thumb, prev_thumb, prev_gray, prev_hist = prev_thumb, thumb, gray, hist
    out = {k: np.asarray(v) for k, v in dict(
        luma=luma, hist_d=hist_d, pix_d=pix_d, skip_d=skip_d, dx=dx, dy=dy, dlogs=dlogs, inl=inl, act=act).items()}
    out["grid"] = np.stack(grids) if grids else np.zeros((0, *gshape, 3), np.uint8)
    out["fps"] = float(fps_eff)
    out["info"] = info
    return out


def _global_motion(prev: np.ndarray, cur: np.ndarray):
    """Dominant (background) similarity transform prev->cur via LK + RANSAC; robust to a moving subject."""
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=250, qualityLevel=0.01, minDistance=8)
    if pts is None or len(pts) < 8:
        return None, 0.0
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
    ok = st.ravel() == 1
    if ok.sum() < 8:
        return None, 0.0
    m, mask = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if m is None:
        return None, 0.0
    return m.astype(np.float64), float(mask.mean())


# ---------------------------------------------------------------- cuts

def detect_cuts(feat: dict, min_strength: float = 0.2, ratio: float = 2.5) -> list[dict]:
    """Cut = frame whose change from the previous frame is large in absolute terms and large compared with
    non-adjacent neighbours. Neighbours at +-1 are excluded so a one-frame insert (two adjacent changes)
    is still detected; it is reported as `insert_in` / `insert_out` when frame i-1 resembles frame i+1.
    Inserts that are brightness flashes are excluded here and reported by `detect_flashes` instead."""
    c = feat["hist_d"] + feat["pix_d"]
    n = len(c)
    fps = feat["fps"]
    cand = []
    for i in range(1, n):
        ctx = np.concatenate([c[max(1, i - 4):max(1, i - 1)], c[i + 2:i + 5]])
        local = ctx.max() if len(ctx) else 0.0
        if c[i] >= min_strength and c[i] > ratio * local:
            cand.append(i)
    flash_frames = {f for a, b in _flash_runs(feat) for f in range(a, b + 1)}
    cuts = []
    cs = set(cand)
    for i in cand:
        kind = "cut"
        if i + 1 in cs and feat["skip_d"][i + 1] < 0.5 * min(c[i], c[i + 1]):
            kind = "insert_in"
        elif i - 1 in cs and feat["skip_d"][i] < 0.5 * min(c[i], c[i - 1]):
            kind = "insert_out"
        if kind != "cut" and (i in flash_frames or i - 1 in flash_frames):
            continue
        cuts.append({"frame": i, "t": round(i / fps, 3), "strength": round(float(c[i]), 3), "kind": kind})
    return cuts


def cuts_cmd(path) -> dict:
    f = extract(path)
    cuts = detect_cuts(f)
    return {"fps": f["fps"], "frames": len(f["luma"]), "cuts": cuts,
            "cut_times": [c["t"] for c in cuts]}


def clean_motion(feat: dict, cuts: list[dict]) -> dict:
    """Return dx/dy/dlogs/act with cut frames zeroed (motion across a cut is meaningless)."""
    idx = [c["frame"] for c in cuts]
    out = {k: feat[k].copy() for k in ("dx", "dy", "dlogs", "act")}
    for k in out:
        out[k][idx] = 0.0
    return out


# ---------------------------------------------------------------- events

def _moving_avg(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) == 0:
        return x.copy()
    k = np.ones(n) / n
    pad = n // 2
    xp = np.pad(x, (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, k, mode="valid")


def _runs(mask: np.ndarray, max_gap: int = 0, min_len: int = 1):
    runs, start, last = [], None, None
    for i in np.flatnonzero(mask):
        if start is None:
            start = last = i
        elif i - last <= max_gap + 1:
            last = i
        else:
            runs.append((start, last)); start = last = i
    if start is not None:
        runs.append((start, last))
    return [(a, b) for a, b in runs if b - a + 1 >= min_len]


def _flash_runs(feat: dict) -> list[tuple[int, int]]:
    """Runs of <= 6 frames whose luma departs sharply from a 7-frame median (white/black flash frames)."""
    L = feat["luma"]
    if len(L) < 7:
        return []
    pad = np.pad(L, 3, mode="edge")
    base = np.array([np.median(pad[i:i + 7]) for i in range(len(L))])
    dev = L - base
    mad = np.median(np.abs(dev - np.median(dev))) + 1e-6
    thr = max(10.0, 6 * 1.4826 * mad)
    return [(a, b) for a, b in _runs(np.abs(dev) > thr, max_gap=1) if b - a + 1 <= 6]


def detect_flashes(feat: dict) -> list[dict]:
    L = feat["luma"]
    pad = np.pad(L, 3, mode="edge")
    base = np.array([np.median(pad[i:i + 7]) for i in range(len(L))])
    dev = L - base
    fps = feat["fps"]
    return [{"t": round(a / fps, 3), "frames": int(b - a + 1),
             "delta": round(float(dev[a:b + 1][np.argmax(np.abs(dev[a:b + 1]))]), 1)}
            for a, b in _flash_runs(feat)]


def detect_shake_and_camera(feat: dict, cuts: list[dict]):
    """Split global translation into a smooth part (camera move) and a high-frequency part (shake)."""
    m = clean_motion(feat, cuts)
    fps = feat["fps"]
    vx, vy = m["dx"], m["dy"]
    k = max(3, int(round(fps / 6)) | 1)
    sx, sy = _moving_avg(vx, k), _moving_avg(vy, k)
    hx, hy = vx - sx, vy - sy
    shake_mag = np.hypot(hx, hy)
    thr = max(0.004, 4 * 1.4826 * np.median(np.abs(shake_mag - np.median(shake_mag))) + np.median(shake_mag))
    flagged = shake_mag > thr
    bursts = []
    for a, b in _runs(flagged, max_gap=2, min_len=3):
        seg_x, seg_y = hx[a:b + 1], hy[a:b + 1]
        flips = int(((np.diff(np.sign(seg_x)) != 0).sum() + (np.diff(np.sign(seg_y)) != 0).sum()))
        if flips >= 2:
            bursts.append({"t": round(a / fps, 3), "duration": round((b - a + 1) / fps, 3),
                           "peak": round(float(shake_mag[a:b + 1].max()), 4)})
    speed = np.hypot(sx, sy) * fps  # frame widths per second
    zoom = np.abs(_moving_avg(m["dlogs"], k)) * fps
    cam = speed + zoom
    cam_thr = 0.03
    moves = [{"t": round(a / fps, 3), "duration": round((b - a + 1) / fps, 3),
              "mean_speed": round(float(cam[a:b + 1].mean()), 3)}
             for a, b in _runs(cam > cam_thr, max_gap=2, min_len=max(2, int(fps * 0.4)))]
    return bursts, moves, cam


def audio_hits(path, info: dict) -> tuple[list[float], float]:
    """Onsets from half-wave rectified spectral flux of 16 kHz mono audio. Returns (times, duration)."""
    if not info["has_audio"]:
        return [], 0.0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < 2048:
        return [], len(x) / 16000
    n, hop = 512, 160
    win = np.hanning(n)
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    mag = np.log1p(30 * spec)
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0], flux])
    med = np.median(flux)
    mad = np.median(np.abs(flux - med)) + 1e-9
    thr = med + 6 * 1.4826 * mad
    hits, last = [], -1.0
    for i in range(1, len(flux) - 1):
        t = i * hop / 16000
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and t - last >= 0.1:
            hits.append(round(t, 3)); last = t
    return hits, len(x) / 16000


def _per_second(times: list[float], n_sec: int) -> list[int]:
    counts = [0] * n_sec
    for t in times:
        counts[min(int(t), n_sec - 1)] += 1
    return counts


def energy(path) -> dict:
    """Per-second rates and event timestamps; compact enough to read in one go."""
    f = extract(path)
    info = f["info"]
    dur = info["duration"] or len(f["luma"]) / f["fps"]
    n_sec = max(1, int(np.ceil(dur)))
    cuts = detect_cuts(f)
    flashes = detect_flashes(f)
    shakes, moves, cam = detect_shake_and_camera(f, cuts)
    hits, _ = audio_hits(path, info)
    cut_t = [c["t"] for c in cuts]
    cam_ps = [round(float(cam[int(s * f["fps"]):int((s + 1) * f["fps"])].mean()) if len(cam) > int(s * f["fps"]) else 0.0, 3)
              for s in range(n_sec)]
    per_sec = {
        "cuts": _per_second(cut_t, n_sec),
        "flashes": _per_second([e["t"] for e in flashes], n_sec),
        "shake_bursts": _per_second([e["t"] for e in shakes], n_sec),
        "camera_motion": cam_ps,
        "audio_hits": _per_second(hits, n_sec),
    }
    rates = {
        "cuts_per_s": len(cuts) / dur, "flashes_per_s": len(flashes) / dur,
        "shake_bursts_per_s": len(shakes) / dur, "audio_hits_per_s": len(hits) / dur,
        "camera_motion_mean": float(np.mean(cam_ps)),
    }
    return {
        "info": {k: info[k] for k in ("width", "height", "fps_rational", "frames", "duration", "has_audio")},
        "rates": {k: round(v, 4) for k, v in rates.items()},
        "per_second": per_sec,
        "events": {"cuts": cuts, "flashes": flashes, "shake_bursts": shakes, "camera_moves": moves, "audio_hits": hits},
    }


# ---------------------------------------------------------------- contact sheet

def sheet(path, out_png, fps: float = 2.0, tile_width: int = 180, cols: int = 6, max_frames: int = 30,
          mark_cuts: bool = True) -> dict:
    info = probe(path)
    dur = info["duration"]
    if dur * fps > max_frames:
        fps = max_frames / dur
    tiles = list(iter_frames(path, tile_width, fps, info))[:max_frames]
    if not tiles:
        raise ValueError("no frames decoded")
    cut_times = []
    if mark_cuts:
        cut_times = [c["t"] for c in detect_cuts(extract(path))]
    th, tw = tiles[0].shape[:2]
    rows = int(np.ceil(len(tiles) / cols))
    canvas = np.zeros((rows * th, cols * tw, 3), np.uint8)
    for k, t in enumerate(tiles):
        t = t.copy()
        ts = k / fps
        has_cut = any(ts <= c < ts + 1 / fps for c in cut_times)
        label = f"{ts:.2f}s" + (" CUT" if has_cut else "")
        cv2.rectangle(t, (0, 0), (tw, 16), (0, 0, 0), -1)
        cv2.putText(t, label, (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255) if has_cut else (255, 255, 255), 1, cv2.LINE_AA)
        if has_cut:
            cv2.rectangle(t, (0, 0), (tw - 1, th - 1), (0, 0, 255), 2)
        r, c = divmod(k, cols)
        canvas[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = t
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), canvas)
    return {"path": str(out_png), "tiles": len(tiles), "fps": round(fps, 3), "size": [canvas.shape[1], canvas.shape[0]]}


# ---------------------------------------------------------------- CLI

# A click first registers when the 20 ms energy window's leading edge reaches it, 2 hops before it plays.
WINDOW_LEAD_HOPS = 2


def beats(path: str | Path, seconds: float = 30.0) -> dict:
    """Beat grid of an audio or video file: the period and phase whose grid lands on the most onset energy."""
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-t", str(seconds), "-vn", "-ac", "1",
                          "-ar", "8000", "-f", "f32le", "-"], capture_output=True)
    if raw.returncode or not raw.stdout:
        raise ValueError(f"no audio in {path}: {raw.stderr.decode(errors='replace')[-200:]}")
    x = np.frombuffer(raw.stdout, np.float32)
    hop = 80
    energy = np.add.reduce(np.lib.stride_tricks.sliding_window_view(x**2, 160)[::hop], axis=1)
    onset = np.maximum(0, np.diff(energy))
    if onset.max() <= 0:
        raise ValueError(f"no onsets in {path}")
    onset = onset / onset.max()
    best = (-1.0, 0, 0)
    for period in range(30, 71):
        for phase in range(period):
            score = float(onset[phase::period].mean())
            if score > best[0]:
                best = (score, period, phase)
    score, period, phase = best
    return {"period_s": period / 100, "bpm": round(6000 / period, 1), "first_beat_s": (phase + WINDOW_LEAD_HOPS) % period / 100, "grid_contrast": round(score / float(onset.mean()), 2),
            "seconds_measured": round(len(x) / 8000, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="analyze", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("video")
    p = sub.add_parser("cuts"); p.add_argument("video")
    p = sub.add_parser("energy"); p.add_argument("video"); p.add_argument("-o", "--out", help="write JSON here")
    p = sub.add_parser("beats"); p.add_argument("audio")
    p = sub.add_parser("sheet"); p.add_argument("video"); p.add_argument("out_png")
    p.add_argument("--fps", type=float, default=2.0); p.add_argument("--tile-width", type=int, default=180)
    p.add_argument("--cols", type=int, default=6); p.add_argument("--max-frames", type=int, default=30)
    a = ap.parse_args(argv)
    if a.cmd == "beats":
        print(json.dumps(beats(a.audio)))
        return 0
    if a.cmd == "probe":
        res = probe(a.video)
    elif a.cmd == "cuts":
        res = cuts_cmd(a.video)
    elif a.cmd == "energy":
        res = energy(a.video)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(res, indent=1))
    else:
        res = sheet(a.video, a.out_png, a.fps, a.tile_width, a.cols, a.max_frames)
    print(json.dumps(res, indent=None if a.cmd != "energy" else 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
