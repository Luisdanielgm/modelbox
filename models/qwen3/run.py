import argparse
import torch
import soundfile as sf
import os
import time
from qwen_tts import Qwen3TTSModel
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rutas relativas al script (modelo self-contained).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "..", "outputs")


def main():
    parser = argparse.ArgumentParser(description="Ejecutar Qwen3 TTS (Transformers Version)")
    parser.add_argument("--text", type=str, help="Texto a convertir a audio")
    parser.add_argument("--voice", type=str, default="serena", help="ID del hablante (para modo preset)")
    parser.add_argument("--output", type=str, help="Archivo de salida (opcional)")
    parser.add_argument("--ref_audio", type=str, help="Ruta al audio de referencia (opcional)")
    parser.add_argument("--ref_text", type=str, help="Texto del audio de referencia (opcional)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # LOGICA DE DETECCIÓN AUTOMÁTICA DE CLONACIÓN
    # Si el usuario no especifica audio, buscamos "reference.ogg/.wav/.mp3" junto al script.
    if not args.ref_audio:
        for default_file in ["reference.ogg", "reference.wav", "reference.mp3"]:
            candidate = os.path.join(SCRIPT_DIR, default_file)
            if os.path.exists(candidate):
                logger.info(f"🎤 Encontrado archivo por defecto: '{default_file}'. Activando modo CLONACIÓN.")
                args.ref_audio = candidate
                break

    # Configuración de dispositivo
    # Forzamos CPU: el modelo 1.7B en FP32 no entra en GPUs con poca VRAM (p. ej. 4GB)
    # y causaría OOM. En CPU corre sin problemas si hay suficiente RAM del sistema.
    device = "cpu"
    logger.info(f"Usando dispositivo: {device}")

    # Optimizaciones de PyTorch
    attn_impl = "sdpa"

    # Cargamos el modelo BASE si vamos a clonar, o el CUSTOM si usamos presets
    model_name = "Qwen/Qwen3-TTS-12Hz-1.7B-Base" if args.ref_audio else "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

    logger.info(f"Cargando modelo {model_name}...")
    try:
        model = Qwen3TTSModel.from_pretrained(
            model_name,
            torch_dtype=torch.float32,
            device_map=device,
            attn_implementation=attn_impl
        )
    except Exception as e:
        logger.error(f"Error cargando el modelo: {e}")
        return

    logger.info("Modelo cargado correctamente.")

    # Loop interactivo si no hay texto
    while True:
        if args.text:
            text_to_gen = args.text
        else:
            try:
                text_to_gen = input("\nIntroduce texto (o 'q' para salir): ")
                if text_to_gen.lower() in ['q', 'quit', 'exit']:
                    break
            except KeyboardInterrupt:
                break

        if not text_to_gen.strip():
            continue

        try:
            with torch.inference_mode():

                # MODO 1: CLONACIÓN DE VOZ
                if args.ref_audio:
                    if not os.path.exists(args.ref_audio):
                        logger.error(f"No se encuentra el archivo de audio: {args.ref_audio}")
                        return

                    ref_audio_path = args.ref_audio
                    # Conversión automática para OGG/MP3 (si soundfile lo soporta) a WAV
                    # Qwen prefiere WAV PCM.
                    if not ref_audio_path.lower().endswith('.wav'):
                        try:
                            logger.info(f"Detectado formato no-WAV. Convirtiendo '{ref_audio_path}' a WAV temporal...")
                            data, sr = sf.read(ref_audio_path)
                            temp_wav = f"temp_ref_{int(time.time())}.wav"
                            sf.write(temp_wav, data, sr)
                            ref_audio_path = temp_wav
                            logger.info(f"Conversión exitosa: {temp_wav}")
                        except Exception as e:
                            logger.warning(f"No se pudo convertir automáticamente (quizás falte codec): {e}. Intentando usar original.")

                    logger.info(f"🎤 Clonando voz de: {ref_audio_path}")

                    # Crear prompt de clonación
                    try:
                        if args.ref_text:
                            prompt_items = model.create_voice_clone_prompt(
                                ref_audio=ref_audio_path,
                                ref_text=args.ref_text,
                                x_vector_only_mode=False
                            )
                        else:
                            prompt_items = model.create_voice_clone_prompt(
                                ref_audio=ref_audio_path,
                                x_vector_only_mode=True
                            )

                        wavs, sr = model.generate_voice_clone(
                            text=text_to_gen,
                            voice_clone_prompt=prompt_items
                        )
                    finally:
                        # Limpieza de archivo temporal si se creó
                        if ref_audio_path != args.ref_audio and os.path.exists(ref_audio_path):
                            try:
                                os.remove(ref_audio_path)
                            except:
                                pass

                # MODO 2: PRESET (SERENA, AIDEN, ETC)
                else:
                    logger.info(f"Generando con voz preset '{args.voice}'")
                    wavs, sr = model.generate_custom_voice(
                        text=text_to_gen,
                        speaker=args.voice
                    )

            # Guardar audio
            output_file = args.output if (args.text and args.output) else \
                os.path.join(OUTPUT_DIR, f"qwen3_{int(time.time())}.wav")
            sf.write(output_file, wavs[0], sr)
            logger.info(f"✅ Audio guardado en: {output_file}")

        except Exception as e:
            logger.error(f"Error en generación: {e}")

        if args.text:
            break


if __name__ == "__main__":
    main()
