"""Tests for studio.analyze on small synthetic clips with known cuts, flashes, shake and audio hits."""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest

from studio import analyze

W, H, FPS = 270, 480, 24


def _background(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = cv2.resize(rng.integers(30, 220, (6, 10, 3)).astype(np.uint8), (W + 200, H + 200), interpolation=cv2.INTER_CUBIC)
    for _ in range(14):
        x, y = int(rng.integers(0, W + 100)), int(rng.integers(0, H + 100))
        w, h = int(rng.integers(20, 120)), int(rng.integers(20, 160))
        cv2.rectangle(base, (x, y), (x + w, y + h), tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    for _ in range(8):
        p, q = rng.integers(0, [W + 200, H + 200], 2), rng.integers(0, [W + 200, H + 200], 2)
        cv2.line(base, tuple(int(v) for v in p), tuple(int(v) for v in q), (255, 255, 255), 3)
    return base


def render_frames(duration=4.0, cuts=(1.0, 2.5), seeds=(1, 2, 3), pan=30.0, subject=(40, 200, 40), subject_radius=45,
                  flashes=(), shake=0.0, inserts=None, time_map=lambda t: t, jitter_seed=0) -> list[np.ndarray]:
    """Frames of a procedurally generated edit. `time_map` maps output time to scene time, so time shifts,
    speed changes and truncation are rendered rather than approximated."""
    bgs = [_background(s) for s in seeds]
    rng = np.random.default_rng(jitter_seed)
    frames = []
    for n in range(int(round(duration * FPS))):
        t = time_map(n / FPS)
        shot = sum(t >= c for c in cuts)
        start = ([0.0] + list(cuts))[shot]
        bg = bgs[shot % len(bgs)]
        if inserts and n in inserts:
            bg = _background(inserts[n])
        ox = int(np.clip(50 + pan * (t - start), 0, 190))
        oy = int(np.clip(60 + 0.4 * pan * np.sin(t * 1.5), 0, 190))
        if shake:
            ox = int(np.clip(ox + rng.normal(0, shake) * (1 if 0.3 < (n / FPS) % 2 < 0.8 else 0), 0, 199))
            oy = int(np.clip(oy + rng.normal(0, shake) * (1 if 0.3 < (n / FPS) % 2 < 0.8 else 0), 0, 199))
        fr = bg[oy:oy + H, ox:ox + W].copy()
        cx = int(W / 2 + 40 * np.sin(t * 2.0))
        cv2.circle(fr, (cx, int(H * 0.55)), subject_radius, subject, -1)
        if n in flashes:
            fr = np.full_like(fr, 250)
        frames.append(fr)
    return frames


def write_video(path: Path, frames: list[np.ndarray], audio_hits=None, audio=False) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    wav = None
    if audio or audio_hits is not None:
        wav = path.with_suffix(".wav")
        dur = len(frames) / FPS
        sr = 16000
        x = np.random.default_rng(0).normal(0, 0.002, int(dur * sr))
        for h in audio_hits or []:
            i = int(h * sr)
            n = min(480, len(x) - i)
            x[i:i + n] += 0.8 * np.sin(2 * np.pi * 1000 * np.arange(n) / sr) * np.exp(-np.arange(n) / 120)
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
        cmd += ["-i", str(wav)]
    cmd += ["-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-g", "24"]
    if wav:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, input=b"".join(f.tobytes() for f in frames), check=True)
    return path


def make_video(path: Path, audio_hits=None, audio=False, **kw) -> Path:
    return write_video(path, render_frames(**kw), audio_hits=audio_hits, audio=audio)


@pytest.fixture(scope="session")
def base_clip(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("an") / "base.mp4")


def test_probe_reports_exact_properties(tmp_path):
    p = make_video(tmp_path / "a.mp4", audio=True)
    info = analyze.probe(p)
    assert (info["width"], info["height"]) == (W, H)
    assert info["fps_rational"] == "24/1" and info["fps"] == 24.0
    assert info["frames"] == 96
    assert info["duration"] == pytest.approx(4.0, abs=0.05)
    assert info["has_audio"] is True


def test_probe_no_audio(base_clip):
    assert analyze.probe(base_clip)["has_audio"] is False


def test_probe_rejects_non_video(tmp_path):
    (tmp_path / "x.txt").write_text("nope")
    with pytest.raises(Exception):
        analyze.probe(tmp_path / "x.txt")


def test_cuts_found_at_known_times(base_clip):
    res = analyze.cuts_cmd(base_clip)
    assert len(res["cuts"]) == 2
    for got, want in zip(res["cut_times"], (1.0, 2.5)):
        assert got == pytest.approx(want, abs=1 / FPS + 1e-6)


def test_no_false_cuts_on_continuous_pan(tmp_path):
    p = make_video(tmp_path / "pan.mp4", cuts=(), pan=60.0, shake=0)
    assert analyze.cuts_cmd(p)["cuts"] == []


def test_one_frame_insert_detected(tmp_path):
    p = make_video(tmp_path / "ins.mp4", cuts=(), inserts={40: 99})
    res = analyze.cuts_cmd(p)
    kinds = {c["frame"]: c["kind"] for c in res["cuts"]}
    assert kinds == {40: "insert_in", 41: "insert_out"}


def test_energy_json_shape_and_rates(tmp_path):
    p = make_video(tmp_path / "e.mp4", flashes=(30, 70), audio_hits=[0.5, 1.5, 3.2])
    out = tmp_path / "energy.json"
    assert analyze.main(["energy", str(p), "-o", str(out)]) == 0
    e = json.loads(out.read_text())
    assert set(e["per_second"]) == {"cuts", "flashes", "shake_bursts", "camera_motion", "audio_hits"}
    assert all(len(v) == 4 for v in e["per_second"].values())
    assert e["rates"]["cuts_per_s"] == pytest.approx(0.5)
    assert [round(f["t"]) for f in e["events"]["flashes"]] == [1, 3]
    hits = e["events"]["audio_hits"]
    assert len(hits) == 3 and all(abs(h - w) < 0.06 for h, w in zip(hits, [0.5, 1.5, 3.2]))
    assert len(out.read_text()) < 6000


def test_shake_bursts_detected_only_when_shaking(tmp_path):
    calm = analyze.energy(make_video(tmp_path / "c.mp4", cuts=(), pan=5.0))
    shaky = analyze.energy(make_video(tmp_path / "s.mp4", cuts=(), pan=5.0, shake=6.0))
    assert calm["events"]["shake_bursts"] == []
    assert len(shaky["events"]["shake_bursts"]) >= 1


def test_camera_motion_tracks_pan_speed(tmp_path):
    slow = analyze.energy(make_video(tmp_path / "slow.mp4", cuts=(), pan=5.0))
    fast = analyze.energy(make_video(tmp_path / "fast.mp4", cuts=(), pan=45.0))
    assert fast["rates"]["camera_motion_mean"] > 3 * max(slow["rates"]["camera_motion_mean"], 0.01)


def test_energy_without_audio_has_no_hits(base_clip):
    assert analyze.energy(base_clip)["events"]["audio_hits"] == []


def test_sheet_labels_and_size(base_clip, tmp_path):
    out = tmp_path / "sheet.png"
    res = analyze.sheet(base_clip, out, fps=2, tile_width=100, cols=4)
    assert res["tiles"] == 8
    img = cv2.imread(str(out))
    assert img.shape[1] == 400 and img.shape[0] == 2 * (round(100 * H / W / 2) * 2)


def test_sheet_caps_frame_count(base_clip, tmp_path):
    res = analyze.sheet(base_clip, tmp_path / "s.png", fps=24, max_frames=15)
    assert res["tiles"] <= 15


def test_beats_finds_period_and_phase_of_a_click_track(tmp_path):
    rate = 8000
    x = np.zeros(rate * 10, np.float32)
    for k in range(20):
        at = int((0.13 + k * 0.5) * rate)
        x[at:at + 200] = 0.8
    wav = tmp_path / "c.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-", str(wav)], input=x.tobytes(), check=True)
    res = analyze.beats(wav)
    assert res["period_s"] == 0.5 and abs(res["first_beat_s"] - 0.13) <= 0.02


def test_beats_refuses_a_file_without_audio(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:r=24", "-frames:v", "4", str(v)], check=True)
    with pytest.raises(ValueError, match="no audio"):
        analyze.beats(v)
