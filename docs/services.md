# Plugging in a generation service

Everything except generating new pixels runs on your machine. To generate, describe your provider in `~/.config/remix-studio/services.json`, put its key in `~/.config/remix-studio/env`, and use the route `http:<name>`. No code changes.

```bash
mkdir -p ~/.config/remix-studio
cp examples/services.example.json ~/.config/remix-studio/services.json
echo 'REPLICATE_API_TOKEN=...' >> ~/.config/remix-studio/env
chmod 600 ~/.config/remix-studio/env
remix-studio doctor
```

The examples follow Replicate's and fal's public API shapes but **have not been run**, because no key was available. Set the model name, the model's input fields and its price, then run one canary.

## What each field does

| Field | Required | Meaning |
|---|---|---|
| `submit` | yes | `url`, `headers`, optional `method`, `timeout_s`. `${NAME}` is filled from the environment or the env file at send time |
| `task_id_path` | | Where the job id is in the answer. Default `id`. Paths look like `output.0.url` |
| `idempotency_header` | | The provider's idempotency header. Without it, a submit with no clear answer is never resent |
| `poll` | | `url` containing `{task_id}`, `headers`. Leave it out if the submit answer already holds the result |
| `status_path` | with `poll` | Where the job status is |
| `succeeded`, `failed` | | Status words, matched without regard to case. Defaults cover the common ones |
| `error_path` | | Where the failure reason is. Default `error` |
| `result` | | A separate endpoint for the output, read after success |
| `result_url_path` | | Where the output URL is. If left out, the first URL-looking field is used |
| `charge_path`, `charge_scale` | | What the job cost, if the provider reports it. Otherwise the line's `worst_case` is recorded, marked as an estimate |
| `download_headers` | | Headers for fetching the output, if it needs them |
| `job_body`, `tags`, `price` | for `swap` | Below |

## For subject swap

`remix-studio gen` lines carry their own request body. `remix-studio swap` builds its request, so it needs a template:

- `job_body`: your provider's request, using `{prompt}`, `{video_url}`, `{image_urls}`, `{duration_s}`, `{resolution}`, `{aspect_ratio}` and `{generate_audio}`. A value that is only a placeholder keeps its type, so `"{image_urls}"` becomes a list.
- `tags`: how the model's prompt refers to references, such as `{"video": "@video{n}", "image": "@image{n}"}`.
- `price`: `currency`, `per_second` by resolution, optional `durations`, `bill_reference_seconds` if input seconds are billed too, optional `margin` (default 1.02). The ledger holds this amount before spending, so overstate rather than understate it.

## What a model has to do

Subject swap needs a model that accepts both a reference video and reference images. Our results came from Seedance 2.5. Other models are untested here, and some ignore a video they accept without complaint, so look at the first output. `docs/model-routes.md` records what was on offer on 2026-11-05.

Check the model's content rules early. One Seedance route refused a clip of a realistic person, which is a dead end for that kind of footage.

## Treg

A route that isn't `http:` is treated as a [treg](https://treg.to) tool id. Treg is optional and holds credentials on its own server. `<host path>` placeholders and `--host-public` use it too. Without treg, pass your own URLs or use `<inline path>`.
