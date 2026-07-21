"""Peças de segurança da API: rate limiting e headers HTTP defensivos.

Rate limit por IP (slowapi/`limits`, em memória): a API é pública e gratuita
— sem isso, um cliente mal-comportado (ou um teste em loop de outro
desenvolvedor) consegue esgotar o pool de conexões do Postgres (só 5 conexões
livres, ver db.py) para todo mundo. Em memória é suficiente para uma
instância única (tier gratuito); múltiplas instâncias precisariam de um
backend compartilhado (Redis) — não é o caso aqui.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{get_settings().api_rate_limit_per_minute}/minute"])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # API é só leitura de dado público (Digimon) — não há cookie/sessão,
        # então não há CSRF a mitigar aqui além dos headers acima.
        return response
