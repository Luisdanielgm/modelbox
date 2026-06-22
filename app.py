"""Interfaz unificada multi-modelo (Gradio): TTS + transcripción (STT) + guía de API.

Controles que se adaptan a las capacidades de cada modelo, pestaña de
transcripción, una pestaña que explica cómo usar la API, y monitor de recursos
+ cola de inferencia en vivo.
"""
import logging
import os
import time

import gradio as gr

from shared import inference, limits, monitor, state, usage
from shared.backends import BACKENDS
from shared.embeddings import EMBEDDERS
from shared.paths import OUTPUTS
from shared.transcribe import TRANSCRIBERS


class _DropContentLengthError(logging.Filter):
    """Silencia el ruido benigno de uvicorn cuando el navegador aborta una
    conexión a mitad de respuesta (LocalProtocolError). No afecta la app."""

    _NEEDLE = "Too little data for declared Content-Length"

    def filter(self, record):
        if self._NEEDLE in record.getMessage():
            return False
        if record.exc_info and record.exc_info[1] and self._NEEDLE in str(record.exc_info[1]):
            return False
        return True


logging.getLogger("uvicorn.error").addFilter(_DropContentLengthError())

MODEL_NAMES = list(BACKENDS.keys())
DEFAULT_MODEL = MODEL_NAMES[0] if MODEL_NAMES else None
WHISPER = next(iter(TRANSCRIBERS.values()), None)
WHISPER_NAME = WHISPER.name if WHISPER else None
EMBEDDER = next(iter(EMBEDDERS.values()), None)
EMBEDDER_NAME = EMBEDDER.name if EMBEDDER else None
API_ENABLED = bool(os.environ.get("API_TOKEN"))


def _friendly_error(e):
    """Traduce excepciones técnicas frecuentes a algo accionable para el usuario."""
    s = str(e)
    low = s.lower()
    if "no module named" in low:
        return "Este modelo no está incluido en este build de Modelbox."
    if "out of memory" in low or "cannot allocate" in low or "memoryerror" in low:
        return "Memoria insuficiente para cargar este modelo. Probá uno más liviano."
    if "gated" in low or "401" in low or "unauthorized" in low or "login" in low:
        return "Faltan los pesos/credenciales gated de Hugging Face para este modelo."
    return s


def _log_panel(call_type, model, started_at, success, status_code, error=None, **extra):
    """Registra una llamada del panel en el mismo log de uso que la API.
    Best-effort: nunca debe romper la interacción del usuario."""
    try:
        usage.append_call({
            "id": usage.new_request_id(),
            "type": call_type,
            "surface": "panel",
            "model": model,
            "duration_seconds": round(time.perf_counter() - started_at, 4),
            "status_code": status_code,
            "success": success,
            "error": error,
            **extra,
        })
    except Exception:
        pass


def _status_md(model_name):
    backend = BACKENDS[model_name]
    dl = "descargado" if backend.is_downloaded() else "no descargado"
    en = "habilitado en API" if state.is_enabled(model_name) else "deshabilitado en API"
    parts = [f"**Estado:** {dl} - {en}"]
    if getattr(backend, "key", None) == "pocket":
        if backend.capabilities.get("clone"):
            parts.append("**Clonacion Pocket:** disponible en la interfaz y API.")
        else:
            parts.append("**Clonacion Pocket:** falta `/modelbox-data/pocket-weights/model.safetensors`.")
    return "\n\n".join(parts)


