#!/usr/bin/env python3
"""Assemble the labelled side-by-side comparison video from finished runs."""
import subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
W, H, FPS = 1920, 1080, 30
GOLD, WHITE, GREY = "0xF2C14E", "white", "0xB8B8B8"

work = Path(tempfile.mkdtemp(prefix="comparison-"))
text_id = 0


def text(line, x, y, size=34, color=WHITE, bold=False):
    global text_id
    text_id += 1
    path = work / f"t{text_id}.txt"
    path.write_text(line)
    font = (BOLD if bold else REG).replace(" ", r"\ ")
    return (f"drawtext=fontfile={font}:textfile={path}:expansion=none:"
            f"x={x}:y={y}:fontsize={size}:fontcolor={color}")


def centered(line, y, size=34, color=WHITE, bold=False):
    return text(line, "(w-text_w)/2", y, size, color, bold)


def encode(graph, out, inputs, seconds):
    script = work / (Path(out).stem + ".graph")
    script.write_text(graph)
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    for item in inputs:
        args += item
    args += ["-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]",
             "-t", str(seconds), "-r", str(FPS), "-c:v", "libx264", "-crf", "18",
             "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", "-ac", "2", str(out)]
    subprocess.run(args, check=True)
    return out


SILENT = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]


def card(name, seconds, draws):
    graph = (f"color=c=0x101014:s={W}x{H}:r={FPS}:d={seconds}[bg];"
             f"[bg]{','.join(draws)}[v];[0:a]anull[a]")
    return encode(graph, work / f"{name}.mp4", [SILENT], seconds)


def fit(index, w, h, seconds, speed):
    pts = f"setpts={speed}*(PTS-STARTPTS)," if speed != 1 else "setpts=PTS-STARTPTS,"
    return (f"[{index}:v]{pts}fps={FPS},tpad=stop_mode=clone:stop_duration={seconds},"
            f"trim=duration={seconds},setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[p{index}];")


def duel(name, left, right, source, labels, notes, seconds, speed=1, audio=None):
    """Two large panels with the source beneath. audio is an input index or None."""
    paths = [left, right, source]
    inputs = [["-i", str(p)] for p in paths] + [SILENT]
    graph = "".join(fit(i, *size, seconds, speed) for i, size in
                    enumerate([(940, 528), (940, 528), (480, 270)]))
    graph += (f"color=c=0x101014:s={W}x{H}:r={FPS}:d={seconds}[bg];"
              "[bg][p0]overlay=13:76[a0];[a0][p1]overlay=967:76[a1];"
              "[a1][p2]overlay=720:650[a2];")
    draws = [text(labels[0], 13, 18, 42, GOLD, True), text(labels[1], 967, 18, 42, GOLD, True),
             centered(labels[2], 612, 28, GREY, True)]
    for i, line in enumerate(notes[0]):
        draws.append(text(line, 40, 660 + i * 46, 30))
    for i, line in enumerate(notes[1]):
        draws.append(text(line, 1240, 660 + i * 46, 30))
    if notes[2]:
        draws.append(centered(notes[2], 980, 30, GREY))
    graph += f"[a2]{','.join(draws)}[v];"
    graph += (f"[{audio}:a]anull[a]" if audio is not None else "[3:a]anull[a]")
    return encode(graph, work / f"{name}.mp4", inputs, seconds)


def quad(name, panels, note, seconds, speed=1):
    inputs = [["-i", str(p)] for p, _ in panels] + [SILENT]
    graph = "".join(fit(i, 450, 800, seconds, speed) for i in range(len(panels)))
    graph += f"color=c=0x101014:s={W}x{H}:r={FPS}:d={seconds}[s0];"
    draws = []
    for i, (_, label) in enumerate(panels):
        x = 30 + i * 470
        graph += f"[s{i}][p{i}]overlay={x}:76[s{i + 1}];"
        draws.append(text(label, x, 20, 38, GOLD, True))
    draws.append(centered(note, 930, 32, GREY))
    graph += f"[s{len(panels)}]{','.join(draws)}[v];[{len(panels)}:a]anull[a]"
    return encode(graph, work / f"{name}.mp4", inputs, seconds)


def table(name, seconds, title, columns, rows, verdict):
    draws = [centered(title, 70, 56, GOLD, True)]
    xs = [120, 1000, 1420]
    for x, col in zip(xs[1:], columns):
        draws.append(text(col, x, 190, 36, GOLD, True))
    for i, row in enumerate(rows):
        y = 256 + i * 66
        draws.append(text(row[0], xs[0], y, 36, GREY))
        draws.append(text(row[1], xs[1], y, 36))
        draws.append(text(row[2], xs[2], y, 36))
    y0 = 256 + len(rows) * 66 + 44
    for i, line in enumerate(verdict):
        draws.append(text(line, 120, y0 + i * 52, 36, WHITE, i == 0))
    return card(name, seconds, draws)


