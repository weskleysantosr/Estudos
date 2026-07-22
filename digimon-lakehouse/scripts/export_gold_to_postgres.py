"""Exporta as tabelas Gold do Databricks para o Postgres de serving.

Por que existe: a API não consulta o Databricks diretamente. Um SQL Warehouse
serverless que ficou ocioso "acorda" em alguns segundos (cold start) — uma API
pública que promete latência baixa não pode depender disso a cada request.
Este job roda periodicamente (GitHub Actions), materializa o Gold num Postgres
sempre ativo, e a API só lê dali. É o padrão "reverse ETL" na prática.

Segurança:
- Usa exclusivamente PG_WRITER_DSN. A API usa PG_READER_DSN (outro usuário,
  sem permissão de escrita) — se a API tiver um bug, o pior caso é leitura.
- Nunca loga a DSN (pode conter senha) nem qualquer valor derivado dela.
- Todo dado vai por bind/adaptação de tipo do driver (COPY binário), nunca por
  f-string concatenada em SQL.

Performance:
- COPY binário em vez de INSERT linha a linha: para as ~5 tabelas do Gold
  (algumas centenas de linhas cada), a diferença é irrelevante aqui, mas é o
  padrão certo a aprender — em produção, com milhões de linhas, INSERT
  linha a linha não escala.
- Troca atômica (staging -> rename): a API nunca vê a tabela pela metade
  durante o refresh. TRUNCATE + INSERT direto na tabela "live" deixaria uma
  janela onde requests concorrentes veem menos linhas do que deveriam.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from databricks import sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("export_gold_to_postgres")

# (nome da tabela gold, DDL da tabela live no Postgres)
TABLES: list[tuple[str, str]] = [
    (
        "digimon_summary",
        """
        digimon_id BIGINT PRIMARY KEY,
        name TEXT NOT NULL,
        x_antibody BOOLEAN,
        release_date TEXT,
        image_url TEXT,
        levels TEXT[],
        types TEXT[],
        attributes TEXT[],
        fields TEXT[],
        next_evolution_count INT
        """,
    ),
    ("stats_by_level", "level_name TEXT PRIMARY KEY, digimon_count INT"),
    ("stats_by_type", "type_name TEXT PRIMARY KEY, digimon_count INT"),
    ("stats_by_attribute", "attribute_name TEXT PRIMARY KEY, digimon_count INT"),
    (
        "longest_evolution_chains",
        """
        root_digimon_name TEXT,
        leaf_digimon_name TEXT,
        depth INT,
        digimon_id_path BIGINT[]
        """,
    ),
]

# Colunas ARRAY por tabela. O databricks-sql-connector devolve ARRAY<...>
# como STRING no formato JSON (ex.: '["Adult"]'), não como lista Python — sem
# converter antes do COPY, o Postgres rejeita ("[" não é a sintaxe de array
# dele, que usa "{...}"). Ver `_coerce_row`.
ARRAY_COLUMNS: dict[str, set[str]] = {
    "digimon_summary": {"levels", "types", "attributes", "fields"},
    "longest_evolution_chains": {"digimon_id_path"},
}


def _coerce_row(row: tuple, columns: list[str], array_columns: set[str]) -> tuple:
    if not array_columns:
        return row
    coerced = list(row)
    for i, col in enumerate(columns):
        if col in array_columns and isinstance(coerced[i], str):
            coerced[i] = json.loads(coerced[i])
    return tuple(coerced)


def _fetch_from_databricks(table_name: str) -> tuple[list[str], list[tuple]]:
    with sql.connect(
        server_hostname=_require_env("DATABRICKS_HOST").replace("https://", ""),
        http_path=_require_env("DATABRICKS_HTTP_PATH"),
        access_token=_require_env("DATABRICKS_TOKEN"),
        catalog=os.environ.get("DATABRICKS_CATALOG", "digimon_lakehouse"),
        schema=os.environ.get("DATABRICKS_SCHEMA_GOLD", "gold"),
    ) as conn:
        with conn.cursor() as cursor:
            # Nome da tabela vem de uma constante interna (TABLES), nunca de
            # input externo — seguro concatenar aqui; dado em si sempre via bind.
            cursor.execute(f"SELECT * FROM {table_name}")  # noqa: S608
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
    return columns, rows


def _replace_table_atomically(
    pg_conn: psycopg.Connection,
    table_name: str,
    ddl: str,
    columns: list[str],
    rows: list[tuple],
    array_columns: set[str],
) -> None:
    staging = f"{table_name}_staging"
    backup = f"{table_name}_old"

    with pg_conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
        cur.execute(f"CREATE TABLE {staging} ({ddl})")

        col_list = ", ".join(columns)
        with cur.copy(f"COPY {staging} ({col_list}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(_coerce_row(row, columns, array_columns))

        # Nunca derruba a tabela "live" antes de ter a substituta pronta: se
        # algo falhar entre o rename e o commit, a versão anterior continua
        # existindo como `_old` em vez de ter sido destruída sem substituto.
        cur.execute(f"DROP TABLE IF EXISTS {backup}")
        cur.execute(f"ALTER TABLE IF EXISTS {table_name} RENAME TO {backup}")
        cur.execute(f"ALTER TABLE {staging} RENAME TO {table_name}")
        cur.execute(f"DROP TABLE IF EXISTS {backup}")
    # commit acontece no `with pg_conn` externo — troca é tudo-ou-nada.
    logger.info("Tabela %s atualizada com %d linhas.", table_name, len(rows))


def run() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    writer_dsn = _require_env("PG_WRITER_DSN")
    with psycopg.connect(writer_dsn) as pg_conn:
        for table_name, ddl in TABLES:
            columns, rows = _fetch_from_databricks(table_name)
            array_columns = ARRAY_COLUMNS.get(table_name, set())
            with pg_conn.transaction():
                _replace_table_atomically(pg_conn, table_name, ddl, columns, rows, array_columns)

    logger.info("Export concluído: %d tabelas atualizadas.", len(TABLES))
    return 0


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente {name} não configurada.")
    return value


if __name__ == "__main__":
    import sys

    sys.exit(run())
