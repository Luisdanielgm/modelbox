# Modelbox Agent Integration Guide

Use this guide when an automated agent needs to call Modelbox without opening the UI.
The visual panel may require username/password, but API execution uses a Bearer token.

## Quick path

1. Read public service state: `GET /api/health`.
2. Read model capabilities: `GET /api/models` with `Authorization: Bearer <API_TOKEN>`.
3. Pick a model where `downloaded=true` and `enabled=true`.
4. Call the right endpoint:
   - `POST /api/tts` for preset TTS.
   - `POST /api/clone` for voice cloning.
   - `POST /api/transcribe` for STT.
5. Read usage later with `GET /api/usage?limit=100`.

Current price: **USD 0** during the initial trial period. Use `/api/pricing` to read the current pricing object.

## Public references

| URL | Purpose | Auth |
|-----|---------|------|
| `/api/health` | Runtime health, queue, storage, downloaded/enabled state | none |
| `/api/pricing` | Current price. During the initial trial it returns USD 0 | none |
| `/api/openapi.json` | Machine-readable OpenAPI schema | none |
| `/api/agent-guide` | This concise agent guide | none |
| `/api/docs` | Swagger UI | may be less convenient for agents |

## Authentication

Protected endpoints require:

```http
Authorization: Bearer <API_TOKEN>
```

Never embed the token in source code. Pass it through environment/configuration.

## Endpoint contracts

### `GET /api/models`

Use this before generating. It returns exact model names and capabilities.

Important fields:

- `name`: exact value to send as `model`.
- `downloaded`: model files are available.
- `enabled`: API access is enabled from the panel.
- `capabilities.clone`: model can use `/api/clone`.
- `capabilities.presets`: valid `voice` values.
- `capabilities.languages`: valid `lang` values.

### `POST /api/tts`

JSON body:

```json
{
  "model": "Pocket-TTS",
  "text": "Hola mundo",
  "voice": "alba"
}
```

Response: `audio/wav` bytes.

### `POST /api/clone`

Multipart form:

- `model`: for example `Pocket-TTS`.
- `text`: text to synthesize.
- `ref_audio`: reference audio file.
- `ref_text`: optional, only for models that require it.

Response: `audio/wav` bytes.

### `POST /api/transcribe`

Multipart form:

- `audio`: input audio file.
- `language`: optional ISO code, for example `es`.
- `model`: optional, defaults to `Whisper`.

Response:

```json
{ "text": "...", "language": "es" }
```

### `GET /api/usage`

Use this to read the persisted call log and summary. It requires the Bearer token.

Query parameters:

- `limit`: number of recent rows to return, max `1000`.
- `type`: optional filter: `tts`, `clone`, or `transcribe`.

Response shape:

```json
{
  "pricing": { "currency": "USD", "price_per_call": 0 },
  "summary": {
    "total_calls": 12,
    "total_text_chars": 4500,
    "max_duration_seconds": 23.61,
    "max_wait_seconds": 4.2
  },
  "calls": [
    {
      "type": "tts",
      "model": "Pocket-TTS",
      "text_chars": 120,
      "duration_seconds": 4.8,
      "wait_seconds": 0,
      "success": true
    }
  ]
}
```

The log lives in the persistent volume at `/modelbox-data/logs/calls.jsonl`. It stores metadata only: no raw text and no audio bytes.

## Minimal examples

### curl TTS

```bash
curl -f -X POST "$MODELBOX_URL/api/tts"   -H "Authorization: Bearer $MODELBOX_API_TOKEN"   -H "Content-Type: application/json"   -d '{"model":"Pocket-TTS","text":"Hola mundo","voice":"alba"}'   --output output.wav
```

### curl clone

```bash
curl -f -X POST "$MODELBOX_URL/api/clone"   -H "Authorization: Bearer $MODELBOX_API_TOKEN"   -F "model=Pocket-TTS"   -F "text=Hola con voz clonada"   -F "ref_audio=@reference.wav"   --output clone.wav
```

### JavaScript TTS

```js
const response = await fetch(`${MODELBOX_URL}/api/tts`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${MODELBOX_API_TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ model: "Pocket-TTS", text: "Hola", voice: "alba" }),
});
if (!response.ok) throw new Error(await response.text());
const audio = await response.arrayBuffer();
```

## Error handling

| Code | Meaning | Agent action |
|------|---------|--------------|
| 401 | Bad/missing API token | Ask operator for a valid API token |
| 403 | Model not enabled for API | Ask operator to enable it in the panel |
| 409 | Model not downloaded | Ask operator to download it in the panel |
| 500 | Runtime/model error | Surface error text and retry only if transient |
| 503 | API token not configured server-side | Ask operator to set `API_TOKEN` |

## Voice cloning readiness

Pocket cloning requires `capabilities.clone=true` for `Pocket-TTS` in `/api/models`.
If false, the server is missing `/modelbox-data/pocket-weights/model.safetensors`.

## Practical limits

Modelbox currently has no hard text-character limit in the API. For reliability, split long TTS jobs into chunks of about 1,000-2,000 characters and merge audio client-side if needed.

Audio upload size is capped by `MODELBOX_MAX_UPLOAD_MB` (default: 25 MB), so STT and cloning duration depend mostly on audio bitrate and file size.
