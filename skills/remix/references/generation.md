# Paid generation with remix-studio gen

Every line is one job. Its `body` is the provider's own request, read from the provider's docs, along with the price for `worst_case`. Use the most the route can settle at.

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

Paths in `out` are relative to the run folder; paths in placeholders are relative to `plan/`.

| Field | Meaning |
|---|---|
| `route` | `http:<name>` for a service in `services.json`, otherwise a treg tool id |
| `kind` | `still`, `plate` (3 attempts), `clip` (2). A plan cannot raise these |
| `family` | Lines sharing a model and identity. Nothing but a `canary` runs until `pass-canary` |
| `poll_tool`, `poll_param` | Treg only, for routes the tool doesn't know |
| `"<result of LINE>"` | That line's accepted URL; refused until it exists |
| `"<inline path>"` | The local file as a base64 data URI inside the request, for services that accept them |
| `"<host path>"` | Uploads a local file through treg to a public URL for 7 days. Requires `hosting_consent: true`, set only after the user agreed for this file |

The command reserves before submitting, records each task id, and on any re-run follows the task it already paid for. A refusal or failure releases the hold and won't retry without `--reroll "<reason>"`.

A submit with no clear answer keeps its hold, because a job may exist. A re-run retries with the same idempotency key if the service has one. If it has none, the command refuses to resend, so check the service by hand.

`worst_case` is settled when a provider reports no charge, and marked as an estimate. Compare with the account balance at the end.

A failed job is not proof of a refund. Compare balances before reporting spend.

## Status

Only one route has produced real clips in this tool's history, and it has since been removed. Treat every current route as unproven until its canary has been looked at: expect response-shape surprises on the first still and clip. The `http:` path has run against a local test server only. Motion transfer goes through the subject-swap skill.
