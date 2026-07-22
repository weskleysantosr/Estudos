# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Silver -> Gold
# MAGIC
# MAGIC Tabelas prontas para consumo (API/site): uma visão desnormalizada por
# MAGIC digimon, estatísticas agregadas, e a cadeia de evolução mais longa a
# MAGIC partir de cada digimon "raiz".
# MAGIC
# MAGIC A cadeia de evolução é calculada em **PySpark com memoização**, não com
# MAGIC `WITH RECURSIVE`. A primeira versão deste notebook usava CTE recursiva —
# MAGIC ficou registrado mais abaixo, comentado, o porquê de ter sido trocada:
# MAGIC o grafo real de evolução (agregado pela DAPI de vários jogos/mídias) é
# MAGIC denso demais pra enumerar caminhos.

# COMMAND ----------

# MAGIC %sql
# MAGIC USE CATALOG digimon_lakehouse;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE gold.digimon_summary AS
# MAGIC SELECT
# MAGIC   d.digimon_id,
# MAGIC   d.name,
# MAGIC   d.x_antibody,
# MAGIC   d.release_date,
# MAGIC   d.image_url,
# MAGIC   sort_array(collect_set(lvl.level_name))  AS levels,
# MAGIC   sort_array(collect_set(typ.type_name))   AS types,
# MAGIC   sort_array(collect_set(attr.attribute_name)) AS attributes,
# MAGIC   sort_array(collect_set(fld.field_name))  AS fields,
# MAGIC   count(DISTINCT ev.to_digimon_id)         AS next_evolution_count
# MAGIC FROM silver.dim_digimon d
# MAGIC LEFT JOIN silver.bridge_digimon_level     bl  ON bl.digimon_id = d.digimon_id
# MAGIC LEFT JOIN silver.dim_level                lvl ON lvl.level_id = bl.level_id
# MAGIC LEFT JOIN silver.bridge_digimon_type      bt  ON bt.digimon_id = d.digimon_id
# MAGIC LEFT JOIN silver.dim_type                 typ ON typ.type_id = bt.type_id
# MAGIC LEFT JOIN silver.bridge_digimon_attribute ba  ON ba.digimon_id = d.digimon_id
# MAGIC LEFT JOIN silver.dim_attribute            attr ON attr.attribute_id = ba.attribute_id
# MAGIC LEFT JOIN silver.bridge_digimon_field     bf  ON bf.digimon_id = d.digimon_id
# MAGIC LEFT JOIN silver.dim_field                fld ON fld.field_id = bf.field_id
# MAGIC LEFT JOIN silver.fact_evolution           ev  ON ev.from_digimon_id = d.digimon_id
# MAGIC GROUP BY d.digimon_id, d.name, d.x_antibody, d.release_date, d.image_url;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Lista completa de evoluções por digimon
# MAGIC
# MAGIC `digimon_summary.next_evolution_count` já existia (só a contagem). Esta
# MAGIC tabela traz a lista de verdade — pra quê e de onde cada digimon evolui —
# MAGIC pro site/API mostrarem ao clicar num digimon. `direction` distingue as
# MAGIC duas pontas da mesma aresta (`next` = evolui pra, `prior` = evoluiu de).

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE gold.digimon_evolutions AS
# MAGIC SELECT
# MAGIC   fe.from_digimon_id AS digimon_id,
# MAGIC   'next' AS direction,
# MAGIC   fe.to_digimon_id AS related_digimon_id,
# MAGIC   d.name AS related_digimon_name,
# MAGIC   d.image_url AS related_digimon_image_url,
# MAGIC   fe.condition
# MAGIC FROM silver.fact_evolution fe
# MAGIC JOIN silver.dim_digimon d ON d.digimon_id = fe.to_digimon_id
# MAGIC
# MAGIC UNION ALL
# MAGIC
# MAGIC SELECT
# MAGIC   fe.to_digimon_id AS digimon_id,
# MAGIC   'prior' AS direction,
# MAGIC   fe.from_digimon_id AS related_digimon_id,
# MAGIC   d.name AS related_digimon_name,
# MAGIC   d.image_url AS related_digimon_image_url,
# MAGIC   fe.condition
# MAGIC FROM silver.fact_evolution fe
# MAGIC JOIN silver.dim_digimon d ON d.digimon_id = fe.from_digimon_id;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE gold.stats_by_level AS
# MAGIC SELECT lvl.level_name, count(DISTINCT b.digimon_id) AS digimon_count
# MAGIC FROM silver.bridge_digimon_level b
# MAGIC JOIN silver.dim_level lvl ON lvl.level_id = b.level_id
# MAGIC GROUP BY lvl.level_name
# MAGIC ORDER BY digimon_count DESC;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE gold.stats_by_type AS
# MAGIC SELECT typ.type_name, count(DISTINCT b.digimon_id) AS digimon_count
# MAGIC FROM silver.bridge_digimon_type b
# MAGIC JOIN silver.dim_type typ ON typ.type_id = b.type_id
# MAGIC GROUP BY typ.type_name
# MAGIC ORDER BY digimon_count DESC;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE gold.stats_by_attribute AS
# MAGIC SELECT attr.attribute_name, count(DISTINCT b.digimon_id) AS digimon_count
# MAGIC FROM silver.bridge_digimon_attribute b
# MAGIC JOIN silver.dim_attribute attr ON attr.attribute_id = b.attribute_id
# MAGIC GROUP BY attr.attribute_name
# MAGIC ORDER BY digimon_count DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Cadeia de evolução — tentativa 1: CTE recursiva (não usar — ver abaixo)
# MAGIC
# MAGIC A ideia óbvia: partir dos digimons "raiz" (nunca aparecem como
# MAGIC `to_digimon_id`) e enumerar todo caminho possível via `WITH RECURSIVE`,
# MAGIC depois escolher o mais longo por raiz. **Isso quebra neste dataset real:**
# MAGIC
# MAGIC ```
# MAGIC [RECURSION_ROW_LIMIT_EXCEEDED] Recursion row limit 1000000 reached
# MAGIC ```
# MAGIC
# MAGIC Mesmo travando a profundidade em 10 (`AND ep.depth < 10`) e o ciclo com
# MAGIC `NOT array_contains(path, ...)`. Por quê? A DAPI agrega evoluções de
# MAGIC vários jogos/mídias no mesmo grafo — um `GROUP BY from_digimon_id` em
# MAGIC `silver.fact_evolution` mostra nós com até **188 arestas de saída**. Com
# MAGIC ramificação assim, o número de CAMINHOS distintos explode
# MAGIC combinatorialmente bem antes de qualquer ciclo — travar a profundidade
# MAGIC não resolve, porque o problema é a largura, não a altura. Isso não é bug
# MAGIC nos dados, é o formato real de um grafo bem conectado.
# MAGIC
# MAGIC A lição: enumerar TODOS os caminhos é a pergunta errada quando você só
# MAGIC quer o MAIS LONGO. A pergunta certa é resolvida com programação
# MAGIC dinâmica, calculada uma vez por NÓ (memoização), não uma vez por
# MAGIC caminho — ver a célula PySpark abaixo, que é a implementação real.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Cadeia de evolução — tentativa 2 (a que fica): DP com memoização
# MAGIC
# MAGIC `cadeia_mais_longa(d) = 1 + max(cadeia_mais_longa(próximo) para próximo
# MAGIC em evoluções de d)`, calculada uma vez por digimon e reaproveitada
# MAGIC (memoização) sempre que outro caminho passa pelo mesmo nó. Isso é
# MAGIC O(nós + arestas) — para ~1.400 nós e ~15 mil arestas deste grafo, roda
# MAGIC em milissegundos, contra a CTE recursiva que nunca termina no mesmo dado.
# MAGIC
# MAGIC **Atenção com o resultado, não só com o erro**: a primeira versão que
# MAGIC rodou sem falhar reportou uma "cadeia de evolução" de **142 estágios** —
# MAGIC e isso não passa o teste de sanidade (Digimon real vai de Baby a Mega em
# MAGIC uns 6-7 estágios). O bug: seguir "qualquer aresta com o maior valor" faz
# MAGIC o caminho pular entre digimons de qualquer nível, em qualquer ordem — o
# MAGIC grafo é denso o bastante (alguns nós com 188 arestas) pra sempre achar
# MAGIC ALGUM vizinho que estende o caminho, sem que isso signifique uma
# MAGIC evolução real. A correção: só seguir uma aresta se o destino está num
# MAGIC **nível estritamente mais avançado** que a origem — usando a mesma
# MAGIC classificação de `levels` que já está correta em `silver.dim_level`.
# MAGIC Isso também transforma o grafo num DAG de verdade (nível só aumenta),
# MAGIC então ciclo deixa de ser possível por construção — `on_stack` fica só
# MAGIC como cinto de segurança, não como a defesa principal.

