"""Foco dos testes: as travas de segurança/robustez, não a lógica de negócio
(que é trivial). Nenhum teste aqui toca um Postgres real — conexão é sempre
uma fake em memória, o que também mantém a suíte rápida o bastante pro CI.
"""

from collections import namedtuple
from contextlib import asynccontextmanager
from unittest.mock import patch

Column = namedtuple("Column", ["name"])


def _fake_get_connection(row: tuple, columns: list[str]):
    description = [Column(name=c) for c in columns]

    class FakeCursor:
        def __init__(self):
            self.description = description

        async def execute(self, *_args, **_kwargs):
            return None

        async def fetchone(self):
            return row

        async def fetchall(self):
            return [row] if row else []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    @asynccontextmanager
    async def _fake():
        yield FakeConnection()

    return _fake


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_digimons_rejects_sqli_shaped_filter(client):
    # O padrão de validação do query param deve barrar isto no schema, antes
    # de qualquer SQL ser montado — nunca deveria virar uma query de verdade.
    response = client.get("/digimons", params={"level": "Baby'; DROP TABLE digimon_summary; --"})
    assert response.status_code == 422


def test_list_digimons_rejects_limit_above_max(client):
    response = client.get("/digimons", params={"limit": 1000})
    assert response.status_code == 422


def test_get_digimon_not_found(client):
    fake_conn = _fake_get_connection(row=None, columns=[])
    with patch("app.routers.digimons.get_connection", fake_conn):
        response = client.get("/digimons/999999")
    assert response.status_code == 404


def test_get_digimon_returns_data(client):
    columns = [
        "digimon_id", "name", "x_antibody", "release_date", "image_url",
        "levels", "types", "attributes", "fields", "next_evolution_count",
    ]
    row = (
        1, "Agumon", False, "1997-03-26", "https://example.com/agumon.png",
        ["Child"], ["Vaccine"], ["Fire"], ["Nature Spirits"], 1,
    )
    fake_conn = _fake_get_connection(row=row, columns=columns)
    with patch("app.routers.digimons.get_connection", fake_conn):
        response = client.get("/digimons/1")
    assert response.status_code == 200
    assert response.json()["name"] == "Agumon"
