"""Pool de conexões assíncrono com o Postgres de serving.

Performance: pool pequeno (min=1, max=5) de propósito — planos free-tier de
Neon/Supabase costumam limitar o total de conexões simultâneas do banco
inteiro; um pool generoso aqui rouba conexões de qualquer outra coisa que use
o mesmo banco. `timeout` limita quanto uma request espera por uma conexão
livre antes de falhar rápido (503) em vez de empilhar requests indefinidamente
sob carga.

Segurança: conecta com PG_READER_DSN — um usuário Postgres que só tem SELECT
nas tabelas do Gold. Mesmo que uma query aqui tivesse um bug de injeção, o
usuário do banco não tem permissão de escrita/DDL para explorar.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

_pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    global _pool
    settings = get_settings()
    _pool = AsyncConnectionPool(
        conninfo=settings.pg_reader_dsn,
        min_size=1,
        max_size=5,
        timeout=5,  # segundos esperando por uma conexão livre do pool
        kwargs={"connect_timeout": 5},  # segundos para o TCP connect inicial
        open=False,
    )
    await _pool.open(wait=True, timeout=10)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


@asynccontextmanager
async def get_connection():
    if _pool is None:
        raise RuntimeError("Pool de conexão não inicializado — init_pool() não foi chamado no startup.")
    async with _pool.connection() as conn:
        yield conn
