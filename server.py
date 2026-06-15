"""Servidor de Modelbox: API REST (con token) + panel Gradio (con login),
todo en un mismo proceso y puerto.

  - Panel:  /                       login usuario/clave (si están configurados)
  - API:    /api/health             estado (abierto)
            /api/models             lista de modelos          (Bearer token)
            /api/tts                genera audio y lo devuelve (Bearer token)

Variables de entorno (todas OPCIONALES):
  PANEL_USER, PANEL_PASSWORD  -> activan el login del panel
  API_TOKEN                   -> activa y protege la API

Sin variables: el panel queda abierto y la API apagada (útil para uso local
de cualquiera que clone el repo). Con ellas: panel con login + API encendida.

Correr:  uvicorn server:app --host 0.0.0.0 --port 7860   (o: python server.py)
"""
import os
from pathlib import Path
import secrets
import tempfile
import time

import gradio as gr
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app import demo
from shared import inference, state, usage
from shared.backends import BACKENDS
from shared.paths import DATA_DIR, LOGS_DIR, OUTPUTS, POCKET_WEIGHTS, STATE_DIR, SUPERTONIC_DIR
from shared.transcribe import TRANSCRIBERS

API_TOKEN = os.environ.get("API_TOKEN")
PANEL_USER = os.environ.get("PANEL_USER")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD")
MAX_UPLOAD_MB = int(os.environ.get("MODELBOX_MAX_UPLOAD_MB", "25"))


def _dir_size_mb(path: str | None) -> float:
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


def _require_token(authorization: str | None = Header(default=None)) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="API deshabilitada (configurar API_TOKEN).")
    # compare_digest: comparación de tiempo constante (no filtra el token byte a byte).
    if not secrets.compare_digest(authorization or "", f"Bearer {API_TOKEN}"):
        raise HTTPException(status_code=401, detail="Token inválido o ausente.")


def _read_capped(upload: UploadFile) -> bytes:
    """Lee el archivo subido con un tope de tamaño (evita DoS de memoria)."""
    limit = MAX_UPLOAD_MB * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Audio demasiado grande (máx {MAX_UPLOAD_MB} MB).")
    return data


def _mb(size_bytes: int | None) -> float:
    return round((size_bytes or 0) / 1e6, 4)


def _call_error(e: HTTPException | None) -> str | None:
    if e is None:
        return None
    return str(e.detail)


def _record_call(call: dict, started_at: float, slot_meta: dict | None,
                 error: HTTPException | None = None) -> None:
    """Best-effort audit log. It must never break the user request."""
    try:
        usage.append_call({
            **call,
            "duration_seconds": round(time.perf_counter() - started_at, 4),
            "status_code": error.status_code if error else 200,
            "success": error is None,
            "error": _call_error(error),
            "wait_seconds": (slot_meta or {}).get("wait_seconds", 0),
            "queue_before": (slot_meta or {}).get("queue_before"),
            "queue_at_start": (slot_meta or {}).get("queue_at_start"),
            "queue_at_finish": inference.status(),
        })
    except Exception:
        pass


app = FastAPI(title="Modelbox API", docs_url="/api/docs", openapi_url="/api/openapi.json")
AGENT_GUIDE = Path(__file__).parent / "docs" / "AGENT_INTEGRATION.md"


class TTSRequest(BaseModel):
    model: str
    text: str
    voice: str | None = None
    lang: str | None = None
    speed: float | None = None
    steps: int | None = None


def _model_info(name, backend) -> dict:
    return {"name": name, "downloaded": backend.is_downloaded(),
            "enabled": state.is_enabled(name)}


@app.get("/api/agent-guide")
def agent_guide():
    """Public, token-free integration guide for automation agents."""
    return Response(content=AGENT_GUIDE.read_text(encoding="utf-8"),
                    media_type="text/markdown; charset=utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok",
            "queue": inference.status(),
            "storage": {
                "modelbox_data_dir": DATA_DIR,
                "state_dir": STATE_DIR,
                "logs_dir": LOGS_DIR,
                "outputs_dir": OUTPUTS,
                "supertonic_dir": SUPERTONIC_DIR,
                "pocket_weights": POCKET_WEIGHTS,
                "hf_home": os.environ.get("HF_HOME"),
                "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
                "sizes_mb": {
                    "modelbox_data": _dir_size_mb(DATA_DIR),
                    "hf_home": _dir_size_mb(os.environ.get("HF_HOME")),
                    "xdg_cache_home": _dir_size_mb(os.environ.get("XDG_CACHE_HOME")),
                    "state": _dir_size_mb(STATE_DIR),
                    "supertonic": _dir_size_mb(SUPERTONIC_DIR),
                    "outputs": _dir_size_mb(OUTPUTS),
                },
                "state": state.diagnostics(),
            },
            "models": [_model_info(n, b) for n, b in BACKENDS.items()],
            "transcribers": [_model_info(n, t) for n, t in TRANSCRIBERS.items()]}


@app.get("/api/pricing")
def pricing():
    return usage.TRIAL_PRICING


@app.get("/api/usage", dependencies=[Depends(_require_token)])
def get_usage(limit: int = Query(100, ge=1, le=1000),
              type: str | None = Query(None, description="Filter by tts, clone, or transcribe")):
    return usage.usage_payload(limit=limit, call_type=type)


@app.get("/api/models", dependencies=[Depends(_require_token)])
def list_models():
    return [{**_model_info(n, b), "capabilities": b.capabilities}
            for n, b in BACKENDS.items()]


