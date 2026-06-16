"""Operational limits for Modelbox requests.

These limits are intentionally service-level limits. They protect the API and UI
before model-specific behavior becomes unpredictable.
"""
from __future__ import annotations

import json
import os
import subprocess


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


MODELBOX_MAX_UPLOAD_MB = _env_int("MODELBOX_MAX_UPLOAD_MB", 30)
MODELBOX_MAX_TTS_CHARS = _env_int("MODELBOX_MAX_TTS_CHARS", 2000)
MODELBOX_MAX_CLONE_CHARS = _env_int("MODELBOX_MAX_CLONE_CHARS", 2000)
MODELBOX_MAX_AUDIO_SECONDS = _env_int("MODELBOX_MAX_AUDIO_SECONDS", 1200)


def text_chars(text: str | None) -> int:
    return len((text or "").strip())


def text_limit_error(text: str | None, max_chars: int, label: str = "texto") -> str | None:
    n = text_chars(text)
    if n > max_chars:
        return f"{label} demasiado largo ({n} caracteres, max {max_chars})."
    return None


def upload_too_large(size_bytes: int) -> bool:
    return size_bytes > MODELBOX_MAX_UPLOAD_MB * 1024 * 1024


def audio_duration_seconds(path: str) -> float | None:
    """Best-effort duration detection for wav/mp3/ogg/m4a/flac.

    Docker installs ffprobe. The soundfile fallback keeps local wav/flac checks
    working even when ffprobe is not available.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            duration = json.loads(proc.stdout or "{}").get("format", {}).get("duration")
            if duration is not None:
                return float(duration)
    except Exception:
        pass

    try:
        import soundfile as sf
        info = sf.info(path)
        if info.samplerate and info.frames:
            return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    return None


def audio_limit_error(path: str) -> str | None:
    duration = audio_duration_seconds(path)
    if duration is not None and duration > MODELBOX_MAX_AUDIO_SECONDS:
        return (
            f"Audio demasiado largo ({duration:.1f}s, "
            f"max {MODELBOX_MAX_AUDIO_SECONDS}s)."
        )
    return None


def public_limits() -> dict:
    return {
        "max_upload_mb": MODELBOX_MAX_UPLOAD_MB,
        "max_tts_chars": MODELBOX_MAX_TTS_CHARS,
        "max_clone_chars": MODELBOX_MAX_CLONE_CHARS,
        "max_audio_seconds": MODELBOX_MAX_AUDIO_SECONDS,
    }
