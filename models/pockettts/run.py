import argparse
import logging
import os
import time

import soundfile as sf
from pocket_tts import TTSModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "..", "outputs")

# Voces preset del catálogo de Pocket TTS (no requieren clonación).
PRESETS = [
    "cosette", "marius", "javert", "alba", "jean", "anna", "vera", "fantine",
    "charles", "paul", "eponine", "azelma", "george", "mary", "jane", "michael",
    "eve", "bill_boerst", "peter_yearsley", "stuart_bell", "caro_davy",
    "giovanni", "lola", "juergen", "rafael", "estelle",
]


def main():
    parser = argparse.ArgumentParser(description="Ejecutar Pocket TTS (Kyutai, CPU)")
    parser.add_argument("--text", type=str, help="Texto a convertir a audio")
    parser.add_argument("--voice", type=str, default="alba",
                        help=f"Voz preset (default alba). Opciones: {', '.join(PRESETS)}")
    parser.add_argument("--ref_audio", type=str,
                        help="Ruta a un .wav para CLONAR voz (requiere acceso gated en HF; ver README)")
    parser.add_argument("--output", type=str, help="Archivo de salida (opcional)")
    parser.add_argument("--max_tokens", type=int, default=200, help="Tope de tokens generados")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logger.info("Cargando Pocket TTS (CPU). Primera vez descarga el modelo (~100M)...")
    try:
        model = TTSModel.load_model()
    except Exception as e:
        logger.error(f"Error cargando el modelo: {e}")
        return

    # El audio de referencia (clonación) o el nombre de voz preset van al mismo método.
    conditioning = args.ref_audio if args.ref_audio else args.voice
    try:
        voice_state = model.get_state_for_audio_prompt(conditioning)
    except ValueError as e:
        logger.error(f"No se pudo preparar la voz: {e}")
        return

    mode = "CLONACIÓN" if args.ref_audio else f"preset '{args.voice}'"
    logger.info(f"Modelo cargado. Modo: {mode}.")

    while True:
        if args.text:
            text_to_gen = args.text
        else:
            try:
                text_to_gen = input("\nIntroduce texto (o 'q' para salir): ")
            except (KeyboardInterrupt, EOFError):
                break
            if text_to_gen.lower() in ("q", "quit", "exit"):
                break

        if not text_to_gen.strip():
            continue

        try:
            audio = model.generate_audio(voice_state, text_to_gen, max_tokens=args.max_tokens)
        except Exception as e:
            logger.error(f"Error en generación: {e}")
            if args.text:
                break
            continue

        output_file = args.output if (args.text and args.output) else \
            os.path.join(OUTPUT_DIR, f"pocket_{int(time.time())}.wav")
        sf.write(output_file, audio.numpy(), model.sample_rate)
        logger.info(f"Audio guardado en: {output_file}")

        if args.text:
            break


if __name__ == "__main__":
    main()
