"""Modelbox server: native API + OpenAI-compatible wrappers + Gradio panel.

Routes:
  - Panel:  /                         optional username/password auth
  - Native: /api/health               public status
            /api/models               native model capabilities (Bearer token)
            /api/tts                  native TTS -> WAV (Bearer token)
            /api/transcribe           native STT -> JSON (Bearer token)
            /api/usage                usage audit log (Bearer token)
  - OpenAI: /v1/models
            /v1/audio/speech
            /v1/audio/transcriptions

The /v1/* routes are additive thin wrappers over the same Modelbox logic.
Existing /api/* clients and the panel keep their current behavior.

Run: uvicorn server:app --host 0.0.0.0 --port 7860   (or: python server.py)
"""
import contextvars
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

import gradio as gr
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app import demo
from shared import inference, limits, state, tokens, usage
from shared.backends import BACKENDS
from shared.embeddings import EMBEDDERS
from shared.paths import DATA_DIR, LOGS_DIR, OUTPUTS, POCKET_WEIGHTS, STATE_DIR, SUPERTONIC_DIR
from shared.transcribe import TRANSCRIBERS

PANEL_USER = os.environ.get("PANEL_USER")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD")

# Superficie de la llamada para el log de uso: "api" (nativo), "openai" (/v1/*).
# Las llamadas del panel se registran aparte con surface="panel".
_surface_var: contextvars.ContextVar[str] = contextvars.ContextVar("surface", default="api")
# Cliente resuelto por token, para atribuir cada llamada en el log de uso. Se setea
# en el body del endpoint (no en la dependencia: FastAPI ejecuta setup/teardown de
# dependencias en contextos distintos, así que un contextvar de dependencia no llega).
_client_var: contextvars.ContextVar[str] = contextvars.ContextVar("client", default="")

# /api/health es público y puede recibir polling (uptime monitors, LB). Recorrer
# HF_HOME (varios GB) en cada llamada sería I/O constante, así que cacheamos los
# tamaños por ruta durante un TTL corto.
_SIZE_CACHE_TTL = float(os.environ.get("MODELBOX_SIZE_CACHE_TTL", "30"))
_size_cache: dict[str, tuple[float, float]] = {}


def _safe_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except ValueError:
        return default


# Rate-limit por token (ventana fija), opt-in. Desactivado salvo que se configure
# MODELBOX_RATE_LIMIT > 0: así un despliegue no empieza a devolver 429 a un cliente
# existente (Cauce) sin coordinación. Aplica a los endpoints de inferencia.
_RATE_LIMIT = max(0, _safe_int("MODELBOX_RATE_LIMIT", 0))
_RATE_WINDOW = max(1, _safe_int("MODELBOX_RATE_WINDOW", 60))
_rate_lock = threading.Lock()
_rate_hits: dict[str, tuple[float, int]] = {}


def _token_from_header(authorization: str | None) -> str:
    return (authorization or "").removeprefix("Bearer ").strip()


def _rate_exceeded(token: str) -> bool:
    """True si el token superó el cupo en la ventana actual. No-op si está desactivado."""
    if _RATE_LIMIT <= 0:
        return False
    now = time.monotonic()
    with _rate_lock:
        start, count = _rate_hits.get(token, (now, 0))
        if now - start >= _RATE_WINDOW:
            start, count = now, 0
        count += 1
        _rate_hits[token] = (start, count)
        return count > _RATE_LIMIT


def _dir_size_mb(path: str | None) -> float:
    if not path or not os.path.exists(path):
        return 0.0
    now = time.monotonic()
    hit = _size_cache.get(path)
    if hit and now - hit[0] < _SIZE_CACHE_TTL:
        return hit[1]
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    value = round(total / 1e6, 2)
    _size_cache[path] = (now, value)
    return value


def _resolve_client(authorization: str | None) -> str | None:
    """Devuelve el cliente del token presentado, o None si no coincide con ninguno.
    Comparación de tiempo constante contra cada token conocido (no filtra byte a byte)."""
    presented = authorization or ""
    for value, client in tokens.TOKENS.items():
        if secrets.compare_digest(presented, f"Bearer {value}"):
            return client
    return None


def _authenticate(authorization: str | None) -> str:
    if not tokens.TOKENS:
        raise HTTPException(status_code=503, detail="API deshabilitada (configurar API_TOKEN).")
    client = _resolve_client(authorization)
    if client is None:
        raise HTTPException(status_code=401, detail="Token inválido o ausente.")
    return client


