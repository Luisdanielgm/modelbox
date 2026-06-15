"""Control de concurrencia para inferencia.

En CPU, una sola inferencia ya usa todos los cores: correr varias a la vez no
da más throughput, solo las hace más lentas. Por eso serializamos con un
semáforo global (límite configurable) y los pedidos extra esperan en cola.

Lo usan TANTO el panel como la API, así comparten el mismo límite real.
"""
import os
import threading
import time
from contextlib import contextmanager

MAX_CONCURRENT = max(1, int(os.environ.get("MODELBOX_MAX_CONCURRENT", "1")))

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_lock = threading.Lock()
_active = 0
_waiting = 0


@contextmanager
def slot():
    """Ocupa un turno de inferencia; bloquea (en cola) si no hay libres."""
    global _active, _waiting
    queued_at = time.perf_counter()
    with _lock:
        queue_before = {"max_concurrent": MAX_CONCURRENT, "active": _active, "waiting": _waiting}
        _waiting += 1
    _slots.acquire()
    with _lock:
        _waiting -= 1
        _active += 1
        queue_at_start = {"max_concurrent": MAX_CONCURRENT, "active": _active, "waiting": _waiting}
    meta = {
        "wait_seconds": round(time.perf_counter() - queued_at, 4),
        "queue_before": queue_before,
        "queue_at_start": queue_at_start,
    }
    try:
        yield meta
    finally:
        with _lock:
            _active -= 1
        _slots.release()


def status() -> dict:
    """Estado de la cola: cuántos corriendo y cuántos esperando."""
    with _lock:
        return {"max_concurrent": MAX_CONCURRENT, "active": _active, "waiting": _waiting}
