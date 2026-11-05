import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio import edl as E
from studio import fx

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skills" / "remix" / "assets"
FONT = str(ASSETS / "fonts" / "Anton-Regular.ttf")
HIT = ASSETS / "sfx" / "hit_owlish.wav"
BITS = 10
W, H = 96, 160


def coded_frame(i: int) -> np.ndarray:
    """Frame i: its index in binary as blocks along the top, over a texture that moves every frame."""
    yy, xx = np.mgrid[0:H, 0:W]
    img = (128 + 60 * np.sin((xx + 3 * i) / 5.0) * np.cos((yy + 2 * i) / 7.0)).astype(np.uint8)
    for b in range(BITS):
        img[:16, b * 8 + 8:b * 8 + 16] = 255 if (i >> b) & 1 else 0
    return img


def encode_gray(path: Path, frames, fps=24, w=W, h=H) -> Path:
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "6", "-g", "10", "-pix_fmt", "yuv420p", str(path)],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    assert p.returncode == 0, p.stderr.read()
    return path


@pytest.fixture(scope="module")
def coded(tmp_path_factory) -> Path:
    return encode_gray(tmp_path_factory.mktemp("src") / "coded.mp4", (coded_frame(i) for i in range(200)))


def decode_gray(path: Path, w=W, h=H) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-fps_mode", "passthrough", "-vf", "format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, h, w)


def read_codes(frames: np.ndarray) -> list[int]:
    out = []
    for f in frames:
        bits = [f[2:14, b * 8 + 9:b * 8 + 15].mean() > 128 for b in range(BITS)]
        out.append(sum(1 << b for b, v in enumerate(bits) if v))
    return out


def base_edl(src: Path, shots, frames=None, **extra) -> dict:
    total = frames if frames is not None else sum(s.get("frames", 0) for s in shots)
    d = {"version": 1, "canvas": {"w": W, "h": H, "fps": 24, "frames": total}, "sources": {"a": str(src)}, "shots": shots,
         "audio": {"none_reason": "test"}}
    d.update(extra)
    return d


def write(tmp: Path, edl: dict) -> Path:
    p = tmp / "edit.json"
    p.write_text(json.dumps(edl))
    return p


def render_ok(tmp: Path, edl: dict, name="out.mp4", **kw) -> tuple[Path, dict]:
    out = tmp / name
    rep = E.render(write(tmp, edl), out, **kw)
    assert rep["ok"], rep["errors"]
    return out, rep


def problems(edl: dict) -> list[str]:
    plan = E.validate(edl, Path("/"), strict=False)
    return [str(p) for p in plan.problems if p.level == "error"]


# ---------------------------------------------------------------- frame exactness

def test_in_points_are_frame_exact_including_between_keyframes(coded, tmp_path):
    shots = [{"id": "a", "source": "a", "in": "f17", "frames": 12}, {"id": "b", "source": "a", "in": "f53", "out": "f65"},
             {"id": "c", "source": "a", "in": "f101", "frames": 7}]
    out, rep = render_ok(tmp_path, base_edl(coded, shots, frames=31))
    expected = list(range(17, 29)) + list(range(53, 65)) + list(range(101, 108))
    assert read_codes(decode_gray(out)) == expected
    assert rep["measured"]["frames"] == 31
    assert abs(rep["measured"]["video_duration"] - 31 / 24) < 1e-3
    assert rep["uses"][1]["src"] == [53, 65]


def test_seconds_snap_to_the_same_frame_as_frame_numbers(coded, tmp_path):
    out, _ = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": 2.5, "out": 3.0}], frames=12))
    assert read_codes(decode_gray(out)) == list(range(60, 72))


def test_speed_up_skips_exactly_and_slow_down_holds(coded, tmp_path):
    out, _ = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": "f40", "frames": 8, "speed": 2.0}]))
    assert read_codes(decode_gray(out)) == list(range(40, 56, 2))
    out, rep = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": "f40", "frames": 8, "speed": 0.25, "slowmo": "nearest"}]), name="slow.mp4")
    got = read_codes(decode_gray(out))
    assert got == sorted(got) and got[0] == 40 and got[-1] in (41, 42)
    assert rep["measured"]["frames"] == 8


