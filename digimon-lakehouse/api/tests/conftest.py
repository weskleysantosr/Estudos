"""Env vars precisam existir ANTES de `app.main` ser importado (Settings() é
avaliado no import do módulo). Setadas aqui, no topo do conftest, valores
fake nunca usados de verdade — init_pool/close_pool são mockados abaixo, então
nenhuma conexão real é aberta durante os testes.
"""

import os

os.environ.setdefault("PG_READER_DSN", "postgresql://test:test@localhost:5432/test")

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("app.main.init_pool", new=AsyncMock()), patch("app.main.close_pool", new=AsyncMock()):
        from app.main import app

        with TestClient(app) as test_client:
            yield test_client
