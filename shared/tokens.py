"""Tokens de API y el cliente asociado a cada uno.

Retrocompatible: el `API_TOKEN` único de siempre sigue siendo válido y se atribuye
al cliente `MODELBOX_DEFAULT_CLIENT` (default "default"). Opcionalmente se pueden
declarar varios tokens con nombre de cliente vía `MODELBOX_TOKENS`, con formato
`cliente:token` separado por comas, por ejemplo:

    MODELBOX_TOKENS="cauce:abc123,sapiens:def456"

Los tokens son secretos: se pasan por entorno/Dokploy, nunca se hardcodean.
Nota: un valor de token no puede contener comas (separan pares) ni empezar el par
con dos puntos; los espacios alrededor de cliente/token en `MODELBOX_TOKENS` se
recortan.
"""
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CLIENT = os.environ.get("MODELBOX_DEFAULT_CLIENT", "default")


def _parse() -> dict[str, str]:
    """Devuelve un mapa token -> cliente a partir del entorno."""
    tokens: dict[str, str] = {}
    single = os.environ.get("API_TOKEN")
    if single:
        tokens[single] = DEFAULT_CLIENT
    for pair in os.environ.get("MODELBOX_TOKENS", "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        client, _, value = pair.partition(":")
        client, value = client.strip(), value.strip()
        if client and value:
            if value in tokens and tokens[value] != client:
                logger.warning("Token duplicado: se reasigna del cliente %r a %r.",
                               tokens[value], client)
            tokens[value] = client
    return tokens


TOKENS = _parse()                      # token -> cliente
CLIENTS = sorted(set(TOKENS.values()))  # nombres de cliente conocidos

# El API_TOKEN único actúa como operador/admin: puede ver el uso de todos los
# clientes. Los tokens con nombre (MODELBOX_TOKENS) solo ven su propio uso.
ADMIN_CLIENT = DEFAULT_CLIENT if os.environ.get("API_TOKEN") else None


def has_tokens() -> bool:
    return bool(TOKENS)