def test_output_rate_differs_from_source(coded, tmp_path):
    d = base_edl(coded, [{"source": "a", "in": "f24", "frames": 30}])
    d["canvas"]["fps"] = 30
    out, rep = render_ok(tmp_path, d)
    got = read_codes(decode_gray(out))
    assert len(got) == 30 and got[0] == 24
    assert all(0 <= b - a <= 1 for a, b in zip(got, got[1:]))
    assert abs(rep["measured"]["video_duration"] - 1.0) < 1e-3


# ---------------------------------------------------------------- validation

def test_timeline_must_sum_to_the_target(coded):
    msgs = problems(base_edl(coded, [{"source": "a", "in": "f0", "frames": 10}], frames=12))
    assert any("short by 2" in m for m in msgs)
    msgs = problems(base_edl(coded, [{"source": "a", "in": "f0", "frames": 13}], frames=12))
    assert any("over by 1" in m for m in msgs)


def test_same_source_frames_placed_twice_is_refused_unless_marked(coded):
    shots = [{"id": "a", "source": "a", "in": "f10", "frames": 10}, {"id": "b", "source": "a", "in": "f15", "frames": 10}]
    assert any("already used by shot a" in m and "15..19" in m for m in problems(base_edl(coded, shots)))
    shots[1]["repeat"] = True
    assert problems(base_edl(coded, shots)) == []


def test_speed_counts_when_measuring_overlap(coded):
    shots = [{"id": "a", "source": "a", "in": "f0", "frames": 10, "speed": 2.0}, {"id": "b", "source": "a", "in": "f18", "frames": 5}]
    assert any("already used" in m for m in problems(base_edl(coded, shots)))


def test_clip_shorter_than_promised_is_caught_with_its_real_length(coded):
    msgs = problems(base_edl(coded, [{"source": "a", "in": "f190", "frames": 20}]))
    assert any("has only 200 frames" in m for m in msgs)


def test_bad_ranges_from_the_logic_review_are_refused(coded):
    d = base_edl(coded, [{"source": "a", "in": "f10", "frames": 10}])
    d["sources"]["a"] = {"path": str(coded), "bad_ranges": [["f15", "f30"]]}
    assert any("rejected range" in m for m in problems(d))


def test_unknown_keys_and_every_problem_reported_together(coded):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 5, "sahke": 1}, {"source": "missing", "frames": 5}], frames=99)
    msgs = problems(d)
    assert any("sahke" in m for m in msgs) and any("unknown source" in m for m in msgs) and any("timeline" in m for m in msgs)


def test_inconsistent_speed_is_refused(coded):
    d = base_edl(coded, [{"source": "a", "in": "f0", "out": "f20", "frames": 10, "speed": 1.0}])
    assert any("imply speed 2.000" in m for m in problems(d))


def test_missing_source_file(tmp_path, coded):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 5}])
    d["sources"]["a"] = str(tmp_path / "nope.mp4")
    assert any("file not found" in m for m in problems(d))


def test_text_needs_an_existing_font_with_every_glyph_and_must_fit(coded):
    shots = [{"source": "a", "in": "f0", "frames": 10}]
    assert any("font file is required" in m for m in problems(base_edl(coded, shots, text=[{"text": "HI"}])))
    assert any("font not found" in m for m in problems(base_edl(coded, shots, text=[{"text": "HI", "font": "/no/such.ttf"}])))
    assert any("no glyph" in m and "字" in m for m in problems(base_edl(coded, shots, text=[{"text": "HI字", "font": FONT}])))
    assert any("clipped" in m for m in problems(base_edl(coded, shots, text=[{"text": "WAY TOO MANY WORDS FOR THIS", "font": FONT, "size": 0.2}])))
    assert problems(base_edl(coded, shots, text=[{"text": "HI", "font": FONT, "size": 0.1}])) == []


def test_audio_must_be_declared_and_every_recording_needs_provenance(coded, tmp_path):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 10}])
    del d["audio"]
    assert any("none_reason" in m for m in problems(d))
    d["audio"] = {"assets": {"h": {"path": str(HIT)}}, "hits": [{"asset": "h", "at": 0}]}
    assert any("provenance" in m for m in problems(d))
    d["audio"]["assets"]["h"]["provenance"] = "CC0"
    assert problems(d) == []


