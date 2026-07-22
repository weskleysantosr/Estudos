"""Endpoints de leitura de digimons.

Toda query usa parâmetros (`%s` + tupla) — os valores de filtro (level/type/
attribute) vêm de query params do usuário e NUNCA são concatenados no texto
do SQL. `limit` tem teto rígido (100): sem isso, `?limit=999999999` deixa
qualquer cliente forçar a API a serializar e transferir a tabela inteira,
sem eu precisar de "injeção" nenhuma para causar dano.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.cache import cached
from app.db import get_connection
from app.models import DigimonEvolution, DigimonSummary, PaginatedDigimons
from app.security import limiter

router = APIRouter(prefix="/digimons", tags=["digimons"])

_ALLOWED_FILTER_PATTERN = r"^[A-Za-zÀ-ÿ0-9 \-]{1,50}$"
# Nomes de Digimon têm mais pontuação que os filtros de vocabulário fixo
# (level/type/attribute) — ex.: "Algomon (Baby I)". Ainda assim travamos o
# formato: a proteção real contra injeção é o bind de parâmetro no ILIKE
# abaixo, isto aqui é só higiene contra input claramente malformado.
_NAME_SEARCH_PATTERN = r"^[\w À-ÿ'.()\-]{1,50}$"


@router.get("", response_model=PaginatedDigimons)
@limiter.limit("30/minute")
async def list_digimons(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    level: str | None = Query(default=None, pattern=_ALLOWED_FILTER_PATTERN),
    type: str | None = Query(default=None, pattern=_ALLOWED_FILTER_PATTERN),
    attribute: str | None = Query(default=None, pattern=_ALLOWED_FILTER_PATTERN),
    name: str | None = Query(default=None, pattern=_NAME_SEARCH_PATTERN),
) -> PaginatedDigimons:
    cache_key = f"list:{limit}:{offset}:{level}:{type}:{attribute}:{name}"

    async def _load() -> PaginatedDigimons:
        conditions = []
        params: list = []
        if level:
            conditions.append("%s = ANY(levels)")
            params.append(level)
        if type:
            conditions.append("%s = ANY(types)")
            params.append(type)
        if attribute:
            conditions.append("%s = ANY(attributes)")
            params.append(attribute)
        if name:
            conditions.append("name ILIKE %s")
            params.append(f"%{name}%")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # noqa: S608 — where_clause só concatena os literais fixos
                # "%s = ANY(...)" declarados acima; nenhum valor de usuário
                # entra no texto do SQL, só via `params`/`execute(..., params)`.
                await cur.execute(f"SELECT count(*) FROM digimon_summary {where_clause}", params)  # noqa: S608
                (total,) = await cur.fetchone()

                await cur.execute(
                    f"""
                    SELECT digimon_id, name, x_antibody, release_date, image_url,
                           levels, types, attributes, fields, next_evolution_count
                    FROM digimon_summary
                    {where_clause}
                    ORDER BY digimon_id
                    LIMIT %s OFFSET %s
                    """,  # noqa: S608
                    [*params, limit, offset],
                )
                columns = [d.name for d in cur.description]
                rows = await cur.fetchall()

        items = [DigimonSummary(**dict(zip(columns, row, strict=True))) for row in rows]
        return PaginatedDigimons(total=total, limit=limit, offset=offset, items=items)

    return await cached(cache_key, _load)


@router.get("/{digimon_id}", response_model=DigimonSummary)
@limiter.limit("60/minute")
async def get_digimon(request: Request, digimon_id: int) -> DigimonSummary:
    cache_key = f"detail:{digimon_id}"

    async def _load() -> DigimonSummary:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT digimon_id, name, x_antibody, release_date, image_url,
                           levels, types, attributes, fields, next_evolution_count
                    FROM digimon_summary
                    WHERE digimon_id = %s
                    """,
                    [digimon_id],
                )
                row = await cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Digimon não encontrado")
                columns = [d.name for d in cur.description]
        return DigimonSummary(**dict(zip(columns, row, strict=True)))

    return await cached(cache_key, _load)


@router.get("/{digimon_id}/evolutions", response_model=list[DigimonEvolution])
@limiter.limit("60/minute")
async def get_digimon_evolutions(request: Request, digimon_id: int) -> list[DigimonEvolution]:
    cache_key = f"evolutions:{digimon_id}"

    async def _load() -> list[DigimonEvolution]:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT direction, related_digimon_id, related_digimon_name,
                           related_digimon_image_url, condition, related_digimon_levels
                    FROM digimon_evolutions
                    WHERE digimon_id = %s
                    ORDER BY direction, related_digimon_name
                    """,
                    [digimon_id],
                )
                columns = [d.name for d in cur.description]
                rows = await cur.fetchall()
        return [DigimonEvolution(**dict(zip(columns, row, strict=True))) for row in rows]

    return await cached(cache_key, _load)
