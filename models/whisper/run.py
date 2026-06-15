import argparse
import logging

from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Transcribir audio con faster-whisper (CPU, int8)")
    parser.add_argument("--audio", type=str, required=True, help="Ruta al audio (mp3/ogg/m4a/flac/wav)")
    parser.add_argument("--size", type=str, default="small",
                        help="Tamaño del modelo: tiny, base, small, medium, large-v3 (default small)")
    parser.add_argument("--lang", type=str, default=None, help="Idioma ISO (opcional; autodetecta si se omite)")
    args = parser.parse_args()

    logger.info(f"Cargando Whisper '{args.size}' (CPU, int8). Primera vez descarga el modelo...")
    model = WhisperModel(args.size, device="cpu", compute_type="int8")

    segments, info = model.transcribe(args.audio, language=args.lang)
    logger.info(f"Idioma detectado: {info.language} (prob {info.language_probability:.2f})")
    text = "".join(seg.text for seg in segments).strip()
    print(text)


if __name__ == "__main__":
    main()
