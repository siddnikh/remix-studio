# Remix final run: report

**Finished: yes, as a complete 8 s video.** K1 and K2 pass. K3 and C1 are not established. Nobody has listened to the audio.

Video: `runs/remix-final/final.mp4` (854x480, 24 fps, 192 frames, 8.000 s, AAC audio, 2.6 MB).

## What was made, and from what

Reference: first 8.000 s of `Orange_Jacket.mp4`, trimmed to `ref/ref.mp4`. It is used for measurement and viewing only, and no reference pixels are in the output. Its lyric wording is not used either; the words are new.

Concept: a fox shows the moves with its paws ("paws up, paws down, tail round"), then the words outgrow the room ("head over heels"), giant and behind it.

| Line | Model and settings | Input | Used for | Credits |
|---|---:|---|---|---:|
| `kf1` | `gpt_image_2_5` medium, 1k, 16:9 | `subject.png` | Identity for both clips | 0.5 |
| `plate_loft` | same | `kf1` | Attic location | 0.5 |
| `plate_lane` | same | `kf1` | Village lane location | 0.5 |
| `c1` | `seedance_2_5` `omni_reference`, 480p, 4 s, no audio | `kf1`, `plate_loft` | Before the cut, 0 to 5.0 s | 12 |
| `c2` | same | `kf1`, `plate_lane` | After the cut, 5.0 to 8.0 s | 12 |

Each line succeeded on its first attempt, so no retries were used. Each clip returned 97 frames for a 96-frame request. From `c2`, source frames 6 to 66 are used.

Type: Archivo Expanded Black, a new bundled font under the SIL OFL, in `#fcc1ee` sampled from the reference. Words hold still and swap on hard frames, as the reference's do.

Audio: "Unbeatable Arcade Machine" by HoliznaCC0 (CC0, bundled excerpt), plus one `hit_owlish.wav` (CC0, bundled) on the cut. Words and the cut sit on the track's measured 12-frame grid. The reference has no audio, so this is an addition.

## Money

| | Credits |
|---|---:|
| Balance at start | 90.05 |
| This run, from the ledger | 25.5 of the 30 cap |
| Two jobs not in this run's logs (`47a29926`, `532eb277`) | 24 |
| Balance at end | 40.55 |

The figures reconcile exactly. The two other jobs ran before this run's first paid job and match the separate Genjutsu test, whose transcript is a different agent's. Their individual prices were inferred from the gap (90.05 to 66.05), not read. Treg was not used. The 4.5 unspent credits could not buy a third clip, so **no clip retry was possible under this cap**.

Billing went through Higgsfield for our pipeline's models, as agreed. No Katana, Genjutsu, preset, `sandbox_exec` or playbook tool was used, and the playbook was not read.

## Tokens (C1)

- This agent: about 10.3 million tokens by the transcript's count when this was written, made up of 9.98 million cache reads, 235 thousand cache writes, 128 uncached input and 104 thousand output. The last few messages add slightly to this.
- Headless sessions: $0.14 across 13 sessions on Haiku (11 in `gen.py status`, $0.1212, plus the first exploration and the last reconciliation, about $0.016).

**C1 is not established.** No real Katana cost on this job was measured, and a credit's dollar price is not in our evidence. **The skill's target of under 1.5 million tokens was missed if cache reads count.** Without them the run used about 340 thousand. Most of that is this run fixing the pipeline; a repeat without fixes should use less, but that was not measured. 16 images were viewed against the skill's budget of about 15.

## Acceptance criteria

| Criterion | Result | Basis |
|---|---|---|
| K1 Finished edit | **Pass** | `score finish`: 8.0 s, audio present, no black or frozen frames, no repeats. `qa/k1.json` |
| K2 Language | **Pass** | `score language`: cuts 0.125/s against 0.125/s; flashes 0 against 0; shake bursts 0 against 0. `qa/k2.json` |
| K3 Quality | **Not measured** | No real Katana output on these inputs |
| C1 Cost | **Not established** | See above |

K2 is close to trivial on this reference: it needed one cut and no effects. Passing says nothing about gesture or type, which are what this reference is about. The renderer measured -16.3 LUFS and a -5.1 dBTP true peak. **This is measured, not heard.** No person has listened to the mix or to this music excerpt.

## Skill and renderer bugs found

Fixed. The full test suite passes after the last change: 192 tests, none of which spend money. The fixes to `gen.py` and the adapter were also exercised live by this run.