def on_model_change(model_name):
    backend = BACKENDS[model_name]
    caps = backend.capabilities
    presets = caps["presets"]
    langs = caps["languages"]
    dl = backend.is_downloaded()
    show_clone_input = caps["clone"] or getattr(backend, "key", None) == "pocket"
    return (
        gr.update(choices=presets, value=presets[0] if presets else None,
                  visible=bool(presets)),
        gr.update(choices=langs, value=langs[0] if langs else None,
                  visible=bool(langs)),
        gr.update(visible=caps["has_speed"]),
        gr.update(visible=caps["has_steps"]),
        gr.update(value=None, visible=show_clone_input),
        gr.update(value="", visible=caps["ref_text"]),
        _status_md(model_name),
        gr.update(visible=not dl),          # download_btn
        gr.update(value=state.is_enabled(model_name)),  # enable_cb
        gr.update(interactive=dl),          # gen_btn: solo si esta descargado
        gr.update(visible=_pocket_clone_download_visible(model_name)),
        None,                               # audio_out: clear previous model result
        "",                                 # status: clear previous model message
    )


def do_download(model_name):
    try:
        BACKENDS[model_name].download()
        msg = f"Listo: {model_name} descargado."
    except Exception as e:
        return _status_md(model_name), gr.update(), gr.update(), f"Error al descargar: {_friendly_error(e)}"
    return _status_md(model_name), gr.update(visible=False), gr.update(interactive=True), msg


def _pocket_clone_download_visible(model_name):
    backend = BACKENDS[model_name]
    return getattr(backend, "key", None) == "pocket" and not backend.capabilities.get("clone")


def do_download_pocket_clone(model_name):
    backend = BACKENDS[model_name]
    if getattr(backend, "key", None) != "pocket":
        return _status_md(model_name), gr.update(), ""
    try:
        backend.download_clone_weights()
        msg = "Listo: clonacion de Pocket disponible."
    except Exception as e:
        return _status_md(model_name), gr.update(visible=True), f"Error al descargar clonacion: {_friendly_error(e)}"
    return _status_md(model_name), gr.update(visible=False), msg


def set_enable(model_name, value):
    state.set_enabled(model_name, bool(value))
    return _status_md(model_name)


def generate(model_name, text, voice, lang, speed, steps, ref_audio, ref_text):
    started_at = time.perf_counter()
    call_type = "clone" if ref_audio else "tts"
    outcome = {"success": False, "status_code": 400, "error": None}
    try:
        if not text or not text.strip():
            outcome["error"] = "Falta el texto a sintetizar."
            yield None, outcome["error"]
            return
        max_chars = limits.MODELBOX_MAX_CLONE_CHARS if ref_audio else limits.MODELBOX_MAX_TTS_CHARS
        label = "Texto de clonacion" if ref_audio else "Texto TTS"
        msg = limits.text_limit_error(text, max_chars, label)
        if msg:
            outcome["error"] = msg
            yield None, msg
            return
        if ref_audio:
            try:
                if limits.upload_too_large(os.path.getsize(ref_audio)):
                    outcome["error"] = f"Audio demasiado grande (max {limits.MODELBOX_MAX_UPLOAD_MB} MB)."
                    yield None, outcome["error"]
                    return
            except OSError:
                pass
            msg = limits.audio_limit_error(ref_audio)
            if msg:
                outcome["error"] = msg
                yield None, msg
                return
        backend = BACKENDS[model_name]
        if not backend.is_downloaded():
            outcome["status_code"] = 409
            outcome["error"] = f"El modelo {model_name} no está descargado (usar el botón Descargar)."
            yield None, outcome["error"]
            return
        caps = backend.capabilities
        opts = {}
        if caps["presets"]:
            opts["voice"] = voice
        if caps["languages"]:
            opts["lang"] = lang
        if caps["has_speed"]:
            opts["speed"] = speed
        if caps["has_steps"]:
            opts["steps"] = steps
        if getattr(backend, "key", None) == "pocket" and ref_audio and not caps["clone"]:
            outcome["error"] = "La clonacion de Pocket requiere /modelbox-data/pocket-weights/model.safetensors."
            yield None, outcome["error"]
            return
        if caps["clone"]:
            opts["ref_audio"] = ref_audio
        if caps["ref_text"]:
            opts["ref_text"] = ref_text or None
        # Aviso de cola: si no hay turno libre, otra inferencia está corriendo.
        q = inference.status()
        if q["active"] >= q["max_concurrent"]:
            yield None, f"En cola, esperando turno… ({q['waiting']} adelante)"
        try:
            # Streaming solo si el modelo corre en GPU (rápido > realtime); en CPU
            # se atasca, así que ahí generamos todo y reproducimos al final.
            stream = hasattr(backend, "synthesize_stream") and backend.uses_gpu()
            if stream:
                import numpy as np
                # Pre-buffer ~3s antes de reproducir y luego mandar bloques de ~6s.
                sr = None
                acc, acc_len, started = [], 0, False
                preroll = block = 0
                with inference.slot():
                    for s, chunk in backend.synthesize_stream(text, **opts):
                        sr = s
                        if not preroll:
                            preroll, block = int(3.0 * sr), int(6.0 * sr)
                        acc.append(chunk)
                        acc_len += len(chunk)
                        if acc_len >= (block if started else preroll):
                            yield (sr, np.concatenate(acc)), "Generando…"
                            acc, acc_len, started = [], 0, True
                    if acc:
                        yield (sr, np.concatenate(acc)), "Generando…"
                state.cleanup_outputs()
                outcome["success"], outcome["status_code"] = True, 200
                yield gr.update(), "Listo"
            else:
                import soundfile as sf
                with inference.slot():
                    path = backend.synthesize(text, **opts)
                data, sr = sf.read(path, dtype="float32")
                state.cleanup_outputs()
                outcome["success"], outcome["status_code"] = True, 200
                yield (sr, data), f"Listo: {os.path.basename(path)}"
        except Exception as e:
            outcome["status_code"] = 500
            outcome["error"] = _friendly_error(e)
            yield None, f"Error: {outcome['error']}"
    finally:
        _log_panel(call_type, model_name, started_at, outcome["success"],
                   outcome["status_code"], outcome["error"],
                   voice=voice, lang=lang, text_chars=len(text or ""))


