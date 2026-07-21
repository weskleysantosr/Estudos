"""Job de ingestão: DAPI -> Bronze (Databricks Delta).

Uso local:
    cp ../.env.example ../.env   # preencher valores
    python extract_digimon.py

Em CI (GitHub Actions), as mesmas variáveis vêm de secrets do repositório —
load_dotenv() é no-op silencioso se não houver .env, então o mesmo script
roda igual nos dois ambientes sem branch de código.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from databricks_writer import DatabricksBronzeWriter
from digi_api_client import DigiApiClient, DigiApiError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("extract_digimon")

# Falha tolerável: se poucos digimons derem erro pontual na API, seguimos em
# frente (a própria API pode ter um registro inconsistente). Acima do limite,
# tratamos como sinal de problema sistêmico e falhamos o job (exit code != 0),
# para o GitHub Actions marcar a run como falha em vez de "sucesso" mentiroso.
MAX_TOLERATED_FAILURES_RATIO = 0.05
WRITE_BATCH_SIZE = 100


def run() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    total = 0
    failures = 0
    batch: list[dict] = []

    with DigiApiClient() as client, DatabricksBronzeWriter() as writer:
        writer.ensure_tables()

        for ref in client.iter_digimon_refs():
            total += 1
            try:
                detail = client.get_digimon_detail(ref.id)
            except DigiApiError as exc:
                failures += 1
                logger.warning("Falha ao buscar digimon id=%s (%s): %s", ref.id, ref.name, exc)
                continue

            batch.append({"digimon_id": detail["id"], "raw_json": json.dumps(detail, ensure_ascii=False)})
            if len(batch) >= WRITE_BATCH_SIZE:
                writer.write_batch(batch)
                batch.clear()

        if batch:
            writer.write_batch(batch)

    failure_ratio = failures / total if total else 0
    logger.info("Ingestão concluída: %d processados, %d falhas (%.1f%%).", total, failures, failure_ratio * 100)

    if total == 0:
        logger.error("Nenhum digimon retornado pela API — abortando como falha.")
        return 1
    if failure_ratio > MAX_TOLERATED_FAILURES_RATIO:
        logger.error("Taxa de falha acima do tolerado (%.1f%%) — marcando job como falho.", failure_ratio * 100)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
