"""Transcripción (STT) con faster-whisper (CTranslate2, CPU, int8).

No necesita torch. faster-whisper decodifica el audio internamente (vía PyAV),
así que acepta mp3/ogg/m4a/flac/wav sin conversión manual. El archivo recibido
es responsabilidad del que llama: se procesa y se borra (la API no guarda nada).
"""
import os

from shared import state

# Tamaño del modelo (configurable). small int8 ≈ 1 GB RAM, rápido en CPU.
WHISPER_SIZE = os.environ.get("MODELBOX_WHISPER_SIZE", "small")


def _dir_size_mb(path: str) -> float:
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


class WhisperTranscriber:
    name = "Whisper"
    key = "whisper"
    size = WHISPER_SIZE   # para mostrar en la UI (small/medium/large-v3)

    def __init__(self):
        self._model = None

    def is_downloaded(self) -> bool:
        if not state.is_downloaded(self.name):
            return False
        # A marker without real HF cache is stale (e.g. old deployment marked it
        # after a failed/wrong-path download). small int8 is hundreds of MB; 10 MB
        # is a conservative floor to detect empty caches without hard-coding model files.
        return _dir_size_mb(os.environ.get("HF_HOME")) > 10

    def download(self):
        """Descarga el modelo a la caché de HF y lo marca disponible."""
        import logging
        logging.getLogger(__name__).info("Descargando modelo: %s (%s)…", self.name, WHISPER_SIZE)
        from faster_whisper import WhisperModel
        download_root = os.environ.get("HF_HOME")
        WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8", download_root=download_root)
        if _dir_size_mb(download_root) <= 10:
            state.unmark_downloaded(self.name)
            raise RuntimeError(f"Whisper no dejo archivos de modelo en HF_HOME={download_root!r}.")
        state.mark_downloaded(self.name)

    def _ensure_loaded(self):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        if self._model is None:
            from faster_whisper import WhisperModel
            # local_files_only: si el marker existe pero la caché se perdió,
            # falla claro en vez de re-descargar en silencio.
            self._model = WhisperModel(WHISPER_SIZE, device="cpu",
                                       compute_type="int8",
                                       download_root=os.environ.get("HF_HOME"),
                                       local_files_only=True)

    def transcribe(self, audio_path, language=None) -> dict:
        """Devuelve {'text', 'language'}. `language` opcional (autodetecta si None)."""
        self._ensure_loaded()
        segments, info = self._model.transcribe(audio_path, language=language or None)
        text = "".join(seg.text for seg in segments).strip()
        return {"text": text, "language": info.language}


# Registro de transcriptores. Se filtra por MODELBOX_MODELS igual que los TTS.
_all = (WhisperTranscriber(),)
_selected = os.environ.get("MODELBOX_MODELS")
if _selected:
    _keys = {k.strip() for k in _selected.split(",") if k.strip()}
    _all = tuple(t for t in _all if t.key in _keys)
TRANSCRIBERS = {t.name: t for t in _all}