FOX = Path(sys.argv[1])
g = RUNS / "final/genjutsu"
k = RUNS / "final/katana"
ours_edit = RUNS / "remix-final/final.mp4"
d = RUNS / "genjutsu-test/D_higgsfield/output.mp4"

parts = [
    card("01", 5, [
        centered("Our version vs Higgsfield", 360, 84, GOLD, True),
        centered("Same inputs, one attempt each, 480p", 490, 44),
        centered("Scores come from our own tool, checked on only two clips.", 570, 34, GREY),
        centered("Generated clips in both versions ran on Higgsfield, the only funded route.", 620, 34, GREY),
    ]),
    card("02", 4, [
        centered("Part 1: Genjutsu vs ours", 400, 72, GOLD, True),
        centered("A clip neither had seen. Our prompt was written before any result.", 510, 40),
    ]),
    duel("03", g / "G.mp4", g / "O.mp4", g / "source.mp4",
         ("Real Genjutsu", "Ours", "SOURCE"),
         (["Normal speed", "Ended at 3.71 of 4 s,", "last frame held"], ["Normal speed", "Ended at 4.00 s"], ""),
         4),
    duel("04", g / "G.mp4", g / "O.mp4", g / "source.mp4",
         ("Real Genjutsu", "Ours", "SOURCE"),
         (["Half speed"], ["Half speed"], "Watch: hand order, blur on fast hands, the arm raise, on-screen words"),
         8, speed=2),
    table("05", 9, "Part 1 scores", ("Genjutsu", "Ours"), [
        ("Tracking of source layout, camera and timing", "0.670", "0.700"),
        ("Action beats found", "1 of 2", "2 of 2"),
        ("Beat timing error", "0.083 s", "0.125 s"),
        ("Duration, target 4.00 s", "3.71 s", "4.00 s"),
        ("Credits", "12", "12"),
    ], ["Not matched: Genjutsu still looks better.",
        "Its face and paws are sharper. Ours smears fast hands and raises the arm late.",
        "Ours fails our beat-timing criterion. Tracking is ahead only narrowly."]),
    quad("06", [
        (FOX / "input/drive_4s.mp4", "Source"),
        (FOX / "output/C_genjutsu_replace.mp4", "Genjutsu swap"),
        (FOX / "output/A_genjutsu_motion.mp4", "Genjutsu motion"),
        (d, "Ours"),
    ], "Earlier clip, half speed. Our prompt method was first tried here, so this favours ours.", 8, speed=2),
    card("07", 4, [
        centered("Part 2: Katana vs ours", 400, 72, GOLD, True),
        centered("Same reference, same fox, same brief, same 30-credit cap", 510, 40),
    ]),
    duel("08", k / "katana.mp4", ours_edit, k / "ref.mp4",
         ("Real Katana", "Ours", "REFERENCE EDIT"),
         (["Playing: Katana's sound"], ["Sound levels not adjusted"], ""),
         8, audio=0),
    duel("09", k / "katana.mp4", ours_edit, k / "ref.mp4",
         ("Real Katana", "Ours", "REFERENCE EDIT"),
         (["Sound levels not adjusted"], ["Playing: our sound"], ""),
         8, audio=1),
    table("10", 9, "Part 2 scores", ("Katana", "Ours"), [
        ("Finish check: duration, no black or frozen frames", "Pass", "Pass"),
        ("Rhythm check against reference", "Fail", "Pass, weak test"),
        ("Credits, cap 30", "28.39", "25.5"),
        ("Agent tokens, including cached", "about 21 M", "about 10.3 M"),
        ("Loudness", "-25.8 LUFS", "-16.3 LUFS"),
    ], ["Close, on one short clip: judge this one by watching it.",
        "Ours echoes the reference's reach-to-camera hands. Katana's first scene is nearly static.",
        "Both break the big words around the fox's head. Neither was reviewed by ear."]),
    card("11", 9, [
        centered("Verdict", 200, 72, GOLD, True),
        centered("Katana: ours is competitive on this clip.", 340, 48, WHITE, True),
        centered("Genjutsu: ours is not yet as good.", 420, 48, WHITE, True),
        centered("This is not proof. Two short clips at 480p, no speech, no lip sync, no independent route.", 560, 36, GREY),
        centered("No saving is established. Higgsfield charged the same for our Genjutsu-style clip.", 620, 36, GREY),
    ]),
]

listing = work / "list.txt"
listing.write_text("".join(f"file '{p}'\n" for p in parts))
out = RUNS / "final/comparison.mp4"
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
print(out)
