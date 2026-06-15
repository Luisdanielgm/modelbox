"""Capa de adaptadores: cada modelo declara sus capacidades y expone una
interfaz común. La UI se construye a partir de `capabilities`, así que agregar
un modelo nuevo = agregar un backend aquí, sin tocar la interfaz.

Cada backend carga su modelo de forma perezosa (solo al primer uso).
"""
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(ROOT, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)


class Backend:
    name = "base"
    capabilities = {
        "clone": False,        # ¿clona voz desde audio de referencia?
        "presets": [],         # voces preset disponibles
        "languages": [],       # idiomas (vacío = no aplica / autodetecta)
        "has_speed": False,    # control de velocidad
        "has_steps": False,    # control de pasos de difusión
    }

    def __init__(self):
        self._model = None

    def _ensure_loaded(self):
        raise NotImplementedError

    def synthesize(self, text, **opts) -> str:
        """Genera audio y devuelve la ruta del .wav."""
        raise NotImplementedError


class SupertonicBackend(Backend):
    name = "Supertonic-3"
    capabilities = {
        "clone": False,
        "presets": ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"],
        # 32 idiomas reales de supertonic-3 (AVAILABLE_LANGUAGES); 'na' = language-agnostic.
        "languages": ["es", "en", "fr", "de", "it", "pt", "ru", "ja", "ko", "ar",
                      "bg", "cs", "da", "el", "et", "fi", "hi", "hr", "hu", "id",
                      "lt", "lv", "nl", "pl", "ro", "sk", "sl", "sv", "tr", "uk",
                      "vi", "na"],
        "has_speed": True,
        "has_steps": True,
    }

    def _ensure_loaded(self):
        if self._model is None:
            from supertonic import TTS
            model_dir = os.path.join(ROOT, "models", "supertonic", "assets")
            self._model = TTS(model="supertonic-3", model_dir=model_dir, auto_download=True)

    def synthesize(self, text, voice="M1", lang="es", speed=1.05, steps=8, **_):
        self._ensure_loaded()
        style = self._model.get_voice_style(voice_name=voice)
        wav, _dur = self._model.synthesize(
            text=text, voice_style=style, lang=lang,
            total_steps=int(steps), speed=float(speed),
        )
        out = os.path.join(OUTPUTS, f"supertonic_{int(time.time())}.wav")
        self._model.save_audio(wav, out)
        return out


class QwenBackend(Backend):
    name = "Qwen3-TTS"
    capabilities = {
        "clone": True,
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

    def _ensure_loaded(self, clone: bool):
        mode = "clone" if clone else "preset"
        if self._model is not None and self._mode == mode:
            return
        import torch
        from qwen_tts import Qwen3TTSModel
        name = ("Qwen/Qwen3-TTS-12Hz-1.7B-Base" if clone
                else "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        self._model = Qwen3TTSModel.from_pretrained(
            name, torch_dtype=torch.float32, device_map="cpu", attn_implementation="sdpa",
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
                temp_ref = os.path.join(OUTPUTS, f"_tmp_ref_{int(time.time())}.wav")
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

        out = os.path.join(OUTPUTS, f"qwen3_{int(time.time())}.wav")
        sf.write(out, wavs[0], sr)
        return out


class PocketBackend(Backend):
    name = "Pocket-TTS"
    capabilities = {
        # clone=True requiere los pesos gated de kyutai/pocket-tts:
        # aceptar términos en HF + `hf auth login`. Una vez hecho, poné clone=True.
        "clone": False,
        "presets": ["alba", "cosette", "marius", "javert", "jean", "anna", "vera",
                    "fantine", "charles", "paul", "eponine", "azelma", "george",
                    "mary", "jane", "michael", "eve", "giovanni", "lola", "juergen",
                    "rafael", "estelle"],
        "languages": [],
        "has_speed": False,
        "has_steps": False,
    }

    def _ensure_loaded(self):
        if self._model is None:
            from pocket_tts import TTSModel
            self._model = TTSModel.load_model()

    def synthesize(self, text, voice="alba", ref_audio=None, max_tokens=200, **_):
        import soundfile as sf
        self._ensure_loaded()
        conditioning = ref_audio if ref_audio else voice
        state = self._model.get_state_for_audio_prompt(conditioning)
        audio = self._model.generate_audio(state, text, max_tokens=int(max_tokens))
        out = os.path.join(OUTPUTS, f"pocket_{int(time.time())}.wav")
        sf.write(out, audio.numpy(), self._model.sample_rate)
        return out


# Registro de backends disponibles. Agregar un modelo = una línea aquí.
BACKENDS = {b.name: b for b in (SupertonicBackend(), PocketBackend(), QwenBackend())}
