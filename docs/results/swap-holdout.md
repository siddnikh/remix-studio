# Hold-out comparison: Genjutsu (G) vs our pipeline (O), 2026-11-05

## Verdict

**The hypothesis did not hold on this clip.** O passes G1, G3 and G4 but fails G2, so it does not meet "as good as" here.
By eye, G is closer overall on identity and on the shape of the hand choreography. O's clear wins are duration, no time
drift and keeping every caption word. O's G1 pass is narrow, and it flips if G is retimed to 4 s. This is still only the second clip, with one
job per configuration and no repeated seed, so it is a counterexample and not an estimate of how often O loses.

## Inputs and protocol

- Source: first 4.000 s of `Orange_Jacket.mp4`, 96 frames at 24 fps, 1280x720, no audio (`source.mp4`).
- Subject: the subject image, reusing its existing Higgsfield media ID. Source media ID:
  `095d343d-fbdf-452c-b4fc-646af27aa722`.
- `shots.json` and `prompt.txt` were written from the contact sheet and locked in `protocol_lock.json` at 03:37, before any
  session started. Prompt sha256 `e7743da6...`. The prompt actually submitted matches that hash.
- The prompt was blind: no Genjutsu or Seedance output or rewrite for this clip existed, and the subject description was
  written fresh from the image.
- G: `hf_mult_replace_object`, 480p, the specified prompt, job `532eb277-32cb-4c13-8ec0-70ee59f04c16`. This model takes only
  `resolution`, so its aspect ratio follows the source.
- O: `seedance_2_5`, `omni_reference`, 480p, 4 s, 16:9, standard bitrate, no audio, job
  `47a29926-b575-488e-b69f-7e649dc6e7ec`.
- `G.mp4` is Higgsfield's raw output, as the baselines were scored. `O.mp4` is the pipeline's output: raw 97 frames, one
  trimmed.

## Scores

`score.tracking` at 12 fps, the same as `docs/baselines.json`. `studio/score.py` was not changed (last modified 02:53).

| Output | Tracking | Layout | Motion | Beats | Beat error | Activity corr. | Duration | Second-half tracking |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| G (raw, the bar) | 0.670 | 0.758 | 0.460 | 1/2 | 0.083 s | 0.79 | 3.708 s | 0.64 |
| **O** | **0.700** | 0.783 | 0.503 | 2/2 | 0.125 s | 0.37 | 4.000 s | 0.69 |
| O raw (97 frames) | 0.698 | 0.782 | 0.494 | 2/2 | 0.125 s | 0.37 | 4.042 s | 0.69 |
| G retimed to 4 s (not the bar) | 0.731 | 0.794 | 0.579 | 2/2 | 0.250 s | 0.91 | 4.000 s | 0.73 |

The source has no cuts, so cut F1 is undefined, and neither output contains a cut. The scorer found only two source beats
(1.17 s and 1.67 s), so most of the choreography is judged by eye below.

## Criteria, applied literally with G as the bar

| Criterion | O | Basis |
|---|---|---|
| G1 Tracking | **Pass** | 0.700 >= 0.670. It would fail against G retimed to 4 s (0.731) |
| G2 Beats | **Fail** | `score.gate` fails it: 0.125 s against 0.083 s. By eye, the eye roll and the pout are missing, and caption order is wrong |
| G3 Identity | **Pass, with flaws** | The fox in all 24 frames sampled at 6 fps. Flaws below |
| G4 Duration | **Pass** | 96 frames, 4.000 s after trimming one frame. Raw also passes at 4.042 s |

G itself fails G4: 3.708 s, 89 frames, the same frame count as baseline C on the first clip.

`score.gate` compares the error over matched beats only, so G's 0.083 s covers its single matched beat while O's 0.125 s
covers two. The literal result stands, but the margin should not be read as G timing the beats better.

## Visual review

Sheets: `compare_2fps.png`, `G_run/sheet_6fps.png`, `O_run/sheet_6fps.png`. There are no cuts to inspect, so the 6 fps sheets
cover the whole clip. These are stills, so temporal flicker was not assessed.

### G2 beats

| Source beat | Source time | G | O |
|---|---|---|---|
| Hand across the eyes | 0.00-0.55 s | Yes, same arm side | Yes, but mirrored: arm from the right, source from the left |
| Second hand rises large from the bottom edge | 0.55-0.75 s | Yes, 0.50-0.66 s | Not as a distinct move: second arm at the lower left by 0.33 s |
| Hands stack toward the lens | 0.83 s | Yes, 0.83 s | Yes, early, 0.66 s |
| Hands flick to the top of the frame | 1.00 s | Yes, 1.00 s | Yes, 1.00-1.16 s |
| Crossed hands hold | 1.25-1.58 s | Yes, shorter, 1.16-1.33 s | Yes, 1.33-1.50 s |
| Fast separation, arm shoots up | 1.58-1.75 s | Yes, early, 1.50 s | Yes, 1.66-1.83 s |
| Arm swings out to the right, leaves | 2.00-2.33 s | Yes, early, gone by 2.16 s | Yes, late, leaving about 2.6 s |
| Eyes roll up, lips purse | 2.75-2.95 s | **Missing**: a half-lidded smirk | **Missing**: neutral |
| Furrowed brows, pout | 3.00-3.25 s | **Missing**: a smile | **Missing**: neutral |
| Face scrunch, wink | 3.33 s | **Missing**: a toothy grin | Yes, late, a squint at 3.50-3.66 s |
| Soft closed-mouth smile | 3.45-4.00 s | Replaced by a toothy grin, ends 3.71 s | Weak: near neutral |

