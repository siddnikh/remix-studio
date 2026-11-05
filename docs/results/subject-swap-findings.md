# Genjutsu-equivalent: findings (2026-11-05)

## Verdict

The method works on this clip; a route we control does not yet exist for it. Seedance 2.5 with a shot-by-shot prompt,
run on Higgsfield's own Seedance endpoint, passed G1, G2, G3 and G4 against both Genjutsu baselines. The same job cannot
run today on any route treg offers, because the only affordable one refused the clip for showing a realistic person,
and the routes that accept it cost more than the balance.

So we cannot match Genjutsu today without Higgsfield. Evidence is one clip, one subject and one job per configuration,
which is enough to choose the next test but not enough to call this solved.

## Hypothesis

Hypothesis under test: a video-reading step, a shot-by-shot prompt and a video-input model together reproduce Genjutsu.

**It held on this clip, with two corrections.**

1. The prompt was enough. B and D are the same model, mode (`omni_reference`), duration, resolution and media; only the
   prompt changed. No Genjutsu-specific model and no `video_edit` mode was needed.
2. The reader can be simple. The shot list came from a 6 fps contact sheet plus `analyze.py`'s measured cuts. Higgsfield's
   rewritten prompt for job C placed cuts at 1, 2 and 3 s; the measured cuts, confirmed by eye, are at 1.33 and 1.83 s.

Not established: whether the difference survives another seed or clip. Neither was repeated, and seeds were not fixed.
The prompt was also not written blind, because I had read Higgsfield's rewritten prompt first. Its subject description
overlaps that text, including one phrase of about ten words on the hands, feet and tail. A seven-word overlap check found
that only in git-ignored run data (`prompt.txt`, the shot list); tracked docs and code contain none. Reword the subject
description before any shot list is committed.

## Results

All scores are from the current `studio/score.py`, against `drive_4s.mp4` (4.000 s, 24 fps). A, B and C match
`docs/baselines.json`.

| Output | Tracking | Layout | Motion | Cut F1 | Beats | Beat error | Duration | Second-half tracking |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: Genjutsu motion | 0.500 | 0.582 | 0.302 | 0.50 | 2/3 | 0.250 s | 4.04 s | 0.36 |
| C: Genjutsu replace | 0.739 | 0.805 | 0.425 | 1.00 | 3/3 | 0.222 s | 3.71 s (fails G4) | 0.65 |
| C retimed to 4 s (not an official baseline) | 0.829 | 0.878 | 0.609 | 1.00 | 3/3 | 0.194 s | 4.00 s | 0.88 |
| B: Seedance, one-line prompt | 0.677 | 0.721 | 0.500 | 0.80 | 3/3 | 0.000 s | 4.04 s | 0.53 |
| **D: Seedance, shot-by-shot prompt** | **0.855** | 0.859 | 0.757 | 1.00 | 3/3 | 0.056 s | 4.00 s (raw 4.04 s) | 0.87 |

The tracking score ignores the subject, so it cannot detect a failed swap. Visual review is what confirms the fox.

### Acceptance criteria for D

| Criterion | Result | Basis |
|---|---|---|
| G1 Tracking | Pass | 0.855 against A 0.500 and C 0.739 |
| G2 Beats | Pass | 3/3 in order; 0.056 s against A 0.250 s and C 0.222 s |
| G3 Identity | Pass, with a flaw | Visual review below |
| G4 Duration | Pass | 96 frames, 4.000 s, after trimming one extra frame |
| C1 Cost | Not established | See "Cost" |

`score.gate` also reports D failing G2 against B, whose beat error is 0.000 s. B is not a Genjutsu baseline, so this does
not affect acceptance.

### Visual review

Sheets: `runs/genjutsu-test/compare_2fps.png` (source, A, C, B, D at 2 fps) and
`runs/genjutsu-test/D_higgsfield/sheet_6fps.png`. These are stills, so temporal flicker was not assessed.