def test_bed_shorter_than_the_edit_is_refused(coded):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 48}])
    d["audio"] = {"assets": {"h": {"path": str(HIT), "provenance": "CC0"}}, "bed": {"asset": "h"}}
    assert any("edit needs it to run" in m for m in problems(d))


def test_transition_on_first_shot_and_oversized_transition_are_refused(coded):
    shots = [{"id": "a", "source": "a", "in": "f0", "frames": 2, "transition_in": {"type": "whip", "frames": 6}},
             {"id": "b", "source": "a", "in": "f10", "frames": 10, "transition_in": {"type": "whip", "frames": 8}}]
    msgs = problems(base_edl(coded, shots))
    assert any("first shot" in m for m in msgs) and any("do not fit" in m for m in msgs)


# ---------------------------------------------------------------- rendered effects

def test_white_flash_blows_out_only_its_frames_and_passes_the_white_check(coded, tmp_path):
    shots = [{"source": "a", "in": "f0", "frames": 24, "flashes": [{"at": "f10", "frames": 3}]}]
    out, rep = render_ok(tmp_path, base_edl(coded, shots))
    means = decode_gray(out).reshape(24, -1).mean(axis=1)
    assert means[10] > 230 and means[11] > means[9] + 20 and means[13] < means[10] - 60
    assert abs(means[9] - means[14]) < 12


def test_coloured_flash_tints_without_bleaching(coded, tmp_path):
    shots = [{"source": "a", "in": "f0", "frames": 12, "flashes": [{"at": "f4", "frames": 2, "color": "#ff00aa", "strength": 0.8}]}]
    out, _ = render_ok(tmp_path, base_edl(coded, shots))
    rgb = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "select=eq(n\\,4),format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    px = np.frombuffer(rgb, np.uint8).reshape(H, W, 3)
    assert px[..., 0].mean() > px[..., 1].mean() + 80


def test_shake_moves_only_inside_its_burst_and_never_shows_an_edge(coded, tmp_path):
    shake = {"camera": {"shake": [{"at": "f6", "frames": 12, "amp": 0.03, "hz": 8, "rot": 1.0}]}}
    plain, _ = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": "f0", "frames": 24}]), name="plain.mp4")
    shook, rep = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": "f0", "frames": 24, **shake}]), name="shook.mp4")
    assert rep["measured"]["frames"] == 24
    a, b = decode_gray(plain).astype(int), decode_gray(shook).astype(int)
    # Shake needs headroom, so the whole shot is zoomed slightly; compare motion rather than pixels.
    centre = lambda f: f[40:120, 20:76]
    shifted = [np.abs(centre(a[i]) - centre(b[i])).mean() for i in range(24)]
    assert max(shifted[6:12]) > min(shifted[:6]) + 2
    cam = fx.Camera(bursts=(fx.Burst(0, 10, 0.03, rot=1.0),))
    assert fx.camera_headroom(cam, W, H) > 1.03


def test_push_changes_scale_smoothly(tmp_path):
    frames = []
    for i in range(48):
        img = np.full((H, W), 40, np.uint8)
        img[60:100, 28:68] = 220
        frames.append((img + (i % 2)).astype(np.uint8))
    src = encode_gray(tmp_path / "box.mp4", frames)
    d = base_edl(src, [{"source": "a", "in": "f0", "frames": 48, "camera": {"push": {"from": 1.0, "to": 1.5, "ease": "linear"}}}])
    out = tmp_path / "pushed.mp4"
    E.render(write(tmp_path, d), out)
    g = decode_gray(out)
    widths = [(f[80] > 130).sum() for f in g]
    assert widths[0] == pytest.approx(40, abs=2) and widths[-1] == pytest.approx(60, abs=2)
    assert all(b >= a - 1 for a, b in zip(widths, widths[1:]))