# --- Transcripción (STT) ---
def _tr_status_md():
    if not WHISPER:
        return "_Whisper no incluido en este build._"
    dl = "✅ descargado" if WHISPER.is_downloaded() else "⬇️ no descargado"
    en = "🔓 habilitado en API" if state.is_enabled(WHISPER_NAME) else "🔒 deshabilitado en API"
    return f"**Whisper** ({WHISPER.size}, int8, ~1 GB RAM): {dl} · {en}"


def do_download_whisper():
    try:
        WHISPER.download()
        msg = "Listo: Whisper descargado."
    except Exception as e:
        return _tr_status_md(), gr.update(), gr.update(), f"Error al descargar: {_friendly_error(e)}"
    return _tr_status_md(), gr.update(visible=False), gr.update(interactive=True), msg


def set_enable_whisper(value):
    state.set_enabled(WHISPER_NAME, bool(value))
    return _tr_status_md()


def on_whisper_load():
    dl = WHISPER.is_downloaded()
    return (
        _tr_status_md(),
        gr.update(visible=not dl),
        gr.update(value=state.is_enabled(WHISPER_NAME)),
        gr.update(interactive=dl),
    )


def transcribe(audio_path, language):
    if not WHISPER:
        return "", "Whisper no está incluido en este build."
    started_at = time.perf_counter()
    outcome = {"success": False, "status_code": 400, "error": None, "text_chars": 0}
    try:
        if not audio_path:
            outcome["error"] = "Falta el audio (subir o grabar uno)."
            return "", outcome["error"]
        if not WHISPER.is_downloaded():
            outcome["status_code"] = 409
            outcome["error"] = "Whisper no está descargado (usar el botón Descargar)."
            return "", outcome["error"]
        try:
            if limits.upload_too_large(os.path.getsize(audio_path)):
                outcome["error"] = f"Audio demasiado grande (max {limits.MODELBOX_MAX_UPLOAD_MB} MB)."
                return "", outcome["error"]
        except OSError:
            pass
        msg = limits.audio_limit_error(audio_path)
        if msg:
            outcome["error"] = msg
            return "", msg
        try:
            with inference.slot():
                result = WHISPER.transcribe(audio_path, language=language or None)
        except Exception as e:
            outcome["status_code"] = 500
            outcome["error"] = _friendly_error(e)
            return "", f"Error: {outcome['error']}"
        outcome["success"], outcome["status_code"] = True, 200
        outcome["text_chars"] = len(result.get("text") or "")
        return result["text"], f"Listo (idioma: {result['language']})"
    finally:
        _log_panel("transcribe", WHISPER_NAME, started_at, outcome["success"],
                   outcome["status_code"], outcome["error"],
                   lang=language or None, text_chars=outcome["text_chars"])


