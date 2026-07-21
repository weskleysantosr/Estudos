"""Dispara o Databricks Job (silver+gold) via REST API e espera terminar.

Existe porque o GitHub Actions é o orquestrador deste projeto (ver comentário
em databricks/bundle/resources/jobs.yml) — depois que a ingestão grava a
Bronze, alguém precisa mandar o Databricks processar silver/gold, e o export
pro Postgres só pode rodar DEPOIS que isso terminar com sucesso. Sem esperar
o job, o export leria um Gold com dados do dia anterior ou pela metade.

Segurança: token só via env var (GH secret), nunca logado — só o run_id e o
estado da run aparecem no log.
Robustez: timeout total (MAX_WAIT_SECONDS) para nunca ficar preso num loop
infinito consumindo minutos do GitHub Actions se o job travar.
"""

from __future__ import annotations

import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("run_databricks_job_and_wait")

POLL_INTERVAL_SECONDS = 15
MAX_WAIT_SECONDS = 20 * 60  # 20 min — dataset é pequeno, job não deveria passar disso
REQUEST_TIMEOUT = 15


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente {name} não configurada.")
    return value


def run() -> int:
    host = _require_env("DATABRICKS_HOST").rstrip("/")
    token = _require_env("DATABRICKS_TOKEN")
    job_id = _require_env("DATABRICKS_JOB_ID")
    headers = {"Authorization": f"Bearer {token}"}

    trigger = requests.post(
        f"{host}/api/2.1/jobs/run-now",
        headers=headers,
        json={"job_id": int(job_id)},
        timeout=REQUEST_TIMEOUT,
    )
    trigger.raise_for_status()
    run_id = trigger.json()["run_id"]
    logger.info("Job disparado: run_id=%s", run_id)

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        status = requests.get(
            f"{host}/api/2.1/jobs/runs/get",
            headers=headers,
            params={"run_id": run_id},
            timeout=REQUEST_TIMEOUT,
        )
        status.raise_for_status()
        state = status.json().get("state", {})
        life_cycle_state = state.get("life_cycle_state")
        logger.info("run_id=%s life_cycle_state=%s", run_id, life_cycle_state)

        if life_cycle_state in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            result_state = state.get("result_state")
            if result_state == "SUCCESS":
                logger.info("Job concluído com sucesso.")
                return 0
            logger.error("Job terminou sem sucesso: result_state=%s state_message=%s", result_state, state.get("state_message"))
            return 1

        time.sleep(POLL_INTERVAL_SECONDS)

    logger.error("Timeout de %ds esperando o job terminar (run_id=%s).", MAX_WAIT_SECONDS, run_id)
    return 1


if __name__ == "__main__":
    sys.exit(run())