def test_punch_lands_on_its_frame(tmp_path):
    frames = [(np.where((np.arange(H)[:, None] > 60) & (np.arange(H)[:, None] < 100) & (np.arange(W)[None, :] > 28) & (np.arange(W)[None, :] < 68), 220, 40) + i % 2).astype(np.uint8) for i in range(24)]
    src = encode_gray(tmp_path / "box.mp4", frames)
    d = base_edl(src, [{"source": "a", "in": "f0", "frames": 24, "camera": {"punch": [{"at": "f10", "frames": 8, "strength": 0.3}]}}])
    out = tmp_path / "punch.mp4"
    E.render(write(tmp_path, d), out)
    widths = [(f[80] > 130).sum() for f in decode_gray(out)]
    assert widths[9] <= 42 and widths[10] >= 48 and widths[20] < widths[10]


def test_whip_changes_only_its_frames_and_keeps_the_count(coded, tmp_path):
    shots = [{"id": "a", "source": "a", "in": "f0", "frames": 12},
             {"id": "b", "source": "a", "in": "f50", "frames": 12, "transition_in": {"type": "whip", "frames": 6, "dir": "left"}}]
    out, rep = render_ok(tmp_path, base_edl(coded, shots))
    assert rep["measured"]["frames"] == 24
    got = read_codes(decode_gray(out))
    assert got[:9] == list(range(0, 9)) and got[15:] == list(range(53, 62))
    assert got[9:15] != list(range(9, 12)) + list(range(50, 53))


def test_glitch_changes_only_its_frames(coded, tmp_path):
    shots = [{"id": "a", "source": "a", "in": "f0", "frames": 12},
             {"id": "b", "source": "a", "in": "f50", "frames": 12, "transition_in": {"type": "glitch", "frames": 4}}]
    glitched, rep = render_ok(tmp_path, base_edl(coded, shots), name="g.mp4")
    clean, _ = render_ok(tmp_path, base_edl(coded, [{**shots[0]}, {"id": "b", "source": "a", "in": "f50", "frames": 12}]), name="c.mp4")
    a, b = decode_gray(clean).astype(int), decode_gray(glitched).astype(int)
    diffs = np.abs(a - b).reshape(24, -1).mean(axis=1)
    assert rep["measured"]["frames"] == 24
    # x264 lookahead shifts neighbouring frames by a level or two; the glitch itself is far larger.
    assert diffs[10:14].min() > 8
    assert diffs[:10].max() < 3 and diffs[14:].max() < 3


def test_glitch_strips_shift_sideways_within_their_own_rows(tmp_path):
    ramp = (np.linspace(30, 220, H)[:, None] * np.ones((1, W))).astype(np.uint8)
    src = encode_gray(tmp_path / "ramp.mp4", [ramp + (i % 2) for i in range(40)])
    shots = [{"id": "a", "source": "a", "in": "f0", "frames": 20},
             {"id": "b", "source": "a", "in": "f20", "frames": 20, "transition_in": {"type": "glitch", "frames": 6, "strength": 0.3}}]
    out = tmp_path / "ramp_out.mp4"
    E.render(write(tmp_path, base_edl(src, shots)), out)
    g = decode_gray(out).astype(int)
    # Luma depends only on the row, so a strip taken from any other row would show up as a step.
    assert np.abs(g[17:23] - g[10]).reshape(6, -1).max(axis=1).max() < 22


def test_whip_blur_grows_with_speed_and_hides_the_mirror():
    blurs = fx._whip_blurs(fx.Whip(3, "left"), None, 12, True, 540)
    sigmas = [float(b.split("sigma=")[1].split(":")[0]) for b in blurs]
    assert sigmas == sorted(sigmas, reverse=True)
    assert sigmas[0] > 0.1 * 540


def test_text_is_drawn_only_in_its_window_on_a_separate_pass(coded, tmp_path):
    shots = [{"source": "a", "in": "f0", "frames": 24}]
    text = [{"text": "HI", "font": FONT, "start": "f6", "end": "f18", "x": 0.5, "y": 0.6, "size": 0.2, "color": "yellow", "border": 0.05, "anim": "pop"}]
    out, rep = render_ok(tmp_path, base_edl(coded, shots, text=text))
    assert rep["text_checks"][0]["drawn"]
    g = decode_gray(out).astype(int)
    plain, _ = render_ok(tmp_path, base_edl(coded, shots), name="plain.mp4")
    p = decode_gray(plain).astype(int)
    band = lambda f: f[70:125]
    d = [np.abs(band(g[i]) - band(p[i])).mean() for i in range(24)]
    assert max(d[:6]) < 1.5 and max(d[18:]) < 1.5 and min(d[8:18]) > 5