# --- Embeddings (retrieval / RAG) ---
def _emb_status_md():
    if not EMBEDDER:
        return "_Embeddings no incluido en este build._"
    dl = "✅ descargado" if EMBEDDER.is_downloaded() else "⬇️ no descargado"
    en = "🔓 habilitado en API" if state.is_enabled(EMBEDDER_NAME) else "🔒 deshabilitado en API"
    return f"**{EMBEDDER.name}** ({EMBEDDER.dims}-d, ONNX, sin torch): {dl} · {en}"


def do_download_embed():
    try:
        EMBEDDER.download()
        msg = f"Listo: {EMBEDDER.name} descargado."
    except Exception as e:
        return _emb_status_md(), gr.update(), gr.update(), f"Error al descargar: {_friendly_error(e)}"
    return _emb_status_md(), gr.update(visible=False), gr.update(interactive=True), msg


def set_enable_embed(value):
    state.set_enabled(EMBEDDER_NAME, bool(value))
    return _emb_status_md()


def on_embed_load():
    dl = EMBEDDER.is_downloaded()
    return (
        _emb_status_md(),
        gr.update(visible=not dl),
        gr.update(value=state.is_enabled(EMBEDDER_NAME)),
        gr.update(interactive=dl),
    )


def do_embed(text, task, dimensions):
    if not EMBEDDER:
        return "", "Embeddings no incluido en este build."
    started_at = time.perf_counter()
    outcome = {"success": False, "status_code": 400, "error": None, "items": 0, "text_chars": 0}
    try:
        if not text or not text.strip():
            outcome["error"] = "Falta el texto."
            return "", outcome["error"]
        if not EMBEDDER.is_downloaded():
            outcome["status_code"] = 409
            outcome["error"] = "EmbeddingGemma no está descargado (usar el botón Descargar)."
            return "", outcome["error"]
        items = [ln for ln in text.splitlines() if ln.strip()] or [text]
        outcome["items"] = len(items)
        outcome["text_chars"] = sum(len(t or "") for t in items)
        if len(items) > limits.MODELBOX_MAX_EMBED_ITEMS:
            outcome["error"] = f"Demasiados textos ({len(items)}, max {limits.MODELBOX_MAX_EMBED_ITEMS})."
            return "", outcome["error"]
        for t in items:
            msg = limits.text_limit_error(t, limits.MODELBOX_MAX_EMBED_CHARS, "Texto de embeddings")
            if msg:
                outcome["error"] = msg
                return "", msg
        dims = int(dimensions) if dimensions else None
        try:
            with inference.slot():
                vecs = EMBEDDER.embed(items, task=task, dimensions=dims)
        except Exception as e:
            outcome["status_code"] = 500
            outcome["error"] = _friendly_error(e)
            return "", f"Error: {outcome['error']}"
        outcome["success"], outcome["status_code"] = True, 200
        d = len(vecs[0]) if vecs else 0
        preview = [round(x, 4) for x in (vecs[0][:8] if vecs else [])]
        out = f"{len(vecs)} vector(es) de {d} dims. Primer vector (8 de {d}): {preview}"
        return out, f"Listo ({len(vecs)} embeddings, {d}-d)"
    finally:
        _log_panel("embeddings", EMBEDDER_NAME, started_at, outcome["success"],
                   outcome["status_code"], outcome["error"],
                   items=outcome["items"], text_chars=outcome["text_chars"],
                   dimensions=int(dimensions) if dimensions else None)


