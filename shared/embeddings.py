"""Embeddings (retrieval / RAG) con EmbeddingGemma-300M en ONNX.

No necesita torch: corre con onnxruntime + el tokenizer de transformers
(tensores numpy). El export ya devuelve ``sentence_embedding`` de 768-d, pooled y
L2-normalizado. Matryoshka permite truncar a 512/256/128 (se re-normaliza tras
truncar). Modelo y archivo ONNX son configurables por variable de entorno.
"""
import os

from shared import state

EMBED_MODEL_ID = os.environ.get("MODELBOX_EMBED_MODEL", "onnx-community/embeddinggemma-300m-ONNX")
EMBED_ONNX_FILE = os.environ.get("MODELBOX_EMBED_ONNX_FILE", "model.onnx")
EMBED_DEFAULT_DIM = 768
EMBED_VALID_DIMS = (768, 512, 256, 128)

# Prefijos por tarea que EmbeddingGemma espera (mejoran la calidad de retrieval).
_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "title: none | text: "


def _dir_size_mb(path) -> float:
    total = 0
    if not path or not os.path.exists(path):
        return 0.0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


class GemmaEmbedder:
    name = "EmbeddingGemma"
    key = "embeddings"
    dims = EMBED_DEFAULT_DIM
    valid_dims = EMBED_VALID_DIMS

    def __init__(self):
        self._session = None
        self._tokenizer = None
        self._input_names = None
        self._output_name = None

    def is_downloaded(self) -> bool:
        if not state.is_downloaded(self.name):
            return False
        # Un marker sin caché real de HF es obsoleto (descarga fallida/ruta vieja).
        return _dir_size_mb(os.environ.get("HF_HOME")) > 10

    def _fetch(self, local_files_only: bool):
        """Descarga (o localiza en caché) el ONNX + tokenizer. Devuelve (ruta_onnx, tokenizer)."""
        from huggingface_hub import hf_hub_download
        from transformers import AutoTokenizer
        model_path = hf_hub_download(EMBED_MODEL_ID, subfolder="onnx", filename=EMBED_ONNX_FILE,
                                     local_files_only=local_files_only)
        # Pesos externos (sidecar) si el export los usa; no todos los modelos lo tienen.
        try:
            hf_hub_download(EMBED_MODEL_ID, subfolder="onnx", filename=EMBED_ONNX_FILE + "_data",
                            local_files_only=local_files_only)
        except Exception:
            pass
        tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_ID, local_files_only=local_files_only)
        return model_path, tokenizer

    def download(self):
        import logging
        logging.getLogger(__name__).info("Descargando modelo: %s (%s)…", self.name, EMBED_MODEL_ID)
        self._fetch(local_files_only=False)
        if _dir_size_mb(os.environ.get("HF_HOME")) <= 10:
            state.unmark_downloaded(self.name)
            raise RuntimeError(f"{self.name} no dejó archivos de modelo en HF_HOME.")
        state.mark_downloaded(self.name)

    def _ensure_loaded(self):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        if self._session is None:
            import onnxruntime as ort
            # local_files_only: usa SOLO la caché; nunca descarga al inferir.
            model_path, tokenizer = self._fetch(local_files_only=True)
            self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            self._tokenizer = tokenizer
            self._input_names = {i.name for i in self._session.get_inputs()}
            outs = [o.name for o in self._session.get_outputs()]
            self._output_name = "sentence_embedding" if "sentence_embedding" in outs else outs[0]

    def embed(self, texts, task: str = "document", dimensions: int | None = None) -> list[list[float]]:
        """Devuelve una lista de vectores (uno por texto). `task`: 'document' o 'query'."""
        import numpy as np
        self._ensure_loaded()
        if isinstance(texts, str):
            texts = [texts]
        prefix = _QUERY_PREFIX if task == "query" else _DOCUMENT_PREFIX
        enc = self._tokenizer([prefix + (t or "") for t in texts],
                              padding=True, truncation=True, return_tensors="np")
        feed = {k: v for k, v in enc.items() if k in self._input_names}
        vecs = np.asarray(self._session.run([self._output_name], feed)[0], dtype="float32")
        if dimensions and dimensions < vecs.shape[1]:
            vecs = vecs[:, :dimensions]
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.clip(norms, 1e-12, None)
        return vecs.tolist()


# Registro de embedders. Se filtra por MODELBOX_MODELS igual que TTS/STT.
_all = (GemmaEmbedder(),)
_selected = os.environ.get("MODELBOX_MODELS")
if _selected:
    _keys = {k.strip() for k in _selected.split(",") if k.strip()}
    _all = tuple(e for e in _all if e.key in _keys)
EMBEDDERS = {e.name: e for e in _all}
