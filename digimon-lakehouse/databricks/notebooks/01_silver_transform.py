# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Bronze -> Silver
# MAGIC
# MAGIC Parseia o JSON bruto (`bronze.raw_digimon.raw_json`, tipo `STRING`) com um
# MAGIC **schema explícito** (`from_json` + `STRUCT`) em vez do operador `:` de
# MAGIC acesso semi-estruturado. Motivo: schema explícito é enforcement, não
# MAGIC sugestão — se a API mudar um campo de tipo, o job falha aqui de forma
# MAGIC clara, em vez de silenciosamente virar `NULL` lá na frente no gold/API.
# MAGIC
# MAGIC Estratégia de refresh: **CREATE OR REPLACE TABLE (CTAS)** completo a cada
# MAGIC run, não MERGE incremental. Isso é uma escolha deliberada, não preguiça:
# MAGIC o dataset inteiro (algumas centenas de digimons) é reprocessado em
# MAGIC segundos, e full-refresh elimina uma classe inteira de bugs de MERGE
# MAGIC incremental (linhas órfãs quando um digimon é removido na fonte, chaves
# MAGIC de bridge desatualizadas, etc.). Para volumes muito maiores, o padrão
# MAGIC mudaria para incremental com Structured Streaming/Auto Loader — mas não
# MAGIC antes de precisar.

# COMMAND ----------

# MAGIC %sql
# MAGIC USE CATALOG digimon_lakehouse;

# COMMAND ----------

digimon_schema = """
    id BIGINT,
    name STRING,
    xAntibody BOOLEAN,
    images ARRAY<STRUCT<href: STRING, transparent: BOOLEAN>>,
    levels ARRAY<STRUCT<id: BIGINT, level: STRING>>,
    types ARRAY<STRUCT<id: BIGINT, type: STRING>>,
    attributes ARRAY<STRUCT<id: BIGINT, attribute: STRING>>,
    fields ARRAY<STRUCT<id: BIGINT, field: STRING, image: STRING>>,
    releaseDate STRING,
    descriptions ARRAY<STRUCT<origin: STRING, language: STRING, description: STRING>>,
    skills ARRAY<STRUCT<id: BIGINT, skill: STRING, translation: STRING, description: STRING>>,
    priorEvolutions ARRAY<STRUCT<id: BIGINT, digimon: STRING, condition: STRING, image: STRING, url: STRING>>,
    nextEvolutions ARRAY<STRUCT<id: BIGINT, digimon: STRING, condition: STRING, image: STRING, url: STRING>>
"""

from pyspark.sql import functions as F

bronze_df = spark.table("bronze.raw_digimon")
parsed_df = bronze_df.select(F.from_json("raw_json", digimon_schema).alias("d")).select("d.*").cache()

# Falha rápido e visível se o parse não bater com o schema esperado, em vez de
# publicar tabelas silver com dados parcialmente nulos sem ninguém notar.
unparsed = bronze_df.count() - parsed_df.filter(F.col("id").isNotNull()).count()
if unparsed > 0:
    raise ValueError(
        f"{unparsed} registro(s) da bronze não bateram com o schema esperado — "
        "revise digimon_schema antes de publicar a silver."
    )

parsed_df.createOrReplaceTempView("v_digimon_parsed")

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE silver.dim_digimon AS
# MAGIC SELECT
# MAGIC   id          AS digimon_id,
# MAGIC   name,
# MAGIC   xAntibody   AS x_antibody,
# MAGIC   releaseDate AS release_date,
# MAGIC   images[0].href AS image_url
# MAGIC FROM v_digimon_parsed;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.dim_level AS
# MAGIC SELECT DISTINCT lvl.id AS level_id, lvl.level AS level_name
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(levels) AS lvl;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.dim_type AS
# MAGIC SELECT DISTINCT t.id AS type_id, t.type AS type_name
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(types) AS t;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.dim_attribute AS
# MAGIC SELECT DISTINCT a.id AS attribute_id, a.attribute AS attribute_name
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(attributes) AS a;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.dim_field AS
# MAGIC SELECT DISTINCT f.id AS field_id, f.field AS field_name, f.image AS field_image_url
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(fields) AS f;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.dim_skill AS
# MAGIC SELECT DISTINCT s.id AS skill_id, s.skill AS skill_name, s.translation, s.description
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(skills) AS s;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE silver.bridge_digimon_level AS
# MAGIC SELECT id AS digimon_id, lvl.id AS level_id
# MAGIC FROM v_digimon_parsed LATERAL VIEW explode(levels) AS lvl;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.bridge_digimon_type AS
# MAGIC SELECT id AS digimon_id, t.id AS type_id
# MAGIC FROM v_digimon_parsed LATERAL VIEW explode(types) AS t;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.bridge_digimon_attribute AS
# MAGIC SELECT id AS digimon_id, a.id AS attribute_id
# MAGIC FROM v_digimon_parsed LATERAL VIEW explode(attributes) AS a;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.bridge_digimon_field AS
# MAGIC SELECT id AS digimon_id, f.id AS field_id
# MAGIC FROM v_digimon_parsed LATERAL VIEW explode(fields) AS f;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE silver.bridge_digimon_skill AS
# MAGIC SELECT id AS digimon_id, s.id AS skill_id
# MAGIC FROM v_digimon_parsed LATERAL VIEW explode(skills) AS s;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Evolução: `fact_evolution`
# MAGIC
# MAGIC Fonte de verdade: `nextEvolutions` (aresta "de -> para"). `priorEvolutions`
# MAGIC é redundante por design da API (é a mesma relação vista do outro lado) —
# MAGIC usamos essa redundância como **checagem de qualidade de dados**: se A diz
# MAGIC que evolui para B, mas B não lista A como prior evolution, os dados de
# MAGIC origem são inconsistentes e vale saber disso.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE silver.fact_evolution AS
# MAGIC SELECT
# MAGIC   id       AS from_digimon_id,
# MAGIC   ne.id    AS to_digimon_id,
# MAGIC   ne.digimon AS to_digimon_name,
# MAGIC   ne.condition
# MAGIC FROM v_digimon_parsed
# MAGIC LATERAL VIEW explode(nextEvolutions) AS ne;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW silver.dq_evolution_inconsistencies AS
# MAGIC SELECT fe.from_digimon_id, fe.to_digimon_id, fe.to_digimon_name
# MAGIC FROM silver.fact_evolution fe
# MAGIC LEFT ANTI JOIN (
# MAGIC   SELECT id AS to_digimon_id, pe.id AS from_digimon_id
# MAGIC   FROM v_digimon_parsed
# MAGIC   LATERAL VIEW explode(priorEvolutions) AS pe
# MAGIC ) reverse
# MAGIC ON fe.from_digimon_id = reverse.from_digimon_id AND fe.to_digimon_id = reverse.to_digimon_id;

# COMMAND ----------

parsed_df.unpersist()