def refresh_monitor():
    md = monitor.format_markdown(monitor.snapshot(OUTPUTS))
    q = inference.status()
    md += (f"\n\n**Cola de inferencia**: {q['active']} en curso · {q['waiting']} esperando "
           f"(máx {q['max_concurrent']} en paralelo)")
    return md


_HISTORY_HEADERS = ["hora (UTC)", "origen", "tipo", "modelo", "chars",
                    "dur (s)", "espera (s)", "ok", "http", "error"]


def _history_data(limit=100, call_type=None):
    """Resumen + filas del historial unificado (API + /v1 + panel)."""
    ct = call_type if call_type and call_type != "todos" else None
    payload = usage.usage_payload(limit=int(limit or 100), call_type=ct)
    s = payload["summary"]
    by_type = " · ".join(f"{k}: {v}" for k, v in (s.get("by_type") or {}).items()) or "—"
    summary = (f"**{s['total_calls']} llamadas** · {s['successful_calls']} ok · "
               f"{s['failed_calls']} con error · por tipo: {by_type}")
    rows = []
    for c in payload["calls"]:
        ts = (c.get("ts") or "").replace("T", " ")[:19]
        rows.append([
            ts,
            c.get("surface", "api"),
            c.get("type", ""),
            c.get("model", ""),
            c.get("text_chars", 0),
            c.get("duration_seconds", 0),
            c.get("wait_seconds", 0),
            "✓" if c.get("success") else "✗",
            c.get("status_code", ""),
            (c.get("error") or "")[:120],
        ])
    return summary, rows


