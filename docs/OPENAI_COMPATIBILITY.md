# Modelbox OpenAI Compatibility

Modelbox exposes OpenAI-compatible audio endpoints under `/v1/*` so existing OpenAI clients, gateways, and routers can call local TTS/STT without custom client code.

The native Modelbox API under `/api/*` remains available and unchanged. Use `/api/*` for Modelbox-specific features such as voice cloning and usage audit.

## Quick path

1. Configure the client base URL as:

   ```txt
   https://inference.cauce.me/v1
   ```

2. Configure the API key as the Modelbox `API_TOKEN`.

3. Discover enabled models:

   ```http
   GET /v1/models
   Authorization: Bearer <API_TOKEN>
   ```

4. Use:

   - `POST /v1/audio/speech` for TTS.
   - `POST /v1/audio/transcriptions` for STT.
   - `POST /v1/embeddings` for embeddings (RAG).
   - `POST /api/clone` for voice cloning.

## Architecture decision

| Surface | Purpose | Compatibility |
|---|---|---|
| `/api/*` | Native Modelbox API, full feature set | Modelbox-specific |
| `/v1/*` | Thin wrappers for OpenAI-style clients | OpenAI-compatible |
| `/api/health` | Public runtime discovery | Public Modelbox metadata |
| `/api/usage` | Call audit and usage summary | Modelbox-specific, token protected |

The `/v1/*` routes are wrappers over the same internal logic used by `/api/*`.
They do not duplicate model loading, queues, state, limits, or storage.

## Authentication

All `/v1/*` endpoints use the same auth as `/api/*`:

```http
Authorization: Bearer <API_TOKEN>
```

If auth fails, `/v1/*` returns OpenAI-shaped errors.

## Voice, language, and capability discovery

Do not extend `/v1/models` with custom voice metadata. Keep it OpenAI-style and simple for client compatibility.

Use native Modelbox discovery instead:

```http
GET /api/models
Authorization: Bearer <API_TOKEN>
```

This is the source of truth for:

- available model ids,
- whether each model is downloaded,
- whether each model is enabled for API use,
- supported voices/presets,
- supported languages,
- whether cloning is supported,
- optional controls such as speed/steps.

Example Supertonic voice usage through OpenAI-compatible TTS:

```json
{
  "model": "Supertonic-3",
  "input": "Hola mundo",
  "voice": "F1",
  "response_format": "wav"
}
```

Known Supertonic voices currently exposed by `/api/models`: `M1`, `M2`, `M3`, `M4`, `M5`, `F1`, `F2`, `F3`, `F4`, `F5`.

Pocket-TTS voices are also discovered from `/api/models`, for example `alba`, `cosette`, `marius`, `javert`, and others.

## Endpoint mapping

### Models

```http
GET /v1/models
```

Returns only models that are both downloaded and enabled for API use.

```json
{
  "object": "list",
  "data": [
    { "id": "Whisper", "object": "model" },
    { "id": "Pocket-TTS", "object": "model" }
  ]
}
```

Native source of truth:

- TTS backends: `shared.backends.BACKENDS`
- STT backends: `shared.transcribe.TRANSCRIBERS`
- Embedding backends: `shared.embeddings.EMBEDDERS`
- enabled state: persisted Modelbox state in `/modelbox-data/state`

### Text to speech

```http
POST /v1/audio/speech
Content-Type: application/json
```

Request:

```json
{
  "model": "Pocket-TTS",
  "input": "Hola mundo",
  "voice": "alba",
  "response_format": "wav"
}
```

Mapping:

| OpenAI-compatible field | Native Modelbox field |
|---|---|
| `model` | `model` |
| `input` | `text` |
| `voice` | `voice` |
| `response_format` | accepted for compatibility; current response is `audio/wav` |

Response: audio bytes with `Content-Type: audio/wav`.

Internal path:

```txt
/v1/audio/speech -> TTSRequest(model, text=input, voice) -> /api/tts logic
```

### Speech to text

