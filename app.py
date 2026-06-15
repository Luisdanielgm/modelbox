"""Interfaz unificada multi-modelo TTS (Gradio).

Selector de modelo + controles que se adaptan a las capacidades de cada uno
+ monitor de recursos (CPU/RAM/almacenamiento) en vivo.
"""
import logging
import os

import gradio as gr

from shared import monitor
from shared.backends import BACKENDS


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

OUTPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

MODEL_NAMES = list(BACKENDS.keys())
DEFAULT_MODEL = MODEL_NAMES[0]


def on_model_change(model_name):
    caps = BACKENDS[model_name].capabilities
    presets = caps["presets"]
    langs = caps["languages"]
    return (
        gr.update(choices=presets, value=presets[0] if presets else None,
                  visible=bool(presets)),
        gr.update(choices=langs, value=langs[0] if langs else None,
                  visible=bool(langs)),
        gr.update(visible=caps["has_speed"]),
        gr.update(visible=caps["has_steps"]),
        gr.update(visible=caps["clone"]),
        gr.update(visible=caps["ref_text"]),
    )


def generate(model_name, text, voice, lang, speed, steps, ref_audio, ref_text):
    if not text or not text.strip():
        yield None, "Escribí algo de texto primero."
        return
    backend = BACKENDS[model_name]
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
    if caps["clone"]:
        opts["ref_audio"] = ref_audio
    if caps["ref_text"]:
        opts["ref_text"] = ref_text or None
    try:
        # Streaming solo si el modelo corre en GPU (rápido > realtime); en CPU
        # se atasca, así que ahí generamos todo y reproducimos al final.
        stream = hasattr(backend, "synthesize_stream") and backend.uses_gpu()
        if stream:
            for sr, chunk in backend.synthesize_stream(text, **opts):
                yield (sr, chunk), "Generando…"
            yield gr.update(), "Listo"
        else:
            import soundfile as sf
            path = backend.synthesize(text, **opts)
            data, sr = sf.read(path, dtype="float32")
            yield (sr, data), f"Listo: {os.path.basename(path)}"
    except Exception as e:
        yield None, f"Error: {e}"


def refresh_monitor():
    return monitor.format_markdown(monitor.snapshot(OUTPUTS))


with gr.Blocks(title="TTS Multi-Modelo") as demo:
    gr.Markdown("# TTS Multi-Modelo\nGenerá voz en local. Cambiá de modelo y la interfaz se adapta.")

    with gr.Row():
        with gr.Column(scale=3):
            model_dd = gr.Dropdown(MODEL_NAMES, value=DEFAULT_MODEL, label="Modelo")
            text_in = gr.Textbox(label="Texto", lines=3, placeholder="Escribí lo que querés sintetizar...")

            init_caps = BACKENDS[DEFAULT_MODEL].capabilities
            voice_dd = gr.Dropdown(init_caps["presets"], value=(init_caps["presets"] or [None])[0],
                                   label="Voz preset", visible=bool(init_caps["presets"]),
                                   allow_custom_value=True)
            lang_dd = gr.Dropdown(init_caps["languages"],
                                  value=(init_caps["languages"] or [None])[0],
                                  label="Idioma", visible=bool(init_caps["languages"]))
            speed_sl = gr.Slider(0.5, 2.0, value=1.05, step=0.05, label="Velocidad",
                                 visible=init_caps["has_speed"])
            steps_sl = gr.Slider(1, 32, value=8, step=1, label="Pasos (calidad/velocidad)",
                                 visible=init_caps["has_steps"])
            ref_audio_in = gr.Audio(sources=["microphone", "upload"], type="filepath",
                                    label="Audio de referencia (clonación)",
                                    visible=init_caps["clone"])
            ref_text_in = gr.Textbox(label="Texto del audio de referencia (opcional)",
                                     visible=init_caps["ref_text"])

            gen_btn = gr.Button("Generar", variant="primary")
            status = gr.Markdown("")

        with gr.Column(scale=2):
            audio_out = gr.Audio(label="Resultado", streaming=True, autoplay=True)
            gr.Markdown("### Recursos")
            mon_md = gr.Markdown(refresh_monitor())
            timer = gr.Timer(1.5)

    model_dd.change(on_model_change, inputs=model_dd,
                    outputs=[voice_dd, lang_dd, speed_sl, steps_sl, ref_audio_in, ref_text_in])
    gen_btn.click(generate,
                  inputs=[model_dd, text_in, voice_dd, lang_dd, speed_sl, steps_sl,
                          ref_audio_in, ref_text_in],
                  outputs=[audio_out, status])
    timer.tick(refresh_monitor, outputs=mon_md)


if __name__ == "__main__":
    demo.launch()
