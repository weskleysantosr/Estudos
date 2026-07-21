"""Configuração via variáveis de ambiente — nunca valores hardcoded no código.

`extra="forbid"` faz o app falhar já no boot se alguém definir uma env var
com nome parecido mas errado (ex.: `PG_READR_DSN`), em vez de silenciosamente
cair no default e só descobrir em produção que a conexão estava errada.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pg_reader_dsn: str = Field(..., description="DSN de um usuário Postgres SOMENTE LEITURA")
    api_cors_origins: str = Field(default="http://localhost:8501")
    api_rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)
    api_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