G compresses time, running up to 0.25 s early and ending 0.29 s short, but keeps every hand and arm beat in order. O holds
its clock with no drift, and its hand beats wander both early and late. Neither reproduces the facial performance.

### G3 identity

Both are clearly the fox in every sampled frame. Neither shows a tail, which is correct: the source framing is chest-up, and
O's prompt said the tail stays out of frame.

- **Shared:** the head is oversized compared with the source's, with ear tips reaching the top edge. O's prompt asked for a
  head about a third of frame height, and O did not honour that. Hands blur during fast moves in both.
- **G:** stronger identity. Fur and facial detail are richer and the face is more expressive, but the teeth and grin were not
  in the source. It adds large animal paws with pink pads that the reference image does not have.
- **O:** the same character with less fur detail. Dark hands and pads are closer to the reference. The glowing eyes bloom
  more, close to overexposed, and the raised arm goes thin and smooth, looking stretched at 2.33-2.50 s. The file is about
  a third of G's size (0.54 MB against 1.71 MB), though a local sharpness measure did not rank it softer (147 against 130).

### Text overlay

Both kept the pink caption style, correctly spelled, sharp and in about the right place. Neither is faithful.

| Source | G | O |
|---|---|---|
| upside 0.00-1.25 s | upside 0.00-1.17 s | upside 0.00-1.17 s |
| down 1.25-2.46 s | down 1.17-2.25 s | down 1.17-2.21 s |
| no word 2.46-3.04 s | no word 2.25-2.79 s | **no gap**: boy 2.21-2.75 s |
| boy 3.04-3.33 s | boy 2.79-3.08 s | you 2.75-3.08 s, then **boy again** 3.08-3.33 s |
| you 3.33-3.58 s | you 3.08-3.25 s | you 3.33-3.54 s |
| turn 3.58-3.92 s | **dropped** | turn 3.54-3.83 s |
| me 3.92-4.00 s | me 3.25-3.71 s | me 3.83-4.00 s |

G keeps order and the gap but drops a word. O has every word and
lands the last three close to source time, but it is out of order mid-clip, and it ignored the prompt's explicit gap and
timings. Change times come from a colour mask in `measurements.json`, confirmed on the sheets.

### Artifacts

- No black frames or background breakup seen in either. The background stays close to the source in both, and the camera is
  stable (largest shift 1.4 px at 640 wide).
- G: heavy spiky fur on the forearms at 0.16-0.50 s.
- O: mirrored opening pose, and the stretched arm noted above.

## Spend

| Item | Balance before | Balance after | Credits |
|---|---|---|---:|
| Preflight: upload and two quotes | 90.05 | 90.05 | 0 |
| G | 90.05 | 78.05 | 12 |
| O | 78.05 | 66.05 | 12 |
| **Total** | 90.05 | **66.05** | **24** (cap 26) |

Quotes were 12 and 12. `ledger.jsonl` has one reserve and settle for each job. Tool-call audit: 0, 1 and 1 paid submissions;
no retries; no violations.

Headless sessions cost $1.49 in total: preflight $0.47, G $0.49, O $0.53. Each stayed under the $1.50 limit.

## Deviations and limits

- **Three sessions instead of one.** I added a separate preflight so both quotes could be checked before any paid call. This
  cost more agent spend than one session would have.
- **Long waits are not possible.** `jobs_wait` rejects `timeout_seconds` above 15, so the sessions used 12 and 8 short polls.
- **The other agent's run:** `runs/remix-final/` was not touched.
- `another project` git status and HEAD are identical before and after. No files were committed, and nothing was changed in `studio/`.

## Adjacent issues, not changed

- `score.gate` compares beat error across different matched counts, so missing a beat can improve a baseline's error.
- `compose_prompt` emits "1 shots" and "hard cuts at none" for a single-shot clip. It also always ends with "No extra ...
  captions", which sits awkwardly with sources that have captions.
- Older logs in the scratchpad, such as `tests/stream-1.jsonl`, still hold signed upload URLs in the clear. While reading that
  file, one of my tool outputs showed part of an expired 2026-11-04 URL, including its temporary access key ID but not the
  signature. Logs from this run were scrubbed and contain no signed URLs.

## Files

`G.mp4`, `O.mp4`, `source.mp4`, `compare_2fps.png`, `shots.json`, `prompt.txt`, `protocol_lock.json`, `scores.json`,
`measurements.json`, `ledger.jsonl`, `driver.py`, `preflight.json`, `G_run/`, `O_run/`.
