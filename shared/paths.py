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
    OUTPUTS = _join(DATA_DIR, "outputs")
    SUPERTONIC_DIR = _join(DATA_DIR, "supertonic")
    POCKET_WEIGHTS = _join(DATA_DIR, "pocket-weights", "model.safetensors")
    # HF cache for Pocket/Qwen/Whisper. This must be set before importing
    # huggingface_hub/transformers/faster-whisper. If an old deployment still
    # has the previous /data/hf default, follow MODELBOX_DATA_DIR automatically;
    # explicit non-default HF_HOME still wins.
    _legacy_hf = "/data/hf"
    _desired_hf = _join(DATA_DIR, "hf")
    if not os.environ.get("HF_HOME") or os.environ.get("HF_HOME") == _legacy_hf:
        os.environ["HF_HOME"] = _desired_hf
else:
    STATE_DIR = os.environ.get("MODELBOX_STATE_DIR") or os.path.join(ROOT, ".state")
    OUTPUTS = os.path.join(ROOT, "outputs")
    SUPERTONIC_DIR = os.path.join(ROOT, "models", "supertonic", "assets")
    POCKET_WEIGHTS = os.path.join(ROOT, "models", "pockettts", "weights", "model.safetensors")

for path in (STATE_DIR, OUTPUTS, SUPERTONIC_DIR, os.path.dirname(POCKET_WEIGHTS), os.environ.get("HF_HOME")):
    if path:
        os.makedirs(path, exist_ok=True)
