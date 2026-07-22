# 🎓 Guia Prático de Databricks — mexendo de verdade no projeto

Este é a "parte 3": o [README.md](README.md) explica **o que** o projeto é,
o [SETUP.md](SETUP.md) explica **como** rodar do zero, e este guia é sobre
**colocar a mão na massa** — alterar estrutura, criar tabela do zero,
entender o que o Databricks oferece além do que a gente já usou. Cada
exercício foi testado de verdade neste workspace antes de entrar aqui (nada
de "deveria funcionar").

Pré-requisito: ter completado o Passo 1-3 do [SETUP.md](SETUP.md) (CLI
autenticada, bundle deployado, pelo menos um run do job feito).

---

## 📖 Sumário

- [Orientação: onde as coisas ficam na UI](#orientação-onde-as-coisas-ficam-na-ui)
- [Exercício 1 — Navegar pelos dados sem medo](#exercício-1--navegar-pelos-dados-sem-medo)
- [Exercício 2 — Criar uma tabela do zero, na mão](#exercício-2--criar-uma-tabela-do-zero-na-mão)
- [Exercício 3 — Alterar uma tabela existente e ver o efeito em cascata](#exercício-3--alterar-uma-tabela-existente-e-ver-o-efeito-em-cascata)
- [Exercício 4 — Adicionar uma tarefa nova ao Job](#exercício-4--adicionar-uma-tarefa-nova-ao-job)
- [Exercício 5 — Time travel: voltar no tempo de uma tabela](#exercício-5--time-travel-voltar-no-tempo-de-uma-tabela)
- [Exercício 6 — Lineage: de onde cada tabela veio](#exercício-6--lineage-de-onde-cada-tabela-veio)
- [Exercício 7 — Quebrar de propósito e aprender a debugar](#exercício-7--quebrar-de-propósito-e-aprender-a-debugar)
- [Cardápio de coisas simples pra alterar e aprender](#cardápio-de-coisas-simples-pra-alterar-e-aprender)
- [O que o Databricks oferece que a gente ainda não usou](#o-que-o-databricks-oferece-que-a-gente-ainda-não-usou)

---

## Orientação: onde as coisas ficam na UI

No menu lateral do workspace:

- **Catalog** (ícone de banco de dados) — o "Data Explorer": navega
  `digimon_lakehouse` > `bronze`/`silver`/`gold` > tabela, vê schema, amostra
  de dados, histórico de versões e **lineage** (de onde a tabela veio), tudo
  sem escrever uma linha de SQL.
- **Workflows** — onde o Job `digimon_transform_pipeline` aparece, com o
  histórico de todos os runs (inclusive os que a gente quebrou nesta sessão).
- **SQL Editor** (ou um notebook novo com célula `%sql`) — pra rodar query
  solta, sem precisar mexer nos notebooks do projeto.
- **Compute** — não tem cluster clássico aqui (Free Edition é serverless),
  mas é onde ficaria se tivesse.

Regra de ouro pra explorar sem medo: **tudo que você faz num notebook/SQL
Editor separado (fora de `databricks/notebooks/`) não afeta o projeto** até
você decidir levar a mudança pra lá. Pode testar à vontade.

## Exercício 1 — Navegar pelos dados sem medo

1. Abre **Catalog** > `digimon_lakehouse` > `gold` > `digimon_summary`.
2. Aba **Sample Data**: veja as linhas de verdade sem escrever SQL.
3. Aba **Details**: repare que é `MANAGED` e formato `DELTA`.
4. Abre um **SQL Editor** novo e roda:
   ```sql
   SELECT levels, count(*) 
   FROM digimon_lakehouse.gold.digimon_summary 
   GROUP BY levels 
   ORDER BY 2 DESC 
   LIMIT 5;
   ```
   Isso não altera nada — é só pra pegar confiança de que consultar é seguro.

## Exercício 2 — Criar uma tabela do zero, na mão

Objetivo: uma tabela nova, sem tocar nos notebooks do projeto ainda.

1. Cria um **notebook novo** no seu workspace (fora de `databricks/notebooks/`
   — pode ser em `/Users/você/scratch`, por exemplo).
2. Anexa ao mesmo warehouse serverless de sempre.
3. Cola isto numa célula `%sql`:
   ```sql
   USE CATALOG digimon_lakehouse;

   CREATE TABLE IF NOT EXISTS gold.digimon_x_antibody_por_tipo AS
   SELECT
     typ.type_name,
     count(*) AS total_x_antibody
   FROM silver.dim_digimon d
   JOIN silver.bridge_digimon_type bt ON bt.digimon_id = d.digimon_id
   JOIN silver.dim_type typ ON typ.type_id = bt.type_id
   WHERE d.x_antibody = true
   GROUP BY typ.type_name
   ORDER BY total_x_antibody DESC;

   SELECT * FROM gold.digimon_x_antibody_por_tipo;
   ```
4. Roda. Isso cria uma tabela **de verdade** em `gold`, visível no Catalog
   Explorer, sem precisar de bundle deploy nem de mexer em código versionado.

Isso é o "criar tabela na mão" — o resto do projeto (bundle, notebooks
versionados) existe pra automatizar isto e deixar reprodutível, mas o
Databricks em si não exige nada disso pra você experimentar.

**Quer limpar depois?** `DROP TABLE gold.digimon_x_antibody_por_tipo;`

## Exercício 3 — Alterar uma tabela existente e ver o efeito em cascata

Objetivo: sentir a cadeia Databricks → export → Postgres → API → site.

1. Abre `databricks/notebooks/02_gold_aggregate.py` no seu editor local.
2. Na `CREATE OR REPLACE TABLE gold.digimon_summary AS SELECT ...`, adiciona
   uma coluna nova, por exemplo:
   ```sql
   CASE WHEN d.x_antibody THEN 'X-Antibody' ELSE 'Normal' END AS variante,
   ```
   (lembra da vírgula certa na lista de colunas)
3. `cd databricks && databricks bundle deploy -t dev`
4. `databricks jobs run-now <job-id>` (ou pelo Workflows na UI)
5. Depois do job terminar: `python scripts/export_gold_to_postgres.py`
6. Adiciona `variante: str | None` em `DigimonSummary` (`api/app/models.py`)
   e na lista de colunas do `SELECT` em `api/app/routers/digimons.py`.
7. Redeploy da API acontece sozinho no próximo `git push` (Render observa o
   branch). Localmente: `uvicorn app.main:app --reload` já reflete na hora.

Isso é o ciclo completo — uma coluna nova percorre exatamente os mesmos 5
saltos que qualquer mudança "de verdade" no projeto percorre.

## Exercício 4 — Adicionar uma tarefa nova ao Job

Objetivo: aprender a estrutura de `databricks/resources/jobs.yml` mexendo
numa tarefa nova, de baixo risco.

1. Cria um notebook simples em `databricks/notebooks/03_data_quality.py`:
   ```python
   # Databricks notebook source
   # MAGIC %sql
   # MAGIC SELECT count(*) AS digimons_sem_nivel
   # MAGIC FROM digimon_lakehouse.gold.digimon_summary
   # MAGIC WHERE size(levels) = 0;
   ```
2. Em `databricks/resources/jobs.yml`, adiciona uma quarta task:
   ```yaml
       - task_key: data_quality_check
         depends_on:
           - task_key: gold_aggregate
         notebook_task:
           notebook_path: ../notebooks/03_data_quality.py
   ```
3. `databricks bundle deploy -t dev` e roda o job — agora tem 4 tasks em vez
   de 3, e você viu a mecânica de `depends_on` funcionando.

## Exercício 5 — Time travel: voltar no tempo de uma tabela

Delta Lake versiona **toda** escrita automaticamente — sem configurar nada.
Roda isto num SQL Editor:

```sql
-- Ver todas as versões e quem/quando escreveu cada uma
DESCRIBE HISTORY digimon_lakehouse.gold.digimon_summary;

-- Consultar como a tabela estava numa versão específica
SELECT count(*) FROM digimon_lakehouse.gold.digimon_summary VERSION AS OF 1;

-- Ou por data/hora
SELECT count(*) FROM digimon_lakehouse.gold.digimon_summary TIMESTAMP AS OF '2026-07-22';
```

Neste workspace, `digimon_summary` já tem 8 versões reais — uma pra cada vez
que o job rodou nesta sessão. Isso é de graça: nenhum notebook do projeto
configurou isso explicitamente, é comportamento padrão do formato Delta.

## Exercício 6 — Lineage: de onde cada tabela veio

No **Catalog Explorer**, abre `gold.digimon_summary` > aba **Lineage**. O
Databricks rastreia sozinho que essa tabela vem de
`silver.dim_digimon` + `silver.bridge_digimon_level` + ... — sem você
declarar isso em lugar nenhum, ele infere direto do plano de execução do
`CREATE TABLE AS SELECT`. Útil pra responder "se eu mudar X, o que mais
quebra?" sem precisar ler todo o SQL de novo.

## Exercício 7 — Quebrar de propósito e aprender a debugar

1. Em `databricks/notebooks/01_silver_transform.py`, muda um nome de coluna
   errado de propósito (ex.: `d.digimon_idd` em vez de `d.digimon_id`).
2. Deploy + roda o job. Vai falhar.
3. **Workflows** > clica no run que falhou > clica na task vermelha > lê o
   erro (Databricks aponta a linha exata do SQL).
4. Usa **Repair Run** (botão no run falho) pra rodar de novo só a task que
   falhou depois de corrigir — não precisa refazer as tasks que já
   passaram. É exatamente o mecanismo que usamos nesta sessão quando o
   `gold_aggregate` travou.

## Cardápio de coisas simples pra alterar e aprender

Ideias pequenas, cada uma ensina um pedaço diferente:

| Mudança | O que aprende |
|---|---|
| Trocar `pause_status: PAUSED` por `UNPAUSED` em `jobs.yml` | Agendamento nativo do Databricks Jobs |
| Expor `silver.dim_skill`/`bridge_digimon_skill` como `gold.stats_by_skill` | Repetir o padrão de agregação já visto, sozinho |
| Mudar `LEVEL_RANK` em `02_gold_aggregate.py` pra outra ordem de estágios | Como a escolha de modelagem muda o resultado (lembra dos 142 estágios?) |
| Adicionar um filtro `?x_antibody=true` na API | Query param booleano + `WHERE` condicional |
| Trocar `page_size = 20` por `50` no site | Efeito de paginação na performance percebida |
| `OPTIMIZE gold.digimon_summary` num SQL Editor | Compactação de arquivos Delta (não muda dado, só performance de leitura) |

## O que o Databricks oferece que a gente ainda não usou

Este projeto usa uma fatia pequena do Databricks de propósito (escopo de
estudo). Se quiser continuar explorando, essas são áreas reais que existem
no produto — confirme disponibilidade no seu workspace, Free Edition pode
ter limites em algumas:

- **Genie** — faz perguntas em português sobre suas tabelas Gold e ele gera
  o SQL sozinho (Catalog > seu catálogo > "Genie space").
- **Lakeview Dashboards** — monta um dashboard visual em cima do Gold sem
  sair do Databricks (alternativa/complemento ao Streamlit).
- **Data Quality Monitors** — cria monitores automáticos de qualidade numa
  tabela (nulos, distribuição, drift) sem escrever a checagem manualmente
  como fizemos no Exercício 4.
- **Query History** — todo SQL que qualquer coisa rodou contra o warehouse
  fica logado ali, com tempo de execução — útil pra achar gargalo.
- **`OPTIMIZE`/`VACUUM`** — comandos de manutenção de tabelas Delta
  (compactar arquivos pequenos, limpar versões antigas de time travel).
- **Databricks CLI além do `bundle`** — `databricks workspace export-dir`,
  `databricks fs cp`, `databricks jobs list-runs` — dá pra automatizar quase
  qualquer coisa que a UI faz.

---

*Achou algum exercício que não bateu com o seu workspace? Anota o erro real
(igual a seção "Perrengues reais" do [SETUP.md](SETUP.md#perrengues-reais-erros-que-já-apanhamos-por-você)) — faz parte do
aprendizado descobrir onde a documentação e a realidade divergem.*