- **D** is the fox in all 24 sampled frames. Scooter, bag, bottle, mirror, steps and building all match. It follows every
  source beat at the source's time: wide view and rush-in, cut to drinking, cut to the steps, push-in with the bag large
  in the foreground, pull-back to full figure.
- **D's flaws:** the tail renders stubbier and rounder than the long tail in the reference image, sometimes close to a
  puff. The picture is soft in the distant final shot, where face detail is small. The source's compact-closing action
  is not clearly legible at the end.
- **A** keeps the drinking shot until about 2.8 s where the source cuts away at 1.83 s, then skips the push-in. Its tail
  and face detail are better than D's.
- **C** has the sharpest identity and the fullest tail, and is the closest Genjutsu result, but runs short and compresses
  the timeline: final offset -0.25 s.
- **B** matches the source for about 2.5 s, then stays in close-up, losing the bag and the pull-back.

## Corrections to what we assumed

- **"Genjutsu followed and Seedance drifted" was only partly true.** B outscores A, whose beat timing is visibly wrong.
  B's drift is confined to the last 1.5 s. Only C beats B.
- **D's lead over Genjutsu's best is narrow.** Against C retimed to 4 s it is 0.855 to 0.829, and C has better layout
  (0.878 to 0.859), second-half tracking (0.88 to 0.87) and identity. D wins on motion and beat timing.
- **"OpenRouter's Seedance route cannot take a video" is out of date.** treg's parameter list omits it, but the route
  accepts `input_references` of type `video_url`. The video reached ByteDance, which refused it. Details are in
  `docs/model-routes.md`.
- **`omni_reference` versus `video_edit` is a live variable.** The Higgsfield contract offers both, and earlier tests used
  only `omni_reference`. `video_edit` remains untested.

## Spend

| Account | Before | After | Actual spend | Limit |
|---|---|---|---:|---|
| Higgsfield | 102.05 | 90.05 | 12 credits | 15 credits, floor 87: both respected |
| treg | $0.5083 | $0.5083 | $0.00 | one job |

- **Higgsfield:** one `get_cost` quote of 12 credits, then one job (`9242ee43-55c6-49f4-82f3-4e9ebd0d4689`). Media IDs from
  `tests/results.md` were still valid. The session audit found one submission and no violations. A free quote for
  `video_edit` on this clip came to 14 credits. Running it now would leave 76.05, below the floor, so it was not run.
- **treg:** no job was charged. Each hold was released or refused: one refused by ByteDance's filter, and the others
  by the balance.
- **Headless Claude session:** $0.79 in agent usage, against a $1.50 session cap, mostly from polling `jobs_wait` across
  27 turns. This belongs to the test harness, not to the product path.
- **Public files:** `treg host` was used on `drive_4s.mp4` and `subject.png` only. They stay public until 2026-11-11.

### Why no treg job ran

The real test clip could not be run within the balance:

| Route | Outcome | Cost |
|---|---|---|
| `openrouter.video-gen.seedance-2-5` | HTTP 400, input video may contain a real person | Free |
| `piapi.video-gen.seedance-2-5` | 402, needs $0.75 | Free |
| `reapi...unrestricted`, edit mode | 402, needs a $3.558 hold | Free |
| `reapi...unrestricted`, fixed 4 s | Not attempted: the $0.4744 hold fits, but it should settle near $0.95 and overdraw the balance | Not run |

Two other options were rejected. Masking the person to pass the filter would defeat a provider's safety control, so I did
not try it. Spending the balance on a fox-to-fox job, using a Genjutsu output as the reference, could not test a subject
swap and would have raised the top-up needed for the real test.

### Cost (C1)

C1 is not established. On Higgsfield, D costs 12 credits, the same as Genjutsu, so that route saves nothing. The credit's
dollar price is not in our evidence, so $0.95 on reAPI cannot yet be compared with 12 credits. Tokens for writing the shot
list were not measured.

## What is needed

