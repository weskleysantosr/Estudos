# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Setup de catálogo/schemas
# MAGIC
# MAGIC Cria a separação **bronze / silver / gold** como schemas distintos dentro de
# MAGIC um catálogo dedicado, e concede permissões mínimas por camada (least
# MAGIC privilege): quem só consome dados (API/BI) não tem motivo para ter acesso
# MAGIC de escrita, e a camada bronze não precisa ser legível por quem só consome
# MAGIC o gold.
# MAGIC
# MAGIC Ajuste os nomes de grupo (`ingestion_principal`, `reporting_readers`) para
# MAGIC os principals reais do seu workspace — em Free/Community Edition, Unity
# MAGIC Catalog e grupos podem ter limitações; se `GRANT` falhar, siga sem essa
# MAGIC etapa e trate o controle de acesso a nível de aplicação (a API já só usa
# MAGIC um usuário Postgres de leitura — ver `api/app/db.py`).

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE CATALOG IF NOT EXISTS digimon_lakehouse;
# MAGIC
# MAGIC CREATE SCHEMA IF NOT EXISTS digimon_lakehouse.bronze;
# MAGIC CREATE SCHEMA IF NOT EXISTS digimon_lakehouse.silver;
# MAGIC CREATE SCHEMA IF NOT EXISTS digimon_lakehouse.gold;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Permissões (ajuste os principals antes de rodar)
# MAGIC
# MAGIC Descomente e adapte se o seu workspace tiver Unity Catalog com grupos
# MAGIC configurados. Caso contrário, esta etapa é opcional para o projeto de
# MAGIC estudo — mas é o padrão real de mercado e vale entender o conceito.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- GRANT USE CATALOG, USE SCHEMA ON SCHEMA digimon_lakehouse.bronze TO `ingestion_principal`;
# MAGIC -- GRANT MODIFY, SELECT ON SCHEMA digimon_lakehouse.bronze TO `ingestion_principal`;
# MAGIC -- GRANT MODIFY, SELECT ON SCHEMA digimon_lakehouse.silver TO `ingestion_principal`;
# MAGIC -- GRANT MODIFY, SELECT ON SCHEMA digimon_lakehouse.gold   TO `ingestion_principal`;
# MAGIC --
# MAGIC -- -- Quem só lê o gold (export job, BI, dashboards) não recebe MODIFY nem
# MAGIC -- -- acesso a bronze/silver (dados brutos/intermediários não são "contrato
# MAGIC -- -- de dados" estável — podem mudar de shape a qualquer momento).
# MAGIC -- GRANT USE CATALOG, USE SCHEMA, SELECT ON SCHEMA digimon_lakehouse.gold TO `reporting_readers`;