def _set_client(authorization: str | None) -> None:
    """Fija el cliente de esta llamada para el log. Solo actúa con un header str real:
    en una reutilización interna (p. ej. /v1 -> /api) la función nativa recibe el
    sentinel `Header(...)` como default (no un str), y así no pisa el cliente ya fijado."""
    if isinstance(authorization, str):
        _client_var.set(_resolve_client(authorization) or "")


def _require_token(authorization: str | None = Header(default=None)) -> None:
    _authenticate(authorization)


class OpenAIHTTPException(Exception):
    def __init__(self, status_code: int, message: str, type_: str, code: str):
        self.status_code = status_code
        self.message = message
        self.type = type_
        self.code = code


def _openai_error_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "server_error"
    return "invalid_request_error"


def _openai_error_code(status_code: int) -> str:
    return {
        400: "invalid_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "model_not_ready",
        413: "file_too_large",
        422: "validation_error",
        429: "rate_limit_exceeded",
        500: "server_error",
        503: "api_disabled",
    }.get(status_code, "error")


def _as_openai_exception(exc: HTTPException) -> OpenAIHTTPException:
    message = str(exc.detail)
    return OpenAIHTTPException(
        status_code=exc.status_code,
        message=message,
        type_=_openai_error_type(exc.status_code),
        code=_openai_error_code(exc.status_code),
    )


def _require_token_openai(authorization: str | None = Header(default=None)) -> None:
    try:
        _require_token(authorization)
    except HTTPException as exc:
        raise _as_openai_exception(exc)


def _rate_limit(authorization: str | None = Header(default=None)) -> None:
    if _rate_exceeded(_token_from_header(authorization)):
        raise HTTPException(
            429, f"Demasiadas solicitudes (max {_RATE_LIMIT} por {_RATE_WINDOW}s)."
        )


def _rate_limit_openai(authorization: str | None = Header(default=None)) -> None:
    try:
        _rate_limit(authorization)
    except HTTPException as exc:
        raise _as_openai_exception(exc)


def _openai_error_response(exc: OpenAIHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": exc.type,
                "code": exc.code,
            }
        },
    )


def _read_capped(upload: UploadFile) -> bytes:
    """Lee el archivo subido con un tope de tamaño (evita DoS de memoria)."""
    limit = limits.MODELBOX_MAX_UPLOAD_MB * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Audio demasiado grande (max {limits.MODELBOX_MAX_UPLOAD_MB} MB).")
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
            "surface": _surface_var.get(),
            "client": _client_var.get(),
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


def _enforce_text_limit(text: str | None, max_chars: int, label: str) -> None:
    msg = limits.text_limit_error(text, max_chars, label)
    if msg:
        raise HTTPException(400, msg)


def _audio_duration_checked(path: str) -> float | None:
    return limits.audio_duration_seconds(path)


app = FastAPI(title="Modelbox API", docs_url="/api/docs", openapi_url="/api/openapi.json")
AGENT_GUIDE = Path(__file__).parent / "docs" / "AGENT_INTEGRATION.md"

# CORS opt-in: solo se activa si MODELBOX_CORS_ORIGINS está configurado (lista
# separada por comas, o "*"). Sin esa variable no se agrega middleware y el
# comportamiento queda igual que antes. La auth es por header Bearer (no cookies),
# por eso allow_credentials=False es seguro incluso con "*".
_cors_origins = [o.strip() for o in os.environ.get("MODELBOX_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )


@app.exception_handler(OpenAIHTTPException)
async def openai_http_exception_handler(_request: Request, exc: OpenAIHTTPException):
    return _openai_error_response(exc)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/"):
        message = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg')}"
            for err in exc.errors()
        ) or "Invalid request"
        return _openai_error_response(
            OpenAIHTTPException(422, message, "invalid_request_error", "validation_error")
        )
    return await request_validation_exception_handler(request, exc)


class TTSRequest(BaseModel):
    model: str
    text: str
    voice: str | None = None
    lang: str | None = None
    speed: float | None = None
    steps: int | None = None
    response_format: str | None = None   # "wav" (default) | "mp3"


class OpenAISpeechRequest(BaseModel):
    model: str
    input: str
    voice: str | None = None
    response_format: str | None = None


class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]
    task: str | None = None          # "query" | "document" (default: document)
    dimensions: int | None = None    # Matryoshka: 512/256/128 (default 768)


class OpenAIEmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]
    dimensions: int | None = None
    encoding_format: str | None = None


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
            "limits": limits.public_limits(),
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
            "transcribers": [_model_info(n, t) for n, t in TRANSCRIBERS.items()],
            "embedders": [_model_info(n, e) for n, e in EMBEDDERS.items()]}