**Decision for you: which sources must this handle?** If realistic people, the relaxed route is required, and you should
be sure you have the right to use that person's likeness, since it switches off the filter that protects it. If only
fictional or animated sources, the OpenRouter route may suffice, at an expected $0.49 for a 4 s job.

| Step | Needs | Expected cost | Answers |
|---|---|---|---|
| 1. Same prompt on `reapi...unrestricted`, fixed 4 s | treg balance at least $0.96 (top-up at least $0.46) | About $0.95 | Does this work off Higgsfield? |
| 2. Second clip and second seed, same configuration | Higgsfield credits above your floor, or treg balance | 12 credits or about $0.95 each | Is D's result real? |
| 3. `video_edit` with an edit-worded prompt | 14 credits (below your floor today), or treg balance at least $3.56 | 14 credits or about $0.95 | Does edit mode beat reference mode? |
| 4. DreamActor, Kling O1 and other Replicate routes | A Replicate key | Unknown; the catalog publishes no price | Is a motion-transfer model better? |
| 5. FLUX Video Edit | An OpenRouter key, as it is not in treg's catalog | About $0.12 at its listed $0.03/s, untested | A much cheaper edit route? |

Step 1 is the one that decides whether we can replace Higgsfield.

## What was built

- `studio/ledger.py`: append-only ledger with a finite cap. It refuses a reservation past the cap and records every
  settlement, including one past the cap, after which it blocks further spending. It fails closed on a corrupt file.
- `studio/providers.py`: adapters for the OpenRouter, reAPI (both filters) and PiAPI Seedance routes, and for Higgsfield
  through a headless session. Quotes separate the provider's hold from the ceiling a call can settle at, which is the trap
  on usage-settled routes: reAPI's 4 s hold is $0.4744, but its charge should be about $0.95.
- `studio/genjutsu.py`: `prepare`, `quote`, `run` and `compare`. `run` is resumable and will not pay twice for one run
  directory. Public hosting requires an explicit flag.

To drive it: run `prepare`, view `source_sheet.png`, write a shot list, then `quote`, then `run` with `--cap`. The shot
list used here is `runs/genjutsu-test/shots_drive_4s.json`, which is git-ignored. Fields: `duration_s`, `subject`
(`name`, `description`), `source_subject`, `setting`, `keep`, and `shots` (`start`, `end`, `framing`, `camera`, `action`).
Shots must be contiguous and cover the clip. Disagreement with the measured cuts is reported as a warning.

Prompts for fixed-duration jobs must not read as edits. reAPI documents that the model classifies the prompt itself and
fails a fixed-duration job it classifies as an edit. D's reference-style wording passed on Higgsfield; the edit wording in
`compose_prompt` has not been run.

## Verification status

- **Tests:** 79 tests pass in `tests/test_ledger.py`, `tests/test_providers.py` and `tests/test_genjutsu.py`. None spends
  money or calls `treg`, `claude` or the network; ffmpeg runs locally.
- **Live, full path:** the Higgsfield adapter, through audit, download, duration enforcement and scoring.
- **Live, partial:** the OpenRouter adapter reached ByteDance and handled a refusal. Its polling and video retrieval have
  never run against a real task, and its $0.49 price is arithmetic from the rate card, not an observed charge.
- **Not run live beyond the balance refusals:** reAPI and PiAPI. Their response parsing follows catalog examples only.
- **Other repos:** `git status` in the other project is the same before and after the session.

## Adjacent issues

- `analyze.sheet` can mark a cut one tile early: 1.333 s is rounded below the 1.3333 s tile boundary, so the 1.17 s tile
  shows "CUT". The cut list itself is correct. I left this for the owner of `analyze.py`.
- `score.py` changed while this work ran: A moved from 0.530 to 0.500. The pipeline now gates against one baselines file so
  it cannot compare across scorer versions, but `baselines.json` needs regenerating whenever the scorer changes.
- The `report.json` for run D holds gates computed against the older baseline numbers. The table above uses a fresh
  re-score, and D passes the same gates either way.
