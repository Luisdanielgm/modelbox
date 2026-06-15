"""Capa de adaptadores: cada modelo declara sus capacidades y expone una
interfaz común. La UI se construye a partir de `capabilities`, así que agregar
un modelo nuevo = agregar un backend aquí, sin tocar la interfaz.

Cada backend carga su modelo de forma perezosa (solo al primer uso).
"""
import logging
import os
import uuid

from shared import state
from shared.paths import OUTPUTS, POCKET_WEIGHTS, SUPERTONIC_DIR

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _peak_normalize(x, target=0.95):
    """Normaliza el pico de amplitud a un nivel parejo (evita que unas voces
    salgan más bajas que otras). Acepta numpy array o torch tensor."""
    import numpy as np
    arr = x.detach().cpu().numpy() if hasattr(x, "detach") else np.asarray(x)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1e-6:
        arr = arr * (target / peak)
    return arr


class Backend:
    name = "base"
    key = "base"   # identificador corto (coincide con el ARG MODELS del Dockerfile)
    capabilities = {
        "clone": False,        # ¿clona voz desde audio de referencia?
        "ref_text": False,     # ¿usa texto del audio de referencia? (solo Qwen)
        "presets": [],         # voces preset disponibles
        "languages": [],       # idiomas (vacío = no aplica / autodetecta)
        "has_speed": False,    # control de velocidad
        "has_steps": False,    # control de pasos de difusión
    }

    def __init__(self):
        self._model = None

    def is_downloaded(self) -> bool:
        return state.is_downloaded(self.name)

    def download(self):
        """Descarga los pesos del modelo (al volumen/caché) y lo marca disponible.
        Solo se ejecuta cuando el usuario lo pide; nada se baja automáticamente."""
        raise NotImplementedError

    def _ensure_loaded(self):
        raise NotImplementedError

    def synthesize(self, text, **opts) -> str:
        """Genera audio y devuelve la ruta del .wav."""
        raise NotImplementedError


class SupertonicBackend(Backend):
    name = "Supertonic-3"
    key = "supertonic"
    capabilities = {
        "clone": False,
        "ref_text": False,
        "presets": ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"],
        # 32 idiomas reales de supertonic-3 (AVAILABLE_LANGUAGES); 'na' = language-agnostic.
        "languages": ["es", "en", "fr", "de", "it", "pt", "ru", "ja", "ko", "ar",
                      "bg", "cs", "da", "el", "et", "fi", "hi", "hr", "hu", "id",
                      "lt", "lv", "nl", "pl", "ro", "sk", "sl", "sv", "tr", "uk",
                      "vi", "na"],
        "has_speed": True,
        "has_steps": True,
    }

    def download(self):
        logger.info("Descargando modelo: %s…", self.name)
        from supertonic import TTS
        os.makedirs(SUPERTONIC_DIR, exist_ok=True)
        TTS(model="supertonic-3", model_dir=SUPERTONIC_DIR, auto_download=True)
        state.mark_downloaded(self.name)

    def _ensure_loaded(self):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        if self._model is None:
            from supertonic import TTS
            self._model = TTS(model="supertonic-3", model_dir=SUPERTONIC_DIR, auto_download=False)

    def synthesize(self, text, voice="M1", lang="es", speed=1.05, steps=8, **_):
        self._ensure_loaded()
        style = self._model.get_voice_style(voice_name=voice)
        wav, _dur = self._model.synthesize(
            text=text, voice_style=style, lang=lang,
            total_steps=int(steps), speed=float(speed),
        )
        out = os.path.join(OUTPUTS, f"supertonic_{uuid.uuid4().hex}.wav")
        self._model.save_audio(_peak_normalize(wav), out)
        return out


