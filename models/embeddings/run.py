import argparse
import logging

from shared.embeddings import GemmaEmbedder

logging.basicConfig(level=logging.INFO)


def main():
    parser = argparse.ArgumentParser(description="Generar embeddings con EmbeddingGemma (ONNX, CPU, sin torch)")
    parser.add_argument("--text", type=str, required=True, help="Texto a embeddear")
    parser.add_argument("--task", type=str, default="document", choices=["document", "query"],
                        help="Tipo de texto: 'document' (default) o 'query'")
    parser.add_argument("--dimensions", type=int, default=None,
                        help="Truncar a 512/256/128 (Matryoshka). Default: 768 completo")
    args = parser.parse_args()

    embedder = GemmaEmbedder()
    if not embedder.is_downloaded():
        logging.info("Descargando EmbeddingGemma (primera vez)...")
        embedder.download()

    vec = embedder.embed(args.text, task=args.task, dimensions=args.dimensions)[0]
    print(f"dims={len(vec)}  primeros 8 valores={[round(x, 4) for x in vec[:8]]}")


if __name__ == "__main__":
    main()