1. **`gen.py` could only pay through treg**, and it built the treg client even when unused. Added `higgsfield.*` lines, `quote-live` and `<media path>` for local stills (`HiggsfieldJobs` in `studio/providers.py`).
2. **The existing Higgsfield adapter was video-only and lacked `generate_image`.** Left unchanged, because the other agent was running it. The new class sits beside it, so the two now overlap and should be merged later.
3. **Audit misread `media_confirm`**, whose id is under `results[]`. This caused three failed attempts that each uploaded the file: free, but they left orphan media items on the account.
4. **Audit counted a call the client rejected as a paid submission.** This raised a false alarm that a quote session had paid. The log showed no job existed.
5. **A balance difference is not a job's cost on a shared account.** 24 credits moved during this run from another agent. Lines now settle at the job's own quote.
6. **The EDL could not put type behind a subject**, which is this reference's signature. Added top-level `matte`, text `behind`, and `edl matte`, using macOS Vision (macOS 14+ only). A stale matte is refused.
7. **The only bundled font was condensed.** Added a wide one with its licence.
8. **The skill says to measure the beat grid, but no command did.** `analyze energy` rejects audio-only files. Added `analyze beats`. It agrees with the skill's 120 BPM note. Its first test run also caught a 20 ms bias in the new code, now corrected.
9. **The skill's "put the energy into the edit" advice is wrong for a reference with no effects.** Rewritten: rates can be zero.
10. **Blending at 0.8x doubled the claws on the fast paw, and every check passed.** Only a native-size crop caught it. Fixed in the edit by ramping speed from measured motion, and written into `craft.md` and `edl.md`.

Documented but not fixed:

- `generation.md` still describes the treg paths as tested against a fake provider only. That remains true.
- The validator does not warn about blending on fast motion. A warning from measured motion would have caught item 10.
- `matte.m` compiles with clang only because Swift fails against this machine's command-line tools. Behind-subject type is macOS-only.

## Fixes made after viewing frames

1. **First render: giant words were unreadable.** The arms ran through them ("h...d", "h...s") and baselines jumped between words. Raised them above the arms on one baseline. `qa/final_v1.mp4` shows the old version.
2. **Second render: ghosted paw**, item 10 above. `qa/final_v2.mp4` shows it.

The side-by-side is `qa/side_by_side.png`. The contact sheet is `qa/final_2fps.png`.

## Harsh critique

- **It looks like a good AI demo, not a real selfie.** The reference's charm is a real person's messy, quick, slightly awkward performance. Our fox moves smoothly and politely, and the lane section is close to a single slow lean-in.
- **The slow-down is doing a lot of work.** Stretching 4 s clips to fill 5 s and 3 s leaves the calm spans floaty, especially "tail" and "round" (about 0.64x), where almost nothing happens. This was seen only in sampled frames, so judge it in playback.
- **"tail round" does not match the picture.** No tail is visible anywhere in the video, so the long leaf tail in the subject photo, its most distinctive feature, is never seen. The belt and legs never appear either.
- **"heels" reads only by completion.** The head hides the middle letters, leaving roughly "he..ls". The reference does the same to "and", but "heels" is a longer and less predictable word.
- **"paws" and "up" sit partly over pink paw pads**, in nearly the same pink. They remain readable, but they are the weakest type in the piece.
- **The ending has no payoff.** To keep "heels" readable, the toothy close-up at the end of `c2` was given up, so the video ends on a half-lidded smile, with a music fade, and no final beat.
- **The rate of type is lower than the reference's**: 9 words in 8 s against about 10 to 12, counted from sampled frames. K2 does not measure this. The cut also falls at 5.0 s against the reference's 5.73 s.
- **Identity holds, with drift.** It is clearly the same fox in all sampled frames, but it is more wolfish in `c2`'s grin than in the photo. Blinks and squints from about 6 to 7 s hide the amber eyes during "over".
- **Temporal flicker and matte stability were not assessed in playback.** All review was of still frames. Edges looked clean in the frames sampled, not in all 72.

## Not verified

- Listening to the audio.
- Playback at speed, including matte shimmer and the feel of the speed joins.
- K3 and C1.
- A retry under this cap, which could not be tested.
- Repeatability: one take per clip, no fixed seed.

Other notes: nothing was committed, `another project`'s `git status` hash is unchanged, and `runs/final/genjutsu/` was not entered.
