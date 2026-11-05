# Katana baseline (one job, unmodified output)

Session: `claude -p "/katana ..." --model opus --max-budget-usd 25` run from another project; stream in `stream.jsonl`. Reference: first 8.000 s trim, copied to `ref.mp4` from remix-final/ref. Subject: the subject image.

## Outcome
Katana finished (stop reason end_turn, subtype success, 120 turns, ~16.5 min API time). It did not refuse or hit a limitation. The credit guard never fired (no kill, no resume). Output: `katana.mp4`, downloaded with curl from the result URL, unaltered.

## Credits and cost
- Balance before 40.55, after 12.16 (headless haiku `balance` call after the run) = 28.39 spent. Katana reported the same figure and said its transaction list matched. Cap was 30; hard floor 8 not approached.
- Session tokens: Opus 5.5 in 144, out 87,003, cache read 20,982,800, cache write 391,769; Sonnet 5.5 (two subagents) in 16, out 23,555, cache read 397,976, cache write 222,907.
- Session cost: $9.90 (budget $25).

## What Katana submitted
| Job | Model | Credits |
|---|---|---|
| Cabin plate, 2 takes (batch) | soul_cinematic 2k 16:9 | 0.24 |
| Woods plate, 2 takes (batch) | soul_cinematic 2k 16:9 | 0.24 |
| Scene 1 clip, 5 s, 480p, no audio | seedance_2_5 omni_reference | 15 |
| Scene 2 clip, 4 s, 480p, no audio | seedance_2_5 omni_reference | 12 |
| Subject mask (97 frames) | sam_3_video | 0.91 |
Total 28.39. Katana added text, punch-ins, sway, cuts and five CC0 sound accents itself in editing. A planned 1-credit background remover was swapped for sam_3_video.

Katana's final message (condensed): says the edit is 8.000 s, 854x480, 30 fps; scene 1 4.9 s cabin with waving fox and pink words "soft paws / the woods call me / back"; scene 2 3.1 s cottage with giant "home", "and", "wild" behind the fox, ending on two punch-ins and a wink. It admits: giant words smaller than the reference's and split by the head ("wild" reads "w d"); a bit of window still visible at the cut; calmer camera than the reference; last punch-ins soft; audio measured (-25.8 LUFS) but not heard; a checker path warning; Desktop download denied by permissions so only the URL exists.

## File facts (ffprobe)
H.264 854x480, 30 fps, 240 frames, 8.000 s, AAC audio present, 1,915,792 bytes.

## Scores (studio.score, same commands as remix-final)
- K1 (`score finish --target-duration 8`): pass. 8.0 s, audio, no black frames, no frozen runs, no repeated shots. `k1.json`.
- K2 (`score language ref katana`): **fail**. Cuts/s 0.125 vs 0.125 (pass). Flashes/s reference 0, output 0.125 (ratio null, reported pass). Shake bursts/s reference 0, output 0.25 (ratio null, reported fail). `k2.json`. The shake bursts are likely the camera sway/punch-ins and the paw wipe; I did not investigate which frames.

## What I see (`sheet_2fps.png`, 16 frames at 2 fps)
- Identity: the same moss-green fox with orange scarf, tan tunic and amber eyes holds throughout both scenes. Fur and face are stable; no drift visible at this sampling. Scene 2 hands look slightly different in scale because of the selfie perspective.
- Text: pink wide caps. Small words ("soft", "paws", "the", "woods", "me", "back") sit at the bottom, partly cut by the frame edge and overlapped by the scarf/arm. Giant "home", "and", "wild" are split by the fox's head and read as "ho me", "a nd", "wi ld"; the reference's hidden-letter effect works for "home" but is poor for "wild". The word "call" from Katana's own caption does not appear in my 2 fps samples (it may fall between samples).
- Motion: scene 1 is nearly static: fox stands and waves one paw, small slow movement. Scene 2 has more motion: arm extended, head tilt, wink, punch-in at the end (soft).
- Cuts: one hard transition at about 4.5 to 5.0 s, hidden by a large paw filling the frame; it works, though the blurry paw frame is a noticeable beat.
- Ending: ends on a close punch-in of the winking fox, with "wild" half-covered. It reads as a deliberate end, but the last frames are somewhat soft.
- Overall: a competent, clean AI demo with a polished fox and a clear cut; calmer and more polite than a real selfie performance. Audio not heard by me.
