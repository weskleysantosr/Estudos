"""Cache TTL em memória para endpoints de leitura pesada.

Os dados no Postgres de serving só mudam quando o export do Gold roda (no
máximo 1x/dia — ver scripts/export_gold_to_postgres.py). Não há motivo para
bater no banco a cada request; um TTL curto (default 5 min, configurável)
já elimina a maior parte da carga sem servir dado velho por muito tempo.

Limitação assumida conscientemente: cache em memória de processo único não é
compartilhado entre instâncias. Para o tier gratuito (uma instância só, ver
Dockerfile/deploy), isso é suficiente — se o projeto crescer para múltiplas
instâncias, o próximo passo seria um cache externo (Redis), não mais isto.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from cachetools import TTLCache

from app.config import get_settings

_cache: TTLCache = TTLCache(maxsize=256, ttl=get_settings().api_cache_ttl_seconds or 1)


async def cached[T](key: str, loader: Callable[[], Awaitable[T]]) -> T:
    if key in _cache:
        return _cache[key]
    value = await loader()
    _cache[key] = value
    return value
