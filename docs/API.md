# Modelbox API

Modelbox exposes two compatible API surfaces in the same server:

- Native Modelbox API: `/api/*`.
- OpenAI-compatible wrappers: `/v1/*`.

The `/v1/*` routes are additive wrappers over the same logic. Existing `/api/*` clients and the panel keep working.

## Authentication

Protected endpoints require:

```http
Authorization: Bearer <API_TOKEN>
```

Public endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health, queue, storage, limits, downloaded/enabled state |
| GET | `/api/pricing` | Current price; USD 0 during initial trial |
| GET | `/api/openapi.json` | OpenAPI schema, including `/v1/*` routes |
| GET | `/api/docs` | Swagger UI |
| GET | `/api/agent-guide` | Concise integration guide for agents |

Protected native endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/models` | Native model list and capabilities |
| GET | `/api/usage` | Usage log and summary |
| POST | `/api/tts` | Native TTS, JSON -> audio/wav |
| POST | `/api/clone` | Native voice clone, multipart -> audio/wav |
| POST | `/api/transcribe` | Native STT, multipart -> `{text, language}` |

Protected OpenAI-compatible endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/models` | OpenAI-style model list: downloaded + enabled models only |
| POST | `/v1/audio/speech` | OpenAI-compatible TTS wrapper over `/api/tts` |
| POST | `/v1/audio/transcriptions` | OpenAI-compatible STT wrapper over `/api/transcribe` |

## Current price

During the initial trial period, the current price is **USD 0**.

- `GET /api/pricing` returns the active pricing object without token.
- `GET /api/usage` includes the same pricing block with usage summary.

## Limits

Read active values from `GET /api/health`, field `limits`.

Default service limits:

| Variable | Default | Applies to | Error |
|---|---:|---|---|
| `MODELBOX_MAX_TTS_CHARS` | `2000` | `/api/tts`, `/v1/audio/speech` | `400` |
| `MODELBOX_MAX_CLONE_CHARS` | `2000` | `/api/clone` | `400` |
| `MODELBOX_MAX_AUDIO_SECONDS` | `1200` | `/api/clone`, `/api/transcribe`, `/v1/audio/transcriptions` | `400` |
| `MODELBOX_MAX_UPLOAD_MB` | `30` | upload endpoints | `413` |

Example health shape:

```json
{
  "status": "ok",
  "queue": { "max_concurrent": 3, "active": 0, "waiting": 0 },
  "limits": {
    "max_upload_mb": 30,
    "max_tts_chars": 2000,
    "max_clone_chars": 2000,
    "max_audio_seconds": 1200
  }
}
```

## OpenAI-compatible endpoints

### `GET /v1/models`

Returns downloaded and API-enabled TTS/STT models only.

```json
{
  "object": "list",
  "data": [
    { "id": "Whisper", "object": "model" },
    { "id": "Pocket-TTS", "object": "model" }
  ]
}
```

### `POST /v1/audio/speech`

Content type: `application/json`.

Request:

```json
{
  "model": "Pocket-TTS",
  "input": "Hola mundo",
  "voice": "alba",
  "response_format": "wav"
}
```

Field mapping:

| OpenAI field | Modelbox native field |
|---|---|
| `input` | `text` |
| `voice` | `voice` |
| `model` | `model` |

Response: audio bytes. Modelbox currently returns `Content-Type: audio/wav` even if `response_format` is omitted.

### `POST /v1/audio/transcriptions`

Content type: `multipart/form-data`.

Fields:

| Field | Required | Notes |
|---|---:|---|
| `file` | yes | Input audio file. Maps to native `audio`. |
| `model` | yes | Usually `Whisper`. |
| `response_format` | no | `json` or `verbose_json`; `verbose_json` is accepted. |
| `language` | no | Optional ISO language code, e.g. `es`. |

Response:

```json
{
  "text": "...",
  "duration": 12.34,
  "language": "es"
}
```

`duration` is mandatory and is measured from the uploaded audio in seconds. Billing systems should use this value for per-second accounting.

## OpenAI error shape for `/v1/*`

All non-2xx `/v1/*` responses use:

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "code": "..."
  }
}
```

Status codes are preserved: `401`, `403`, `404`, `409`, `413`, `422`, `500`, `503`.

## Examples

### OpenAI-compatible TTS

```bash
curl -X POST http://localhost:7860/v1/audio/speech \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Pocket-TTS","input":"Hola mundo","voice":"alba"}' \
  --output speech.wav
```

### OpenAI-compatible STT

```bash
curl -X POST http://localhost:7860/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@grabacion.wav" \
  -F "model=Whisper" \
  -F "response_format=verbose_json" \
  -F "language=es"
```

### Native TTS

```bash
curl -X POST http://localhost:7860/api/tts \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Supertonic-3","text":"Hola mundo","voice":"F1","lang":"es"}' \
  --output salida.wav
```

### Usage

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:7860/api/usage?limit=100"
```

## Native endpoint notes

- `/api/tts` response is `audio/wav`.
- `/api/clone` response is `audio/wav`.
- `/api/transcribe` response is `{ "text": "...", "language": "es" }`.
- `/api/usage` persists metadata in `/modelbox-data/logs/calls.jsonl`; it does not store raw text or audio.
