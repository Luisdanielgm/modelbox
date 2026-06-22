"""Persistent usage/audit log for protected API calls.

Records are stored as JSON Lines in the configured Modelbox data volume. The log
intentionally stores metadata only: no raw prompt text and no audio bytes.
"""
from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from shared.paths import LOGS_DIR

USAGE_LOG = os.path.join(LOGS_DIR, "calls.jsonl")
_BACKUP_LOG = USAGE_LOG + ".1"
_lock = threading.Lock()

# Rotación por tamaño: cuando el log activo supera el tope, se renombra a .1
# (pisando el backup anterior). Así el archivo activo queda acotado y no crece
# sin límite. El historial visible conserva backup + activo.
_MAX_LOG_BYTES = max(1, int(os.environ.get("MODELBOX_MAX_LOG_MB", "5"))) * 1024 * 1024

TRIAL_PRICING = {
    "currency": "USD",
    "price_per_call": 0,
    "price_per_character": 0,
    "price_per_audio_minute": 0,
    "status": "initial_trial",
    "note": "Current price is 0 during the initial trial period. Pricing may change later.",
}


def new_request_id() -> str:
    return uuid4().hex


def _rotate_if_needed() -> None:
    """Rota el log activo a .1 si superó el tope (pisa el backup previo)."""
    try:
        if os.path.getsize(USAGE_LOG) >= _MAX_LOG_BYTES:
            os.replace(USAGE_LOG, _BACKUP_LOG)
    except OSError:
        pass


def append_call(record: dict[str, Any]) -> dict[str, Any]:
    os.makedirs(LOGS_DIR, exist_ok=True)
    clean = {
        "id": record.get("id") or new_request_id(),
        "ts": record.get("ts") or datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with _lock:
        _rotate_if_needed()
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")
    return clean


def _read_all(call_type: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with _lock:
        # Backup primero (más viejo), luego activo: queda ordenado de viejo a nuevo.
        for path in (_BACKUP_LOG, USAGE_LOG):
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if call_type and row.get("type") != call_type:
                        continue
                    rows.append(row)
    return rows


def read_calls(limit: int = 100, call_type: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 1000))
    rows = _read_all(call_type=call_type)
    return rows[-limit:][::-1]


def summarize(calls: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(c.get("type", "unknown")) for c in calls)
    total_chars = sum(int(c.get("text_chars") or 0) for c in calls)
    durations = [float(c.get("duration_seconds") or 0) for c in calls]
    waits = [float(c.get("wait_seconds") or 0) for c in calls]
    max_text_chars = max((int(c.get("text_chars") or 0) for c in calls), default=0)
    max_upload_mb = max((float(c.get("upload_mb") or 0) for c in calls), default=0.0)
    return {
        "total_calls": len(calls),
        "successful_calls": sum(1 for c in calls if c.get("success") is True),
        "failed_calls": sum(1 for c in calls if c.get("success") is False),
        "total_text_chars": total_chars,
        "max_text_chars": max_text_chars,
        "max_duration_seconds": round(max(durations, default=0.0), 4),
        "max_wait_seconds": round(max(waits, default=0.0), 4),
        "max_upload_mb": round(max_upload_mb, 4),
        "by_type": dict(by_type),
    }


def usage_payload(limit: int = 100, call_type: str | None = None) -> dict[str, Any]:
    all_calls = _read_all(call_type=call_type)
    calls = all_calls[-max(1, min(int(limit or 100), 1000)):][::-1]
    return {
        "pricing": TRIAL_PRICING,
        "log_file": USAGE_LOG,
        "summary": summarize(all_calls),
        "calls": calls,
    }