```http
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

Request fields:

| Field | Required | Notes |
|---|---:|---|
| `file` | yes | Uploaded audio. Maps to native `audio`. |
| `model` | yes | Usually `Whisper`. |
| `response_format` | no | `json` or `verbose_json`; `verbose_json` is accepted. |
| `language` | no | Optional ISO code, for example `es`. |

Response:

```json
{
  "text": "...",
  "duration": 12.34,
  "language": "es"
}
```

`duration` is mandatory. It is measured from the uploaded audio before transcription and must be used for per-second billing/accounting.

Internal path:

```txt
/v1/audio/transcriptions -> file mapped to audio -> shared transcribe implementation -> {text,duration,language}
```

### Embeddings

```http
POST /v1/embeddings
Content-Type: application/json
```

Request:

```json
{ "model": "EmbeddingGemma", "input": ["text 1", "text 2"], "dimensions": 256 }
```

| OpenAI-compatible field | Native Modelbox field |
|---|---|
| `model` | `model` |
| `input` (string or list of strings) | `input` |
| `dimensions` | `dimensions` (Matryoshka 512/256/128; default 768; other values return `400`) |
| `encoding_format` | accepted only as `float` |

Response (OpenAI shape): `{ "object": "list", "data": [ { "object": "embedding", "index": 0, "embedding": [ ... ] } ], "model": "...", "usage": { ... } }`.

The `/v1/embeddings` wrapper always uses `task=document`. For query-side prompts use native `POST /api/embeddings` with `"task": "query"`.

**Per-text limit — chunk long documents.** Each text is processed up to **~2048 tokens** (EmbeddingGemma's context; hard cap 8000 chars, max 64 texts per call). Longer text is **truncated** (the tail is lost). Split long documents into chunks and send each chunk as an `input` item (one vector per chunk). Modelbox does not chunk.

Internal path:

```txt
/v1/embeddings -> EmbeddingsRequest(model, input, task=document, dimensions) -> /api/embeddings logic
```

## Voice cloning

Voice cloning is intentionally not exposed as an OpenAI-compatible `/v1/audio/speech` feature because the OpenAI speech endpoint does not define a `ref_audio` field.

Use native Modelbox instead:

```http
POST /api/clone
Content-Type: multipart/form-data
Authorization: Bearer <API_TOKEN>
```

Fields:

- `model`: for example `Pocket-TTS`.
- `text`: text to synthesize.
- `ref_audio`: reference voice audio file.
- `ref_text`: optional, only for models that require it.

Response: `audio/wav` bytes.

## Error contract

All non-2xx `/v1/*` responses use OpenAI-compatible error shape:

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "code": "..."
  }
}
```

Status codes are preserved.

| Status | Typical code | Meaning |
|---:|---|---|
| `400` | `invalid_request` | Bad request, unsupported response format, text too long, audio too long |
| `401` | `unauthorized` | Missing or invalid token |
| `403` | `forbidden` | Model exists but is not enabled for API |
| `404` | `not_found` | Unknown model/transcriber |
| `409` | `model_not_ready` | Model exists but is not downloaded |
| `413` | `file_too_large` | Uploaded audio exceeds size limit |
| `422` | `validation_error` | Required field missing or invalid request shape |
| `500` | `server_error` | Runtime/model error |
| `503` | `api_disabled` | `API_TOKEN` not configured server-side |

Native `/api/*` endpoints keep their existing FastAPI error shape.

## Limits and policies

Read active values from:

```http
GET /api/health
```

Field:

```json
{
  "limits": {
    "max_upload_mb": 30,
    "max_tts_chars": 2000,
    "max_clone_chars": 2000,
    "max_audio_seconds": 1200
  }
}
```

Defaults:

| Variable | Default | Applies to |
|---|---:|---|
| `MODELBOX_MAX_TTS_CHARS` | `2000` | `/api/tts`, `/v1/audio/speech` |
| `MODELBOX_MAX_CLONE_CHARS` | `2000` | `/api/clone` |
| `MODELBOX_MAX_AUDIO_SECONDS` | `1200` | clone/STT uploads, including `/v1/audio/transcriptions` |
| `MODELBOX_MAX_UPLOAD_MB` | `30` | clone/STT uploads |
| `MODELBOX_MAX_CONCURRENT` | deployment-specific | inference queue concurrency |
| `MODELBOX_MAX_EMBED_CHARS` | `8000` | `/api/embeddings`, `/v1/embeddings` |
| `MODELBOX_MAX_EMBED_ITEMS` | `64` | `/api/embeddings`, `/v1/embeddings` |
| `MODELBOX_MAX_LOG_MB` | `5` | usage log rotation size |
| `MODELBOX_SIZE_CACHE_TTL` | `30` | `/api/health` storage-size cache (seconds) |
| `MODELBOX_CORS_ORIGINS` | _(unset)_ | comma-separated allowed origins, or `*`; unset = no CORS |

Requests above text/audio-duration limits return `400`. Files above upload-size limit return `413`.

**CORS** is opt-in: set `MODELBOX_CORS_ORIGINS` (e.g. `https://app.example.com,https://example.com` or `*`) to allow browser clients to call the API cross-origin. Auth is via the `Authorization: Bearer` header (not cookies), so credentials are not allowed. Leave it unset for server-to-server use.

## Usage and billing

Current price is USD 0 during the initial trial period.

Public pricing endpoint:

```http
GET /api/pricing
```

Usage endpoint:

```http
GET /api/usage?limit=100
Authorization: Bearer <API_TOKEN>
```

Usage records include call type, surface (`api`, `openai`, or `panel`), model, character counts, upload size, duration, wait time, queue snapshots, HTTP status, and errors. They do not store raw text or audio. Panel (playground) calls are logged to the same audit trail. The log rotates by size (`MODELBOX_MAX_LOG_MB`, default 5).

For STT billing, use the `duration` field returned by `/v1/audio/transcriptions`.

## Implementation notes

Relevant files:

| File | Responsibility |
|---|---|
| `server.py` | `/api/*`, `/v1/*`, OpenAI error wrapping, STT duration response |
| `shared/limits.py` | service-level text/audio/upload limits |
| `shared/inference.py` | concurrency queue |
| `shared/usage.py` | JSONL usage log and summary |
| `shared/backends.py` | TTS backends |
| `shared/transcribe.py` | STT backend |
| `shared/embeddings.py` | Embeddings backend (EmbeddingGemma ONNX) |
| `docs/API.md` | full public API reference |
| `docs/AGENT_INTEGRATION.md` | concise public agent guide served by `/api/agent-guide` |

## Verification checklist

- [ ] `GET /api/health` includes `limits`.
- [ ] `GET /v1/models` returns OpenAI-style list.
- [ ] `POST /v1/audio/speech` returns non-empty `audio/wav` bytes.
- [ ] `POST /v1/audio/transcriptions` returns `text`, `duration`, and `language`.
- [ ] `POST /v1/embeddings` returns `data[].embedding` with the requested `dimensions`.
- [ ] `/v1/*` auth/validation errors return `error.message`.
- [ ] `/api/clone` still works for voice cloning.
- [ ] `/api/usage` records TTS, clone, and STT calls.

Windows smoke script:

```cmd
set HOST=https://inference.cauce.me
set API_TOKEN=TU_TOKEN
"C:\Users\Orion\OneDrive\Desktop\sistemas\all_projects\modelbox\scripts\test_v1_prod.cmd"
```
