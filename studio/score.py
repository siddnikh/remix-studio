"""Objective comparison of an output video against its source (acceptance criteria G1, G2, G4, K1, K2).

Tracking score design. The subject differs between source and output, so whole-frame pixel similarity is
not used. Instead, per frame (resampled to a common 12 fps):

* Layout similarity: low-res luma grid and gradient-magnitude (edge) grid, each z-normalised per frame so
  exposure and colour changes do not matter. Cell differences are *trimmed* (best 60% of cells), so the
  region the subject occupies drops out automatically without assuming where it is. The result is
  normalised against a spatial-roll null (the same output frame shifted by half the grid), so unrelated
  content scores ~0 and an identical frame scores 1.
* Camera motion: dominant background motion (LK features + RANSAC similarity transform; a moving subject
  is rejected as outlier) per frame. Per-second agreement is 1 - |a-b| / (|a|+|b|+floor).
* Cut timing: F1 of cut times within 0.15 s.
* Timing: the layout score is read both at the same timestamp (sync, includes timing errors; frames after
  the output ends score 0) and along a banded DTW path (aligned, ignores timing). The DTW offset curve
  shows drift over time.

tracking = layout/motion per-second values (weights 0.6/0.25) blended with cut F1 (weight 0.15).
Weights and the 60% trim are design choices, not fitted to any clip.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from . import analyze

FPS = 12.0
TRIM = 0.6
BAND_S = 3.0
STEP_PENALTY = 0.05
CUT_TOL_S = 0.15
BEAT_TOL_S = 0.5
W_LAYOUT, W_MOTION, W_CUTS = 0.6, 0.25, 0.15
MOTION_FLOOR = 0.001  # fraction of frame width per frame (~0.4 px at 360 px): below this is sensor noise


# ---------------------------------------------------------------- layout similarity

def _descriptors(grid: np.ndarray) -> np.ndarray:
    """grid (T,H,W,3 Lab) -> (T,4,h,w) float32 cell descriptors at half resolution: luma, edge magnitude,
    chroma a, chroma b. Luma/edge are z-normalised per frame (exposure invariant); chroma is divided by the
    *luma* spread so a near-grey scene carries almost no chroma weight instead of amplified noise."""
    t, h, w = grid.shape[:3]
    size = (w // 2, h // 2)
    out = np.zeros((t, 4, h // 2, w // 2), np.float32)
    for n, g in enumerate(grid):
        gf = cv2.GaussianBlur(g[..., 0].astype(np.float32), (3, 3), 0)
        gx, gy = cv2.Sobel(gf, cv2.CV_32F, 1, 0), cv2.Sobel(gf, cv2.CV_32F, 0, 1)
        out[n, 0] = cv2.resize(gf, size, interpolation=cv2.INTER_AREA)
        out[n, 1] = cv2.resize(np.log1p(np.hypot(gx, gy) / 8.0), size, interpolation=cv2.INTER_AREA)
        out[n, 2] = cv2.resize(g[..., 1].astype(np.float32), size, interpolation=cv2.INTER_AREA)
        out[n, 3] = cv2.resize(g[..., 2].astype(np.float32), size, interpolation=cv2.INTER_AREA)
    flat = out.reshape(t, 4, -1)
    mu = flat.mean(axis=2, keepdims=True)
    sd_l = np.maximum(flat[:, 0].std(axis=1), 2.0)
    sd_e = np.maximum(flat[:, 1].std(axis=1), 0.05)
    scale = np.stack([sd_l, sd_e, sd_l, sd_l], axis=1)[:, :, None]
    return ((flat - mu) / scale).reshape(out.shape).astype(np.float32)


def _trimmed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Mean of the smallest TRIM fraction of per-cell distances (L1 across descriptor channels)."""
    d = np.abs(a - b).sum(axis=1).reshape(len(a), -1)
    k = max(1, int(d.shape[1] * TRIM))
    return np.partition(d, k - 1, axis=1)[:, :k].mean(axis=1)


