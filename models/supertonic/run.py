import argparse
import logging
import os
import time

from supertonic import TTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carpeta de modelos junto a este script (mantiene el modelo self-contained por modelo).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(SCRIPT_DIR, "assets")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "..", "outputs")


def main():
    parser = argparse.ArgumentParser(description="Ejecutar Supertonic-3 TTS (ONNX, CPU)")
    parser.add_argument("--text", type=str, help="Texto a convertir a audio")
    parser.add_argument("--voice", type=str, default="M1",
                        help="Voz preset: M1..M5, F1..F5 (default M1)")
    parser.add_argument("--lang", type=str, default="en",
                        help="Idioma ISO (en, es, fr, ...) o 'na' para language-agnostic")
    parser.add_argument("--output", type=str, help="Archivo de salida (opcional)")
    parser.add_argument("--speed", type=float, default=1.05, help="Velocidad de habla")
    parser.add_argument("--steps", type=int, default=8, help="Pasos de difusión (calidad/velocidad)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logger.info("Cargando Supertonic-3 (ONNX, CPU). Primera vez descarga el modelo...")
    try:
        tts = TTS(model="supertonic-3", model_dir=MODEL_DIR, auto_download=True)
    except Exception as e:
        logger.error(f"Error cargando el modelo: {e}")
        return

    try:
        style = tts.get_voice_style(voice_name=args.voice)
    except Exception as e:
        logger.error(f"No se pudo cargar la voz '{args.voice}': {e}")
        return

    logger.info(f"Modelo cargado. Voz='{args.voice}', idioma='{args.lang}'.")

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
            wav, _duration = tts.synthesize(
                text=text_to_gen,
                voice_style=style,
                lang=args.lang,
                total_steps=args.steps,
                speed=args.speed,
            )
        except Exception as e:
            logger.error(f"Error en generación: {e}")
            if args.text:
                break
            continue

        output_file = args.output if (args.text and args.output) else \
            os.path.join(OUTPUT_DIR, f"supertonic_{int(time.time())}.wav")
        tts.save_audio(wav, output_file)
        logger.info(f"Audio guardado en: {output_file}")

        if args.text:
            break


if __name__ == "__main__":
    main()
