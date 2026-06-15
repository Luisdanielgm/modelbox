# Modelbox — API REST

API HTTP para generar audio TTS desde tus aplicaciones. Convive con el panel
visual en el mismo proceso y puerto (por defecto `7860`).

> Docs interactivas (Swagger) autogeneradas en **`/api/docs`**.

## Autenticación

La API se **activa** solo si está configurada la variable de entorno `API_TOKEN`.
Sin ella, los endpoints protegidos devuelven `503`.

Todas las llamadas protegidas requieren el header:

```
Authorization: Bearer <API_TOKEN>
```

## Requisito previo

Un modelo solo responde por API si está:

1. **Descargado** — desde el panel (botón *Descargar*), o no estará disponible.
2. **Habilitado** — toggle *Habilitar en la API* del panel.

Si falta alguno, `/api/tts` devuelve `409` (no descargado) o `403` (no habilitado).

## Endpoints

| Método | Ruta | Auth | Descripción |
|--------|------|:----:|-------------|
| `GET`  | `/api/health`     | — | Estado, cola, modelos y transcriptores |
| `GET`  | `/api/models`     | ✅ | Modelos TTS con sus `capabilities` |
| `POST` | `/api/tts`        | ✅ | Genera audio (voz preset) y devuelve el WAV |
| `POST` | `/api/clone`      | ✅ | Clona voz desde un audio de referencia → WAV |
| `POST` | `/api/transcribe` | ✅ | Transcribe un audio (STT) → texto |

### `GET /api/health`

Sin token. Útil para health checks.

```json
{
  "status": "ok",
  "queue": { "max_concurrent": 1, "active": 0, "waiting": 0 },
  "models": [
    { "name": "Supertonic-3", "downloaded": true,  "enabled": true  },
    { "name": "Pocket-TTS",   "downloaded": false, "enabled": false }
  ],
  "transcribers": [
    { "name": "Whisper", "downloaded": true, "enabled": true }
  ]
}
```

`queue` muestra cuántas inferencias hay en curso (`active`) y cuántas esperando
(`waiting`). En CPU el límite suele ser 1 (una inferencia ya usa todos los cores);
los pedidos extra esperan su turno, no fallan.

### `GET /api/models`

Devuelve cada modelo con sus capacidades (voces preset, idiomas, controles).
Usalo para saber qué `voice`/`lang` acepta cada modelo.

```json
[
  {
    "name": "Supertonic-3",
    "downloaded": true,
    "enabled": true,
    "capabilities": {
      "clone": false, "ref_text": false,
      "presets": ["M1", "F1", "..."],
      "languages": ["es", "en", "..."],
      "has_speed": true, "has_steps": true
    }
  }
]
```

### `POST /api/tts`

Cuerpo JSON:

| Campo   | Tipo   | Req. | Notas |
|---------|--------|:----:|-------|
| `model` | string | ✅ | Nombre exacto (ver `/api/models`), p. ej. `"Supertonic-3"` |
| `text`  | string | ✅ | Texto a sintetizar |
| `voice` | string | — | Voz preset (según el modelo) |
| `lang`  | string | — | Idioma ISO (solo modelos con idiomas, p. ej. Supertonic) |
| `speed` | number | — | Velocidad (solo si el modelo la soporta) |
| `steps` | int    | — | Pasos de difusión (solo si el modelo lo soporta) |

**Respuesta:** `200` con el cuerpo binario del audio (`Content-Type: audio/wav`).
El servidor **no guarda** el audio: lo devuelve y lo descarta.

### `POST /api/clone`

Clona voz desde un audio de referencia. Es **multipart/form-data** (no JSON),
porque incluye un archivo. El modelo debe soportar clonación (`capabilities.clone`).

| Campo | Tipo | Req. | Notas |
|-------|------|:----:|-------|
| `model` | form | ✅ | Modelo con clonación (p. ej. `"Pocket-TTS"`) |
| `text` | form | ✅ | Texto a sintetizar con la voz clonada |
| `ref_audio` | file | ✅ | Audio de referencia (se procesa y se **borra** al instante) |
| `ref_text` | form | — | Transcripción del audio de referencia (solo si el modelo lo usa) |

**Respuesta:** `200` con el WAV (`audio/wav`). El audio de referencia **no se almacena**.

### `POST /api/transcribe`

Transcribe audio a texto (STT, Whisper). **multipart/form-data**. Acepta varios
formatos (mp3/ogg/m4a/flac/wav); se decodifica internamente.

| Campo | Tipo | Req. | Notas |
|-------|------|:----:|-------|
| `audio` | file | ✅ | Audio a transcribir (se procesa y se **borra**) |
| `language` | form | — | Idioma ISO (vacío = autodetecta) |
| `model` | form | — | Transcriptor (default `"Whisper"`) |

**Respuesta:** `200` con JSON `{ "text": "...", "language": "es" }`.

## Ejemplos

### curl

```bash
# Estado (sin token)
curl http://localhost:7860/api/health

# Modelos disponibles
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:7860/api/models

# Generar y guardar el WAV
curl -X POST http://localhost:7860/api/tts \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Supertonic-3","text":"Hola mundo","voice":"F1","lang":"es"}' \
  --output salida.wav

# Clonar voz (multipart: texto + audio de referencia)
curl -X POST http://localhost:7860/api/clone \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "model=Pocket-TTS" -F "text=Hola con voz clonada" \
  -F "ref_audio=@mi_voz.wav" \
  --output clon.wav

# Transcribir un audio (multipart)
curl -X POST http://localhost:7860/api/transcribe \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "audio=@grabacion.mp3" -F "language=es"
```

### Python

```python
import requests

BASE = "http://localhost:7860"
TOKEN = "tu-api-token"

resp = requests.post(
    f"{BASE}/api/tts",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"model": "Pocket-TTS", "text": "Hola mundo", "voice": "alba"},
)
resp.raise_for_status()
with open("salida.wav", "wb") as f:
    f.write(resp.content)
```

### JavaScript (fetch)

```js
const BASE = "http://localhost:7860";
const TOKEN = "tu-api-token";

const resp = await fetch(`${BASE}/api/tts`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ model: "Supertonic-3", text: "Hola mundo", voice: "F1", lang: "es" }),
});
if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
const blob = await resp.blob(); // audio/wav
```

## Códigos de error

| Código | Significado |
|:------:|-------------|
| `400` | Falta el texto, o el modelo no soporta clonación |
| `401` | Token inválido o ausente |
| `403` | El modelo no está habilitado para la API |
| `404` | Modelo/transcriptor desconocido |
| `409` | El modelo no está descargado |
| `503` | API deshabilitada (no se configuró `API_TOKEN`) |
| `500` | Error de síntesis / clonación / transcripción |