@pytest.mark.parametrize("anim", fx.TEXT_ANIMS)
def test_every_text_animation_renders(coded, tmp_path, anim):
    shots = [{"source": "a", "in": "f0", "frames": 24}]
    text = [{"text": "GO'T: 100%", "font": FONT, "start": "f2", "end": "f22", "size": 0.09, "anim": anim, "fade_out": 3, "border": 0.05, "shadow": 0.04}]
    _, rep = render_ok(tmp_path, base_edl(coded, shots, text=text))
    assert rep["text_checks"][0]["drawn"], anim


def test_rewording_text_does_not_rerender_picture(coded, tmp_path):
    shots = [{"source": "a", "in": "f0", "frames": 12}]
    text = [{"text": "ONE", "font": FONT, "start": "f0", "end": "f12", "size": 0.1}]
    render_ok(tmp_path, base_edl(coded, shots, text=text))
    cache = tmp_path / ".edl-cache"
    before = {p.name: p.stat().st_mtime_ns for p in cache.glob("seg_*.mp4")} | {p.name: p.stat().st_mtime_ns for p in cache.glob("picture_*.mp4")}
    text[0]["text"] = "TWO"
    render_ok(tmp_path, base_edl(coded, shots, text=text))
    after = {p.name: p.stat().st_mtime_ns for p in cache.glob("seg_*.mp4")} | {p.name: p.stat().st_mtime_ns for p in cache.glob("picture_*.mp4")}
    assert before == after


def test_changing_effect_code_invalidates_cached_segments(coded, tmp_path, monkeypatch):
    shots = [{"source": "a", "in": "f0", "frames": 12}]
    plain, _ = render_ok(tmp_path, base_edl(coded, shots), name="one.mp4")
    original = E.shot_graph
    monkeypatch.setattr(E, "shot_graph", lambda *a: original(*a).replace("null[out]", "negate[out]"))
    changed, _ = render_ok(tmp_path, base_edl(coded, shots), name="two.mp4")
    assert np.abs(decode_gray(plain).astype(int) - decode_gray(changed).astype(int)).mean() > 20


def test_preview_is_smaller_and_keeps_the_exact_length(coded, tmp_path):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 24}])
    _, rep = render_ok(tmp_path, d, preview=True, scale=0.5)
    assert rep["size"] == [48, 80] and rep["measured"]["frames"] == 24 and rep["mode"] == "preview"


def test_still_with_a_push_renders_and_a_still_without_motion_is_flagged_frozen(tmp_path):
    png = tmp_path / "still.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r=1", "-frames:v", "1", str(png)], check=True)
    _, rep = render_ok(tmp_path, base_edl(png, [{"source": "a", "frames": 24, "camera": {"push": {"from": 1.0, "to": 1.3}}}]))
    assert rep["measured"]["frames"] == 24
    rep = E.render(write(tmp_path, base_edl(png, [{"source": "a", "frames": 24}])), tmp_path / "held.mp4")
    assert not rep["ok"] and any("frozen" in e for e in rep["errors"])


# ---------------------------------------------------------------- checks on bad video

def test_check_catches_black_frames_in_real_video(tmp_path):
    frames = [np.zeros((H, W), np.uint8) if 10 <= i < 15 else coded_frame(i) for i in range(30)]
    src = encode_gray(tmp_path / "blackish.mp4", frames)
    rep = E.check(src, None)
    assert any("black frames 10..14" in e for e in rep["errors"])


def test_check_catches_frozen_run_in_real_video(tmp_path):
    frames = [coded_frame(min(i, 10) if i < 22 else i) for i in range(48)]
    rep = E.check(encode_gray(tmp_path / "frozen.mp4", frames), None)
    assert any("frozen frames" in e for e in rep["errors"])