def _pair_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-row similarity in [0,1] of aligned descriptor stacks a, b (n,4,h,w); roll-null normalised."""
    kd = _trimmed(a, b)
    h, w = b.shape[2:]
    kn = np.mean([_trimmed(a, np.roll(b, s, axis=(2, 3))) for s in ((h // 2, 0), (0, w // 2), (h // 2, w // 2))], axis=0)
    sim = np.clip(1.0 - kd / np.maximum(kn, 1e-6), 0.0, 1.0)
    flat = kn < 1e-3
    sim[flat] = (kd[flat] < 1e-3).astype(float)
    return sim


def layout_band(src_grid: np.ndarray, out_grid: np.ndarray, band: int) -> np.ndarray:
    """sim[k+band, i] = layout similarity of source frame i and output frame i+k; NaN outside both clips."""
    ds, do = _descriptors(src_grid), _descriptors(out_grid)
    n, m = len(src_grid), len(out_grid)
    sim = np.full((2 * band + 1, n), np.nan)
    for k in range(-band, band + 1):
        lo, hi = max(0, -k), min(n, m - k)
        if hi <= lo:
            continue
        sim[k + band, lo:hi] = _pair_sim(ds[lo:hi], do[lo + k:hi + k])
    return sim


def dtw_path(sim: np.ndarray, band: int, n: int, m: int) -> list[tuple[int, int]]:
    """Banded DTW (steps: diagonal, source-advance, output-advance) over cost 1-sim, closed endpoints."""
    inf = np.inf
    cost = 1.0 - sim
    D = np.full((n, 2 * band + 1), inf)
    back = np.zeros((n, 2 * band + 1), np.int8)

    def c(i, k):
        v = cost[k, i]
        return inf if np.isnan(v) else v

    for i in range(n):
        for k in range(2 * band + 1):
            j = i + k - band
            if not 0 <= j < m or np.isnan(cost[k, i]):
                continue
            if i == 0 and j == 0:
                D[i, k] = c(i, k); continue
            best, arg = inf, 0
            if i > 0 and j > 0 and D[i - 1, k] < best:
                best, arg = D[i - 1, k], 0                       # diagonal: (i-1, j-1) has the same lag
            if i > 0 and k + 1 <= 2 * band and D[i - 1, k + 1] + STEP_PENALTY < best:
                best, arg = D[i - 1, k + 1] + STEP_PENALTY, 1    # source advances, output holds
            if j > 0 and k > 0 and D[i, k - 1] + STEP_PENALTY < best:
                best, arg = D[i, k - 1] + STEP_PENALTY, 2        # output advances, source holds
            if best < inf:
                D[i, k] = best + c(i, k)
                back[i, k] = arg
    i, k = n - 1, (m - 1) - (n - 1) + band
    if not 0 <= k <= 2 * band or not np.isfinite(D[i, k]):
        raise ValueError("DTW band too narrow")
    path = []
    while True:
        path.append((i, i + k - band))
        if i == 0 and i + k - band == 0:
            break
        a = back[i, k]
        if a == 0:
            i -= 1
        elif a == 1:
            i, k = i - 1, k + 1
        else:
            k -= 1
    return path[::-1]


# ---------------------------------------------------------------- motion, cuts, beats

def _motion_vec(f: dict, cuts: list[dict]) -> np.ndarray:
    m = analyze.clean_motion(f, cuts)
    return np.stack([m["dx"], m["dy"], m["dlogs"]], axis=1)


def _motion_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a, axis=1).sum(), np.linalg.norm(b, axis=1).sum()
    num = np.linalg.norm(a - b, axis=1).sum()
    return float(np.clip(1.0 - num / (na + nb + MOTION_FLOOR * len(a)), 0.0, 1.0))


def _pearson(a: np.ndarray, b: np.ndarray):
    if a.std() < 1e-9 or b.std() < 1e-9:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def match_times(ref: list[float], out: list[float], tol: float):
    """Maximum monotone matching of two sorted time lists within tol (ties: smaller total error)."""
    n, m = len(ref), len(out)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            opts = [best[i - 1][j], best[i][j - 1]]
            if abs(ref[i - 1] - out[j - 1]) <= tol:
                cnt, err = best[i - 1][j - 1]
                opts.append((cnt + 1, err - abs(ref[i - 1] - out[j - 1])))
            best[i][j] = max(opts, key=lambda o: (o[0], o[1]))
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        cnt, err = best[i][j]
        if (cnt, err) == best[i - 1][j]:
            i -= 1
        elif (cnt, err) == best[i][j - 1]:
            j -= 1
        else:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
    return pairs[::-1]


def _f1(ref: list[float], out: list[float], tol: float):
    if not ref and not out:
        return None
    pairs = match_times(ref, out, tol)
    p = len(pairs) / len(out) if out else 0.0
    r = len(pairs) / len(ref) if ref else 0.0
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _beats(act: np.ndarray, fps: float) -> list[float]:
    """Peaks of the smoothed motion-compensated residual (subject/object action) as beat times."""
    if len(act) < 4:
        return []
    s = analyze._moving_avg(act, max(3, int(round(fps * 0.25))))
    thr = s.mean() + 0.5 * s.std()
    w = max(2, int(round(fps * 0.25)))
    out = []
    for i in range(len(s)):
        win = s[max(0, i - w):i + w + 1]
        if s[i] >= thr and s[i] == win.max() and (not out or i / fps - out[-1] >= 2 * w / fps):
            out.append(i / fps)
    return out


# ---------------------------------------------------------------- tracking score

def tracking(source: str, output: str, fps: float = FPS) -> dict:
    fs = analyze.extract(source, fps=fps)
    gshape = fs["grid"].shape[1:3]
    fo = analyze.extract(output, fps=fps, grid_shape=gshape)
    ns, no = len(fs["luma"]), len(fo["luma"])
    band = int(round(BAND_S * fps))
    band = max(band, abs(ns - no) + int(fps))
    sim = layout_band(fs["grid"], fo["grid"], band)

    tol = 1  # +-1 frame of timing slack for the synchronous read
    sync = np.zeros(ns)
    for i in range(ns):
        vals = [sim[k + band, i] for k in range(-tol, tol + 1) if not np.isnan(sim[k + band, i])]
        sync[i] = max(vals) if vals else 0.0

    path = dtw_path(sim, band, ns, no)
    pi = np.array([p[0] for p in path]); pj = np.array([p[1] for p in path])
    path_sim = np.array([sim[j - i + band, i] for i, j in path])
    aligned = np.array([path_sim[pi == i].mean() for i in range(ns)])
    offset = np.array([(pj[pi == i].mean() - i) / fps for i in range(ns)])

    cuts_12_s, cuts_12_o = analyze.detect_cuts(fs), analyze.detect_cuts(fo)
    cs, co = _native_cuts(source, fs, fps, cuts_12_s), _native_cuts(output, fo, fps, cuts_12_o)
    cut_s, cut_o = [c["t"] for c in cs], [c["t"] for c in co]
    cut_f1 = _f1(cut_s, cut_o, CUT_TOL_S)

    ms, mo = _motion_vec(fs, cuts_12_s), _motion_vec(fo, cuts_12_o)
    mo_pad = np.zeros_like(ms)
    mo_pad[:min(ns, no)] = mo[:min(ns, no)]
    n_sec = int(np.ceil(ns / fps))
    per_sec = []
    for s in range(n_sec):
        a, b = int(s * fps), min(ns, int((s + 1) * fps))
        has_out = a < no
        msim = _motion_sim(ms[a:b], mo_pad[a:b]) if has_out else 0.0
        lay = float(sync[a:b].mean())
        per_sec.append({
            "t": s, "tracking": round((W_LAYOUT * lay + W_MOTION * msim) / (W_LAYOUT + W_MOTION), 4),
            "layout_sync": round(lay, 4), "layout_aligned": round(float(aligned[a:b].mean()), 4),
            "motion": round(msim, 4), "offset_s": round(float(offset[a:b].mean()), 3),
        })
    base = float(np.mean([p["tracking"] for p in per_sec]))
    score = base if cut_f1 is None else (1 - W_CUTS) * base + W_CUTS * cut_f1

    per_t = np.array([p["tracking"] for p in per_sec])
    trend = float(np.polyfit(np.arange(len(per_t)), per_t, 1)[0]) if len(per_t) > 1 else 0.0
    half = len(per_t) // 2
    sec_idx = np.arange(ns) / fps
    slope = float(np.polyfit(sec_idx, offset, 1)[0]) if ns > 2 else 0.0
    valid = ~np.isnan(sim)
    cnt = valid.sum(axis=1)
    lag_mean = np.where(cnt >= max(3, ns // 2), np.where(valid, sim, 0).sum(axis=1) / np.maximum(cnt, 1), -1)
    best_lag = (int(np.argmax(lag_mean)) - band) / fps

    bs, bo = _beats(fs["act"] * _mask_cuts(ns, cuts_12_s), fps), _beats(fo["act"] * _mask_cuts(no, cuts_12_o), fps)
    pairs = match_times(bs, bo, BEAT_TOL_S)
    errs = [bo[j] - bs[i] for i, j in pairs]
    act_s, act_o = analyze._moving_avg(fs["act"], 3), analyze._moving_avg(fo["act"], 3)
    n_c = min(ns, no)
    dur_s, dur_o = fs["info"]["duration"], fo["info"]["duration"]
    return {
        "source": str(source), "output": str(output), "fps": fps,
        "tracking": round(score, 4),
        "components": {
            "layout_sync": round(float(sync.mean()), 4), "layout_aligned": round(float(aligned.mean()), 4),
            "motion": round(float(np.mean([p["motion"] for p in per_sec])), 4),
            "motion_corr": _r(_pearson(ms[:n_c, :2].ravel(), mo[:n_c, :2].ravel())),
            "cut_f1": _r(cut_f1),
        },
        "timing": {
            "best_constant_offset_s": round(best_lag, 3), "mean_dtw_offset_s": round(float(offset.mean()), 3),
            "drift_s_per_s": round(slope, 4), "final_offset_s": round(float(offset[-1]), 3),
        },
        "G1_tracking": round(score, 4),
        "trend": {"per_second_slope": round(trend, 4), "first_half": round(float(per_t[:max(1, half)].mean()), 4),
                  "second_half": round(float(per_t[half:].mean()), 4)},
        "G2_beats": {
            "source_beats": [round(t, 2) for t in bs], "output_beats": [round(t, 2) for t in bo],
            "matched": len(pairs), "recall": round(len(pairs) / len(bs), 3) if bs else None,
            "mean_abs_err_s": round(float(np.mean(np.abs(errs))), 3) if errs else None,
            "mean_signed_err_s": round(float(np.mean(errs)), 3) if errs else None,
            "activity_corr": _r(_pearson(act_s[:n_c], act_o[:n_c])),
        },
        "G4_duration": {"source_s": dur_s, "output_s": dur_o, "delta_s": round(dur_o - dur_s, 3),
                        "pass": bool(abs(dur_o - dur_s) <= 0.1)},
        "cuts": {"source": cut_s, "output": cut_o},
        "per_second": per_sec,
    }


def _native_cuts(path: str, feat: dict, fps: float, cuts_at_fps: list[dict]) -> list[dict]:
    """One-frame inserts need the native frame rate; reuse the resampled pass when it already is native."""
    if abs(feat["info"]["fps"] - fps) < 1e-6:
        return cuts_at_fps
    return analyze.detect_cuts(analyze.extract(path))


def _mask_cuts(n: int, cuts: list[dict]) -> np.ndarray:
    m = np.ones(n)
    for c in cuts:
        m[c["frame"]] = 0.0
    return m


def _r(x):
    return None if x is None else round(float(x), 4)


def table(res: dict) -> str:
    lines = [f"{'sec':>3} {'track':>6} {'layout':>6} {'aligned':>7} {'motion':>6} {'offset_s':>8}"]
    for p in res["per_second"]:
        lines.append(f"{p['t']:>3} {p['tracking']:>6.2f} {p['layout_sync']:>6.2f} {p['layout_aligned']:>7.2f} "
                     f"{p['motion']:>6.2f} {p['offset_s']:>+8.2f}")
    c, t, g2, g4 = res["components"], res["timing"], res["G2_beats"], res["G4_duration"]
    lines += [
        f"TRACKING {res['tracking']:.3f}  (layout_sync {c['layout_sync']}, aligned {c['layout_aligned']}, "
        f"motion {c['motion']}, cut_f1 {c['cut_f1']})",
        f"trend: first half {res['trend']['first_half']:.2f}, second half {res['trend']['second_half']:.2f}, "
        f"slope {res['trend']['per_second_slope']:+.3f}/s",
        f"timing: best offset {t['best_constant_offset_s']:+.2f}s, drift {t['drift_s_per_s']:+.3f} s/s, "
        f"final offset {t['final_offset_s']:+.2f}s",
        f"G2 beats: matched {g2['matched']}/{len(g2['source_beats'])}, mean abs err {g2['mean_abs_err_s']}, "
        f"signed {g2['mean_signed_err_s']}",
        f"G4 duration: {g4['output_s']}s vs {g4['source_s']}s, delta {g4['delta_s']:+.2f}s -> {'PASS' if g4['pass'] else 'FAIL'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- G gates against a baseline

def gate(res: dict, baseline: dict, g2_slack: float = 0.0) -> dict:
    """G1: tracking >= baseline. G2: beat recall >= baseline and abs timing error no worse. G4: within 0.1 s."""
    b2, r2 = baseline["G2_beats"], res["G2_beats"]
    g1 = res["tracking"] >= baseline["tracking"]
    rec_ok = (r2["recall"] or 0) >= (b2["recall"] or 0)
    err_ok = r2["mean_abs_err_s"] is None or b2["mean_abs_err_s"] is None or r2["mean_abs_err_s"] <= b2["mean_abs_err_s"] + g2_slack
    return {"G1": bool(g1), "G2": bool(rec_ok and err_ok), "G4": res["G4_duration"]["pass"]}


# ---------------------------------------------------------------- K1 / K2

def k1_check(video: str, target_duration: float | None = None, tol_s: float = 0.1) -> dict:
    """Finished-edit checks: duration, audio, black frames, frozen runs, repeated placement."""
    f = analyze.extract(video)
    info, fps = f["info"], f["fps"]
    black = [i for i, l in enumerate(f["luma"]) if l < 16]
    black_runs = analyze._runs(np.isin(np.arange(len(f["luma"])), black), min_len=2)
    still = (f["pix_d"] < 0.0015) & (f["hist_d"] < 0.002)
    still[0] = False
    frozen = [(a, b) for a, b in analyze._runs(still, max_gap=1, min_len=int(0.5 * fps))]
    cuts = analyze.detect_cuts(f)
    bounds = [0] + [c["frame"] for c in cuts if c["kind"] != "insert_in"] + [len(f["luma"])]
    shots = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= 3]
    repeats = []
    if len(shots) > 1:
        ds = _descriptors(f["grid"])
        mids = [(a + b) // 2 for a, b in shots]
        for x in range(len(shots)):
            for y in range(x + 1, len(shots)):
                s = float(_pair_sim(ds[mids[x]:mids[x] + 1], ds[mids[y]:mids[y] + 1])[0])
                if s >= 0.85:
                    repeats.append({"shots": [x, y], "t": [round(mids[x] / fps, 2), round(mids[y] / fps, 2)], "similarity": round(s, 3)})
    dur_ok = target_duration is None or abs(info["duration"] - target_duration) <= tol_s
    res = {
        "duration_s": info["duration"], "duration_ok": bool(dur_ok), "has_audio": info["has_audio"],
        "black_frames": [{"t": round(float(a) / fps, 2), "frames": int(b - a + 1)} for a, b in black_runs],
        "frozen_runs": [{"t": round(float(a) / fps, 2), "duration": round(float(b - a + 1) / fps, 2)} for a, b in frozen],
        "repeated_shots": repeats,
    }
    res["pass"] = bool(dur_ok and info["has_audio"] and not black_runs and not frozen and not repeats)
    return res


def k2_compare(reference: str, output: str, tol: float = 0.25) -> dict:
    """Cut, flash and shake rates within 25% of the reference (or within one event when the reference is ~empty)."""
    ref, out = analyze.energy(reference), analyze.energy(output)
    res = {}
    for key, ek in (("cuts_per_s", "cuts"), ("flashes_per_s", "flashes"), ("shake_bursts_per_s", "shake_bursts")):
        r, o = ref["rates"][key], out["rates"][key]
        allowed = max(tol * r, 1.0 / out["info"]["duration"] if r == 0 else 0.0)
        res[key] = {"reference": r, "output": o, "ratio": round(o / r, 3) if r else None, "pass": bool(abs(o - r) <= allowed)}
    res["pass"] = all(v["pass"] for v in res.values())
    return res


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="score", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("track", help="G1/G2/G4 tracking comparison")
    p.add_argument("source"); p.add_argument("output"); p.add_argument("-o", "--json")
    p.add_argument("--baseline", help="baselines.json to gate against"); p.add_argument("--name", help="baseline entry name")
    p = sub.add_parser("finish", help="K1 finished-edit checks"); p.add_argument("video"); p.add_argument("--target-duration", type=float)
    p = sub.add_parser("language", help="K2 rate comparison"); p.add_argument("reference"); p.add_argument("output")
    a = ap.parse_args(argv)
    if a.cmd == "track":
        res = tracking(a.source, a.output)
        print(table(res))
        if a.baseline:
            res["gate"] = gate(res, json.loads(Path(a.baseline).read_text())[a.name])
            print("gate vs", a.name, res["gate"])
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(res, indent=1))
    elif a.cmd == "finish":
        res = k1_check(a.video, a.target_duration); print(json.dumps(res, indent=1))
    else:
        res = k2_compare(a.reference, a.output); print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
