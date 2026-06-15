"""Transcripción (STT) con faster-whisper (CTranslate2, CPU, int8).

No necesita torch. faster-whisper decodifica el audio internamente (vía PyAV),
así que acepta mp3/ogg/m4a/flac/wav sin conversión manual. El archivo recibido
es responsabilidad del que llama: se procesa y se borra (la API no guarda nada).
"""
import os

from shared import state

# Tamaño del modelo (configurable). small int8 ≈ 1 GB RAM, rápido en CPU.
WHISPER_SIZE = os.environ.get("MODELBOX_WHISPER_SIZE", "small")


class WhisperTranscriber:
    name = "Whisper"
    key = "whisper"
    size = WHISPER_SIZE   # para mostrar en la UI (small/medium/large-v3)

    def __init__(self):
        self._model = None

    def is_downloaded(self) -> bool:
        return state.is_downloaded(self.name)

    def download(self):
        """Descarga el modelo a la caché de HF y lo marca disponible."""
        import logging
        logging.getLogger(__name__).info("Descargando modelo: %s (%s)…", self.name, WHISPER_SIZE)
        from faster_whisper import WhisperModel
        WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")
        state.mark_downloaded(self.name)

    def _ensure_loaded(self):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        if self._model is None:
            from faster_whisper import WhisperModel
            # local_files_only: si el marker existe pero la caché se perdió,
            # falla claro en vez de re-descargar en silencio.
            self._model = WhisperModel(WHISPER_SIZE, device="cpu",
                                       compute_type="int8", local_files_only=True)

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