@app.post("/api/tts", dependencies=[Depends(_require_token)])
def tts(req: TTSRequest):
    started_at = time.perf_counter()
    slot_meta = None
    error = None
    call = {
        "id": usage.new_request_id(),
        "type": "tts",
        "model": req.model,
        "voice": req.voice,
        "lang": req.lang,
        "text_chars": len(req.text or ""),
        "output_bytes": 0,
    }
    try:
        backend = BACKENDS.get(req.model)
        if backend is None:
            raise HTTPException(404, f"Modelo desconocido: {req.model}")
        if not backend.is_downloaded():
            raise HTTPException(409, f"El modelo {req.model} no est? descargado.")
        if not state.is_enabled(req.model):
            raise HTTPException(403, f"El modelo {req.model} no est? habilitado para la API.")
        if not req.text or not req.text.strip():
            raise HTTPException(400, "Falta el texto.")

        caps = backend.capabilities
        opts = {}
        if caps["presets"] and req.voice:
            opts["voice"] = req.voice
        if caps["languages"] and req.lang:
            opts["lang"] = req.lang
        if caps["has_speed"] and req.speed is not None:
            opts["speed"] = req.speed
        if caps["has_steps"] and req.steps is not None:
            opts["steps"] = req.steps

        with inference.slot() as slot_meta:
            path = backend.synthesize(req.text, **opts)
        data = _read_wav_and_cleanup(path)
        call["output_bytes"] = len(data)
        return Response(content=data, media_type="audio/wav")
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de s?ntesis: {e}")
        raise error
    finally:
        _record_call(call, started_at, slot_meta, error)


def _read_wav_and_cleanup(path) -> bytes:
    """Lee el WAV, lo borra (la API no guarda audios) y lo devuelve."""
    if not path or not os.path.exists(path):
        raise HTTPException(500, "La s?ntesis no produjo un archivo de audio.")
    with open(path, "rb") as f:
        data = f.read()
    try:
        os.remove(path)
    except OSError:
        pass
    return data


@app.post("/api/clone", dependencies=[Depends(_require_token)])
def clone(model: str = Form(...), text: str = Form(...),
          ref_audio: UploadFile = File(...), ref_text: str = Form(None)):
    """Clona voz desde un audio de referencia. El audio se procesa y se borra."""
    started_at = time.perf_counter()
    slot_meta = None
    error = None
    call = {
        "id": usage.new_request_id(),
        "type": "clone",
        "model": model,
        "text_chars": len(text or ""),
        "ref_text_chars": len(ref_text or ""),
        "upload_filename": ref_audio.filename,
        "upload_mb": 0,
        "output_bytes": 0,
    }
    suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        backend = BACKENDS.get(model)
        if backend is None:
            raise HTTPException(404, f"Modelo desconocido: {model}")
        if not backend.capabilities.get("clone"):
            raise HTTPException(400, f"{model} no soporta clonaci?n.")
        if not backend.is_downloaded():
            raise HTTPException(409, f"El modelo {model} no est? descargado.")
        if not state.is_enabled(model):
            raise HTTPException(403, f"El modelo {model} no est? habilitado para la API.")
        if not text or not text.strip():
            raise HTTPException(400, "Falta el texto.")

        data = _read_capped(ref_audio)
        call["upload_mb"] = _mb(len(data))
        tmp.write(data)
        tmp.close()
        opts = {"ref_audio": tmp.name}
        if backend.capabilities.get("ref_text") and ref_text:
            opts["ref_text"] = ref_text
        with inference.slot() as slot_meta:
            path = backend.synthesize(text, **opts)
        wav = _read_wav_and_cleanup(path)
        call["output_bytes"] = len(wav)
        return Response(content=wav, media_type="audio/wav")
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de clonaci?n: {e}")
        raise error
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        _record_call(call, started_at, slot_meta, error)


@app.post("/api/transcribe", dependencies=[Depends(_require_token)])
def transcribe(audio: UploadFile = File(...), language: str = Form(None),
               model: str = Form("Whisper")):
    """Transcribe un audio (cualquier formato). El audio se procesa y se borra."""
    started_at = time.perf_counter()
    slot_meta = None
    error = None
    call = {
        "id": usage.new_request_id(),
        "type": "transcribe",
        "model": model,
        "lang": language,
        "upload_filename": audio.filename,
        "upload_mb": 0,
        "text_chars": 0,
    }
    suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tr = TRANSCRIBERS.get(model)
        if tr is None:
            raise HTTPException(404, f"Transcriptor desconocido: {model}")
        if not tr.is_downloaded():
            raise HTTPException(409, f"{model} no est? descargado.")
        if not state.is_enabled(model):
            raise HTTPException(403, f"{model} no est? habilitado para la API.")

        data = _read_capped(audio)
        call["upload_mb"] = _mb(len(data))
        tmp.write(data)
        tmp.close()
        with inference.slot() as slot_meta:
            result = tr.transcribe(tmp.name, language=language)
        call["text_chars"] = len((result or {}).get("text") or "")
        return result
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de transcripci?n: {e}")
        raise error
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        _record_call(call, started_at, slot_meta, error)


# Panel Gradio montado en la raíz. Con credenciales, exige login.
demo.queue()
_auth = (PANEL_USER, PANEL_PASSWORD) if (PANEL_USER and PANEL_PASSWORD) else None
app = gr.mount_gradio_app(app, demo, path="/", auth=_auth)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "7860")))
