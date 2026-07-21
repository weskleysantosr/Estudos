# Digimon Lakehouse

Projeto de estudo de engenharia de dados: pipeline 100% na nuvem, 100%
gratuito, ponta a ponta — de uma API pública até um site consumindo a própria
API criada aqui. Tema dos dados: [DAPI](https://digi-api.com), API pública
sobre Digimon (níveis, tipos, atributos, cadeias de evolução).

## Arquitetura

```
DAPI (fonte pública)
   │  Python: paginação, retry/backoff, rate limit
   ▼
GitHub Actions (cron diário) ──┬─▶ Databricks
                                │     ├─ bronze.raw_digimon      (JSON bruto, MERGE idempotente)
                                │     ├─ silver.*                (dimensões + bridges + fact_evolution)
                                │     └─ gold.*                  (agregados + cadeia de evolução recursiva)
                                │
                                └─▶ Postgres free-tier (Neon/Supabase)
                                      via export com troca atômica (staging + rename)
                                            │
                                            ▼
                                  FastAPI (api/) — Render/Fly/HF Spaces
                                            │
                                            ▼
                                  Streamlit (site/) — Streamlit Community Cloud
```

GitHub Actions é o orquestrador: dispara a ingestão, chama o Job do Databricks
via REST API e espera terminar, depois exporta o Gold pro Postgres. Isso
existe porque o tier gratuito do Databricks pode não ter agendamento nativo
confiável — ver comentário em `databricks/bundle/resources/jobs.yml`.

## Estrutura

```
ingestion/    → script Python: DAPI -> Bronze
databricks/
  notebooks/  → 00 setup, 01 silver, 02 gold (notebooks versionados, formato "Databricks notebook source")
  bundle/     → Databricks Asset Bundle (infra do pipeline como código)
scripts/      → export Gold -> Postgres, trigger+wait do job Databricks
api/          → FastAPI (lê do Postgres, serve a "sua API")
site/         → Streamlit (consome a FastAPI)
.github/      → workflows (ci, ingest) + dependabot
```

## Pré-requisitos

- Conta Databricks (Free/Community Edition) — ✅ você já tem
- Conta GitHub — ✅ você já tem
- Um Postgres gratuito: [Neon](https://neon.tech) ou [Supabase](https://supabase.com) (free tier, always-on)
- Uma conta em [Render](https://render.com) (ou Fly.io / Hugging Face Spaces) para a API
- Conta em [Streamlit Community Cloud](https://streamlit.io/cloud) para o site

## Setup

### 1. Databricks

1. Compute > crie (ou reaproveite) um cluster all-purpose single-node. Copie o **Cluster ID**.
2. Compute > SQL Warehouses > crie um warehouse serverless pequeno. Copie o **HTTP Path**.
3. User Settings > Developer > Access Tokens > gere um **PAT** com prazo de expiração curto (rotacione periodicamente).
4. Rode `databricks/notebooks/00_setup_schemas.py` uma vez (importe a pasta `databricks/notebooks` como Databricks Repo, ou cole o conteúdo num notebook novo) para criar catalog/schemas.

### 2. Databricks Asset Bundle (job silver→gold)

```bash
# `pip install databricks-cli` instala a CLI antiga (não tem `bundle`) — a
# certa é esta, distribuída como binário:
winget install Databricks.DatabricksCLI          # Windows
# curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh   # macOS/Linux

databricks auth login --host <seu-workspace>     # abre o navegador, sem token pra copiar/colar
cd databricks/bundle
# Edite resources/jobs.yml: troque REPLACE_COM_SEU_CLUSTER_ID pelo Cluster ID do passo 1
databricks bundle validate
databricks bundle deploy -t dev
```

Depois do deploy, anote o **Job ID** (Workflows na UI do Databricks, ou saída do `bundle deploy`) — vai virar o secret `DATABRICKS_JOB_ID`.

### 3. Postgres (Neon/Supabase) — dois usuários, least privilege

```sql
-- Rode como owner/admin do banco
CREATE ROLE writer_user LOGIN PASSWORD 'defina-uma-senha-forte';
CREATE ROLE reader_user LOGIN PASSWORD 'defina-outra-senha-forte';

GRANT CREATE, USAGE ON SCHEMA public TO writer_user;
GRANT CONNECT ON DATABASE <seu_banco> TO reader_user;
GRANT USAGE ON SCHEMA public TO reader_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader_user;
```

```sql
-- Conecte COMO writer_user e rode isto (o export recria tabelas via
-- staging+rename a cada run — sem isto, reader_user perderia o SELECT
-- toda vez que uma tabela é recriada):
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO reader_user;
```

`writer_user` vira `PG_WRITER_DSN` (só o job de export usa), `reader_user` vira `PG_READER_DSN` (só a API usa).

### 4. GitHub Secrets

Settings > Secrets and variables > Actions, adicione:

| Secret | De onde vem |
|---|---|
| `DATABRICKS_HOST` | URL do workspace |
| `DATABRICKS_TOKEN` | PAT do passo 1 |
| `DATABRICKS_HTTP_PATH` | HTTP Path do SQL Warehouse |
| `DATABRICKS_JOB_ID` | Job ID do passo 2 |
| `PG_WRITER_DSN` | DSN do `writer_user` |

### 5. Deploy da API (Render — exemplo)

New Web Service > conectar este repo > **Root Directory**: `digimon-lakehouse/api` > runtime **Docker**. Nas env vars do serviço, configure `PG_READER_DSN`, `API_CORS_ORIGINS` (domínio do Streamlit, depois de publicá-lo) e `API_RATE_LIMIT_PER_MINUTE`. Deploy automático a cada push em `main` — não precisa de workflow de CI/CD adicional, é nativo do Render.

### 6. Deploy do site (Streamlit Community Cloud)

New app > conectar este repo > **Main file path**: `site/streamlit_app.py`. Em *Secrets*, adicione `SITE_API_BASE_URL = "https://<sua-api>.onrender.com"`. Também redeploya automático a cada push.

## Rodando localmente

```bash
cp .env.example .env   # preencha com os valores reais

cd ingestion  && pip install -r requirements.txt && python extract_digimon.py
cd ../scripts && pip install -r requirements.txt && python export_gold_to_postgres.py
cd ../api      && pip install -r requirements-dev.txt && uvicorn app.main:app --reload
cd ../site     && pip install -r requirements.txt && streamlit run streamlit_app.py
```

## Segurança — o que já está aplicado

- Segredos só via `.env`/GitHub Secrets, nunca hardcoded (`.env` no `.gitignore`, `.env.example` só com placeholders).
- Postgres com dois usuários (writer/reader) — a API nunca tem permissão de escrita.
- Todo SQL usa bind de parâmetros (ingestão, export, API) — nenhuma f-string com valor de usuário vira texto SQL.
- API: CORS restrito a origens explícitas, rate limit por IP, validação de input (regex nos filtros, teto de paginação), handler de exceção que nunca vaza stack trace, headers de segurança (`X-Content-Type-Options`, `X-Frame-Options`).
- Docker da API roda como usuário não-root, imagem multi-stage enxuta.
- GitHub Actions: `permissions: contents: read` mínimo, secrets nunca expostos a `pull_request` de fork, `concurrency` evita duas execuções do pipeline se pisarem.
- Dependabot (`pip`, `docker`, `github-actions`) atualizando dependências vulneráveis automaticamente.
- Ruff com regras de segurança (`S`, flake8-bandit) no CI.

## Performance — o que já está aplicado

- Ingestão: sessão HTTP reaproveitada, retry com backoff exponencial só em erro transitório, rate limit pra não sobrecarregar a API pública, timeout em toda chamada.
- Bronze: `MERGE` em lote (staging + upsert) em vez de linha a linha.
- API: pool de conexões assíncrono (tamanho pequeno, calibrado pro limite do Postgres free-tier), cache TTL em memória (dados mudam no máximo 1x/dia), GZip nas respostas, paginação com teto.
- Export Gold→Postgres: `COPY` em vez de `INSERT` linha a linha, troca atômica (staging+rename) — a API nunca vê tabela pela metade.
- Site: `st.cache_data` em toda chamada à API, evitando refetch a cada interação.

## Limitações conhecidas (tier gratuito)

- Databricks Free/Community Edition: compute suspende por inatividade; confirme se Jobs/Workflows está disponível no seu workspace (por isso o GitHub Actions cobre o agendamento).
- Render/Fly/Streamlit Cloud free: "dormem" após inatividade — primeira request após um tempo é mais lenta (cold start), comportamento esperado.
- Postgres free (Neon/Supabase): limite de conexões simultâneas — por isso o pool da API é pequeno de propósito.

## Roteiro de estudo

- [ ] Fundamentos Databricks (clusters, notebooks, Delta Lake, Unity Catalog)
- [ ] Ingestão (paginação, idempotência, resiliência a falhas de API externa)
- [ ] Modelagem silver (schema explícito, qualidade de dados)
- [ ] Modelagem gold (agregações, CTE recursiva para grafos/hierarquias)
- [ ] Orquestração (Databricks Jobs + GitHub Actions, Databricks Asset Bundles como IaC)
- [ ] API própria (segurança, performance, deploy)
- [ ] Site consumindo a API própria
- [ ] Observabilidade básica (logs estruturados, status de pipeline)
