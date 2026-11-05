# Model routes for a reference-video job

Read with `treg catalog get` and `treg catalog search` on 2026-11-05 (CLI 0.29.0). Team balance at the time: $0.5083.
"Callable now" means callable on this team today: no key to connect, and the job fits the balance. A 4 s 480p job
is the yardstick wherever the route offers it.

The table states only what the catalog shows. Anything learned elsewhere is under "Outside the catalog", marked with
its source.

## Routes asked about

| Route | Video input in catalog | Output duration | Catalog price | Callable now |
|---|---|---|---|---|
| `piapi.video-gen.seedance-2-5` | Yes: `input.video_urls`, up to 3 | 5, 8, 10, 15, 20, 25, 30 s (no 4 s) | $0.15 / $0.35 / $0.80 per s at 480p / 720p / 1080p | No: shortest job $0.75 exceeds balance |
| `piapi.video-gen.seedance-2-5.less-restriction` | Yes: `input.video_urls`, up to 3 | Same as above | $0.165 / $0.385 / $0.88 per s | No: shortest job $0.825 exceeds balance |
| `reapi.video-gen.seedance-2-5` | Yes, in notes only: not in the parameter list, but `price_note` names `video_urls` and `duration` describes editing a @video | 4-30 s, or -1 (auto), which the catalog says the provider requires when the prompt edits a @video | $0.1186 / $0.26683 / $0.462 per s; -1 reserves $3.558 at 480p; settles at reported usage; references add their length to billable seconds | No: 4 s holds $0.4744, which fits, but references add billable seconds and settlement can exceed the hold (see below); -1 hold exceeds balance |
| `reapi.video-gen.seedance-2-5.unrestricted` | Yes, in notes only, as above | Same as above | Same as above; `content_filter:false` required | No: same as above |
| `openrouter.x.alibaba-wan-2-7` | No video parameter; summary says reference images | Up to 10 s, default 2 | $0.10/s flat, 720p or 1080p only; a settle may exceed the hold | Yes, but not as a video-input route, and has no 480p |
| `replicate.x.wan-video-wan-2-7-r2v` | Yes: `input.reference_videos` | 2-10 s, default 5 | Not published; BYOK only | No: no Replicate key |
| `replicate.x.bytedance-dreamactor-m2-0` | Yes: `input.video` plus `input.image` | No duration parameter; `cut_first_second` defaults to true | Not published; BYOK only | No: no Replicate key |
| `replicate.x.prunaai-p-video-animate` | Yes: `input.video` plus `input.image` | No duration parameter | Not published; BYOK only | No: no Replicate key |
| `replicate.x.kwaivgi-kling-o1` | Yes: `input.reference_video`, `video_reference_type` | Default 5 s; no range listed | Not published; BYOK only | No: no Replicate key |

Every Replicate row above says "no replicate credential in this org yet". None of these rows has been verified against
the live API by the catalog ("verified: not verified against the live API") except the four Seedance rows (2026-09-14).

## Other routes found by search