def test_check_catches_footage_placed_twice_even_when_the_edl_cannot_see_it(tmp_path):
    frames = [coded_frame(i) * 0 + coded_frame(i + 500) for i in range(30)] + [coded_frame(i + 900) for i in range(30)] + [coded_frame(i + 500) for i in range(30)]
    rep = E.check(encode_gray(tmp_path / "dup.mp4", frames), None)
    assert any("placed twice" in e for e in rep["errors"])


def test_check_requires_audio_without_a_declared_reason(coded):
    rep = E.check(coded, None)
    assert "no audio stream" in rep["errors"]


def test_clean_check_on_moving_video_has_no_false_alarms(coded):
    rep = E.check(coded, None)
    assert rep["errors"] == ["no audio stream"]


def test_frame_faults_exempts_declared_flashes_and_repeats():
    gray = np.stack([coded_frame(i)[::2, ::2] for i in range(60)])
    gray[20] = 0
    gray[40] = 255
    assert fault_keys(gray) == {"black", "white"}
    assert fault_keys(gray, flash={20, 40}) == set()


def fault_keys(gray, flash=frozenset()):
    return {k for k, v in E.frame_faults(gray, 24.0, flash).items() if v}


def test_frame_faults_detects_letterbox_bars():
    gray = np.stack([coded_frame(i)[::2, ::2] for i in range(10)]).copy()
    gray[:, :6] = 0
    gray[:, -6:] = 0
    assert E.frame_faults(gray, 24.0)["bars"] == ["top", "bottom"]


# ---------------------------------------------------------------- audio

def test_mix_has_exact_length_safe_peak_and_hits_land_on_their_cue(coded, tmp_path):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 48}])
    d["audio"] = {"assets": {"h": {"path": str(HIT), "provenance": "CC0 test"}},
                  "hits": [{"asset": "h", "at": "f24", "gain_db": 0}]}
    out, rep = render_ok(tmp_path, d)
    assert abs(rep["measured"]["audio_duration"] - 2.0) < 0.03
    assert rep["measured"]["true_peak_db"] <= -1.0
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(pcm, "<f4"))
    onset = np.argmax(x >= x.max() * 0.1) / 48000
    assert abs(onset - 1.0) < 0.015


def test_chapters_in_a_music_file_do_not_become_a_stream_in_the_output(coded, tmp_path):
    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=one\n")
    music = tmp_path / "chaptered.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ASSETS / "music" / "unbeatable-arcade-machine-165s-45s.mp3"), "-i", str(meta),
                    "-map_chapters", "1", "-t", "3", "-c", "copy", str(music)], check=True)
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 24}])
    d["audio"] = {"assets": {"m": {"path": str(music), "provenance": "CC0 test"}}, "bed": {"asset": "m"}}
    out, _ = render_ok(tmp_path, d)
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True).stdout.split()
    assert sorted(kinds) == ["audio", "video"]


def test_mixing_the_bed_keeps_its_recorded_level(coded, tmp_path):
    d = base_edl(coded, [{"source": "a", "in": "f0", "frames": 48}])
    music = ASSETS / "music" / "unbeatable-arcade-machine-165s-45s.mp3"
    d["audio"] = {"assets": {"m": {"path": str(music), "provenance": "CC0 test"}, "h": {"path": str(HIT), "provenance": "CC0 test"}},
                  "bed": {"asset": "m", "in": 3.0, "gain_db": -4, "fade_out": 0.2}, "hits": [{"asset": "h", "at": "f12"}]}
    _, rep = render_ok(tmp_path, d)
    assert rep["audio"]["provenance"]["m"] == "CC0 test"
    assert -30 < rep["measured"]["lufs"] < -5
    assert not any("unexpected" in e for e in rep["errors"])


def test_silence_is_only_allowed_with_a_reason_and_is_then_reported(coded, tmp_path):
    _, rep = render_ok(tmp_path, base_edl(coded, [{"source": "a", "in": "f0", "frames": 12}]))
    assert any("as declared" in w for w in rep["warnings"])


# ---------------------------------------------------------------- font reader

def test_font_reader_measures_and_reports_coverage():
    f = fx.Font(FONT)
    assert f.missing("ABC 123 !") == []
    assert f.missing("A字") == ["字"]
    assert 0.2 < f.width("A") < 1.0
    assert f.width("AAAA") == pytest.approx(4 * f.width("A"))


