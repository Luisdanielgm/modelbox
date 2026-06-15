"""Central path resolution for persistent Modelbox data.

In Docker, mount a single volume at ``/modelbox-data`` and set
``MODELBOX_DATA_DIR=/modelbox-data``. Everything that must survive redeploys lives under
that directory: Hugging Face cache, state, Supertonic weights, generated audio,
and optional Pocket-TTS gated clone weights.

In local development, when ``MODELBOX_DATA_DIR`` is not set, paths stay in their
traditional repo locations so existing CLI/dev workflows keep working.
"""
import os
import posixpath

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("MODELBOX_DATA_DIR")


def _join(base: str, *parts: str) -> str:
    """Join paths preserving Linux-style absolute paths when configured on Windows."""
    if base.startswith("/"):
        return posixpath.join(base, *parts)
    return os.path.join(base, *parts)

if DATA_DIR:
    STATE_DIR = _join(DATA_DIR, "state")
    LOGS_DIR = _join(DATA_DIR, "logs")
    OUTPUTS = _join(DATA_DIR, "outputs")
    SUPERTONIC_DIR = _join(DATA_DIR, "supertonic")
    POCKET_WEIGHTS = _join(DATA_DIR, "pocket-weights", "model.safetensors")
    # Cache dirs must be stable and visible in Dokploy terminal. If the image
    # default points at /data or /modelbox-data but the operator changed
    # MODELBOX_DATA_DIR, follow the custom data dir automatically. Explicit
    # non-default overrides still win.
    _default_homes = {"/data/hf", "/modelbox-data/hf"}
    _desired_hf = _join(DATA_DIR, "hf")
    if not os.environ.get("HF_HOME") or os.environ.get("HF_HOME") in _default_homes:
        os.environ["HF_HOME"] = _desired_hf
    _desired_xdg = _join(DATA_DIR, "cache")
    if not os.environ.get("XDG_CACHE_HOME") or os.environ.get("XDG_CACHE_HOME") in {"/data/cache", "/modelbox-data/cache"}:
        os.environ["XDG_CACHE_HOME"] = _desired_xdg
else:
    STATE_DIR = os.environ.get("MODELBOX_STATE_DIR") or os.path.join(ROOT, ".state")
    LOGS_DIR = os.environ.get("MODELBOX_LOGS_DIR") or os.path.join(ROOT, ".logs")
    OUTPUTS = os.path.join(ROOT, "outputs")
    SUPERTONIC_DIR = os.path.join(ROOT, "models", "supertonic", "assets")
    POCKET_WEIGHTS = os.path.join(ROOT, "models", "pockettts", "weights", "model.safetensors")

for path in (STATE_DIR, LOGS_DIR, OUTPUTS, SUPERTONIC_DIR, os.path.dirname(POCKET_WEIGHTS), os.environ.get("HF_HOME"), os.environ.get("XDG_CACHE_HOME")):
    if path:
        os.makedirs(path, exist_ok=True)
