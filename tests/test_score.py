"""Tests for studio.score: identity, unrelated content, synthetic transforms, real-clip sanity checks."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio import score
from test_analyze import FPS, make_video

SCRATCH = Path("/private/tmp/claude-501/-Users-siddnikh-Development-automations-rea/3397b306-c87c-4176-b3de-7e4b6515ebdf/scratchpad/tests")
BASELINES = Path(__file__).resolve().parents[1] / "docs" / "baselines.json"


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("sc") / "src.mp4", subject=(40, 200, 40))


@pytest.fixture(scope="module")
def variants(tmp_path_factory, src):
    d = tmp_path_factory.mktemp("var")
    return {
        "other_subject": make_video(d / "subj.mp4", subject=(200, 40, 160), subject_radius=60),
        "unrelated": make_video(d / "unrel.mp4", seeds=(11, 12, 13), cuts=(1.4, 2.2)),
        "late": make_video(d / "late.mp4", time_map=lambda t: max(0.0, t - 0.5)),
        "fast": make_video(d / "fast.mp4", duration=3.2, time_map=lambda t: t * 1.25),
        "cropped": make_video(d / "crop.mp4", subject=(40, 200, 40)),
        "second_half_wrong": make_video(d / "half.mp4", seeds=(1, 2, 3), cuts=(1.0, 2.5), time_map=lambda t: t if t < 2.0 else 99.0),
    }


def test_self_score_is_near_perfect(src):
    r = score.tracking(src, src)
    assert r["tracking"] > 0.99
    assert r["timing"]["best_constant_offset_s"] == 0
    assert r["G4_duration"]["pass"]


def test_ffmpeg_test_pattern_scores_low(src, tmp_path):
    tp = tmp_path / "tp.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=270x480:rate=24:duration=4",
                    "-pix_fmt", "yuv420p", str(tp)], check=True)
    assert score.tracking(src, tp)["tracking"] < 0.25


def test_unrelated_scene_scores_far_below_replaced_subject(src, variants):
    unrelated = score.tracking(src, variants["unrelated"])["tracking"]
    swapped = score.tracking(src, variants["other_subject"])["tracking"]
    assert swapped > 0.85
    assert unrelated < 0.45
    assert swapped - unrelated > 0.4


def test_subject_swap_beats_unrelated_per_second_everywhere(src, variants):
    a = score.tracking(src, variants["other_subject"])["per_second"]
    b = score.tracking(src, variants["unrelated"])["per_second"]
    assert all(x["tracking"] > y["tracking"] for x, y in zip(a, b))


def test_time_shift_is_measured_not_hidden(src, variants):
    r = score.tracking(src, variants["late"])
    assert r["timing"]["best_constant_offset_s"] == pytest.approx(0.5, abs=0.1)
    assert r["components"]["layout_aligned"] > r["components"]["layout_sync"] + 0.1
    assert r["tracking"] < score.tracking(src, src)["tracking"]
    assert max(abs(p["offset_s"]) for p in r["per_second"]) > 0.25


def test_speed_change_shows_drift_and_duration_failure(src, variants):
    r = score.tracking(src, variants["fast"])
    assert r["timing"]["drift_s_per_s"] == pytest.approx(-0.2, abs=0.07)
    assert r["G4_duration"]["pass"] is False
    assert r["G4_duration"]["delta_s"] == pytest.approx(-0.8, abs=0.05)
    assert r["per_second"][-1]["layout_sync"] < r["per_second"][0]["layout_sync"]


def test_output_ending_early_scores_zero_for_missing_tail(src, tmp_path):
    short = make_video(tmp_path / "short.mp4", duration=2.0)
    r = score.tracking(src, short)
    assert r["per_second"][-1]["layout_sync"] == 0.0 and r["per_second"][-1]["motion"] == 0.0
    assert r["per_second"][0]["layout_sync"] > 0.9


def test_decline_over_time_is_visible(src, variants):
    r = score.tracking(src, variants["second_half_wrong"])
    ps = [p["tracking"] for p in r["per_second"]]
    assert ps[0] > 0.9 and ps[-1] < 0.5
    assert r["trend"]["second_half"] < r["trend"]["first_half"] - 0.3
    assert r["trend"]["per_second_slope"] < -0.1


def test_zoomed_crop_still_tracks_better_than_unrelated(src, tmp_path, variants):
    import cv2
    from test_analyze import render_frames, write_video
    frames = render_frames()
    h, w = frames[0].shape[:2]
    m = int(0.075 * w), int(0.075 * h)
    zoomed = [cv2.resize(f[m[1]:h - m[1], m[0]:w - m[0]], (w, h)) for f in frames]
    c = write_video(tmp_path / "zoom.mp4", zoomed)
    cropped = score.tracking(src, c)["tracking"]
    assert cropped > 0.6
    assert cropped > score.tracking(src, variants["unrelated"])["tracking"] + 0.2


def test_cut_timing_f1(src, tmp_path):
    missing = make_video(tmp_path / "miss.mp4", cuts=(1.0,))
    r = score.tracking(src, missing)
    assert r["components"]["cut_f1"] == pytest.approx(2 / 3, abs=0.01)
    assert r["cuts"]["source"] == pytest.approx([1.0, 2.5], abs=0.05)
    assert score.tracking(src, src)["components"]["cut_f1"] == 1.0


def test_static_scene_identity_not_penalised(tmp_path):
    p = make_video(tmp_path / "static.mp4", cuts=(), pan=0.0)
    q = make_video(tmp_path / "static2.mp4", cuts=(), pan=0.0, subject=(0, 0, 255))
    r = score.tracking(p, q)
    assert r["components"]["cut_f1"] is None
    assert r["tracking"] > 0.85


def test_dtw_identity_and_stretch():
    n = 30
    sim = np.full((2 * 6 + 1, n), np.nan)
    for i in range(n):
        sim[6, i] = 1.0
    path = score.dtw_path(sim, 6, n, n)
    assert path == [(i, i) for i in range(n)]


def test_match_times_is_monotone_and_tolerant():
    pairs = score.match_times([1.0, 2.0, 3.0], [1.1, 3.05, 2.0], 0.2)
    assert pairs == [(0, 0), (2, 1)] or pairs == [(0, 0), (1, 2)]
    assert score.match_times([], [1.0], 0.2) == []


def test_gate_against_baseline():
    base = {"tracking": 0.6, "G2_beats": {"recall": 0.7, "mean_abs_err_s": 0.2}}
    ok = {"tracking": 0.65, "G2_beats": {"recall": 0.8, "mean_abs_err_s": 0.1}, "G4_duration": {"pass": True}}
    bad = {"tracking": 0.5, "G2_beats": {"recall": 0.5, "mean_abs_err_s": 0.3}, "G4_duration": {"pass": False}}
    assert score.gate(ok, base) == {"G1": True, "G2": True, "G4": True}
    assert score.gate(bad, base) == {"G1": False, "G2": False, "G4": False}


def test_k1_flags_black_frozen_and_missing_audio(tmp_path):
    import cv2
    from test_analyze import render_frames, write_video
    frames = render_frames(cuts=(), pan=20.0)
    frames[30:36] = [np.zeros_like(frames[0])] * 6
    frames[50:70] = [frames[50]] * 20
    k = score.k1_check(write_video(tmp_path / "bad.mp4", frames), target_duration=4.0)
    assert not k["pass"] and not k["has_audio"]
    assert k["black_frames"] and k["frozen_runs"]
    good = score.k1_check(make_video(tmp_path / "good.mp4", cuts=(), pan=20.0, audio=True), target_duration=4.0)
    assert good["pass"], good


def test_k1_flags_repeated_shot(tmp_path):
    p = make_video(tmp_path / "rep.mp4", cuts=(1.3, 2.6), seeds=(5, 6, 5), pan=0.0, audio=True)
    k = score.k1_check(p)
    assert any(r["shots"] == [0, 2] for r in k["repeated_shots"])
    p2 = make_video(tmp_path / "norep.mp4", cuts=(1.3, 2.6), seeds=(5, 6, 7), pan=0.0, audio=True)
    assert score.k1_check(p2)["repeated_shots"] == []


def test_k2_rates_within_tolerance(src, tmp_path):
    same = score.k2_compare(src, src)
    assert same["pass"]
    busy = make_video(tmp_path / "busy.mp4", cuts=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0), seeds=(1, 2, 3, 4))
    assert not score.k2_compare(src, busy)["cuts_per_s"]["pass"]


def test_cli_writes_json_and_table(src, tmp_path, capsys):
    out = tmp_path / "r.json"
    assert score.main(["track", str(src), str(src), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "TRACKING" in printed and "G4 duration" in printed
    data = json.loads(out.read_text())
    assert len(data["per_second"]) == 4


real = pytest.mark.skipif(not (SCRATCH / "input/drive_4s.mp4").exists(), reason="real clips not available")


@real
def test_real_clips_sanity():
    s = SCRATCH / "input/drive_4s.mp4"
    res = {n: score.tracking(s, SCRATCH / f"output/{n}.mp4") for n in ("A_genjutsu_motion", "B_seedance", "C_genjutsu_replace")}
    assert res["C_genjutsu_replace"]["tracking"] > res["B_seedance"]["tracking"]
    assert res["C_genjutsu_replace"]["G4_duration"]["pass"] is False
    b = res["B_seedance"]["per_second"]
    assert b[0]["tracking"] - b[-1]["tracking"] > 0.25
    assert res["C_genjutsu_replace"]["timing"]["drift_s_per_s"] < -0.04


@real
def test_real_baselines_file_matches_current_scorer():
    if not BASELINES.exists():
        pytest.skip("baselines not generated")
    saved = json.loads(BASELINES.read_text())
    s = SCRATCH / "input/drive_4s.mp4"
    r = score.tracking(s, SCRATCH / "output/B_seedance.mp4")
    assert saved["B"]["tracking"] == pytest.approx(r["tracking"], abs=0.005)
