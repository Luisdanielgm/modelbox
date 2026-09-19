# Changelog

Registro de cambios de Modelbox. Cada entrada indica su **impacto en la
comunicación con los clientes de la API** (en particular, Cauce):

- 🟢 **Transparente:** aditivo o interno; el cliente no debe adaptarse.
- 🟡 **Aditivo con preparación:** el cliente sigue funcionando, pero conviene que
  adopte algo (manejo de `429`, token propio, etc.). Requiere aviso.
- 🔴 **Cambio de contrato o comercial:** requiere coordinación previa.

Las entradas 🟡 y 🔴 incluyen una **Nota para integradores**.

## [No publicado]

Semana 1 del roadmap (robustez y limpieza). Todo desplegable sin cambios para
Cauce: el rate-limit viene **desactivado** por defecto.

### Agregado
- 🟢 **`response_format: mp3` en TTS.** `/api/tts` y `/v1/audio/speech` devuelven
  `audio/mpeg` cuando `response_format` es `"mp3"`. El default sigue siendo `wav`,
  y cualquier otro valor también devuelve `wav` (retrocompatible). La conversión
  usa ffmpeg (ya presente en la imagen).
- 🟡 **Rate-limit por token (opt-in).** Ventana fija configurable con
  `MODELBOX_RATE_LIMIT` (solicitudes; `0` = desactivado, default) y
  `MODELBOX_RATE_WINDOW` (segundos, default 60). Aplica a los endpoints de
  inferencia (`/api/tts`, `/api/clone`, `/api/transcribe`, `/api/embeddings` y sus
  equivalentes `/v1/*`). Al excederse devuelve `429` (shape OpenAI en `/v1/*`).

  > **Nota para integradores (Cauce):** por defecto está **desactivado**, así que
  > desplegarlo no cambia nada. Si se activa, conviene fijar el límite por encima
  > del uso real de Cauce y que el cliente maneje `429` con reintentos/backoff.
  > Se coordinará antes de activarlo.

### Cambiado
- 🟢 **Detección de descarga por modelo.** El `is_downloaded()` de Whisper y
  EmbeddingGemma mide la carpeta de caché del propio modelo
  (`models--org--repo`) en vez de todo `HF_HOME`. Evita un falso "descargado"
  cuando otro modelo llenó la caché compartida.
- 🟢 **Limpieza interna.** Se eliminó `read_calls()` (sin uso) y el timer de
  recursos del panel pasó de 1.5 s a 4 s (menos polling por cliente conectado).

## Desplegado

### Agregado
- 🟢 **Historial de uso unificado.** El panel ahora registra sus propias llamadas
  (TTS, clonación, transcripción, embeddings) en el mismo log de auditoría que la
  API. Nueva pestaña **Historial** con filtro por tipo y refresco.
- 🟢 **Campo `surface` en el registro de uso** (`api`, `openai`, `panel`), para
  distinguir el origen de cada llamada. Aditivo: no cambia ninguna respuesta.
- 🟢 **CORS opcional** vía `MODELBOX_CORS_ORIGINS` (lista separada por comas o
  `*`). Sin esa variable, no se agrega middleware y el comportamiento es idéntico
  al anterior. `allow_credentials=False` (auth por header Bearer, no cookies).

### Cambiado
- 🟢 **Rotación del log de uso.** `calls.jsonl` rota por tamaño
  (`MODELBOX_MAX_LOG_MB`, default 5) y conserva un backup `.1`. El historial une
  backup + activo. Interno: no cambia el contrato.
- 🟢 **`/api/health` cacheado.** Los tamaños de almacenamiento se cachean por ruta
  con TTL (`MODELBOX_SIZE_CACHE_TTL`, default 30 s) para evitar recorrer
  `HF_HOME` en cada llamada. Misma forma de respuesta.
- 🟡 **Validación de `dimensions` en embeddings.** `/api/embeddings` y
  `/v1/embeddings` ahora aceptan solo `768`, `512`, `256` o `128`; cualquier otro
  valor devuelve `400`. Antes, un valor fuera de rango devolvía un vector de 768
  en silencio (comportamiento incorrecto).

  > **Nota para integradores (Cauce):** si se envía `dimensions`, debe ser uno de
  > `768/512/256/128`, o se omite (default 768). Un cliente que ya usa esos
  > valores —o que no envía `dimensions`— no requiere ningún cambio. Solo se vería
  > afectado quien enviara un valor no estándar, que antes recibía un vector
  > silenciosamente truncado/erróneo y ahora recibe un `400` explícito.

### Compatibilidad
- La superficie OpenAI (`/v1/models`, `/v1/audio/speech`,
  `/v1/audio/transcriptions`, `/v1/embeddings`) mantiene su contrato.
- Verificado por smoke test (TestClient) que las rutas existentes responden igual
  y que el registro distingue `surface=openai` para `/v1/*` y `surface=api` para
  `/api/*`.
