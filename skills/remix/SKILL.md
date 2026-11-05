---
name: remix
description: >-
  Make a finished video remix from a reference edit plus subject images, in the reference's visual language (cut rhythm, flashes, shakes, camera moves, typography, music), or edit supplied footage to a brief. Runs the whole job: measure the reference, plan, buy stills and clips under a spend cap, assemble with an EDL, check, deliver. Use for "remix this edit", "make this edit with my character", "recreate this style". Not for a single subject swap on one clip: use the subject-swap skill.
---

# Remix

Author a new edit for the user's subject that speaks the reference's language. It is never a shot-for-shot rebuild, even when asked to "copy" it: take the grammar, make new scenes. Footage the user has rights to may be reused where it fits; say which parts are reused.

Work in the user's project folder. Layout: `runs/<slug>/{ref,plan,stills,clips,qa,audio}`. If `remix-studio` is not found, tell the user to run `install.sh` from the remix-studio repo.

Run `remix-studio doctor` first. It says which generation routes are ready. **If none is, the measuring, rendering and scoring still work with footage and images the user already has. To generate anything new, ask the user which service to use and point them at `remix-studio where` (`services_example`). Never set one up or pay for one on your own.** `remix-studio where` also gives the bundled fonts, music and sound effects as absolute paths: use those in the EDL.

**The two things that decide quality:** craft lessons in [references/craft.md](references/craft.md) (read before writing prompts) and looking at real frames rather than trusting any number.

## Budget for attention

Tokens are the largest hidden cost. Past runs of this kind of workflow used 4.5 to 5.6 million tokens, mostly from re-reading a long chat. Aim for under 1.5 million.

- View about 15 images in total before delivery: 5 on the reference, 4 on stills and the canary, 6 on the render. Add more only for a specific doubt.
- Scripts measure every frame, so you don't need to view every frame. Look where the measurements point.
- Keep tool output short: pipe logs to files, print the tail. Don't read the tool's source; use `remix-studio <command> --help`.
- No exploratory subagents. One QA round, then one regression check after fixes.
- Save decisions to `plan/` as you go, so a new session can resume from files.

## 1. Inspect the reference

```bash
remix-studio analyze probe ref.mp4
remix-studio analyze energy ref.mp4 -o runs/<slug>/ref/energy.json
remix-studio analyze sheet ref.mp4 runs/<slug>/qa/ref_all.png --fps 2 --max-frames 30
```

`energy.json` holds cuts, flashes, shake bursts, camera moves and audio hits with per-second rates. Read its `rates` and `per_second`, not the whole event list. Trim first if only part of the file is the reference: rates are per second of what you measure. K2's 25% band can be strict on a sparse edit: one cut in 8 s allows exactly one cut in 8 s. Note any `insert_in` cuts: one-frame inserts are easy to miss on a sparse sheet, so spend one dense sheet (`ffmpeg ... select`) around the busiest second and native-size crops on the typography. Sheets show the frames you ask for, so say what you did not see.

Separate what you observed from what you are inferring, and write the roles of useful moments: intro, escalation, reveal, payoff.

## 2. Task contract: `plan/task-contract.json`

