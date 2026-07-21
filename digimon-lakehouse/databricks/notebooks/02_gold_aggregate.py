# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Silver -> Gold
# MAGIC
# MAGIC Tabelas prontas para consumo (API/site): uma visão desnormalizada por
# MAGIC digimon, estatísticas agregadas, e a cadeia de evolução completa via
# MAGIC **CTE recursiva**.
# MAGIC
# MAGIC `WITH RECURSIVE` requer Databricks Runtime relativamente recente (SQL
# MAGIC recursivo foi adicionado em 2023). Se o seu SQL Warehouse/runtime não
# MAGIC suportar, use o fallback em PySpark (BFS) na última célula — o dataset é
# MAGIC pequeno o suficiente (algumas centenas de arestas) para caber em memória
# MAGIC no driver sem problema.

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
# MAGIC ### Cadeia de evolução (CTE recursiva)
# MAGIC
# MAGIC Ponto de partida: digimons "raiz" (nunca aparecem como `to_digimon_id` —
# MAGIC ou seja, ninguém evolui *para* eles). `NOT array_contains(path, ...)`
# MAGIC existe para travar qualquer ciclo acidental nos dados de origem — sem essa
# MAGIC guarda, um ciclo faria a recursão nunca terminar.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE gold.evolution_chains AS
# MAGIC WITH RECURSIVE evolution_path (root_digimon_id, digimon_id, path, depth) AS (
# MAGIC   SELECT
# MAGIC     from_digimon_id,
# MAGIC     from_digimon_id,
# MAGIC     ARRAY(from_digimon_id),
# MAGIC     1
# MAGIC   FROM silver.fact_evolution
# MAGIC   WHERE from_digimon_id NOT IN (SELECT to_digimon_id FROM silver.fact_evolution)
# MAGIC
# MAGIC   UNION ALL
# MAGIC
# MAGIC   SELECT
# MAGIC     ep.root_digimon_id,
# MAGIC     fe.to_digimon_id,
# MAGIC     ep.path || ARRAY(fe.to_digimon_id),
# MAGIC     ep.depth + 1
# MAGIC   FROM evolution_path ep
# MAGIC   JOIN silver.fact_evolution fe ON fe.from_digimon_id = ep.digimon_id
# MAGIC   WHERE NOT array_contains(ep.path, fe.to_digimon_id)
# MAGIC )
# MAGIC SELECT
# MAGIC   root_digimon_id,
# MAGIC   digimon_id AS leaf_digimon_id,
# MAGIC   path       AS digimon_id_path,
# MAGIC   depth
# MAGIC FROM evolution_path;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Fallback em PySpark (BFS) — usar só se `WITH RECURSIVE` não estiver disponível
# MAGIC
# MAGIC Descomente a célula abaixo e comente a célula SQL acima caso seu SQL
# MAGIC Warehouse não suporte CTE recursiva.

# COMMAND ----------

# from collections import defaultdict
# from pyspark.sql import Row
#
# edges = spark.table("silver.fact_evolution").select("from_digimon_id", "to_digimon_id").collect()
# adjacency = defaultdict(list)
# has_incoming = set()
# for e in edges:
#     adjacency[e.from_digimon_id].append(e.to_digimon_id)
#     has_incoming.add(e.to_digimon_id)
#
# all_sources = {e.from_digimon_id for e in edges}
# roots = all_sources - has_incoming
#
# rows = []
# for root in roots:
#     frontier = [(root, [root], 1)]
#     while frontier:
#         node, path, depth = frontier.pop()
#         rows.append(Row(root_digimon_id=root, leaf_digimon_id=node, digimon_id_path=path, depth=depth))
#         for neighbor in adjacency.get(node, []):
#             if neighbor not in path:  # guarda de ciclo, igual à versão SQL
#                 frontier.append((neighbor, path + [neighbor], depth + 1))
#
# spark.createDataFrame(rows).write.mode("overwrite").saveAsTable("gold.evolution_chains")

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
# MAGIC QUALIFY row_number() OVER (PARTITION BY ec.root_digimon_id ORDER BY ec.depth DESC) = 1
# MAGIC ORDER BY ec.depth DESC;
