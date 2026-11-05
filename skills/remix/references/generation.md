# Paid generation with remix-studio gen

Every line is one job. Its `body` is the provider's own request. On Sume the fields are in `https://www.sume.com/docs/video` and `/docs/images`. Set `worst_case` from `remix-studio gen estimate PLAN --line X`, which is free.

```json
{
  "cap": 3.0,
  "currency": "USD",
  "hosting_consent": false,
  "lines": [
    {"id": "kf1", "kind": "still", "family": "stills", "canary": true, "route": "http:sume-image", "worst_case": 0.02,
     "body": {"model": "gpt-image-2.5", "prompt": "...", "image_size": "1024x1536", "quality": "low",
              "image_urls": ["<upload ../ref/style.png>", "<upload ../subject.png>"]},
     "out": "stills/kf1.png"},
    {"id": "plate1", "kind": "plate", "family": "stills", "route": "http:sume-image", "worst_case": 0.02,
     "body": {"model": "gpt-image-2.5", "prompt": "... environment only, nobody in frame", "image_size": "1024x1536", "quality": "low"},
     "out": "stills/plate1.png"},
    {"id": "c1", "kind": "clip", "family": "fox", "canary": true, "route": "http:sume", "worst_case": 0.7,
     "body": {"model": "seedance-2.5", "prompt": "image 1 ... image 2 ...", "duration": 6, "aspect_ratio": "9:16",
              "resolution": "480p", "generate_audio": false,
              "reference_image_urls": ["<result of kf1>", "<result of plate1>"]},
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
| `"<upload path>"` | Uploads a local file to the service, once per file content, at a URL anyone with the link can open. Requires `hosting_consent: true`, set only after the user agreed for this file |
| `"<inline path>"` | The local file as a base64 data URI inside the request, for services that accept them. Sume does not |
| `"<host path>"` | Uploads a local file through treg to a public URL for 7 days. Requires `hosting_consent: true`, set only after the user agreed for this file |

On Sume, a price above `worst_case` or an input the model would refuse stops the line before it's submitted, with nothing spent. The command reserves before submitting, records each task id, and on any re-run follows the task it already paid for. A refusal or failure releases the hold and won't retry without `--reroll "<reason>"`.

A submit with no clear answer keeps its hold, because a job may exist. A re-run retries with the same idempotency key if the service has one. If it has none, the command refuses to resend, so check the service by hand.

With no reported charge and no estimate, `worst_case` is settled and marked as an estimate. Sume says its estimate is the charge, so a Sume line settles at that. Compare with the account balance at the end.

A failed job is not proof of a refund. Compare balances before reporting spend.

## Status

No clip has run through Sume yet. Its estimates are live, but its upload, submit and polling have run only against a local server that follows Sume's docs, so expect surprises on the first still and clip and run them as canaries. How Seedance on Sume wants references named in a prompt is undocumented: the example uses `image 1`, so check the canary used them. Motion transfer goes through the subject-swap skill.