@app.get("/api/pricing")
def pricing():
    return usage.TRIAL_PRICING


@app.get("/api/usage", dependencies=[Depends(_require_token)])
def get_usage(limit: int = Query(100, ge=1, le=1000),
              type: str | None = Query(None, description="Filter by tts, clone, transcribe, or embeddings"),
              client: str | None = Query(None, description="Admin (API_TOKEN) only; a named token sees only its own usage"),
              authorization: str | None = Header(default=None)):
    # Aislamiento por cliente: el operador (API_TOKEN) ve todo y puede filtrar por
    # cualquier cliente; un token con nombre solo ve su propio uso.
    caller = _resolve_client(authorization)
    if caller is None:
        raise HTTPException(401, "Token inválido o ausente.")
    is_admin = tokens.ADMIN_CLIENT is not None and caller == tokens.ADMIN_CLIENT
    if not is_admin:
        if client and client != caller:
            raise HTTPException(403, "No autorizado a ver el uso de otro cliente.")
        client = caller
    return usage.usage_payload(limit=limit, call_type=type, client=client)


@app.get("/api/models", dependencies=[Depends(_require_token)])
def list_models():
    return [{**_model_info(n, b), "capabilities": b.capabilities}
            for n, b in BACKENDS.items()]


@app.post("/api/tts", dependencies=[Depends(_require_token), Depends(_rate_limit)])
def tts(req: TTSRequest, authorization: str | None = Header(default=None)):
    _set_client(authorization)
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
        "format": (req.response_format or "wav").lower(),
    }
    try:
        backend = BACKENDS.get(req.model)
        if backend is None:
            raise HTTPException(404, f"Modelo desconocido: {req.model}")
        if not req.text or not req.text.strip():
            raise HTTPException(400, "Falta el texto.")
        _enforce_text_limit(req.text, limits.MODELBOX_MAX_TTS_CHARS, "Texto TTS")
        if not backend.is_downloaded():
            raise HTTPException(409, f"El modelo {req.model} no esta descargado.")
        if not state.is_enabled(req.model):
            raise HTTPException(403, f"El modelo {req.model} no esta habilitado para la API.")

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
        # wav por defecto (retrocompatible); mp3 solo si se pide explícitamente.
        if call["format"] == "mp3":
            data, media = _to_mp3(data), "audio/mpeg"
        else:
            media = "audio/wav"
        call["output_bytes"] = len(data)
        return Response(content=data, media_type=media)
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de sintesis: {e}")
        raise error
    finally:
        _record_call(call, started_at, slot_meta, error)


def _read_wav_and_cleanup(path) -> bytes:
    """Lee el WAV, lo borra (la API no guarda audios) y lo devuelve."""
    if not path or not os.path.exists(path):
        raise HTTPException(500, "La sintesis no produjo un archivo de audio.")
    with open(path, "rb") as f:
        data = f.read()
    try:
        os.remove(path)
    except OSError:
        pass
    return data


