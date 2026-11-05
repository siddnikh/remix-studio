# Generation services

Everything except generating new pixels runs on your machine. **Sume is built in**, so all it needs is a key:

```bash
mkdir -p ~/.config/remix-studio
echo 'SUME_API_KEY=your-key' >> ~/.config/remix-studio/env
chmod 600 ~/.config/remix-studio/env
remix-studio doctor
```

## Sume routes

| Route | Endpoint | Use |
|---|---|---|
| `http:sume` | `POST /v1/video-router/generate` | Video. `remix-studio swap` sends Seedance 2.5. In a `gen` plan line, name any model from Sume's [model list](https://www.sume.com/docs/models) |
| `http:sume-image` | `POST /v1/image-router/generate` | Stills, with the model named in the plan line |
| `http:sume-remix` | `POST /v1/sume/remix/motion-transfer/v1.0` | Sume's own motion transfer, with `swap --task edit`. Source 4 to 15 s, 1 to 8 images |

Sume's [estimate endpoint](https://www.sume.com/docs/billing) is free, needs no key and also rejects bad inputs, so the tool uses it in three ways:

- `remix-studio gen estimate PLAN --line X` prices a line on request.
- Before each paid submit, a price above the line's `worst_case`, or a rejected input, stops the job with nothing spent.
- Sume says the estimate is the charge, so the line settles at that price.

Local files go up through `<upload path>` in a plan or `--upload` in `swap`. The resulting URL can be opened by anyone who has the link, so a plan needs `hosting_consent: true`, and it should be set only after the user agrees for that file.

Not verified yet: a real job through any of these routes, how Seedance on Sume wants references named in a prompt (the config uses `image 1` and `video 1`), and Sume's rules on realistic people.

## Adding another service

Describe the provider in `~/.config/remix-studio/services.json` and use the route `http:<name>`. No code changes are needed. An entry with a built-in's name replaces that built-in. `studio/default_services.json` is a full worked example.

| Field | Required | Meaning |
|---|---|---|
| `submit` | yes | `url`, `headers`, optional `method`, `timeout_s`. `${NAME}` is filled from the environment or the env file at send time |
| `task_id_path` | | Where the job id is in the answer. Default `id`. Paths look like `data.result.artifacts.0.url` |
| `idempotency_header` | | The provider's idempotency header. Without it, a submit with no clear answer is never resent |
| `poll` | | `url` containing `{task_id}`, `headers`. Leave it out if the submit answer already holds the result |
| `status_path` | with `poll` | Where the job status is |
| `succeeded`, `failed` | | Status words, matched without regard to case. Defaults cover the common ones |
| `error_path` | | Where the failure reason is. Default `error`. Also tried on the `result` endpoint |
| `result` | | A separate endpoint for the output, read after success |
| `result_url_path` | | Where the output URL is. If left out, the first URL-looking field is used |
| `charge_path`, `charge_scale` | | What the job cost, if the provider reports it. Otherwise the estimate, or else the line's `worst_case` marked as an estimate |
| `estimate` | | `url` and `usd_path` of a free price check, sent as `{"model": ..., "input": {...}}` |
| `upload` | | `url`, `headers`, and the paths `public_url_path`, `upload_url_path`, `upload_headers_path`, for a two-step signed upload |
| `download_headers` | | Headers for fetching the output, if it needs them |
| `job_body`, `tags`, `tasks`, `price` | for `swap` | Below |

### For subject swap

`remix-studio gen` lines carry their own request body. `remix-studio swap` builds its request, so it needs a template:

- `job_body`: your provider's request, using `{prompt}`, `{video_url}`, `{image_urls}`, `{duration_s}`, `{resolution}`, `{aspect_ratio}` and `{generate_audio}`. A value that is only a placeholder keeps its type, so `"{image_urls}"` becomes a list.
- `tags`: how the prompt refers to references, such as `{"video": "@video{n}", "image": "@image{n}"}`.
- `tasks`: `reference` (fixed output length, the default), `edit` (output follows the source), or both.
- `price`: needed if there's no `estimate`. `currency`, `per_second` by resolution, optional `durations`, `bill_reference_seconds` if input seconds are billed too, optional `margin` (default 1.02). The ledger holds this amount before spending, so overstate rather than understate it.

The model has to accept both a reference video and reference images. Some models ignore a video they accept without complaint, so look at the first output.

## Treg

A route that isn't `http:` is treated as a [treg](https://treg.to) tool id. Treg is optional and holds credentials on its own server. `<host path>` placeholders and `--host-public` use it too. `docs/model-routes.md` records what it offered on 2026-11-05.
