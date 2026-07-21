"""Escrita idempotente do payload bruto da DAPI na camada Bronze (Delta).

Decisões de segurança/performance:
- Credenciais só via variáveis de ambiente (nunca hardcoded) — ver .env.example.
- Uma única conexão reaproveitada para todo o run (não abre/fecha conexão por
  digimon) — conexão HTTP/Thrift do Databricks SQL Warehouse tem custo de
  handshake não-trivial.
- Todo SQL usa parâmetros nomeados (`%(nome)s` + dict), nunca f-string/format
  direto no corpo do SQL — evita injeção mesmo vindo de uma API "confiável"
  (descrições/nomes são texto livre e podem conter aspas).
- Staging + MERGE (upsert por `digimon_id`) em vez de INSERT puro: reprocessar
  o mesmo dia não duplica linhas — pipeline idempotente por design.
- Para volumes bem maiores que este dataset (algumas centenas de registros),
  o padrão de mercado é escrever arquivos num Volume + `COPY INTO` (bulk
  loader) em vez de INSERT linha a linha via conector SQL. Aqui o dataset é
  pequeno o suficiente para `executemany` + MERGE ser simples e suficiente;
  fica registrado o tradeoff para quando o projeto crescer.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from databricks import sql

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


class DatabricksBronzeWriter:
    def __init__(
        self,
        server_hostname: str | None = None,
        http_path: str | None = None,
        access_token: str | None = None,
        catalog: str | None = None,
        schema: str | None = None,
    ):
        # Falha rápido e claro se algum segredo não foi configurado, em vez de
        # deixar o connector estourar um erro genérico de autenticação.
        self._server_hostname = _require_env("DATABRICKS_HOST", server_hostname).replace("https://", "")
        self._http_path = _require_env("DATABRICKS_HTTP_PATH", http_path)
        self._access_token = _require_env("DATABRICKS_TOKEN", access_token)
        self._catalog = catalog or os.environ.get("DATABRICKS_CATALOG", "digimon_lakehouse")
        self._schema = schema or os.environ.get("DATABRICKS_SCHEMA_BRONZE", "bronze")
        self._connection = None

    def __enter__(self) -> DatabricksBronzeWriter:
        self._connection = sql.connect(
            server_hostname=self._server_hostname,
            http_path=self._http_path,
            access_token=self._access_token,
            catalog=self._catalog,
            schema=self._schema,
        )
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        if self._connection is not None:
            self._connection.close()

    def ensure_tables(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_digimon (
                    digimon_id  BIGINT NOT NULL,
                    raw_json    STRING NOT NULL,
                    ingested_at TIMESTAMP NOT NULL
                ) USING DELTA
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_digimon_staging (
                    digimon_id  BIGINT NOT NULL,
                    raw_json    STRING NOT NULL,
                    ingested_at TIMESTAMP NOT NULL
                ) USING DELTA
                """
            )

    def write_batch(self, records: list[dict]) -> int:
        """Recebe uma lista de {"digimon_id", "raw_json"} e faz upsert idempotente."""
        if not records:
            return 0

        now = datetime.now(UTC)
        rows = [
            {"digimon_id": r["digimon_id"], "raw_json": r["raw_json"], "ingested_at": now}
            for r in records
        ]

        with self._connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE raw_digimon_staging")

            for i in range(0, len(rows), BATCH_SIZE):
                chunk = rows[i : i + BATCH_SIZE]
                cursor.executemany(
                    """
                    INSERT INTO raw_digimon_staging (digimon_id, raw_json, ingested_at)
                    VALUES (%(digimon_id)s, %(raw_json)s, %(ingested_at)s)
                    """,
                    chunk,
                )

            cursor.execute(
                """
                MERGE INTO raw_digimon AS target
                USING raw_digimon_staging AS source
                ON target.digimon_id = source.digimon_id
                WHEN MATCHED THEN UPDATE SET
                    target.raw_json = source.raw_json,
                    target.ingested_at = source.ingested_at
                WHEN NOT MATCHED THEN INSERT (digimon_id, raw_json, ingested_at)
                    VALUES (source.digimon_id, source.raw_json, source.ingested_at)
                """
            )
            cursor.execute("TRUNCATE TABLE raw_digimon_staging")

        logger.info("Upsert concluído: %d registros processados na Bronze.", len(rows))
        return len(rows)


def _require_env(name: str, override: str | None) -> str:
    value = override or os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variável de ambiente {name} não configurada. Copie .env.example para .env e preencha."
        )
    return value
