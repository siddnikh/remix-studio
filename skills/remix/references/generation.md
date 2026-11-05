# Paid generation with gen.py

Every line is one job. On a treg route the body is the provider's own request; read it from `treg catalog get <route>`, along with the price for `worst_case`. Use the most the route can settle at. Routes and what can run today: `docs/model-routes.md`. If the plan bills through Higgsfield, see "Higgsfield lines" below.

```json
{
  "cap": 9.0,
  "currency": "USD",
  "hosting_consent": false,
  "lines": [
    {"id": "kf1", "kind": "still", "family": "stills", "canary": true,
     "route": "reapi.image-gen.gpt-image-2-5", "poll_tool": "reapi.tasks.get", "worst_case": 0.0173,
     "body": {"model": "gpt-image-2.5-flare", "prompt": "...", "size": "1024x1536", "n": 1,
              "image_urls": ["<host ../ref/style.png>", "<host ../subject.png>"]},
     "out": "stills/kf1.png"},
    {"id": "plate1", "kind": "plate", "family": "stills", "route": "reapi.image-gen.gpt-image-2-5",
     "poll_tool": "reapi.tasks.get", "worst_case": 0.0173,
     "body": {"model": "gpt-image-2.5-flare", "prompt": "... environment only, nobody in frame", "size": "1024x1536", "n": 1},
     "out": "stills/plate1.png"},
    {"id": "c1", "kind": "clip", "family": "fox", "canary": true,
     "route": "reapi.video-gen.seedance-2-5", "worst_case": 0.72,
     "body": {"model": "doubao-seedance-2.5-face", "prompt": "@image1 ... @image2 ...", "duration": 6, "size": "9:16",
              "resolution": "480p", "generate_audio": false,
              "image_urls": ["<result of kf1>", "<result of plate1>"]},
     "out": "clips/c1.mp4"}
  ]
}
```

Paths in `out` are relative to the run folder; `<host path>` is relative to `plan/`.

| Field | Meaning |
|---|---|
| `kind` | `still`, `plate` (3 attempts), `clip` (2). A plan cannot raise these |
| `family` | Lines sharing a model and identity. Nothing but a `canary` runs until `pass-canary` |
| `poll_tool`, `poll_param` | Needed for routes `studio/providers.py` doesn't know |
| `"<result of LINE>"` | That line's accepted URL; refused until it exists |
| `"<host path>"` | Uploads a local file to a public URL for 7 days. Requires `hosting_consent: true`, set only after the user agreed for this file |

`gen.py` reserves before submitting, records each task id, and on any re-run follows the task it already paid for. A submit that times out keeps its hold and retries with the same idempotency key. A refusal or failure releases the hold and won't retry without `--reroll "<reason>"`.

`worst_case` is settled when a provider reports no charge, and marked as an estimate. Compare with `treg balance` at the end.

## Higgsfield lines

Use these when the plan is billed in Higgsfield credits. The route starts with `higgsfield.`, `currency` is `credits`, and the plan names the folder where the Higgsfield MCP is connected. Read a model's parameters and its media roles with `models_explore` (`action: get`) before writing its `params`.

```json
{
  "cap": 30,
  "currency": "credits",
  "higgsfield": {"cwd": "/path/with/the/mcp", "model": "haiku", "quote_media": "../ref/subject.png"},
  "lines": [
    {"id": "c1", "kind": "clip", "family": "fox", "canary": true, "route": "higgsfield.seedance_2_5", "worst_case": 12,
     "body": {"tool": "generate_video", "params": {"model": "seedance_2_5", "mode": "omni_reference", "resolution": "480p",
              "aspect_ratio": "16:9", "duration": 4, "generate_audio": false, "bitrate_mode": "standard", "count": 1,
              "medias": [{"role": "image_references", "value": "<media ../stills/kf1.png>"}],
              "prompt": "@Image1 ..."}},
     "out": "clips/c1.mp4"}
  ]
}
```

`"<media path>"` uploads a local file once per file content and becomes its media id. Reference an accepted still this way: it needs to be on disk, so it is the file you looked at.

`gen.py quote-live PLAN --line X` gets a free `get_cost`. Run it once per new settings combination and set `worst_case` from it; a paid session quotes again and submits nothing if the price exceeds `worst_case`. `quote_media` stands in for stills that don't exist yet.

Each upload, quote or job is one headless `claude -p` session, and facts are read from its recorded tool results in `plan/hf_logs/`, never from its summary. A re-run reads the log and only waits on a known job, so it cannot pay twice. A session with no result keeps its hold, because a job may exist: check `show_generations` before going on.

The charge is the job's own quote, not a balance difference, because the balance is account-wide and another run on the account would inflate it. Compare balances at the end anyway and account for any gap.

## Status

Live on 2026-11-05 for `gpt_image_2_5` and `seedance_2_5` through Higgsfield. The treg paths have run against a fake provider only, so expect their first real still and clip to expose response-shape surprises: run them as canaries. The Seedance body uses `image_urls` with no video, which `studio/providers.py`'s `Job` does not model (it requires a `video_url`); that's why this script calls the route directly. Motion transfer goes through `studio/genjutsu.py`.