`explicit_locks` (attribute, scope, value, the user's words), `creative_freedoms`, `references`, the hero and the clothing in the user's image, the finite spend cap and who set it. A lock binds only what it names. Resolve taste decisions yourself. Ask the user only for what you cannot decide: the spend cap if none was given, and consent before hosting a private photo at a public URL.

## 3. Creative plan: `plan/creative-plan.json`

Concept or motif in one or two lines; `output_target` (size, fps, frames, audio interval); shots with role, one main action, camera, location id, text, source of footage; `energy`: where cuts, flashes, shakes and hits land, from the reference's rates. Plan the wording now, because text is drawn in the edit, never generated. Plan sound now: [references/audio.md](references/audio.md). Mark what comes from the reference and what is new.

Route each shot by the cheapest thing that truly serves it: existing footage, then a still with code motion, then a generated clip. Use the subject-swap skill only where a shot's role needs a particular performance copied.

## 4. Quote and cap

Write `plan/gen_plan.json` ([references/generation.md](references/generation.md)). Read prices from the service's own page or `treg catalog get <route>`. Never quote from memory.

```bash
remix-studio gen quote runs/<slug>/plan/gen_plan.json
```

Tell the user the total and the cap. **No paid call happens outside `remix-studio gen` or `remix-studio swap`**: they reserve under the ledger first. The cap is fixed once set; a higher cap needs a new request from the user. Never top up.

## 5. Stills

If the user supplied a good subject image and footage, skip what you can: stills also need a generation route. Keyframe first. Approve a single keyframe of the hero, then pass it as the first input image to all remaining stills, so style and identity don't drift. Separately generate one environment-only plate per location and reuse it. Look at every still at full size before any video uses it, with the checklist in [craft.md](references/craft.md). **Plates must be checked for people**: the prompt saying "no people" does not stop them. Record each verdict and reason in `qa/stills.json`.

## 6. Canary, then batch

Run one representative clip per model and identity family, the one with the hardest constraint. View a sheet of it. Then `remix-studio gen pass-canary`, and only then run the rest of that family; the script refuses otherwise. Wait for every job to finish before cataloguing footage or writing the EDL: a clip that arrives late gets wasted.

```bash
remix-studio gen run  runs/<slug>/plan/gen_plan.json --line c1
remix-studio analyze sheet runs/<slug>/clips/c1.mp4 runs/<slug>/qa/c1.png --fps 2
```

`remix-studio gen` prints the real frame count. Look at each clip's sheet once: hands, props, merging limbs, identity, stray lettering. Write rejected spans to that source's `bad_ranges` in the EDL. Choose in-points from what the frames show, never from the times the prompt asked for.

## 7. Assemble: `plan/edit.json`

Format and examples: [references/edl.md](references/edl.md). Cuts land on the beat grid of the chosen music. Generated clips come back calmer than the reference, so put its camera energy into the edit: pushes, shake bursts, punches, whips, flashes at the reference's rates. Those rates can be zero. A reference whose energy comes from gesture and type gets no added effects at all: its language is the absence of them, and an added flash fails K2.

Type that sits behind the subject takes `behind` and a matte ([edl.md](references/edl.md)). Choose the font by the reference's letter shape: `Anton` for condensed, `Archivo-ExpandedBlack` for wide.

```bash
remix-studio edl validate runs/<slug>/plan/edit.json
remix-studio edl render runs/<slug>/plan/edit.json --preview -o runs/<slug>/qa/preview.mp4
```

The validator catches frames placed twice, clips shorter than assumed, rejected ranges, wrong totals, missing fonts or glyphs and clipped text. Fix the plan rather than marking things `repeat`, unless the reference really repeats footage there.

## 8. Check

```bash
remix-studio edl render runs/<slug>/plan/edit.json -o runs/<slug>/final.mp4
remix-studio score finish runs/<slug>/final.mp4 --target-duration <s>
remix-studio score language ref.mp4 runs/<slug>/final.mp4
```

`edl render` already checks length, black, white and frozen frames, repeated footage, bars, stream set, audio length, loudness and true peak. `score language` compares measured rates, using the same detector on both videos, within 25%. Whips and flashes can register as shake and flash events, so tune against this measured number, not the rates you authored.

**Numbers can pass while the video looks bad; in testing the checks passed on a render whose glitch strips were visibly wrong.** So view:

1. One sheet of the whole edit (every 4th frame).
2. Tiles of each event's frames: whip, glitch, flash, text entrance and exit, numbered.
3. One native-size frame per subject appearance, next to the user's photo, for identity and clothing.
4. The reference and output side by side (`remix-studio swap compare`) for grade, type and energy.

Ask: does it say the concept? Is every word readable and correctly spelled? Is a face torn by a glitch at a moment that matters? You cannot hear the mix: report audio as measured, not listened to, unless a person listened.

Fix in this order, and stop as soon as it passes:

1. **Code and EDL, free:** timing, in-points, grade, text, mix. At most two passes.
2. **Re-cast, free:** another in-point, unused seconds of a paid clip, a still with motion.
3. **One paid retry per failed job**, only for something on the fail list: identity, clothing, garbled text or logo, anatomy, or a missing beat no footage supplies. "A better take" does not qualify. `remix-studio gen run PLAN --line X --reroll "<reason>"`.

After fixes, compare the changed shots with the previous version once to catch regressions, then stop. Report anything unresolved instead of looping.

## 9. Deliver

Give the final file, actual spend (`remix-studio gen status`, plus the account balance before and after), what was reused, licences of music, sound and fonts, and every unresolved deviation. Label a partial result as partial. Report each acceptance criterion in the spec (`remix-studio where` gives its path) that was measured, and name those that weren't.
