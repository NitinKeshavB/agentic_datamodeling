# Databricks notebook source
# MAGIC %md
# MAGIC # PostgreSQL Connection Test
# MAGIC Tests connectivity to an external PostgreSQL database using the `adm` wheel.

# COMMAND ----------

# MAGIC %pip install psycopg2-binary sqlalchemy

# COMMAND ----------

# DBTITLE 1, Configuration — fill these in or use a secret reference
import os

# Option A: hardcode for a quick test (never commit passwords)
HOST     = "10.255.255.254"   # or your Azure Postgres host
PORT     = 5432
DATABASE = "postgres"
USER     = "postgres"
# PASSWORD read from Databricks secret so it never appears in plaintext
PASSWORD = dbutils.secrets.get(scope="adm", key="ERP_POSTGRES_CONNECTION_STRING")  # noqa

# Option B: paste a full SQLAlchemy URL directly
# CONN_URL = "postgresql+psycopg2://user:pass@host:5432/dbname"

# COMMAND ----------

# DBTITLE 1, Build connection URL
from urllib.parse import quote_plus

CONN_URL = f"postgresql+psycopg2://{USER}:{quote_plus(PASSWORD)}@{HOST}:{PORT}/{DATABASE}"
print(f"Connecting to: postgresql+psycopg2://{USER}:***@{HOST}:{PORT}/{DATABASE}")

# COMMAND ----------

# DBTITLE 1, Test 1 — raw psycopg2 ping
import psycopg2

try:
    conn = psycopg2.connect(
        host=HOST, port=PORT, dbname=DATABASE,
        user=USER, password=PASSWORD,
        connect_timeout=5,
    )
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    conn.close()
    print(f"✓ psycopg2 connected\n  {version}")
except Exception as e:
    print(f"✗ psycopg2 failed: {e}")

# COMMAND ----------

# DBTITLE 1, Test 2 — SQLAlchemy engine ping
from sqlalchemy import create_engine, text

try:
    engine = create_engine(CONN_URL, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_database(), current_user, inet_server_addr(), inet_server_port()"))
        row = result.fetchone()
        print(f"✓ SQLAlchemy connected")
        print(f"  database : {row[0]}")
        print(f"  user     : {row[1]}")
        print(f"  server   : {row[2]}:{row[3]}")
except Exception as e:
    print(f"✗ SQLAlchemy failed: {e}")

# COMMAND ----------

# DBTITLE 1, Test 3 — list schemas
from sqlalchemy import inspect

try:
    engine = create_engine(CONN_URL)
    inspector = inspect(engine)
    schemas = inspector.get_schema_names()
    print(f"✓ Schemas found: {schemas}")
except Exception as e:
    print(f"✗ Schema listing failed: {e}")

# COMMAND ----------

# DBTITLE 1, Test 4 — list tables in public schema
try:
    engine = create_engine(CONN_URL)
    inspector = inspect(engine)
    tables = inspector.get_table_names(schema="public")
    print(f"✓ Tables in public schema ({len(tables)} found):")
    for t in tables:
        print(f"  - {t}")
except Exception as e:
    print(f"✗ Table listing failed: {e}")

# COMMAND ----------

# DBTITLE 1, Test 5 — adm JDBCConnector (uses the deployed wheel)
try:
    from adm.catalog.sources.jdbc import JDBCConnector

    connector = JDBCConnector(
        connection_string=CONN_URL,
        schema="public",
        source_type="postgresql",
    )
    result = connector.ping()
    print(f"✓ adm JDBCConnector ping: {result}")
except Exception as e:
    print(f"✗ adm JDBCConnector failed: {e}")
