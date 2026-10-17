# MinerU Cloud Parsing (read chain)

MinerU (mineru.net, OpenDataLab / Shanghai AI Lab) parses PDFs and images into
Markdown with layout, table and formula recognition. This skill uses it as the
**first tier for PDF and images only**; other formats stay local.

Added 2026-10-17. Read chain only — `edit_*.py` are untouched.

## Why PDF/images only

The official API also accepts Office (doc/docx/ppt/pptx/xls/xlsx) and HTML, but
this skill routes only PDF + images through it. Local docling already handles
Office well, and routing Office to the cloud adds latency for no gain.

## Token

Resolved by `mineru_token()` in `office_common.py`, in order:

1. Environment variable `MINERU_TOKEN`
2. Credential file `~/.hanako/secrets/mineru.token` (bare token, single line, mode 600)

Both missing → the mineru tier fails fast with
`MINERU_TOKEN not set and no credential file` and the chain falls through to docling.
The token is never written into source.

## Endpoints used

Base: `https://mineru.net/api/v4`, header `Authorization: Bearer <token>`.

| Step | Endpoint |
|---|---|
| Apply upload URLs (batch) | `POST /file-urls/batch` |
| Upload file | `PUT <file_urls[i]>` (raw body, no Content-Type needed) |
| Poll batch result | `GET /extract-results/batch/{batch_id}` |

Local files **must** use the batch endpoint — `/extract/task` only accepts a
public URL. A single file is just a batch of one. After upload the service
auto-submits; no explicit submit call.

Submit body:

```json
{"files": [{"name": "a.pdf", "data_id": "id0"}], "model_version": "vlm"}
```

- `model_version`: `vlm` (this skill's choice; official default is `pipeline`)
- `is_ocr`: not sent (official default `false`; scanned files are recognised anyway)
- `data_id` is echoed back on results and used to map file → verdict
- Upload links are valid for 24h

## Task states

`waiting-file → pending → running → converting → done` (or `failed`).
`done` = success, `failed` = failure, anything else = keep polling.
While `running`, the response carries `extract_progress.total_pages`.

Poll interval 3s, timeout 300s (independent from docling's 600s).

## Result zip

`full_zip_url` downloads a zip containing:

```
full.md                        Markdown (this skill reads this)
<hash>_origin.<ext>            original file
<hash>_model.json              model output
<hash>_content_list.json
<hash>_content_list_v2.json
layout.json                    middle representation
images/                        extracted images (PDF with images only)
```

This skill extracts **only** `full.md` (text) and `images/*` (to
`{stem}_mineru/images/`), then rewrites the image links in `full.md` so they
resolve relative to the output file. The rest of the zip is discarded.

## Limits (official)

| Limit | Value |
|---|---|
| File size | ≤ 200 MB |
| Pages per file | ≤ 200 |
| Upload links per `file-urls/batch` call | ≤ 50 |
| Files per batch job | ≤ 200 |
| Priority quota | 1000 pages/day at top priority; beyond that, lower priority (not a hard cap) |

## Error codes (official)

Non-zero `code` → the tier fails and the chain falls through.

| code | meaning |
|---|---|
| `A0202` | Token error (check the Bearer prefix / replace token) |
| `A0211` | Token expired |
| `-500` | Bad request parameters |
| `-10001` | Service error |
| `-10002` | Request parameter error |
| `-60001` | Failed to generate upload URL |
| `-60002` | File format mismatch (needs a correct suffix) |
| `-60003` | File read failed (corrupt) |
| `-60004` | Empty file |
| `-60005` | File size exceeds limit (>200MB) |
| `-60006` | Page count exceeds limit |
| `-60007` | Model service temporarily unavailable |
| `-60008` | File read timed out |
| `-60009` | Submit queue full |
| `-60010` | Parse failed |
| `-60011` | Could not get a valid file (upload failed) |
| `-60012` | Task not found |
| `-60013` | No permission for this task |
| `-60015` / `-60016` | File conversion failed |
| `-60017` | Retry limit reached |
| `-60018` | **Daily parse task count reached** (not a concurrency limit) |
| `-60019` | HTML parse quota exhausted |
| `-60021` | Failed to read page count |
| `-60022` | Web page read failed (URL mode only) |

## Usage / quota query

`GET /api/v1/usage` (Bearer) returns the account's limits, e.g.:

```json
{"max_pages_per_file": 200, "max_file_size_bytes": 209715200,
 "max_files_per_job": 200, "max_concurrent_jobs": 500,
 "max_file_retention_days": 30}
```

`max_file_retention_days` semantics (uploaded file vs result zip) is unverified;
this skill always pulls results to local disk immediately and never relies on
cloud retention.

## HTTP client

Requests go through `curl` (`subprocess`), not `urllib`/`requests`: `urllib`
against `mineru.net` gets `Connection reset` on this machine, while `curl` works.
`office_common.py` stays standard-library-only.
