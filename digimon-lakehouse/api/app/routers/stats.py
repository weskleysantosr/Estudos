from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.cache import cached
from app.db import get_connection
from app.models import EvolutionChain, StatItem
from app.security import limiter

router = APIRouter(prefix="/stats", tags=["stats"])


async def _load_stat(table: str, label_column: str) -> list[StatItem]:
    # `table`/`label_column` são sempre literais fixos passados pelas funções
    # abaixo (nunca input do usuário) — seguro compor no texto do SQL aqui.
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {label_column}, digimon_count FROM {table} ORDER BY digimon_count DESC")
            rows = await cur.fetchall()
    return [StatItem(label=row[0], digimon_count=row[1]) for row in rows]


@router.get("/by-level", response_model=list[StatItem])
@limiter.limit("30/minute")
async def stats_by_level(request: Request) -> list[StatItem]:
    return await cached("stat:stats_by_level", lambda: _load_stat("stats_by_level", "level_name"))


@router.get("/by-type", response_model=list[StatItem])
@limiter.limit("30/minute")
async def stats_by_type(request: Request) -> list[StatItem]:
    return await cached("stat:stats_by_type", lambda: _load_stat("stats_by_type", "type_name"))


@router.get("/by-attribute", response_model=list[StatItem])
@limiter.limit("30/minute")
async def stats_by_attribute(request: Request) -> list[StatItem]:
    return await cached("stat:stats_by_attribute", lambda: _load_stat("stats_by_attribute", "attribute_name"))


@router.get("/longest-evolution-chains", response_model=list[EvolutionChain])
@limiter.limit("30/minute")
async def longest_evolution_chains(request: Request, limit: int = Query(default=10, ge=1, le=50)) -> list[EvolutionChain]:
    async def _load() -> list[EvolutionChain]:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT root_digimon_name, leaf_digimon_name, depth, digimon_id_path
                    FROM longest_evolution_chains
                    ORDER BY depth DESC
                    LIMIT %s
                    """,
                    [limit],
                )
                rows = await cur.fetchall()
        return [
            EvolutionChain(root_digimon_name=r[0], leaf_digimon_name=r[1], depth=r[2], digimon_id_path=list(r[3]))
            for r in rows
        ]

    return await cached(f"chains:{limit}", _load)