# COMMAND ----------

from collections import defaultdict
import sys

# Ranking aproximado dos estágios canônicos — Armor/Hybrid são formas
# alternativas (evoluções especiais fora da linha principal Baby->Mega), por
# isso ficam pareadas com o estágio mais próximo em vez de um rank exclusivo.
LEVEL_RANK = {
    "Baby I": 1, "Baby II": 2, "Child": 3,
    "Adult": 4, "Armor": 4,
    "Perfect": 5, "Ultimate": 6, "Hybrid": 6,
    "Unknown": 0,
}

level_rows = (
    spark.table("silver.bridge_digimon_level")
    .join(spark.table("silver.dim_level"), "level_id")
    .select("digimon_id", "level_name")
    .collect()
)
digimon_rank: dict[int, int] = defaultdict(int)
for r in level_rows:
    rank = LEVEL_RANK.get(r.level_name, 0)
    digimon_rank[r.digimon_id] = max(digimon_rank[r.digimon_id], rank)

edges = spark.table("silver.fact_evolution").select("from_digimon_id", "to_digimon_id").collect()
adjacency = defaultdict(list)
has_incoming = set()
for e in edges:
    # só é "evolução" se avança de estágio — corta as arestas que fariam o
    # caminho pular lateralmente/pra trás entre digimons de mídias diferentes.
    if digimon_rank[e.to_digimon_id] > digimon_rank[e.from_digimon_id]:
        adjacency[e.from_digimon_id].append(e.to_digimon_id)
        has_incoming.add(e.to_digimon_id)