def _to_mp3(wav_bytes: bytes) -> bytes:
    """Convierte WAV a MP3 vía ffmpeg (presente en la imagen). El WAV es corto
    (tope de caracteres de TTS), así que la conversión es rápida."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0", "-f", "mp3", "pipe:1"],
            input=wav_bytes, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"No se pudo convertir el audio a mp3: {e}")
    if proc.returncode != 0 or not proc.stdout:
        raise HTTPException(500, "No se pudo convertir el audio a mp3.")
    return proc.stdout


@app.post("/api/clone", dependencies=[Depends(_require_token), Depends(_rate_limit)])
def clone(model: str = Form(...), text: str = Form(...),
          ref_audio: UploadFile = File(...), ref_text: str = Form(None),
          authorization: str | None = Header(default=None)):
    """Clona voz desde un audio de referencia. El audio se procesa y se borra."""
    _set_client(authorization)
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
            raise HTTPException(400, f"{model} no soporta clonacion.")
        if not text or not text.strip():
            raise HTTPException(400, "Falta el texto.")
        _enforce_text_limit(text, limits.MODELBOX_MAX_CLONE_CHARS, "Texto de clonacion")
        if not backend.is_downloaded():
            raise HTTPException(409, f"El modelo {model} no esta descargado.")
        if not state.is_enabled(model):
            raise HTTPException(403, f"El modelo {model} no esta habilitado para la API.")

        data = _read_capped(ref_audio)
        call["upload_mb"] = _mb(len(data))
        tmp.write(data)
        tmp.close()
        audio_seconds = _audio_duration_checked(tmp.name)
        if audio_seconds is not None:
            call["upload_seconds"] = round(audio_seconds, 4)
            if audio_seconds > limits.MODELBOX_MAX_AUDIO_SECONDS:
                raise HTTPException(
                    400,
                    f"Audio demasiado largo ({audio_seconds:.1f}s, max {limits.MODELBOX_MAX_AUDIO_SECONDS}s).",
                )
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
        error = HTTPException(500, f"Error de clonacion: {e}")
        raise error
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        _record_call(call, started_at, slot_meta, error)


def _transcribe_upload(audio: UploadFile, language: str | None, model: str,
                       include_duration: bool = False) -> dict:
    """Shared STT implementation for /api/transcribe and /v1/audio/transcriptions."""
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
            raise HTTPException(409, f"{model} no esta descargado.")
        if not state.is_enabled(model):
            raise HTTPException(403, f"{model} no esta habilitado para la API.")

        data = _read_capped(audio)
        call["upload_mb"] = _mb(len(data))
        tmp.write(data)
        tmp.close()
        audio_seconds = _audio_duration_checked(tmp.name)
        if audio_seconds is None and include_duration:
            raise HTTPException(400, "No se pudo medir la duracion del audio.")
        if audio_seconds is not None:
            call["upload_seconds"] = round(audio_seconds, 4)
            if audio_seconds > limits.MODELBOX_MAX_AUDIO_SECONDS:
                raise HTTPException(
                    400,
                    f"Audio demasiado largo ({audio_seconds:.1f}s, max {limits.MODELBOX_MAX_AUDIO_SECONDS}s).",
                )
        with inference.slot() as slot_meta:
            result = tr.transcribe(tmp.name, language=language)
        call["text_chars"] = len((result or {}).get("text") or "")
        if include_duration:
            result = {**result, "duration": round(float(audio_seconds), 4)}
        return result
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de transcripcion: {e}")
        raise error
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        _record_call(call, started_at, slot_meta, error)


@app.post("/api/transcribe", dependencies=[Depends(_require_token), Depends(_rate_limit)])
def transcribe(audio: UploadFile = File(...), language: str = Form(None),
               model: str = Form("Whisper"),
               authorization: str | None = Header(default=None)):
    """Transcribe audio with the native Modelbox API response shape."""
    _set_client(authorization)
    return _transcribe_upload(audio=audio, language=language, model=model, include_duration=False)


@app.post("/api/embeddings", dependencies=[Depends(_require_token), Depends(_rate_limit)])
def embeddings(req: EmbeddingsRequest, authorization: str | None = Header(default=None)):
    """Genera embeddings (texto -> vectores) para retrieval/RAG. No guarda nada."""
    _set_client(authorization)
    started_at = time.perf_counter()
    slot_meta = None
    error = None
    inputs = [req.input] if isinstance(req.input, str) else list(req.input)
    call = {
        "id": usage.new_request_id(),
        "type": "embeddings",
        "model": req.model,
        "items": len(inputs),
        "text_chars": sum(len(t or "") for t in inputs),
        "dimensions": req.dimensions,
    }
    try:
        embedder = EMBEDDERS.get(req.model)
        if embedder is None:
            raise HTTPException(404, f"Modelo de embeddings desconocido: {req.model}")
        if not inputs:
            raise HTTPException(400, "Falta el texto de entrada.")
        if len(inputs) > limits.MODELBOX_MAX_EMBED_ITEMS:
            raise HTTPException(400, f"Demasiados textos ({len(inputs)}, max {limits.MODELBOX_MAX_EMBED_ITEMS}).")
        valid_dims = getattr(embedder, "valid_dims", (768, 512, 256, 128))
        if req.dimensions is not None and req.dimensions not in valid_dims:
            allowed = ", ".join(str(d) for d in valid_dims)
            raise HTTPException(400, f"dimensions invalido ({req.dimensions}); valores permitidos: {allowed}.")
        for t in inputs:
            _enforce_text_limit(t, limits.MODELBOX_MAX_EMBED_CHARS, "Texto de embeddings")
        if not embedder.is_downloaded():
            raise HTTPException(409, f"El modelo {req.model} no esta descargado.")
        if not state.is_enabled(req.model):
            raise HTTPException(403, f"El modelo {req.model} no esta habilitado para la API.")

        task = "query" if (req.task or "").lower() == "query" else "document"
        with inference.slot() as slot_meta:
            vectors = embedder.embed(inputs, task=task, dimensions=req.dimensions)
        return {
            "model": req.model,
            "task": task,
            "dimensions": len(vectors[0]) if vectors else 0,
            "embeddings": vectors,
        }
    except HTTPException as e:
        error = e
        raise
    except Exception as e:
        error = HTTPException(500, f"Error de embeddings: {e}")
        raise error
    finally:
        _record_call(call, started_at, slot_meta, error)


def _enabled_model_ids() -> list[str]:
    ids = []
    for name, backend in BACKENDS.items():
        if backend.is_downloaded() and state.is_enabled(name):
            ids.append(name)
    for name, transcriber in TRANSCRIBERS.items():
        if transcriber.is_downloaded() and state.is_enabled(name):
            ids.append(name)
    for name, embedder in EMBEDDERS.items():
        if embedder.is_downloaded() and state.is_enabled(name):
            ids.append(name)
    return ids


@app.get("/v1/models", dependencies=[Depends(_require_token_openai)])
def v1_models():
    """OpenAI-compatible model list with downloaded+enabled Modelbox models."""
    try:
        return {
            "object": "list",
            "data": [{"id": model_id, "object": "model"} for model_id in _enabled_model_ids()],
        }
    except Exception as exc:
        raise OpenAIHTTPException(500, str(exc), "server_error", "server_error")


@app.post("/v1/audio/speech", dependencies=[Depends(_require_token_openai), Depends(_rate_limit_openai)])
def v1_audio_speech(req: OpenAISpeechRequest, authorization: str | None = Header(default=None)):
    """OpenAI-compatible TTS wrapper over /api/tts. Returns audio/wav (default) or
    audio/mpeg when response_format is 'mp3'."""
    token = _surface_var.set("openai")
    _set_client(authorization)
    try:
        return tts(TTSRequest(model=req.model, text=req.input, voice=req.voice,
                              response_format=req.response_format))
    except HTTPException as exc:
        raise _as_openai_exception(exc)
    finally:
        _surface_var.reset(token)


@app.post("/v1/audio/transcriptions", dependencies=[Depends(_require_token_openai), Depends(_rate_limit_openai)])
def v1_audio_transcriptions(file: UploadFile = File(...), model: str = Form(...),
                            response_format: str = Form("json"),
                            language: str | None = Form(None),
                            authorization: str | None = Header(default=None)):
    """OpenAI-compatible STT wrapper over /api/transcribe with mandatory duration."""
    if response_format not in (None, "json", "verbose_json"):
        raise OpenAIHTTPException(
            400,
            "response_format must be 'json' or 'verbose_json'.",
            "invalid_request_error",
            "invalid_response_format",
        )
    token = _surface_var.set("openai")
    _set_client(authorization)
    try:
        result = _transcribe_upload(
            audio=file, language=language, model=model, include_duration=True
        )
        return {
            "text": result.get("text", ""),
            "duration": float(result["duration"]),
            "language": result.get("language"),
        }
    except HTTPException as exc:
        raise _as_openai_exception(exc)
    finally:
        _surface_var.reset(token)


@app.post("/v1/embeddings", dependencies=[Depends(_require_token_openai), Depends(_rate_limit_openai)])
def v1_embeddings(req: OpenAIEmbeddingsRequest, authorization: str | None = Header(default=None)):
    """OpenAI-compatible embeddings wrapper over /api/embeddings (task=document)."""
    if req.encoding_format not in (None, "float"):
        raise OpenAIHTTPException(
            400, "encoding_format must be 'float'.",
            "invalid_request_error", "invalid_encoding_format",
        )
    token = _surface_var.set("openai")
    _set_client(authorization)
    try:
        result = embeddings(EmbeddingsRequest(
            model=req.model, input=req.input, task="document", dimensions=req.dimensions,
        ))
    except HTTPException as exc:
        raise _as_openai_exception(exc)
    finally:
        _surface_var.reset(token)
    vectors = result["embeddings"]
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    approx_tokens = sum(len(t or "") for t in texts)  # aproximado (no exacto en tokens)
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": req.model,
        "usage": {"prompt_tokens": approx_tokens, "total_tokens": approx_tokens},
    }


# Panel Gradio montado en la raíz. Con credenciales, exige login.
demo.queue()
_auth = (PANEL_USER, PANEL_PASSWORD) if (PANEL_USER and PANEL_PASSWORD) else None
app = gr.mount_gradio_app(app, demo, path="/", auth=_auth)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "7860")))