def _api_md():
    """Guia de uso de la API, embebida en el panel."""
    estado = ("**API activa** - el servidor tiene `API_TOKEN` configurado."
              if API_ENABLED else
              "**API desactivada** - el servidor NO tiene `API_TOKEN`. "
              "Configurar esa variable de entorno para habilitarla.")
    incl_tts = ", ".join(MODEL_NAMES) or "- (ninguno en este build)"
    incl_stt = WHISPER.name if WHISPER else "- (no incluido)"
    incl_emb = EMBEDDER.name if EMBEDDER else "- (no incluido)"
    header = (
        f"## Usar Modelbox por API\n\n{estado}\n\n"
        f"**Modelos en este build** - Voz (TTS): {incl_tts} - Transcripcion (STT): {incl_stt} "
        f"- Embeddings: {incl_emb}\n\n"
        "**Precio actual:** USD 0 durante el periodo inicial de prueba.\n\n"
        "Hay dos superficies compatibles: `/api/*` nativa de Modelbox y `/v1/*` compatible con clientes OpenAI. "
        "Ambas usan el mismo `API_TOKEN`; los endpoints `/v1/*` son wrappers aditivos, no reemplazan `/api/*`.\n\n"
    )
    body = """### Autenticacion

Todas las rutas protegidas requieren el header:

```
Authorization: Bearer <API_TOKEN>
```

### Endpoints nativos `/api/*`

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/api/health` | Estado + cola + limites + modelos (sin token) |
| GET | `/api/pricing` | Precio actual: USD 0 |
| GET | `/api/models` | Modelos TTS y capacidades |
| GET | `/api/usage` | Registro de llamadas + resumen |
| POST | `/api/tts` | Genera voz (preset) -> WAV |
| POST | `/api/clone` | Clona voz desde audio -> WAV |
| POST | `/api/transcribe` | Transcribe audio -> JSON |
| POST | `/api/embeddings` | Texto -> vectores (RAG); soporta `task` y `dimensions` |

### Endpoints OpenAI-compatible `/v1/*`

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/v1/models` | Lista OpenAI-style de modelos descargados + habilitados |
| POST | `/v1/audio/speech` | TTS OpenAI-compatible. Mapea `input` -> `text` y devuelve audio/wav |
| POST | `/v1/audio/transcriptions` | STT OpenAI-compatible. Mapea `file` -> `audio` y devuelve `text`, `duration`, `language` |
| POST | `/v1/embeddings` | Embeddings OpenAI-compatible (`input`, `dimensions`) -> `data[].embedding` |

Errores en `/v1/*` usan shape OpenAI:

```json
{"error":{"message":"...","type":"invalid_request_error","code":"..."}}
```

### Limites activos

Consultar `/api/health`, campo `limits`. Defaults del servicio:

- `MODELBOX_MAX_TTS_CHARS=2000`
- `MODELBOX_MAX_CLONE_CHARS=2000`
- `MODELBOX_MAX_AUDIO_SECONDS=1200`
- `MODELBOX_MAX_UPLOAD_MB=30`
- `MODELBOX_MAX_EMBED_CHARS=8000` / `MODELBOX_MAX_EMBED_ITEMS=64`

**Embeddings — chunking obligatorio para textos largos:** cada texto se procesa
hasta **~2048 tokens** (contexto de EmbeddingGemma). Texto más largo se trunca.
Para documentos largos, dividirlos en chunks y mandar cada uno como ítem de
`input` (un vector por chunk). Modelbox no chunkea; lo hace el consumidor.

### Ejemplos OpenAI-compatible

```bash
# TTS compatible OpenAI
curl -X POST <host>/v1/audio/speech \
  -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"model":"Pocket-TTS","input":"Hola mundo","voice":"alba"}' \
  --output salida.wav

# STT compatible OpenAI: duration es obligatorio en la respuesta
curl -X POST <host>/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@grabacion.wav" -F "model=Whisper" -F "response_format=verbose_json" -F "language=es"
```

Docs interactivas (Swagger): **[/api/docs](/api/docs)** - OpenAPI JSON: **[/api/openapi.json](/api/openapi.json)**.
Guia detallada en repo: `docs/OPENAI_COMPATIBILITY.md`.
Reemplazar `<host>` por la URL de este servidor.
"""
    return header + body