def test_font_reader_rejects_non_fonts(tmp_path):
    p = tmp_path / "x.ttf"
    p.write_bytes(b"not a font at all")
    with pytest.raises(fx.FontError):
        fx.Font(p)


def test_documented_example_is_a_valid_edl(coded, tmp_path):
    doc = (ROOT / "skills" / "remix" / "references" / "edl.md").read_text()
    edl = json.loads(doc.split("```json")[1].split("```")[0])
    png = tmp_path / "kf.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=96x160:r=1", "-frames:v", "1", str(png)], check=True)
    edl["sources"]["kf"] = str(png)
    edl["sources"]["c1"]["path"] = str(coded)
    edl["text"][0]["font"] = FONT
    edl["audio"]["assets"]["music"]["path"] = str(ASSETS / "music" / "unbeatable-arcade-machine-165s-45s.mp3")
    assert problems(edl) == []


def test_cli_validate_reports_json_and_exit_code(coded, tmp_path):
    good = write(tmp_path, base_edl(coded, [{"source": "a", "in": "f0", "frames": 12}]))
    assert E.main(["validate", str(good)]) == 0
    bad = write(tmp_path, base_edl(coded, [{"source": "a", "in": "f0", "frames": 12}], frames=20))
    assert E.main(["validate", str(bad)]) == 1


def _flat_clip(path: Path, frames=24, color="0x808080"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24", "-frames:v", str(frames),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)], check=True)


def _centre_matte(path: Path, frames=24):
    """White in the middle third, black elsewhere: the 'subject' stands in the centre."""
    m = np.zeros((180, 320), np.uint8)
    m[:, 107:213] = 255
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "320x180", "-r", "24", "-i", "-",
                          "-c:v", "ffv1", str(path)], stdin=subprocess.PIPE)
    p.communicate(m.tobytes() * frames)


def _behind_edl(tmp: Path, **text) -> Path:
    _flat_clip(tmp / "c.mp4")
    doc = {"version": 1, "canvas": {"w": 320, "h": 180, "fps": 24, "frames": 24}, "sources": {"c": "c.mp4"},
           "shots": [{"source": "c", "in": "f0", "frames": 24}], "matte": "m.mkv",
           "text": [{"text": "WIDE WORD", "font": str(ASSETS / "fonts" / "Anton-Regular.ttf"), "size": 0.4, "color": "#ff0000", "behind": True, **text}],
           "audio": {"none_reason": "test"}}
    p = tmp / "e.json"
    p.write_text(json.dumps(doc))
    return p


def test_behind_text_is_hidden_where_the_matte_is_white_and_drawn_elsewhere(tmp_path):
    p = _behind_edl(tmp_path)
    _centre_matte(tmp_path / "m.mkv")
    edl, base = E.load(p)
    (tmp_path / "m.json").write_text(json.dumps({"picture": E._picture_key(edl, E.validate(edl, base))}))
    res = E.render(p, tmp_path / "o.mp4")
    assert res["text_checks"][0]["drawn"]
    out = decode_gray(tmp_path / "o.mp4", 320, 180)[12]
    untouched = decode_gray(tmp_path / "c.mp4", 320, 180)[12]
    assert np.abs(out[:, 125:195].astype(int) - untouched[:, 125:195]).max() <= 3
    assert np.abs(out[:, :100].astype(int) - untouched[:, :100]).max() > 25


def test_behind_text_refuses_a_matte_made_for_a_different_picture(tmp_path):
    p = _behind_edl(tmp_path)
    _centre_matte(tmp_path / "m.mkv")
    (tmp_path / "m.json").write_text(json.dumps({"picture": "something else"}))
    with pytest.raises(E.RenderError, match="different picture"):
        E.render(p, tmp_path / "o.mp4")


def test_behind_text_reports_a_missing_or_short_matte(tmp_path):
    p = _behind_edl(tmp_path)
    edl, base = E.load(p)
    with pytest.raises(E.EDLError, match="edl matte"):
        E.validate(edl, base)
    _centre_matte(tmp_path / "m.mkv", frames=10)
    with pytest.raises(E.EDLError, match="10 frames"):
        E.validate(edl, base)
