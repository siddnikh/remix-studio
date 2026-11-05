---
name: subject-swap
description: >-
  Put a different subject into an existing video clip while keeping its shots, camera, framing, blocking and timing, from a source clip plus subject images. Also called motion transfer, character swap or object swap. Use for "make my character do what's in this video", "replace the person in this clip", "copy this motion onto this image". Not for building a whole new edit from a reference: use the remix skill.
---

# Subject swap

Replace one subject in one clip. The method is a shot-by-shot prompt written after looking at the source's frames, sent to a model that takes a reference video and images. The prompt does most of the work, so take time on it.

If `remix-studio` is not found, tell the user to run `install.sh` from the remix-studio repo. Run `remix-studio doctor` first. **If it shows no generation route, ask the user which service to use and point them at `remix-studio where` (`services_example`). Never set one up or pay for one on your own.** Ask for the spend cap if none was given. Check that the user has the right to use the people and footage involved.

## 1. Prepare (spends nothing)

```bash
remix-studio swap prepare source.mp4 --run-dir runs/<slug>
```

This trims to whole seconds at exactly 24 fps and writes `source.mp4`, `analysis.json` (measured cuts, camera moves) and `source_sheet.png`. Look at the sheet, plus `frames/` where it isn't clear. Clips under about 4 s tend to be rejected.

## 2. Write the shot list

Write `runs/<slug>/shots.json` from what the frames show. Format and worked advice: [references/method.md](references/method.md).

## 3. Quote, then run

Local files reach the model in one of three ways, and the second and third send them to another party, so get the user's agreement:

- `--video-url URL --image-url URL`: files already hosted.
- `--inline --subject FILE`: inside the request, if the service takes data URIs.
- `--host-public --subject FILE`: public for 7 days through treg. Needs explicit consent for these files.

```bash
remix-studio swap quote --run-dir runs/<slug> --shot-list runs/<slug>/shots.json --route http:<name> --inline --subject subject.png
remix-studio swap run   --run-dir runs/<slug> --shot-list runs/<slug>/shots.json --route http:<name> --inline --subject subject.png --cap <cap>
```

`quote` prints the price and the composed prompt. Read the prompt before spending. `run` reserves under the ledger, submits, polls, downloads, enforces the duration and scores. It resumes the same task after a crash, so re-run it rather than resubmitting. A failed run needs a new `--run-dir`.

## 4. Check, by looking

```bash
remix-studio swap compare runs/<slug>/compare.png source=runs/<slug>/source.mp4 output=runs/<slug>/output.mp4
```

The tracking score ignores the subject, so it can't detect a failed swap, and it can pass while the video looks bad. Read down each column of the sheet and check:

- Identity in every sampled frame, against the subject image.
- Each action beat of the source, in order, on a table of source time against output time.
- Hands and fast limbs, which are the known weakness: they smear and arrive late.
- Faces: facial performance is usually lost.
- Burned-in captions: wrong order, dropped or repeated words.

Report the scores and the review separately. Say you reviewed stills, not playback. Don't retry for "a better take": only for wrong identity, broken anatomy or a missing beat, and only if the cap allows.
