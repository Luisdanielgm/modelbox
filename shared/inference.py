"""Control de concurrencia para inferencia.

En CPU, una sola inferencia ya usa todos los cores: correr varias a la vez no
da más throughput, solo las hace más lentas. Por eso serializamos con un
semáforo global (límite configurable) y los pedidos extra esperan en cola.

Lo usan TANTO el panel como la API, así comparten el mismo límite real.
"""
import os
import threading
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
    with _lock:
        _waiting += 1
    _slots.acquire()
    with _lock:
        _waiting -= 1
        _active += 1
    try:
        yield
    finally:
        with _lock:
            _active -= 1
        _slots.release()


def status() -> dict:
    """Estado de la cola: cuántos corriendo y cuántos esperando."""
    with _lock:
        return {"max_concurrent": MAX_CONCURRENT, "active": _active, "waiting": _waiting}