with gr.Blocks(title="Modelbox") as demo:
    gr.Markdown("# Modelbox\nSíntesis de voz, transcripción (STT) y API REST — todo en local.")

    with gr.Row():
        with gr.Column(scale=3):
            with gr.Tabs():
                if MODEL_NAMES:
                    with gr.Tab("Generar voz (TTS)"):
                        gr.Markdown(f"Modelos incluidos en esta imagen: **{', '.join(MODEL_NAMES)}**. "
                                    "Seleccionar uno, **descargarlo** y generar.")
                        model_dd = gr.Dropdown(MODEL_NAMES, value=DEFAULT_MODEL, label="Modelo")
                        model_status = gr.Markdown(_status_md(DEFAULT_MODEL))
                        with gr.Row():
                            download_btn = gr.Button(
                                "Descargar modelo",
                                visible=not BACKENDS[DEFAULT_MODEL].is_downloaded())
                            enable_cb = gr.Checkbox(
                                label="Habilitar en la API",
                                info="Permite consumir este modelo por /api/tts (requiere API_TOKEN en el servidor).",
                                value=state.is_enabled(DEFAULT_MODEL))
                        pocket_clone_btn = gr.Button(
                            "Descargar clonacion Pocket",
                            visible=_pocket_clone_download_visible(DEFAULT_MODEL),
                        )
                        text_in = gr.Textbox(label="Texto", lines=3,
                                             placeholder="Texto a sintetizar...")

                        init_caps = BACKENDS[DEFAULT_MODEL].capabilities
                        voice_dd = gr.Dropdown(init_caps["presets"],
                                               value=(init_caps["presets"] or [None])[0],
                                               label="Voz preset", visible=bool(init_caps["presets"]),
                                               allow_custom_value=True)
                        lang_dd = gr.Dropdown(init_caps["languages"],
                                              value=(init_caps["languages"] or [None])[0],
                                              label="Idioma", visible=bool(init_caps["languages"]))
                        speed_sl = gr.Slider(0.5, 2.0, value=1.05, step=0.05, label="Velocidad",
                                             visible=init_caps["has_speed"])
                        steps_sl = gr.Slider(1, 32, value=8, step=1, label="Pasos (calidad/velocidad)",
                                             visible=init_caps["has_steps"])
                        ref_audio_in = gr.Audio(
                            sources=["microphone", "upload"],
                            type="filepath",
                            label="Audio de referencia (clonacion)",
                            visible=(init_caps["clone"] or getattr(BACKENDS[DEFAULT_MODEL], "key", None) == "pocket"),
                        )
                        ref_text_in = gr.Textbox(label="Texto del audio de referencia (opcional)",
                                                 visible=init_caps["ref_text"])
                        gen_btn = gr.Button("Generar", variant="primary",
                                            interactive=BACKENDS[DEFAULT_MODEL].is_downloaded())
                        status = gr.Markdown("")
                        audio_out = gr.Audio(label="Resultado", streaming=True, autoplay=True)

                if WHISPER:
                    with gr.Tab("Transcribir (STT)") as tr_tab:
                        tr_status = gr.Markdown(_tr_status_md())
                        with gr.Row():
                            tr_download_btn = gr.Button("Descargar Whisper",
                                                        visible=not WHISPER.is_downloaded())
                            tr_enable_cb = gr.Checkbox(
                                label="Habilitar en la API",
                                info="Permite consumir Whisper por /api/transcribe (requiere API_TOKEN).",
                                value=state.is_enabled(WHISPER_NAME))
                        tr_audio_in = gr.Audio(sources=["upload", "microphone"], type="filepath",
                                               label="Audio a transcribir (mp3 / ogg / m4a / flac / wav)")
                        tr_lang_in = gr.Dropdown(["", "es", "en", "pt", "fr", "de", "it"], value="",
                                                 label="Idioma (vacío = autodetecta)",
                                                 allow_custom_value=True)
                        tr_btn = gr.Button("Transcribir", variant="primary",
                                           interactive=WHISPER.is_downloaded())
                        tr_text_out = gr.Textbox(label="Transcripción", lines=4)
                        tr_msg = gr.Markdown("")

                if EMBEDDER:
                    with gr.Tab("Embeddings") as emb_tab:
                        emb_status = gr.Markdown(_emb_status_md())
                        with gr.Row():
                            emb_download_btn = gr.Button("Descargar EmbeddingGemma",
                                                         visible=not EMBEDDER.is_downloaded())
                            emb_enable_cb = gr.Checkbox(
                                label="Habilitar en la API",
                                info="Permite consumir por /api/embeddings y /v1/embeddings (requiere API_TOKEN).",
                                value=state.is_enabled(EMBEDDER_NAME))
                        gr.Markdown(
                            "**Límite ~2048 tokens por texto** (tope 8000 chars · máx 64 por lote). "
                            "Texto más largo se trunca → para documentos largos, chunkealos "
                            "(un texto por línea = un vector). La salida siempre es un vector de "
                            "tamaño fijo (768, o el de `dimensions`).")
                        emb_text_in = gr.Textbox(label="Texto (uno por línea para procesar en lote)", lines=4,
                                                 placeholder="Texto a embeddear...")
                        with gr.Row():
                            emb_task_in = gr.Dropdown(["document", "query"], value="document", label="Tarea")
                            emb_dims_in = gr.Dropdown(["768", "512", "256", "128"], value="768",
                                                      label="Dimensiones (Matryoshka)")
                        emb_btn = gr.Button("Generar embeddings", variant="primary",
                                            interactive=EMBEDDER.is_downloaded())
                        emb_out = gr.Textbox(label="Resultado", lines=3)
                        emb_msg = gr.Markdown("")

                with gr.Tab("Historial") as hist_tab:
                    gr.Markdown("Registro unificado de llamadas: panel, API nativa (`/api/*`) "
                                "y OpenAI-compatible (`/v1/*`). Solo metadatos — no guarda texto ni audio.")
                    with gr.Row():
                        hist_type = gr.Dropdown(
                            ["todos", "tts", "clone", "transcribe", "embeddings"],
                            value="todos", label="Filtrar por tipo")
                        hist_limit = gr.Dropdown(["50", "100", "200", "500"], value="100",
                                                 label="Máximo de filas")
                        hist_refresh = gr.Button("Actualizar", variant="primary")
                    hist_summary = gr.Markdown("")
                    hist_table = gr.Dataframe(headers=_HISTORY_HEADERS, datatype="str",
                                              wrap=True, interactive=False, label="Llamadas recientes")

                with gr.Tab("API"):
                    gr.Markdown(_api_md())

        with gr.Column(scale=2):
            gr.Markdown("### Recursos")
            mon_md = gr.Markdown(refresh_monitor())
            timer = gr.Timer(1.5)

    if MODEL_NAMES:
        tts_state_outputs = [voice_dd, lang_dd, speed_sl, steps_sl, ref_audio_in, ref_text_in,
                             model_status, download_btn, enable_cb, gen_btn, pocket_clone_btn, audio_out, status]
        demo.load(on_model_change, inputs=model_dd, outputs=tts_state_outputs)
        model_dd.change(on_model_change, inputs=model_dd, outputs=tts_state_outputs)
        download_btn.click(do_download, inputs=model_dd,
                           outputs=[model_status, download_btn, gen_btn, status])
        pocket_clone_btn.click(do_download_pocket_clone, inputs=model_dd,
                               outputs=[model_status, pocket_clone_btn, status])
        enable_cb.change(set_enable, inputs=[model_dd, enable_cb], outputs=model_status)
        gen_btn.click(generate,
                      inputs=[model_dd, text_in, voice_dd, lang_dd, speed_sl, steps_sl,
                              ref_audio_in, ref_text_in],
                      outputs=[audio_out, status])

    if WHISPER:
        whisper_state_outputs = [tr_status, tr_download_btn, tr_enable_cb, tr_btn]
        demo.load(on_whisper_load, outputs=whisper_state_outputs)
        tr_tab.select(on_whisper_load, outputs=whisper_state_outputs)
        tr_download_btn.click(do_download_whisper,
                              outputs=[tr_status, tr_download_btn, tr_btn, tr_msg])
        tr_enable_cb.change(set_enable_whisper, inputs=tr_enable_cb, outputs=tr_status)
        tr_btn.click(transcribe, inputs=[tr_audio_in, tr_lang_in], outputs=[tr_text_out, tr_msg])

    if EMBEDDER:
        embed_state_outputs = [emb_status, emb_download_btn, emb_enable_cb, emb_btn]
        demo.load(on_embed_load, outputs=embed_state_outputs)
        emb_tab.select(on_embed_load, outputs=embed_state_outputs)
        emb_download_btn.click(do_download_embed,
                               outputs=[emb_status, emb_download_btn, emb_btn, emb_msg])
        emb_enable_cb.change(set_enable_embed, inputs=emb_enable_cb, outputs=emb_status)
        emb_btn.click(do_embed, inputs=[emb_text_in, emb_task_in, emb_dims_in], outputs=[emb_out, emb_msg])

    hist_outputs = [hist_summary, hist_table]
    hist_tab.select(_history_data, inputs=[hist_limit, hist_type], outputs=hist_outputs)
    hist_refresh.click(_history_data, inputs=[hist_limit, hist_type], outputs=hist_outputs)

    timer.tick(refresh_monitor, outputs=mon_md)


if __name__ == "__main__":
    demo.launch()