| Route | Video input in catalog | Output duration | Catalog price | Callable now |
|---|---|---|---|---|
| `openrouter.video-gen.seedance-2-5` | Not in the parameter list (first/last frame images only), but `price_note` says a reference-video input, "not listed", bills at a cheaper token rate | 4-30 s | $0.1028 / $0.2312 per s at 480p / 720p; settles at `usage.cost` | Yes on price; see live test below |
| `openrouter.x.runway-aleph-2` | No video parameter; summary says it edits existing footage | Up to 30 s, default 5 | $0.28/s | No: 4 s is $1.12 |
| `openrouter.x.alibaba-wan-2-6` | No video parameter; summary mentions reference videos | Up to 10 s, default 5 | $0.04-$0.15/s | Yes on price, but no documented video input |
| `openrouter.x.bytedance-seedance-2-0`, `-fast`, `-mini` | No parameter; `-mini` summary names video input | Up to 15 s | Not published; BYOK only | No: needs an OpenRouter key |
| `openrouter.x.minimax-hailuo-3` | No video parameter; summary says multimodal editing | Up to 15 s | Not published; BYOK only | No: needs an OpenRouter key |
| `replicate.x.bytedance-seedance-2-0`, `-fast` | Yes: `input.reference_videos` | -1 to 15 s | Not published; BYOK only | No: no Replicate key |
| `replicate.x.kwaivgi-kling-v3-omni-video` | Yes: `input.reference_video` | 3-15 s | Not published; BYOK only | No: no Replicate key |
| `replicate.x.luma-modify-video` | Yes: `input.video` or `input.video_url`; no reference image parameter | No duration parameter | Not published; BYOK only | No: no Replicate key |
| `openrouter.x.kwaivgi-kling-video-o1` | No: text and image only, unlike Replicate's Kling O1 | Up to 10 s | $0.112/s | Yes, but no video input |
| `minimax.video-gen.h3.generate` | No: text and up to two frame images | 4-15 s | $0.05-$0.13/s | Yes, but no video input |
| `openrouter.video-gen.wan-3-0.*`, `replicate.video-gen.veo-3.1-fast`, `replicate.video-gen.seedance-1-lite` | No | Various | $0.018-$0.20/s | Yes, but no video input |

## Outside the catalog

These come from provider documents and live calls, not from treg.

- **OpenRouter's request schema** (`/openapi.json`) has `input_references`, which accepts `image_url`, `audio_url` and
  `video_url`. It says video references are honoured by Seedance 2 and newer, and that other providers ignore them, so
  sending a video to a non-Seedance model on OpenRouter may silently produce a text-only clip you still pay for.
- **OpenRouter's rate card** (`/api/v1/videos/models`) prices Seedance 2.5 with video input at $0.0000064 per video
  token, and describes Wan 2.7's allowed passthrough parameters as including `video` and `videos`.
- **reAPI's documentation** gives billable seconds with a reference video as the larger of reference plus output, and
  5/3 of the output. For a 4 s reference and 4 s output that is 8 s, or $0.9488 at 480p, twice the $0.4744 hold. It
  also documents an `omni_reference_task_type` of `reference`, `edit` or `extend`, where `edit` forces duration -1.
- **FLUX Video Edit** (`black-forest-labs/flux-video-edit`) is on OpenRouter's rate card at $0.03/s, and its page says
  output keeps the source's duration. It is **not in the treg catalog**, so it needs an OpenRouter key. Whether it takes
  a subject reference image is unknown.

## Live checks of the OpenRouter Seedance route

These were free, because the requests never created a task.

1. A body with a malformed `input_references` entry came back as OpenRouter's own validation error listing `image_url`,
   `audio_url` and `video_url`. So treg forwards this unlisted field. Hold $0.4112 reserved and released at once.
2. A valid body with the test clip and fox came back as HTTP 400 from ByteDance:
   `InputVideoSensitiveContentDetected.PrivacyInformation`, "the input video ... may contain real person". So the video
   does reach the model on this route, and the default filter blocks it for this clip.

The earlier assumption that this route cannot take a video is therefore out of date. It can take one, but its default
filter refuses clips that show a realistic person, and its fixed durations rule out an edit task.

## Verdict for this job

No route in the catalog can run the realistic test clip today. The routes that accept the clip's content need more
balance or a key:

| Need | What it unlocks | Expected cost for a 4 s 480p job |
|---|---|---|
| Balance of at least $0.96 | `reapi...unrestricted` in reference mode, fixed duration | About $0.95 |
| Balance of at least $3.56 | `reapi...unrestricted` in edit mode (hold is 30 s of output; settles to actual) | About $0.95 |
| Replicate key | DreamActor, Kling O1, Wan 2.7 r2v, p-video-animate | Unknown: catalog publishes no price |
| OpenRouter key | FLUX Video Edit, outside this catalog | About $0.12 at its listed rate, untested |
