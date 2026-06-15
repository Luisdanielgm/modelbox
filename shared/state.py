"""Estado persistente del servicio: qué modelos están descargados y cuáles
están habilitados para la API. Se guarda en disco (un volumen en Docker) para
sobrevivir reinicios y redeploys.

También limpia los audios viejos de outputs/ para que no se acumulen.
"""
import json
import logging
import os
import threading
import time

from shared.paths import OUTPUTS, STATE_DIR

logger = logging.getLogger(__name__)

_ENABLED_FILE = os.path.join(STATE_DIR, "enabled.json")
_KEEP_OUTPUTS = 20    # audios a conservar en outputs/; el resto se borra.
_MIN_AGE_SECS = 60    # nunca borrar audios más nuevos que esto (evita pisar uno en uso).
_enabled_lock = threading.Lock()


def _marker(name: str) -> str:
    safe = name.replace("/", "_").replace(" ", "_")
    return os.path.join(STATE_DIR, f"{safe}.downloaded")


def is_downloaded(name: str) -> bool:
    return os.path.exists(_marker(name))


def mark_downloaded(name: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_marker(name), "w", encoding="utf-8") as f:
        f.write("ok")


def _load_enabled() -> dict:
    try:
        with open(_ENABLED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # JSON corrupto: avisamos en vez de resetear silenciosamente todo a off.
        logger.warning("enabled.json ilegible o corrupto; se asume todo deshabilitado.")
        return {}


def is_enabled(name: str) -> bool:
    return bool(_load_enabled().get(name, False))


def set_enabled(name: str, value: bool) -> None:
    """Lock + escritura atómica: dos toggles concurrentes no se pisan ni
    dejan el archivo a medio escribir."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with _enabled_lock:
        data = _load_enabled()
        data[name] = bool(value)
        tmp = f"{_ENABLED_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _ENABLED_FILE)


def cleanup_outputs(keep: int = _KEEP_OUTPUTS) -> None:
    """Borra los audios más viejos de outputs/, dejando los últimos `keep` y
    nunca tocando los creados en los últimos `_MIN_AGE_SECS` (uno podría estar
    en uso por otra request en paralelo)."""
    try:
        entries = [os.path.join(OUTPUTS, f) for f in os.listdir(OUTPUTS)]
    except OSError:
        return
    now = time.time()
    files = sorted((p for p in entries if os.path.isfile(p)),
                   key=os.path.getmtime, reverse=True)
    for p in files[keep:]:
        try:
            if now - os.path.getmtime(p) < _MIN_AGE_SECS:
                continue
            os.remove(p)
        except OSError:
            pass


def diagnostics() -> dict:
    """Small read-only snapshot to verify persisted state/markers in production."""
    try:
        markers = sorted(f for f in os.listdir(STATE_DIR) if f.endswith(".downloaded"))
    except OSError:
        markers = []
    return {
        "state_dir": STATE_DIR,
        "enabled_file": _ENABLED_FILE,
        "enabled": _load_enabled(),
        "download_markers": markers,
    }
