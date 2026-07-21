"""API pública de leitura sobre o Gold do lakehouse de Digimon.

Camadas de segurança aplicadas aqui (defesa em profundidade — nenhuma sozinha
é suficiente):
1. CORS restrito às origens configuradas (nunca "*"), só métodos GET.
2. Rate limit por IP (slowapi) em cada rota.
3. Conexão ao Postgres com um usuário só-leitura (ver db.py/config.py).
4. Handler de exceção genérico: nunca vaza stack trace/detalhe interno pro
   cliente — o traceback completo vai só pro log do servidor.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.db import close_pool, init_pool
from app.routers import digimons, stats
from app.security import SecurityHeadersMiddleware, limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("digimon_api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    logger.info("Pool de conexão Postgres inicializado.")
    yield
    await close_pool()


settings = get_settings()

app = FastAPI(
    title="Digimon Lakehouse API",
    description="API de leitura sobre dados de Digimon processados via Databricks (projeto de estudo).",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno — já registrado para investigação."},
    )


@app.get("/healthz", tags=["health"])
async def healthz() -> dict:
    return {"status": "ok"}


app.include_router(digimons.router)
app.include_router(stats.router)
