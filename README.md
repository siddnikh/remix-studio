# remix-studio

**A self-hosted alternative to Higgsfield Katana and Higgsfield Genjutsu.** Two skills and a command-line tool let your own coding agent (Claude Code, Codex, Cursor and others) make video edits on your machine. The only thing you rent is the model that generates new footage, through [Sume](https://www.sume.com) at its per-second prices.

| Skill | Alternative to | What it does |
|---|---|---|
| `remix` | Katana | Makes a finished edit in the visual language of a reference video: cut rhythm, camera moves, type and music |
| `subject-swap` | Genjutsu | Puts your character into an existing clip and keeps its shots, camera and timing |

**How close is it?** In our side-by-side tests, `remix` was competitive with Katana, and `subject-swap` didn't yet match Genjutsu's picture quality. Those clips were generated through Higgsfield's models, and no clip has run through Sume yet. See [Results](#results) for the evidence.

This project isn't affiliated with Higgsfield or Sume.

## Why use it

- **Your agent, your files.** The logic is plain skills and Python that you can read and change, and every plan and render stays on your disk.
- **Pay per job.** There's no subscription or credit bundle. On Sume, a 4 s subject swap at 480p prices at about $0.49, and it's checked before anything is spent.
- **Hard spend caps.** Every paid call is held against a cap first. A re-run follows the job it already paid for, and a submit with no clear answer is never blindly resent.
- **Local editing.** Measuring, rendering, type, music, sound and scoring run on your machine at no cost per run. Your agent's own usage is still billed by its provider.
- **Not locked to one vendor.** Sume is the default, and any other HTTP API can be added through a config file.

## Install

You need Python 3.11 or newer and ffmpeg (`brew install ffmpeg`).

```bash
git clone https://github.com/siddnikh/remix-studio.git
cd remix-studio
./install.sh --dry-run   # shows what would change
./install.sh
```

The installer is safe to re-run. It creates `.venv` in this folder, adds the `remix-studio` command to `~/.local/bin`, and links both skills into every agent it finds. It only adds symlinks, and it won't touch anything already using those names. Because they are symlinks, a `git pull` updates every agent at once.

| Agent | Skills folder | Checked on 2026-11-05 |
|---|---|---|
| Claude Code | `~/.claude/skills` | Both skills loaded |
| Codex | `~/.codex/skills` | Ran `subject-swap` step 1 from another folder |
| Hermes | `~/.hermes/skills/media` | Both listed by `hermes skills list` |
| OpenCode | `~/.config/opencode/skills` | Both listed by `opencode debug skill` |
| Cursor, Gemini, Factory, Kiro, Continue, `~/.agents` | Each agent's own folder | Linked, but not confirmed from inside the agent |

For any other agent, run `.venv/bin/remix-studio install-skills --target /path/to/its/skills`. An agent with no skill system can still run the command directly, so point it at the two `SKILL.md` files.

To remove everything, run `remix-studio uninstall-skills`. It removes only the links that point here.

## Connect Sume

Create a key at [sume.com/dashboard/api-keys](https://www.sume.com/dashboard/api-keys) and add funds (from $5). Giving the key its own spend limit there adds a second safety net.

```bash
mkdir -p ~/.config/remix-studio
echo 'SUME_API_KEY=your-key' >> ~/.config/remix-studio/env
chmod 600 ~/.config/remix-studio/env
remix-studio doctor
```

Keys are read only when a request is sent, so they never reach a plan, state file or log. Keep them out of your agent's chat.

| Route | Runs | Live price checked on 2026-11-05 |
|---|---|---|
| `http:sume` | Seedance 2.5, the model all our results used | $0.4112 for 4 s at 480p, or about $0.49 with a 4 s reference clip |
| `http:sume-image` | Stills such as GPT Image 2.5 | $0.0059 per low-quality image, $0.0132 at medium |
| `http:sume-remix` | Sume's own motion transfer model | $0.123 per source second at 480p. Not yet tried by this project |

A reference clip adds its own length to the billed seconds: a 10 s reference with a 4 s output prices at $0.8609. Sume's estimator is free and needs no key, so `remix-studio gen estimate` can check a line first, and every paid line is checked again before it's submitted.

To use another provider, see [docs/services.md](docs/services.md). [Treg](https://treg.to) routes work too, if you have it.

## Use

Ask in plain words, such as "remix this edit with my character" or "put this fox into this clip". You can also call a skill directly: `/remix` or `/subject-swap` in Claude Code, `$remix` or `$subject-swap` in Codex.

```bash
remix-studio doctor              # what's ready on this machine
remix-studio where               # fonts, music, sound effects and config, as absolute paths
remix-studio analyze  --help     # cuts, flashes, shakes, camera moves, beats, contact sheets
remix-studio edl      --help     # validate and render an edit decision list with ffmpeg
remix-studio gen      --help     # run paid stills and clips under a spend cap
remix-studio swap     --help     # the subject-swap pipeline
remix-studio score    --help     # compare an output with its source or reference
```

### What has been run so far

The first clip through Sume is a real test, so run one canary and look at it before spending more.

| Path | Status |
|---|---|
| Sume price estimates, through the tool | **Live**, for video and stills |
| Sume's refusal of a bad key | **Live**: a real 401, with nothing charged |
| Sume upload, submit, polling, result and failure handling | Tested against a local server that follows Sume's documented responses. **No real job yet, because no key was available** |
| Naming references in a Sume prompt (`image 1`, `video 1`) | **Unverified.** Sume doesn't document this for Seedance, so check it on the first clip |
| Sume's rules on realistic people | **Unknown.** Another Seedance route refused our test clip for showing a real person |
| Another route, through `remix-studio gen` | Live on treg: one real image for $0.005, matching the account balance |

Seedance 2.5 on Sume also requires reference clips of at least 407,696 pixels in total, about 640×640. 854×480 clears that.

The Higgsfield adapter that produced the clips below has been removed from the code. It's kept in git under the tag `baseline-with-higgsfield-adapter`.

## Results

**Watch: [docs/results/comparison.mp4](docs/results/comparison.mp4)** (76 s, 1080p, side by side and labelled).

These tests ran on 2026-11-05 at 480p, on short clips with no speech or lip sync. Each setup had one attempt, with no fixed seed. Generated clips in both our versions ran through Higgsfield's Seedance 2.5, so they compare the method rather than the Sume route. Sume lists the same model, but its results may differ. Nobody has listened to the audio, and review was of frames rather than playback.

### `remix` as a Katana alternative: competitive

Same reference (the first 8 s of a selfie-style clip), same subject and brief, and a 30-credit cap. The rows are the reference, Katana and ours.

![Reference, Katana and our edit, sampled across 8 seconds](docs/results/remix-vs-katana.png)

| | Katana | `remix` |
|---|---:|---:|
| Finished edit (K1) | Pass | Pass |
| Rhythm matches the reference (K2) | Fail: 0.25 shake bursts/s against 0 | Pass, but it's a weak test on this reference |
| Credits | 28.39 | 25.5 |
| Loudness | -25.8 LUFS | -16.3 LUFS |
| Agent tokens, including cache reads | about 22 M | about 10.3 M |

- **Where `remix` is stronger:** it echoes the reference's reach-to-camera hands, while Katana's first scene is nearly static.
- **Where Katana is stronger:** its fox stays more consistent between scenes, and it can mask subjects with a video segmentation model. `remix` has no video masks, face lock, stickers or dissolves.
- **Where both fall short:** both look like clean AI demos, and both split big words around the fox's head. Our critique of `remix`: floaty slowed spans, no tail, and no payoff at the end.
- **About the tokens:** our run included fixing ten bugs, and the figure doesn't count building the skill. Neither run is a clean measure of a repeat run.

### `subject-swap` as a Genjutsu alternative: not there yet

Same clip, same subject, one attempt each. On the clip we held back, with the prompt fixed before anything ran, Genjutsu looks better overall.

![Source, Genjutsu and ours at 2 frames per second, on the held-back clip](docs/results/swap-holdout.png)

| Held-back clip (4 s) | Genjutsu | `subject-swap` |
|---|---:|---:|
| Tracking score | 0.670 | 0.700 |
| Action beats found | 1 of 2 | 2 of 2 |
| Beat timing error | 0.083 s | 0.125 s (fails G2) |
| Duration | 3.71 s (fails G4) | 4.00 s |
| Credits | 12 | 12 |

- **Genjutsu's advantages:** a sharper, more expressive face, richer fur, and hand beats that stay in order. Our hands smear and arrive late.
- **`subject-swap`'s advantages:** exact duration, no time drift, and every caption word kept, though out of order mid-clip.
- **Don't lean on our tracking win:** it's narrow, and it flips to 0.731 for Genjutsu if its clip is retimed to 4 s.
- **Neither reproduced the facial performance** in the last second of the clip.

On the first clip, where the prompt method was developed (so it isn't a fair blind test), `subject-swap` did better on the numbers:

| First clip (4 s) | Genjutsu replace | Genjutsu motion | `subject-swap` |
|---|---:|---:|---:|
| Tracking score | 0.739 | 0.500 | 0.855 |
| Action beats found | 3 of 3 | 2 of 3 | 3 of 3 |
| Beat timing error | 0.222 s | 0.250 s | 0.056 s |
| Duration | 3.71 s | 4.04 s | 4.00 s |

The character was weaker there too. The sheet is at [docs/results/swap-first-clip.png](docs/results/swap-first-clip.png).

### What it costs

These are Sume's live estimates for the same job shapes, not completed jobs. The Higgsfield figures are what we paid in credits, because its credits don't have a single dollar price in our evidence.

| Job | On Higgsfield | On Sume |
|---|---:|---:|
| 4 s subject swap with a 4 s reference, 480p | 12 credits | about $0.49 |
| `remix` generation: two 4 s clips and three medium-quality stills, 480p | 25.5 credits | about $0.86 |

Published credit prices put that first job at roughly $0.52 to $0.90 on Higgsfield. Sume's own [comparison page](https://www.sume.com/compare/sume-remix-vs-higgsfield-genjutsu), checked by Sume on 2026-10-27, lists Genjutsu at $0.318 per input second at 480p, or half that at the maximum discount, which is about $0.64 to $1.27 for 4 s. That's the vendor's own comparison, and we haven't checked it against Higgsfield's API prices.

So a saving looks likely but isn't proven. Retries and your agent's own usage add to any of these figures.

The full reports are in [docs/results/](docs/results/), and the acceptance criteria, written before the results, are in [docs/SPEC.md](docs/SPEC.md).

## Layout

```text
skills/remix/          the remix skill, with references, CC0 music and sound, and OFL fonts
skills/subject-swap/   the subject-swap skill
studio/                the Python package behind the remix-studio command
docs/services.md       Sume, and adding other generation services
docs/results/          what was measured, and the comparison video
docs/SPEC.md           acceptance criteria
docs/model-routes.md   treg routes and prices seen on 2026-11-05
tests/                 no test spends money
```

```bash
.venv/bin/python -m pytest -q
```

The suite takes about 3 minutes. Tests that need the original evaluation clips run only when `REMIX_STUDIO_EVAL_CLIPS` points at them.

## Before making this repo public

This repo is private and has no licence yet. Three things need a decision first:

- **Higgsfield's terms.** A third-party summary reports no-reverse-engineering and no-competing-products clauses, which we haven't checked on their live page. This project was built by studying how their products behave, and `skills/remix/references/craft.md` paraphrases lessons learned from them. No Higgsfield scripts are included.
- **Footage in `docs/results/`.** The comparison video and sheets contain a sample clip showing a real person, plus Higgsfield's output. We don't hold rights to these, so remove them from the repo and its history before publishing.
- **Git history.** The `baseline-with-higgsfield-adapter` tag and first commit contain the removed adapter and earlier notes.

Bundled music, sound effects and fonts have their own licences, listed in [skills/remix/assets/LICENSES.md](skills/remix/assets/LICENSES.md).

Use footage and likenesses you have the right to use.
