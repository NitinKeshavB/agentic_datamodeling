# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC # Catalog Relationship Discovery
# MAGIC
# MAGIC Crawls a Unity Catalog schema, detects table relationships (explicit FK + inferred),
# MAGIC profiles data quality, and runs an AI agent analysis using Claude.
# MAGIC
# MAGIC **Set the parameters in the next cell before running.**

# COMMAND ----------

# Parameters — update before running
CATALOG = "dev_catalog"       # Target Unity Catalog catalog
SCHEMA = "your_schema"        # Target schema (leave empty "" to scan all schemas)
WAREHOUSE_ID = None           # Leave None to auto-select the first available warehouse

# COMMAND ----------

import json
from adm.catalog.crawler import CatalogCrawler
from adm.catalog.relationships import RelationshipDetector

crawler = CatalogCrawler(catalog=CATALOG, schema=SCHEMA or None, warehouse_id=WAREHOUSE_ID)

# COMMAND ----------
# MAGIC %md ## 1. Tables & Columns

# COMMAND ----------

tables = crawler.list_tables()
print(f"Found {len(tables)} tables in {CATALOG}.{SCHEMA}\n")
for t in tables:
    pks = ", ".join(t["primary_keys"]) if t["primary_keys"] else "(no PK declared)"
    print(f"  {t['name']:40s}  {len(t['columns'])} cols   PK: {pks}")

# COMMAND ----------
# MAGIC %md ## 2. Explicit FK Constraints

# COMMAND ----------

fk_rows = crawler.get_foreign_keys()
if fk_rows:
    print(f"Explicit FK constraints: {len(fk_rows)}\n")
    for fk in fk_rows:
        print(f"  {fk['child_table']}.{fk['child_column']}  →  {fk['parent_table']}.{fk['parent_column']}")
else:
    print("No explicit FK constraints declared in Unity Catalog (common for ingested data).")

# COMMAND ----------
# MAGIC %md ## 3. All Relationships (explicit + inferred)

# COMMAND ----------

metadata = crawler.crawl()
detector = RelationshipDetector()
relationships = detector.detect_all(metadata)

print(f"Total relationships detected: {len(relationships)}\n")
print(f"{'TYPE':<20} {'CONFIDENCE':>10}  RELATIONSHIP")
print("-" * 80)
for r in relationships:
    print(f"{r.relationship_type:<20} {r.confidence:>10.0%}  {r.child_table}.{r.child_column}  →  {r.parent_table}.{r.parent_column}")

# COMMAND ----------
# MAGIC %md ## 4. Data Quality — Row Counts & Null Rates

# COMMAND ----------

for t in tables[:10]:    # limit to first 10 tables to avoid long runtimes
    stats = crawler.get_table_stats(t["full_name"])
    high_null = {col: rate for col, rate in stats["null_rates"].items() if rate > 0.1}
    print(f"{t['name']:40s}  rows={stats['row_count']:>10,}  high-null cols: {high_null or 'none'}")

# COMMAND ----------
# MAGIC %md ## 5. Duplicate Check on Primary Keys

# COMMAND ----------

for t in tables[:10]:
    if t["primary_keys"]:
        dupes = crawler.check_duplicates(t["full_name"], t["primary_keys"])
        if dupes["duplicate_groups"] > 0:
            print(f"⚠  {t['name']}: {dupes['duplicate_groups']} duplicate groups ({dupes['duplicate_rows']} extra rows)")
        else:
            print(f"✓  {t['name']}: no duplicates on {t['primary_keys']}")

# COMMAND ----------
# MAGIC %md ## 6. AI Agent Analysis
# MAGIC
# MAGIC Requires `ANTHROPIC_API_KEY` to be set as a Databricks secret:
# MAGIC ```
# MAGIC databricks secrets create-scope adm
# MAGIC databricks secrets put-secret adm ANTHROPIC_API_KEY --string-value <key>
# MAGIC ```

# COMMAND ----------

import os
from adm.agents.catalog_agent import CatalogAgent

# Load API key from Databricks secret (if running on cluster) or env var
try:
    from pyspark.dbutils import DBUtils
    dbutils = DBUtils(spark)
    os.environ["ANTHROPIC_API_KEY"] = dbutils.secrets.get(scope="adm", key="ANTHROPIC_API_KEY")
except Exception:
    pass  # Running locally — expects ANTHROPIC_API_KEY in environment

agent = CatalogAgent(catalog=CATALOG, schema=SCHEMA or None, warehouse_id=WAREHOUSE_ID)
report = agent.run()
print(report)

# COMMAND ----------
# MAGIC %md ## 7. Export Results

# COMMAND ----------

output = {
    "catalog": CATALOG,
    "schema": SCHEMA,
    "tables": metadata["tables"],
    "relationships": [r.to_dict() for r in relationships],
    "ai_analysis": report,
}

output_path = f"/tmp/catalog_discovery_{CATALOG}_{SCHEMA}.json"
with open(output_path, "w") as f:
    json.dump(output, f, indent=2, default=str)

print(f"Results written to {output_path}")
