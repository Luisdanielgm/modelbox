# Modelbox Agent Integration Guide

Use this guide when an automated agent needs to call Modelbox without opening the UI.
The visual panel may require username/password, but API execution uses a Bearer token.

Production base URL:

```txt
https://inference.cauce.me
```

## Recommended path for OpenAI clients

If your client already supports the OpenAI API shape, use `/v1/*`:

1. Read public state: `GET /api/health`.
2. Read available OpenAI-style models: `GET /v1/models` with Bearer token.
3. Use:
   - `POST /v1/audio/speech` for TTS.
   - `POST /v1/audio/transcriptions` for STT.
4. Handle OpenAI-shaped errors from `/v1/*`.

Current price: **USD 0** during the initial trial period. Use `/api/pricing` to read the active pricing object.

## Public references

| URL | Purpose | Auth |
|-----|---------|------|
| `/api/health` | Runtime health, queue, limits, storage, downloaded/enabled state | none |
| `/api/pricing` | Current price; initial trial returns USD 0 | none |
| `/api/openapi.json` | Machine-readable OpenAPI schema, including `/v1/*` | none |
| `/api/agent-guide` | This concise agent guide | none |
| `/api/docs` | Swagger UI | none |

## Authentication

Protected endpoints require:

```http
Authorization: Bearer <API_TOKEN>
```

Never embed the token in source code. Pass it through environment/configuration.

## OpenAI-compatible endpoints

### `GET /v1/models`

Returns downloaded and enabled models only:

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

JSON body:

```json
{
  "model": "Pocket-TTS",
  "input": "Hola mundo",
  "voice": "alba",
  "response_format": "wav"
}
```

Response: audio bytes. Modelbox currently returns `audio/wav`.

### `POST /v1/audio/transcriptions`

Multipart form:

- `file`: audio file, required.
- `model`: model id, required, usually `Whisper`.
- `response_format`: optional. `verbose_json` is accepted.
- `language`: optional ISO code, for example `es`.

Response:

```json
{ "text": "...", "duration": 12.34, "language": "es" }
```

`duration` is mandatory and is measured from the uploaded audio in seconds.
Use it for per-second billing/accounting.

## OpenAI error shape

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

Preserved status codes include `401`, `403`, `404`, `409`, `413`, `422`, `500`, and `503`.

## Native Modelbox endpoints

Use these when you need Modelbox-specific features like voice cloning or usage audit.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/models` | Native models and capabilities |
| GET | `/api/usage` | Usage log + summary |
| POST | `/api/tts` | Native TTS -> WAV |
| POST | `/api/clone` | Voice clone -> WAV |
| POST | `/api/transcribe` | Native STT -> `{text, language}` |

## Usage audit

`GET /api/usage?limit=100` returns pricing, summary, and recent calls.
The log lives in the persistent volume at `/modelbox-data/logs/calls.jsonl`.
It stores metadata only: no raw text and no audio bytes.

## Limits

Read active limits from `GET /api/health`, field `limits`. Current service defaults:

- `MODELBOX_MAX_TTS_CHARS=2000` for `/api/tts` and `/v1/audio/speech`.
- `MODELBOX_MAX_CLONE_CHARS=2000` for `/api/clone`.
- `MODELBOX_MAX_AUDIO_SECONDS=1200` for uploaded clone/STT audio.
- `MODELBOX_MAX_UPLOAD_MB=30` for uploaded clone/STT audio.

If text/audio duration is too large, Modelbox returns `400`. If the uploaded file is too large, it returns `413`.

## Minimal curl examples

### OpenAI-compatible TTS

```bash
curl -f -X POST "$MODELBOX_URL/v1/audio/speech" \
  -H "Authorization: Bearer $MODELBOX_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Pocket-TTS","input":"Hola mundo","voice":"alba"}' \
  --output output.wav
```

### OpenAI-compatible STT

```bash
curl -f -X POST "$MODELBOX_URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $MODELBOX_API_TOKEN" \
  -F "file=@reference.wav" \
  -F "model=Whisper" \
  -F "response_format=verbose_json" \
  -F "language=es"
```

### Native clone

```bash
curl -f -X POST "$MODELBOX_URL/api/clone" \
  -H "Authorization: Bearer $MODELBOX_API_TOKEN" \
  -F "model=Pocket-TTS" \
  -F "text=Hola con voz clonada" \
  -F "ref_audio=@reference.wav" \
  --output clone.wav
```
