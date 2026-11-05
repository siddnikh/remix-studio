# EDL format

JSON, version 1. Paths are relative to the EDL file. Unknown keys are errors; keys starting with `_` are comments. Run `remix-studio edl validate` to get every problem at once.

**Times:** a number means seconds; `"f123"` means an exact frame. Shot `in`/`out` count source frames; everything else counts output frames. Prefer frames: they are what you saw on the sheet.

**Sizes:** fractions of the canvas, so previews match the final.

```json
{
  "version": 1,
  "canvas": {"w": 1080, "h": 1920, "fps": 24, "frames": 36},
  "sources": {
    "kf": "../stills/kf1.png",
    "c1": {"path": "../clips/c1.mp4", "bad_ranges": [["f40", "f58"]]}
  },
  "grade": {"contrast": 1.08, "saturation": 1.12, "vignette": 0.45},
  "shots": [
    {"id": "open", "source": "c1", "in": "f6", "frames": 24,
     "camera": {"push": {"from": 1.0, "to": 1.1, "y": 0.55, "ease": "out"}}},
    {"id": "hit", "source": "kf", "frames": 12,
     "transition_in": {"type": "whip", "frames": 6, "dir": "left"},
     "flashes": [{"at": "f0", "frames": 2}],
     "camera": {"shake": [{"at": "f0", "frames": 10, "amp": 0.016, "hz": 9, "rot": 0.6}],
                "push": {"from": 1.0, "to": 1.05}}}
  ],
  "text": [
    {"text": "SAME ROUTINE", "font": "/abs/path/from/remix-studio-where/fonts/Anton-Regular.ttf",
     "start": "f6", "end": "f36", "x": 0.5, "y": 0.17, "size": 0.085, "anim": "slam", "border": 0.05}
  ],
  "audio": {
    "assets": {"music": {"path": "../audio/track.mp3", "provenance": "who, licence, URL, date"}},
    "bed": {"asset": "music", "in": 0.11, "gain_db": -6, "fade_out": 0.4},
    "hits": []
  }
}
```

## Shots

| Key | Meaning |
|---|---|
| `source`, `id` | Source key; unique id (defaults to its index) |
| `in`, `out`, `frames`, `speed` | Give `in` plus `out` or `frames`. `out` plus `frames` implies the speed. A still needs only `frames` |
| `repeat` | Allow source frames already used. Only when the reference repeats footage |
| `flip`, `focus` | Mirror; `[x, y]` crop centre, 0 to 1, when aspect differs |
| `slowmo` | `blend` or `nearest`. Defaults to blend from 0.3x to 1x |
| `grade` | Same keys as the global grade, applied first |
| `camera` | Below |
| `transition_in` | Below. The first shot has none |
| `flashes` | Shot-relative. Top-level `flashes` use edit time |

The total of shot frames must equal `canvas.frames` exactly. A clip stretched to its slot can be split at a source frame into contiguous shots at different speeds: the join doesn't read as a cut, and the measured cut rate ignores it.

## Camera

`push`: `from`, `to` zoom (1 to 4), `x`, `y` centre, optional `to_x`, `to_y` to pan, `ease` of `linear`, `in`, `out`, `snap`, `inout`.

`shake`: list of bursts: `at`, `frames`, `amp` (0.0005 to 0.08 of frame height; 0.01 to 0.03 is normal), `hz`, `rot` (degrees), `decay`. It is deterministic per shot id. The renderer adds just enough zoom that no edge shows, so very large shakes crop in noticeably.

`punch`: list of snap zooms: `at`, `frames`, `strength` (0.05 to 0.15 is normal).

## Transitions and flashes

`whip`: `frames`, `dir` of `left`, `right`, `up`, `down`. `glitch`: `frames`, `strength`. Half the frames sit at the end of the previous shot and half at the start of this one, so no extra footage is needed and the timeline doesn't change. Four to eight frames is normal. One shot can whip on one axis only. The glitch is seeded by shot id, so a re-render matches.

`flashes`: `at`, `frames`, `color` (`white` or `#rrggbb`), `strength`. White adds exposure; a colour tints. Frames in a flash are exempt from the white and black checks.

## Grade

`contrast`, `brightness`, `saturation`, `gamma`, `temperature` (kelvin), `lut` (a .cube file), `sharpen`, `vignette` (angle in radians; smaller is lighter), `grain`.

## Text

`text` (one line), `font` (a file; required, no fallback exists), `start`, `end`, `x`, `y` (centre), `size` (fraction of height), `color`, `border` and `shadow` (fraction of the font size), `border_color`, `box` (`#000000@0.6`), `anim`, `fade_out` (frames), `behind`.

`behind: true` puts the word between the background and the subject. Set a top-level `"matte": "../matte.mkv"`, then run `remix-studio edl matte edit.json` once the picture is settled. It masks the finished picture on those frames with macOS's Vision framework (macOS 14 or later, plus clang; nothing is downloaded). Rendering refuses a matte made for a different picture, so re-run it after any change to shots, camera or grade; wording, timing within the same frames and audio don't need it unless the frames covered change. Look at the edges: the mask finds the most salient subject, and it can follow a large foreground object instead. Faces stay untouched, because the subject's pixels are the picture's own.

`anim`: `none`, `pop`, `slam`, `slide_up`, `flicker`, `typewriter`. Validation refuses a missing glyph or a line that would be clipped. Rendering confirms each event changed pixels.

## Audio

`assets` maps a key to a `path` and a required `provenance`. `bed`: `asset`, `in`, `gain_db`, `fade_in`, `fade_out`; it must run the whole edit. `hits`: `asset`, `at`, `gain_db`; the sound's measured onset, not its file start, lands on the frame. For a silent edit, `none_reason` must say why.

The mix is limited to a true peak of -1 dBTP or lower, measured after AAC encoding.

## Commands

`validate` prints problems and authored rates. `matte` writes the file for `behind` text. `render` writes the video and `<name>.report.json`, and exits 1 if a check fails. `--preview` renders half size with a fast encode; `--scale` changes the size. `check VIDEO --edl EDIT` re-checks a file. Segments cache in `.edl-cache/` beside the output, keyed by the filter graph, source file and encode settings, so edits re-render only what changed. Delete that folder when the run ends.

## Limits

No dissolves, hand-drawn masks or tracked graphics, stickers, keyed layers, or speed ramps inside a shot. The one mask is the subject matte above. For a ramp, split the shot. Sources must be constant frame rate and SDR: the validator says how to convert others. Tested on ffmpeg 7.1; older versions lack the option used to read filter scripts.