all_sources = set(adjacency.keys())
roots = sorted(all_sources - has_incoming)

sys.setrecursionlimit(10_000)  # grafo tem ~1.400 nós; folga generosa sobre a profundidade real

memo: dict[int, tuple[int, int | None]] = {}  # digimon_id -> (nº de próximas evoluções na melhor cadeia, próximo nó)
on_stack: set[int] = set()


def longest_from(node: int) -> tuple[int, int | None]:
    if node in memo:
        return memo[node]
    if node in on_stack:
        return (0, None)  # ciclo: beco sem saída, não memoiza (nó real ainda será calculado depois)

    on_stack.add(node)
    best_len, best_next = 0, None
    for neighbor in adjacency.get(node, []):
        neighbor_len, _ = longest_from(neighbor)
        if neighbor_len + 1 > best_len:
            best_len, best_next = neighbor_len + 1, neighbor
    on_stack.discard(node)

    memo[node] = (best_len, best_next)
    return memo[node]


# O valor numérico em memo[node] (best_len) é sempre seguro — o corte por
# on_stack garante que ele termina. Mas o PONTEIRO best_next pode, sim,
# formar um ciclo entre si: se A calcula "meu melhor próximo é B" enquanto B
# ainda está sendo calculado (on_stack), e depois B calcula "meu melhor
# próximo é A" com base nesse valor parcial, memo[A]=(_, B) e memo[B]=(_, A)
# ficam apontando um pro outro — sem o `visited` abaixo, isso trava o loop
# pra sempre (foi exatamente o que aconteceu na primeira versão: o job real
# ficou preso aqui, não deu erro, só nunca terminou).
rows = []
for root in roots:
    longest_from(root)
    path = [root]
    visited = {root}
    while True:
        _, next_node = memo[path[-1]]
        if next_node is None or next_node in visited:
            break
        path.append(next_node)
        visited.add(next_node)
    rows.append((root, path[-1], path, len(path)))

from pyspark.sql.types import ArrayType, LongType, StructField, StructType

schema = StructType(
    [
        StructField("root_digimon_id", LongType(), False),
        StructField("leaf_digimon_id", LongType(), False),
        StructField("digimon_id_path", ArrayType(LongType()), False),
        StructField("depth", LongType(), False),
    ]
)
spark.createDataFrame(rows, schema=schema).write.mode("overwrite").saveAsTable("gold.evolution_chains")

# COMMAND ----------

# MAGIC %md
# MAGIC `gold.evolution_chains` já traz exatamente **uma linha por raiz** (a
# MAGIC melhor cadeia, não todas) — diferente da tentativa via CTE, que
# MAGIC precisaria de um `QUALIFY row_number()` pra filtrar depois de enumerar
# MAGIC tudo. Aqui só falta juntar os nomes pra exibição.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE gold.longest_evolution_chains AS
# MAGIC SELECT
# MAGIC   root.name AS root_digimon_name,
# MAGIC   leaf.name AS leaf_digimon_name,
# MAGIC   ec.depth,
# MAGIC   ec.digimon_id_path
# MAGIC FROM gold.evolution_chains ec
# MAGIC JOIN silver.dim_digimon root ON root.digimon_id = ec.root_digimon_id
# MAGIC JOIN silver.dim_digimon leaf ON leaf.digimon_id = ec.leaf_digimon_id
# MAGIC ORDER BY ec.depth DESC;
