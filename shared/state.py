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


def unmark_downloaded(name: str) -> None:
    try:
        os.remove(_marker(name))
    except FileNotFoundError:
        pass


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
    return round(total / 1e6, 2)


def hf_cache_size_mb(repo_id: str) -> float:
    """MB en la caché de HF para un repo específico (carpeta ``models--org--repo``).

    Reemplaza medir todo HF_HOME: distintos modelos comparten esa caché, así que
    el tamaño global no dice si ESTE modelo está descargado. Busca en HF_HOME y en
    HF_HOME/hub porque distintas librerías (huggingface_hub vs faster-whisper)
    usan una u otra ubicación.
    """
    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        return 0.0
    folder = "models--" + repo_id.replace("/", "--")
    # Solo una ubicación existe por modelo; se devuelve la primera encontrada
    # (sin sumar ambas, para no inflar el tamaño si algún día coexistieran).
    for base in (hf_home, os.path.join(hf_home, "hub")):
        candidate = os.path.join(base, folder)
        if os.path.isdir(candidate):
            return _dir_size_mb(candidate)
    return 0.0


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
        "state_dir_mb": _dir_size_mb(STATE_DIR),
        "outputs_mb": _dir_size_mb(OUTPUTS),
    }
