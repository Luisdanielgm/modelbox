"""Interfaz unificada multi-modelo (Gradio): TTS + transcripción (STT) + guía de API.

Controles que se adaptan a las capacidades de cada modelo, pestaña de
transcripción, una pestaña que explica cómo usar la API, y monitor de recursos
+ cola de inferencia en vivo.
"""
import logging
import os

import gradio as gr

from shared import inference, limits, monitor, state
from shared.backends import BACKENDS
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
    if not text or not text.strip():
        yield None, "Falta el texto a sintetizar."
        return
    max_chars = limits.MODELBOX_MAX_CLONE_CHARS if ref_audio else limits.MODELBOX_MAX_TTS_CHARS
    label = "Texto de clonacion" if ref_audio else "Texto TTS"
    msg = limits.text_limit_error(text, max_chars, label)
    if msg:
        yield None, msg
        return
    if ref_audio:
        try:
            if limits.upload_too_large(os.path.getsize(ref_audio)):
                yield None, f"Audio demasiado grande (max {limits.MODELBOX_MAX_UPLOAD_MB} MB)."
                return
        except OSError:
            pass
        msg = limits.audio_limit_error(ref_audio)
        if msg:
            yield None, msg
            return
    backend = BACKENDS[model_name]
    if not backend.is_downloaded():
        yield None, f"El modelo {model_name} no está descargado (usar el botón Descargar)."
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
        yield None, "La clonacion de Pocket requiere /modelbox-data/pocket-weights/model.safetensors."
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
            yield gr.update(), "Listo"
        else:
            import soundfile as sf
            with inference.slot():
                path = backend.synthesize(text, **opts)
            data, sr = sf.read(path, dtype="float32")
            state.cleanup_outputs()
            yield (sr, data), f"Listo: {os.path.basename(path)}"
    except Exception as e:
        yield None, f"Error: {_friendly_error(e)}"


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
    if not audio_path:
        return "", "Falta el audio (subir o grabar uno)."
    if not WHISPER.is_downloaded():
        return "", "Whisper no está descargado (usar el botón Descargar)."
    try:
        if limits.upload_too_large(os.path.getsize(audio_path)):
            return "", f"Audio demasiado grande (max {limits.MODELBOX_MAX_UPLOAD_MB} MB)."
    except OSError:
        pass
    msg = limits.audio_limit_error(audio_path)
    if msg:
        return "", msg
    try:
        with inference.slot():
            result = WHISPER.transcribe(audio_path, language=language or None)
    except Exception as e:
        return "", f"Error: {_friendly_error(e)}"
    return result["text"], f"Listo (idioma: {result['language']})"


def refresh_monitor():
    md = monitor.format_markdown(monitor.snapshot(OUTPUTS))
    q = inference.status()
    md += (f"\n\n**Cola de inferencia**: {q['active']} en curso · {q['waiting']} esperando "
           f"(máx {q['max_concurrent']} en paralelo)")
    return md


def _api_md():
    """Guia de uso de la API, embebida en el panel."""
    estado = ("**API activa** - el servidor tiene `API_TOKEN` configurado."
              if API_ENABLED else
              "**API desactivada** - el servidor NO tiene `API_TOKEN`. "
              "Configurar esa variable de entorno para habilitarla.")
    incl_tts = ", ".join(MODEL_NAMES) or "- (ninguno en este build)"
    incl_stt = WHISPER.name if WHISPER else "- (no incluido)"
    header = (
        f"## Usar Modelbox por API\n\n{estado}\n\n"
        f"**Modelos en este build** - Voz (TTS): {incl_tts} - Transcripcion (STT): {incl_stt}\n\n"
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

### Endpoints OpenAI-compatible `/v1/*`

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/v1/models` | Lista OpenAI-style de modelos descargados + habilitados |
| POST | `/v1/audio/speech` | TTS OpenAI-compatible. Mapea `input` -> `text` y devuelve audio/wav |
| POST | `/v1/audio/transcriptions` | STT OpenAI-compatible. Mapea `file` -> `audio` y devuelve `text`, `duration`, `language` |

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

    timer.tick(refresh_monitor, outputs=mon_md)


if __name__ == "__main__":
    demo.launch()