class QwenBackend(Backend):
    name = "Qwen3-TTS"
    key = "qwen"
    capabilities = {
        "clone": True,
        "ref_text": True,
        # Speakers reales del modelo CustomVoice (model.get_supported_speakers()).
        "presets": ["serena", "aiden", "dylan", "eric", "ryan", "vivian",
                    "sohee", "ono_anna", "uncle_fu"],
        "languages": [],         # multilingüe, sin selector explícito
        "has_speed": False,
        "has_steps": False,
    }

    def __init__(self):
        super().__init__()
        self._mode = None  # 'clone' o 'preset' (define qué modelo cargar)

    def download(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        # Bajamos AMBOS modelos para que el marker sea veraz y la clonación NO
        # dispare una autodescarga sorpresa: CustomVoice (presets) + Base (clon).
        for repo in ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                     "Qwen/Qwen3-TTS-12Hz-1.7B-Base"):
            logger.info("Descargando modelo: %s (%s)…", self.name, repo)
            Qwen3TTSModel.from_pretrained(
                repo, torch_dtype=torch.float32, device_map="cpu", attn_implementation="sdpa",
            )
        state.mark_downloaded(self.name)

    def _ensure_loaded(self, clone: bool):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        mode = "clone" if clone else "preset"
        if self._model is not None and self._mode == mode:
            return
        import torch
        from qwen_tts import Qwen3TTSModel
        name = ("Qwen/Qwen3-TTS-12Hz-1.7B-Base" if clone
                else "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        # local_files_only: cargar usa SOLO la caché; nunca descarga (eso es
        # exclusivo de download()). Si falta, falla claro en vez de bajar en silencio.
        self._model = Qwen3TTSModel.from_pretrained(
            name, torch_dtype=torch.float32, device_map="cpu",
            attn_implementation="sdpa", local_files_only=True,
        )
        self._mode = mode

    def synthesize(self, text, voice="serena", ref_audio=None, ref_text=None, **_):
        import torch
        import soundfile as sf
        clone = bool(ref_audio)
        self._ensure_loaded(clone)

        # Qwen prefiere WAV PCM. Si la referencia es ogg/mp3, convertimos a WAV temporal.
        temp_ref = None
        ref_path = ref_audio
        if clone and ref_audio and not ref_audio.lower().endswith(".wav"):
            try:
                data, sr_in = sf.read(ref_audio)
                temp_ref = os.path.join(OUTPUTS, f"_tmp_ref_{uuid.uuid4().hex}.wav")
                sf.write(temp_ref, data, sr_in)
                ref_path = temp_ref
            except Exception:
                ref_path = ref_audio  # fallback: que qwen intente con el original

        try:
            with torch.inference_mode():
                if clone:
                    if ref_text:
                        prompt = self._model.create_voice_clone_prompt(
                            ref_audio=ref_path, ref_text=ref_text, x_vector_only_mode=False)
                    else:
                        prompt = self._model.create_voice_clone_prompt(
                            ref_audio=ref_path, x_vector_only_mode=True)
                    wavs, sr = self._model.generate_voice_clone(text=text, voice_clone_prompt=prompt)
                else:
                    wavs, sr = self._model.generate_custom_voice(text=text, speaker=voice)
        finally:
            if temp_ref and os.path.exists(temp_ref):
                try:
                    os.remove(temp_ref)
                except OSError:
                    pass

        out = os.path.join(OUTPUTS, f"qwen3_{uuid.uuid4().hex}.wav")
        sf.write(out, _peak_normalize(wavs[0]), sr)
        return out


class PocketBackend(Backend):
    name = "Pocket-TTS"
    key = "pocket"
    # Pesos gated de clonación (los baja el usuario con su login HF). Si existen,
    # la clonación se habilita sola; si no, queda en modo solo-presets.
    _DIR = os.path.join(ROOT, "models", "pockettts")   # template clone_spanish.yaml (en la imagen)
    _CLONE_WEIGHTS = POCKET_WEIGHTS                      # pesos gated (en el volumen de datos)
    _PRESETS = ["alba", "cosette", "marius", "javert", "jean", "anna", "vera",
                "fantine", "charles", "paul", "eponine", "azelma", "george",
                "mary", "jane", "michael", "eve", "giovanni", "lola", "juergen",
                "rafael", "estelle"]

    @property
    def capabilities(self):
        # `clone` se recalcula en runtime: si bajás los pesos gated después de
        # arrancar el servicio, la clonación se habilita sin reiniciar.
        return {
            "clone": os.path.exists(self._CLONE_WEIGHTS),
            "ref_text": False,
            "presets": self._PRESETS,
            "languages": [],
            "has_speed": False,
            "has_steps": False,
        }

    def _resolved_config(self):
        """Genera el yaml de clonación con la ruta absoluta de los pesos locales.
        Se escribe en un tempdir para no ensuciar el volumen de pesos del usuario."""
        import tempfile
        template = os.path.join(self._DIR, "clone_spanish.yaml")
        with open(template, encoding="utf-8") as f:
            text = f.read()
        text = text.replace("__LOCAL_CLONE_WEIGHTS__", self._CLONE_WEIGHTS.replace("\\", "/"))
        resolved = os.path.join(tempfile.gettempdir(), "modelbox_pocket_resolved.yaml")
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(text)
        return resolved

    _device = "cpu"

    def _download_clone_weights_if_token(self) -> bool:
        """Download optional gated Pocket clone weights when HF_TOKEN is configured."""
        if os.path.exists(self._CLONE_WEIGHTS):
            return True
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not token:
            return False
        logger.info("Descargando pesos gated de clonacion de %s...", self.name)
        from huggingface_hub import hf_hub_download
        src = hf_hub_download(
            repo_id="kyutai/pocket-tts",
            filename="languages/spanish/model.safetensors",
            token=token,
        )
        os.makedirs(os.path.dirname(self._CLONE_WEIGHTS), exist_ok=True)
        import shutil
        tmp = f"{self._CLONE_WEIGHTS}.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, self._CLONE_WEIGHTS)
        return True

    def download_clone_weights(self) -> None:
        if self._download_clone_weights_if_token():
            from pocket_tts import TTSModel
            TTSModel.load_model(config=self._resolved_config())
            return
        raise RuntimeError(
            "Configura HF_TOKEN en el servidor y acepta los terminos de kyutai/pocket-tts en Hugging Face."
        )

    def download(self):
        logger.info("Descargando modelo: %s…", self.name)
        from pocket_tts import TTSModel
        TTSModel.load_model()  # modelo de presets (~100M) a la caché de HF
        # Si están los pesos gated de clonación, pre-bajamos también el modelo del
        # config de clonación para que el PRIMER clon no dispare una descarga sorpresa.
        if os.path.exists(self._CLONE_WEIGHTS):
            logger.info("Descargando pesos de clonación de %s…", self.name)
            TTSModel.load_model(config=self._resolved_config())
        state.mark_downloaded(self.name)

    def _ensure_loaded(self):
        if not self.is_downloaded():
            raise RuntimeError(f"{self.name} no está descargado. Descargalo desde el panel.")
        if self._model is None:
            import torch
            from pocket_tts import TTSModel
            if os.path.exists(self._CLONE_WEIGHTS):
                self._model = TTSModel.load_model(config=self._resolved_config())
                # Cargar desde un config propio hace que el modelo pierda la
                # asociación de idioma que las voces preset necesitan. Apuntamos
                # `origin` al config de idioma del paquete: así los presets
                # resuelven sus embeddings y la clonación (pesos locales ya
                # cargados) sigue funcionando, todo en un solo modelo.
                from pocket_tts.utils.config import CONFIGS_DIR
                self._model.origin = CONFIGS_DIR / "spanish.yaml"
            else:
                self._model = TTSModel.load_model()
            # Pocket es liviano (~540MB en VRAM): si hay GPU, la usamos. Mucho
            # más rápido (RTF < 1) y habilita streaming real.
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            if self._device == "cuda":
                self._model.to("cuda")

    def uses_gpu(self):
        """True si el modelo corre en GPU (suficientemente rápido para streaming)."""
        self._ensure_loaded()
        return self._device == "cuda"

    def synthesize(self, text, voice="alba", ref_audio=None, max_tokens=200, **_):
        import soundfile as sf
        self._ensure_loaded()
        conditioning = ref_audio if ref_audio else voice
        audio_state = self._model.get_state_for_audio_prompt(conditioning)
        audio = self._model.generate_audio(audio_state, text, max_tokens=int(max_tokens))
        out = os.path.join(OUTPUTS, f"pocket_{uuid.uuid4().hex}.wav")
        sf.write(out, _peak_normalize(audio), self._model.sample_rate)
        return out

    def synthesize_stream(self, text, voice="alba", ref_audio=None, max_tokens=200, **_):
        """Generador: produce (sample_rate, chunk_float32) a medida que genera.
        Solo conviene en GPU (RTF < 1). Al terminar guarda el WAV completo."""
        import numpy as np
        import soundfile as sf
        self._ensure_loaded()
        conditioning = ref_audio if ref_audio else voice
        audio_state = self._model.get_state_for_audio_prompt(conditioning)
        sr = self._model.sample_rate
        chunks = []
        for chunk in self._model.generate_audio_stream(audio_state, text, max_tokens=int(max_tokens)):
            arr = chunk.detach().cpu().numpy().astype("float32")
            chunks.append(arr)
            yield sr, arr
        if chunks:
            full = _peak_normalize(np.concatenate(chunks))
            sf.write(os.path.join(OUTPUTS, f"pocket_{uuid.uuid4().hex}.wav"), full, sr)


# Registro de backends. Agregar un modelo = una línea acá.
_all_backends = (SupertonicBackend(), PocketBackend(), QwenBackend())
# MODELBOX_MODELS (lo setea el Dockerfile desde el ARG MODELS) limita qué modelos
# están disponibles en esta imagen: solo se incluyen los que tienen sus librerías
# instaladas. Sin la variable (uso local), están todos.
_selected = os.environ.get("MODELBOX_MODELS")
if _selected:
    # Filtro estricto: solo los modelos cuyas librerías se instalaron en este
    # build. Puede dejar BACKENDS vacío (p. ej. build whisper-solo); la UI lo maneja.
    _keys = {k.strip() for k in _selected.split(",") if k.strip()}
    _all_backends = tuple(b for b in _all_backends if b.key in _keys)
BACKENDS = {b.name: b for b in _all_backends}
