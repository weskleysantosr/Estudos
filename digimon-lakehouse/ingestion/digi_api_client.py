"""Cliente HTTP para a DAPI (https://digi-api.com).

Decisões de segurança/performance:
- `requests.Session` reaproveitada: evita reabrir conexão TCP/TLS a cada chamada.
- Retry com backoff exponencial só em erros transitórios (5xx, timeout, conexão) —
  nunca em 4xx, que indica erro do cliente e repetir não resolve.
- Timeout explícito em toda chamada: sem isso, uma API pública lenta/instável
  pode travar o pipeline indefinidamente.
- Rate limit (delay mínimo entre requests) para não sobrecarregar uma API
  pública gratuita e de terceiros — sem isso corremos risco de bloqueio de IP.
- Validação mínima de schema na resposta antes de repassar adiante: uma API de
  terceiros pode mudar/retornar payload inesperado sem aviso.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://digi-api.com/api/v1"
DEFAULT_TIMEOUT = 10  # segundos — evita conexões penduradas indefinidamente
DEFAULT_PAGE_SIZE = 100
MIN_INTERVAL_BETWEEN_CALLS = 0.3  # segundos — respeita o serviço de terceiros gratuito

REQUIRED_DETAIL_FIELDS = {"id", "name", "levels", "types", "attributes", "fields"}


class DigiApiError(RuntimeError):
    """Erro ao consumir a DAPI (payload inválido ou falha persistente)."""


@dataclass
class DigimonRef:
    id: int
    name: str
    href: str


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.5,  # 1.5s, 3s, 6s, 12s, 24s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "digimon-lakehouse-study-project/1.0"})
    return session


class DigiApiClient:
    def __init__(self, base_url: str = BASE_URL, min_interval: float = MIN_INTERVAL_BETWEEN_CALLS):
        self._base_url = base_url
        self._min_interval = min_interval
        self._session = _build_session()
        self._last_call_ts = 0.0

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "DigiApiClient":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    def _throttled_get(self, path: str, params: dict | None = None) -> dict:
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        url = f"{self._base_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise DigiApiError(f"Falha de rede ao chamar {url}: {exc}") from exc
        finally:
            self._last_call_ts = time.monotonic()

        if response.status_code >= 400:
            raise DigiApiError(f"DAPI retornou {response.status_code} para {url}: {response.text[:200]}")

        try:
            return response.json()
        except ValueError as exc:
            raise DigiApiError(f"Resposta não-JSON de {url}") from exc

    def iter_digimon_refs(self, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[DigimonRef]:
        """Percorre a listagem paginada de digimons (id/name/href apenas)."""
        page = 0
        while True:
            payload = self._throttled_get("/digimon", params={"page": page, "pageSize": page_size})
            content = payload.get("content", [])
            if not content:
                break
            for item in content:
                yield DigimonRef(id=item["id"], name=item["name"], href=item["href"])

            pageable = payload.get("pageable", {})
            if page >= pageable.get("totalPages", 1) - 1:
                break
            page += 1

    def get_digimon_detail(self, digimon_id: int) -> dict:
        """Busca o detalhe completo de um digimon e valida o shape mínimo esperado."""
        detail = self._throttled_get(f"/digimon/{digimon_id}")
        missing = REQUIRED_DETAIL_FIELDS - detail.keys()
        if missing:
            raise DigiApiError(
                f"Digimon {digimon_id}: payload sem campos esperados {missing} — "
                "a API pode ter mudado o contrato."
            )
        return detail
