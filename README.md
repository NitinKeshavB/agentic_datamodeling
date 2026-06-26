# Agentic Data Modeling

AI-powered enterprise data modeling on Databricks. Point it at any database and get a logical model, physical model, Databricks DDL, and a Mermaid ER diagram — all in one command.

---

## Quick Start

If you already have a Databricks workspace and a PAT token, this is all you need:

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Set credentials
export DATABRICKS_HOST=https://adb-<id>.azuredatabricks.net
export DATABRICKS_TOKEN=<your-pat-token>
export SERVING_ENDPOINT=databricks-claude-opus-4-8

# 3. Run
discover \
  --source unity_catalog \
  --catalog hackathon_demo \
  --schema snapshot_global_realestate
```

Four files are written to `/Workspace/Shared/hackathon/agentic-datamodeling/outputs/YYYY-MM-DD/HH-MM-SS/`:

| File | Contents |
|------|----------|
| `catalog_discovery_<catalog>_<schema>.json` | Full metadata, profiles, AI analysis |
| `catalog_discovery_<catalog>_<schema>.sql` | Databricks DDL with PK/FK constraints |
| `catalog_discovery_<catalog>_<schema>.erwin_notes.txt` | ERwin import instructions |
| `catalog_discovery_<catalog>_<schema>.er_diagram.md` | Mermaid ER diagram (renders on GitHub/VS Code) |

---

## Table of Contents

1. [How it Works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Local Setup](#local-setup)
4. [Databricks Bundle Setup](#databricks-bundle-setup)
5. [Model Serving Setup](#model-serving-setup)
6. [Running the Pipeline](#running-the-pipeline)
7. [MCP Server — Claude Desktop Integration](#mcp-server--claude-desktop-integration)
8. [Understanding the Outputs](#understanding-the-outputs)
9. [Viewing the ER Diagram](#viewing-the-er-diagram)
10. [DDL and ERwin Import](#ddl-and-erwin-import)
11. [Adding New Sources](#adding-new-sources)
12. [Troubleshooting](#troubleshooting)
13. [Project Structure](#project-structure)
14. [Make Commands](#make-commands)

---

## How it Works

The pipeline runs end-to-end in a single `discover` command:

```
Step 1  Crawl schema          → tables, columns, types, FK constraints
Step 2  Detect relationships  → explicit FK + naming-convention inference (e.g. lease.PropertyID → property.PropertyID)
Step 3  Sample & profile      → 10 rows per table, null rates, distinct counts, min/max/mean per column
Step 4  AI descriptions       → Claude generates a plain-English description for every table and column
Step 5  AI agent analysis     → 5-phase agent: logical model (3NF), physical model, data quality, DDL hints
Step 6  Write JSON report     → full enriched metadata in one structured file
Step 7  Write DDL             → Databricks SQL with NOT ENFORCED PK/FK constraints, auto-generated
Step 8  Write ERwin notes     → step-by-step ERwin import instructions
Step 9  Write ER diagram      → Mermaid erDiagram markdown, renders anywhere Mermaid is supported
```

All AI calls go through a **Databricks Model Serving endpoint** — no outbound internet to Anthropic is needed from the job network.

---

## Prerequisites

| Tool | Required version | Notes |
|------|-----------------|-------|
| Python | ≥ 3.10 | [pyenv](https://github.com/pyenv/pyenv) recommended; 3.12 used for local dev |
| Databricks CLI | ≥ 0.200 | `pip install databricks-cli` |
| `uv` (optional) | latest | `pip install uv` — faster installs |
| `make` | any | `sudo apt-get install make` (Linux/WSL) |

You also need:
- A Databricks workspace (Azure Databricks tested)
- A [Personal Access Token](https://docs.databricks.com/dev-tools/auth/pat.html) (PAT)
- A Databricks Model Serving endpoint with a Claude model (see [Model Serving Setup](#model-serving-setup))
- `CAN USE` permission on the serving endpoint and `SELECT` on the Unity Catalog schema

---

## Local Setup

### 1. Clone and install

```bash
git clone https://github.com/<your-org>/agentic-datamodeling.git
cd agentic-datamodeling
pip install -e ".[dev]"
```

For PostgreSQL support: `pip install -e ".[dev,postgresql]"`  
For SQL Server support: `pip install -e ".[dev,sqlserver]"`

### 2. Configure `~/.databrickscfg`

Create or edit `~/.databrickscfg` with a named profile per environment:

```ini
[dev]
host  = https://adb-<dev-workspace-id>.azuredatabricks.net
token = dapi...

[prod]
host  = https://adb-<prod-workspace-id>.azuredatabricks.net
token = dapi...
```

Verify a profile works:

```bash
databricks auth status --profile prod
```

### 3. Set environment variables

```bash
export DATABRICKS_HOST=https://adb-<id>.azuredatabricks.net
export DATABRICKS_TOKEN=<your-pat-token>
export SERVING_ENDPOINT=databricks-claude-opus-4-8
export DATABRICKS_CONFIG_PROFILE=prod          # used by bundle commands
```

Add to `~/.bashrc` or `~/.zshrc` to persist across sessions.

### 4. Smoke-test the serving endpoint

```bash
python test_endpoint.py
# Expected output:
# Response: Hello
# Model: databricks-claude-opus-4-8
```

If this fails, check [Troubleshooting](#troubleshooting).

### 5. Run unit tests

```bash
make test
```

---

## Databricks Bundle Setup

The project uses [Databricks Asset Bundles (DAB)](https://docs.databricks.com/dev-tools/bundles/index.html) to deploy jobs.

**Key principle:** `sources.yml` is the single source of truth. Every source you add automatically gets its own Databricks job when you run `make bundle-deploy`.

### 1. Update `databricks.yml`

Edit the `targets` section to match your workspace:

```yaml
targets:
  dev:
    workspace:
      profile: dev-azara          # must match a profile in ~/.databrickscfg
    variables:
      serving_endpoint: "databricks-claude-opus-4-8"

  prod:
    mode: production
    workspace:
      profile: prod
      root_path: /Workspace/Shared/hackathon/agentic-datamodeling/prod
    variables:
      serving_endpoint: "databricks-claude-opus-4-8"
```

### 2. Add your sources to `sources.yml`

```bash
cp sources.yml.example sources.yml
# Edit sources.yml — add every database you want to discover
```

Each source gets its own Databricks job automatically. No manual job YAML needed.

### 3. Validate the bundle

```bash
make bundle-validate prod
```

### 4. Deploy

```bash
make bundle-deploy prod
```

`bundle-deploy` does three things in order:
1. **`generate-jobs`** — reads `sources.yml`, writes `resources/source_jobs.yml` with one job per source
2. **Build wheel** — packages the Python code
3. **`bundle deploy`** — uploads the wheel and creates/updates all jobs in Databricks

### 5. Run a job on Databricks

Job names follow the pattern `[{target}] Discover — {source_name}`:

```bash
databricks bundle run discover_unity_prod -t prod
databricks bundle run discover_erp_postgres -t prod
# or any other source name from sources.yml
```

Watch runs in the Databricks UI under **Workflows**.

### Bundle environments

| Target | Profile | Mode | Use for |
|--------|---------|------|---------|
| `dev` | `dev-azara` | development | testing changes |
| `prod` | `prod` | production | production runs |

---

## Model Serving Setup

All AI calls route through a Databricks Model Serving endpoint — the Anthropic API key is stored in the endpoint config and is never exposed to application code.

### Check whether the endpoint already exists

```bash
databricks serving-endpoints list --profile prod
# Look for: databricks-claude-opus-4-8
```

### Create the endpoint (if missing)

```bash
databricks serving-endpoints create --profile prod --json '{
  "name": "databricks-claude-opus-4-8",
  "config": {
    "served_entities": [{
      "external_model": {
        "name": "claude-opus-4-8",
        "provider": "anthropic",
        "task": "llm/v1/chat",
        "anthropic_config": {
          "anthropic_api_key_plaintext": "<your-anthropic-api-key>"
        }
      }
    }]
  }
}'
```

> **Note:** The endpoint is **not** managed by the bundle. Running `make bundle-destroy` will **not** delete it.

### Credential flow

| Variable | On Databricks jobs | Locally |
|---|---|---|
| `DATABRICKS_HOST` | Injected automatically by the runtime | `export DATABRICKS_HOST=...` |
| `DATABRICKS_TOKEN` | Injected automatically by the runtime | `export DATABRICKS_TOKEN=...` |
| `SERVING_ENDPOINT` | Read from `databricks.yml` variable | `export SERVING_ENDPOINT=...` |

---

## Running the Pipeline

### Unity Catalog (most common)

```bash
discover \
  --source unity_catalog \
  --catalog hackathon_demo \
  --schema snapshot_global_realestate
```

Optional flags:
- `--warehouse-id <id>` — force a specific SQL Warehouse (auto-selected if omitted)
- `--output-path /tmp/my_run.json` — override the default timestamped output path

### PostgreSQL

```bash
discover \
  --source postgresql \
  --schema public \
  --connection-string "postgresql+psycopg2://user:pass@host:5432/mydb"
```

Or set the connection string via env var to avoid passing secrets on the command line:

```bash
export ADM_CONNECTION_STRING="postgresql+psycopg2://user:pass@host:5432/mydb"
discover --source postgresql --schema public
```

### SQL Server / Azure SQL

```bash
discover \
  --source sqlserver \
  --schema dbo \
  --connection-string "mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+17+for+SQL+Server"
```

### Generate DDL from an existing JSON report (no re-crawl)

```bash
main ddl /path/to/catalog_discovery.json \
  --target-catalog hackathon_demo \
  --output-sql /path/to/output.sql
```

This regenerates the `.sql`, `.erwin_notes.txt`, and `.er_diagram.md` files without recrawling the source.

### Run via Databricks job

Jobs are named after your `sources.yml` entries:

```bash
databricks bundle run discover_unity_prod -t prod
databricks bundle run discover_erp_postgres -t prod
```

---

## MCP Server — Claude Desktop Integration

The MCP (Model Context Protocol) server wraps the `adm` package so you can trigger data modeling directly from Claude Desktop — no CLI needed.

```
"discover the public schema in PostgreSQL" → Claude calls discover_schema → results in chat
"show me an ER diagram for the sales schema" → Claude calls get_er_diagram → Mermaid diagram in chat
"run AI analysis on the public schema" → Claude calls run_ai_analysis → model + recommendations
```

All credentials are stored in a startup script and read from environment variables at runtime. Claude never asks you for a connection string, token, or warehouse ID.

### How it works

- **First request** — crawls live from Databricks or PostgreSQL, saves 4 output files to `~/adm-outputs/`, returns results.
- **Repeat request** — finds the most recent saved file in `~/adm-outputs/` and returns it instantly (no DB connection).
- **"Give me a new analysis"** — set `force_refresh=True`; bypasses cache and re-crawls live.

### Prerequisites

- Claude Desktop (Windows/macOS)
- WSL2 with Ubuntu (Windows only) — Claude Desktop is on Windows, the Python code runs in WSL2
- Python 3.10+ with the `adm` package installed: `pip install -e ".[dev,postgresql]"`
- `mcp` package: `pip install mcp`

### Step 1 — Create the startup script

Create `/home/<you>/start_adm_mcp.sh` in WSL2:

```bash
#!/bin/bash

# Databricks Unity Catalog
export DATABRICKS_HOST=https://adb-<workspace-id>.azuredatabricks.net
export DATABRICKS_TOKEN=<your-databricks-pat>
export SERVING_ENDPOINT=databricks-claude-opus-4-8
export DATABRICKS_CATALOG=<your-default-catalog>
export WAREHOUSE_ID=<your-warehouse-id>            # optional — auto-selected if omitted

# PostgreSQL (omit if not using PostgreSQL)
export PG_CONNECTION_STRING="postgresql+psycopg2://user:pass@host:5432/dbname?sslmode=require"

# Runtime
export PYTHONPATH=/path/to/agentic-datamodeling/src
exec /path/to/python -m adm.mcp_server
```

Make it executable: `chmod +x ~/start_adm_mcp.sh`

> **WSL2 tip:** `localhost` in WSL2 refers to the Linux VM, not the Windows host. To reach a Windows-hosted PostgreSQL, use the Windows IP from `/etc/resolv.conf`:
> ```bash
> WIN_HOST=$(grep nameserver /etc/resolv.conf | awk '{print $2}')
> export PG_CONNECTION_STRING="postgresql+psycopg2://user:pass@${WIN_HOST}:5432/mydb"
> ```

### Step 2 — Configure Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "data-modeling": {
      "command": "C:\\Windows\\System32\\wsl.exe",
      "args": ["-d", "Ubuntu-20.04", "bash", "/home/<you>/start_adm_mcp.sh"]
    }
  }
}
```

> Replace `Ubuntu-20.04` with your actual WSL2 distro name (`wsl --list` to check). The `-d` flag is required — without it, WSL2 defaults to whichever distro was set as default (which may be Docker Desktop, not Ubuntu).

Restart Claude Desktop. A hammer icon in the chat bar confirms the MCP server connected.

### Available tools

| Tool | What it does |
|------|-------------|
| `list_schemas` | List all schemas in the configured data source |
| `discover_schema` | Crawl a schema — tables, columns, PKs, FKs, relationships. Saves all 4 output files on fresh crawl. |
| `get_er_diagram` | Return a Mermaid ER diagram for a schema (cached after first crawl) |
| `get_relationships` | List all detected relationships (explicit FK + inferred from column names) |
| `get_table_info` | Columns, PKs, relationships, and sample rows for a specific table |
| `run_ai_analysis` | Full AI pipeline — logical model (3NF), physical model, data quality, DDL recommendations |

### Routing: Databricks vs PostgreSQL

Every tool accepts a `source` parameter that Claude uses automatically based on your question:

| `source` value | Behaviour |
|---|---|
| `"auto"` (default) | Uses `DATABRICKS_CATALOG` env var if set; otherwise falls back to `PG_CONNECTION_STRING` |
| `"postgresql"` | Always uses `PG_CONNECTION_STRING`; ignores Databricks settings |
| `"databricks"` | Always uses `DATABRICKS_CATALOG` + `DATABRICKS_TOKEN`; ignores PostgreSQL |

**Example phrases that steer routing:**

- *"show me the PostgreSQL schemas"* → Claude passes `source="postgresql"`
- *"discover the public schema in Postgres"* → `source="postgresql"`, `schema="public"`
- *"what tables are in the hackathon catalog?"* → `source="databricks"`
- *"get me an ER diagram"* → `source="auto"`, resolved by env vars

### Caching and output files

On every fresh crawl, 4 files are saved to `~/adm-outputs/YYYY-MM-DD/HH-MM-SS/`:

| File | Contents |
|------|----------|
| `databricks_{catalog}_{schema}.json` | Full metadata + relationships |
| `databricks_{catalog}_{schema}.sql` | Databricks DDL |
| `databricks_{catalog}_{schema}.erwin_notes.txt` | ERwin import instructions |
| `databricks_{catalog}_{schema}.er_diagram.md` | Mermaid ER diagram |

PostgreSQL outputs use `postgresql_{dbname}_{schema}.*` naming.

Subsequent calls load from these saved files — no DB connection is made unless the user asks for a "new" or "fresh" analysis.

### Troubleshooting MCP

**Hammer icon not visible in Claude Desktop**
- Check Claude Desktop logs: `%APPDATA%\Claude\logs\`
- Common causes: wrong WSL distro name in `-d` flag; startup script not executable; Python path wrong in script.

**"No Databricks credentials" when asking about PostgreSQL**
- The `DATABRICKS_CATALOG` env var is set, so `source="auto"` defaults to Databricks. Ask Claude explicitly: *"use PostgreSQL"* or *"query the Postgres database"*.

**MCP server silent on stdio — how to verify it's running**
```bash
# Check if the process is alive
wsl -d Ubuntu-20.04 pgrep -f mcp_server

# Restart
wsl -d Ubuntu-20.04 pkill -f mcp_server
# Then restart Claude Desktop
```

---

## Understanding the Outputs

Every run produces four files in the same folder.

### 1. `catalog_discovery_<catalog>_<schema>.json`

Full structured report. Top-level keys:

```json
{
  "source_type": "unity_catalog",
  "catalog": "hackathon_demo",
  "schema": "snapshot_global_realestate",

  "tables": [
    {
      "name": "property",
      "columns": [
        { "name": "PropertyID", "type": "bigint", "nullable": false, "comment": null }
      ],
      "primary_keys": [],
      "comment": null
    }
  ],

  "relationships": [
    {
      "child_table": "lease",
      "child_column": "PropertyID",
      "parent_table": "property",
      "parent_column": "PropertyID",
      "type": "inferred_name",
      "confidence": 0.9
    }
  ],

  "profiles": {
    "property": {
      "sample_rows": [ { "PropertyID": 1, "PropertyName": "One Market Plaza" } ],
      "column_profiles": {
        "PropertyID": {
          "null_rate": 0.0,
          "n_distinct": 50,
          "min": 1,
          "max": 50,
          "mean": 25.5,
          "sample_values": ["1", "2", "3", "4", "5"]
        }
      },
      "table_description": "Stores physical real estate property records.",
      "column_descriptions": {
        "PropertyID": "Unique integer identifier for each property.",
        "PropertyName": "Human-readable name or label for the property."
      }
    }
  },

  "ai_analysis": {
    "source_info": { "source_type": "unity_catalog", "catalog": "...", "schema": "..." },
    "tables": [ ... ],
    "relationships": [ ... ],
    "data_quality": [ { "table": "lease", "issue_type": "null_rate", "detail": "LeaseEndDate 8% nulls" } ],
    "logical_model": {
      "subject_areas": { "Asset": ["property", "unit"], "Party": ["tenant"], "Transaction": ["lease"] },
      "entities": [ { "name": "property", "attributes": [...], "pk": "PropertyID", "fks": [] } ]
    },
    "physical_model": {
      "denormalizations": [ "Embed address columns directly into property for BI queries" ],
      "recommended_indexes": [ "lease(PropertyID)", "lease(TenantID)" ]
    },
    "ddl_hints": [ { "table": "lease", "suggested_ddl_fragment": "ZORDER BY (LeaseStartDate)" } ],
    "modeling_recommendations": [
      "Add a calendar dimension table for lease date range analysis",
      "Consider a status history table for LeaseStatus transitions"
    ]
  }
}
```

### 2. `catalog_discovery_<catalog>_<schema>.sql`

Databricks DDL ready to execute. Contains:

- `CREATE TABLE IF NOT EXISTS` with correct Databricks types
- `NOT NULL` on all PK and FK columns
- `CONSTRAINT pk_<table> PRIMARY KEY (...) NOT ENFORCED` inline in each table
- `ALTER TABLE ... ADD CONSTRAINT fk_... FOREIGN KEY ... NOT ENFORCED` after all creates
- `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL` for key columns
- `OPTIMIZE / ZORDER BY` hints as comments (run after data load)

### 3. `catalog_discovery_<catalog>_<schema>.erwin_notes.txt`

Step-by-step instructions for importing the `.sql` file into ERwin Data Modeler:

```
1. Open ERwin Data Modeler
2. File → Reverse Engineer → From Script
3. Select database: 'Databricks' or 'Generic ANSI SQL'
4. Browse to the generated .sql file
5. ERwin reads CREATE TABLE + CONSTRAINT statements and builds the ERD
```

### 4. `catalog_discovery_<catalog>_<schema>.er_diagram.md`

A Mermaid `erDiagram` that renders automatically on GitHub, GitLab, VS Code (with the Mermaid extension), and Databricks notebooks.

```mermaid
erDiagram
    property {
        bigint PropertyID "PK"
        string PropertyName
        string PropertyType
    }
    lease {
        bigint LeaseID "PK"
        bigint PropertyID "FK"
        bigint TenantID "FK"
        timestamp LeaseStartDate
    }
    tenant {
        bigint TenantID "PK"
        string TenantName
    }
    property ||--o{ lease : "~90%"
    tenant ||--o{ lease : "~90%"
```

The file also includes a **Tables summary table** and a **Relationships table** in plain Markdown below the diagram.

---

## Viewing the ER Diagram

The `.er_diagram.md` file renders anywhere Mermaid is supported:

| Viewer | How to open |
|--------|-------------|
| **GitHub** | Push the file — GitHub renders Mermaid in `.md` files automatically |
| **VS Code** | Install the [Mermaid Preview](https://marketplace.visualstudio.com/items?itemName=bierner.markdown-mermaid) extension, then open the `.md` file |
| **Databricks notebook** | Copy the fenced block into a Markdown cell — rendered automatically |
| **mermaid.live** | Paste the diagram block at [mermaid.live](https://mermaid.live) |
| **GitLab** | Push the file — GitLab renders Mermaid in `.md` files automatically |

---

## DDL and ERwin Import

### Apply the DDL to a Databricks schema

```sql
-- Run catalog_discovery_<catalog>_<schema>.sql in a Databricks SQL editor or notebook
-- First, create the target catalog and schema if they don't exist:
CREATE CATALOG IF NOT EXISTS hackathon_demo;
CREATE SCHEMA IF NOT EXISTS hackathon_demo.snapshot_global_realestate;

-- Then paste / run the generated .sql file
```

### Import into ERwin Data Modeler

1. Open ERwin Data Modeler
2. **File → Reverse Engineer → From Script**
3. Set database dialect: **Databricks** (ERwin 2021+) or **Generic ANSI SQL**
4. Browse to the generated `.sql` file
5. ERwin parses `CREATE TABLE` and `CONSTRAINT` statements and builds the ERD automatically

> **Tip:** The `.sql` file is the correct input for ERwin — the raw JSON cannot be imported directly.

---

## Adding New Sources

`sources.yml` is the single config file for all source databases. Adding an entry here does two things automatically:
- Enables local discovery via the CLI (`discover`, `check-sources`)
- Creates a dedicated Databricks job when you next run `make bundle-deploy`

### Step 1 — Add the source to `sources.yml`

```yaml
sources:
  # Databricks Unity Catalog
  - name: my_uc_prod
    type: unity_catalog
    catalog: prod_catalog
    schema: common_data_model

  # PostgreSQL with SSL
  - name: erp_postgres
    type: postgresql
    schema: public
    sslmode: require             # optional — appended to connection string automatically
    secret_scope: adm
    secret_key: ERP_POSTGRES_CONNECTION_STRING

  # SQL Server
  - name: crm_sqlserver
    type: sqlserver
    schema: dbo
    secret_scope: adm
    secret_key: CRM_SQLSERVER_CONNECTION_STRING

  # Azure SQL
  - name: finance_azuresql
    type: azuresql
    schema: dbo
    secret_scope: adm
    secret_key: FINANCE_AZURESQL_CONNECTION_STRING
```

### Step 2 — Store credentials as Databricks secrets

```bash
# Create the secret scope once (skip if it already exists)
databricks secrets create-scope adm --profile prod

# Store the connection string
databricks secrets put-secret adm ERP_POSTGRES_CONNECTION_STRING \
  --string-value "postgresql+psycopg2://user:pass@host:5432/erp_db" \
  --profile prod

# Update an existing secret with a new value (same command — it's an upsert)
databricks secrets put-secret adm ERP_POSTGRES_CONNECTION_STRING \
  --string-value "postgresql+psycopg2://user:newpass@host:5432/erp_db" \
  --profile prod
```

> **Local dev alternative:** set `ADM_<SECRET_KEY>` as an env var instead of a Databricks secret:
> ```bash
> export ADM_ERP_POSTGRES_CONNECTION_STRING="postgresql+psycopg2://user:pass@host:5432/erp_db"
> ```

### Step 3 — Check connectivity

```bash
make check-sources prod
```

### Step 4 — Deploy (creates the Databricks job automatically)

```bash
make bundle-deploy prod
```

`generate-jobs` runs first, reads `sources.yml`, and writes `resources/source_jobs.yml` with one job per source. Then the bundle deploys everything.

To preview what jobs would be generated without deploying:

```bash
make generate-jobs
cat resources/source_jobs.yml
```

### Credential resolution order (first match wins)

1. `connection_string` field in `sources.yml` — dev only, never commit passwords
2. Databricks secret (`secret_scope` + `secret_key`) — recommended for prod
3. Environment variable `ADM_<secret_key>` — local dev alternative to secrets

### Connection string notes

The connector automatically normalises common URL variants — you don't need to pick the exact dialect manually:

| Input format | Normalised to |
|---|---|
| `postgres://` | `postgresql+psycopg2://` |
| `postgresql://` | `postgresql+psycopg2://` |
| `postgresql+asyncpg://` | `postgresql+psycopg2://` |
| `jdbc:postgresql://` | `postgresql+psycopg2://` |
| `mssql://` | `mssql+pyodbc://` |
| `jdbc:sqlserver://` | `mssql+pyodbc://` |

For **SQL Server / Azure SQL**, append `?driver=ODBC+Driver+17+for+SQL+Server` to the connection string.

### Secrets in Databricks jobs

`{{secrets/scope/key}}` in `python_wheel_task` parameters is **not resolved** by Databricks (only notebook/SQL tasks resolve secrets this way). The connector detects the unresolved reference pattern and reads the secret directly via the Databricks SDK instead — no change needed in `sources.yml`.

Prerequisite: the job's running principal must have at least `READ` on the secret scope:

```bash
databricks secrets put-acl adm datamodeling_hackathon READ --profile prod
```

### WSL2 / local PostgreSQL

If PostgreSQL runs on your Windows laptop and your code runs in WSL2, `localhost` won't work. Use the Windows host IP instead:

```bash
WIN_HOST=$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}')
export ADM_ERP_POSTGRES_CONNECTION_STRING="postgresql+psycopg2://user:pass@${WIN_HOST}:5432/mydb"
```

Add to `~/.bashrc` to persist (the IP changes on each WSL restart).

---

## Troubleshooting

### `ERROR_UNSUPPORTED_PYTHON_VERSION` on job run

The wheel requires Python ≥ 3.10. Databricks serverless runs Python 3.10. If you see this error it means the `requires-python` constraint in the wheel is stricter than the runtime. Check `version.txt` and rebuild:

```bash
make build
make bundle-deploy prod
```

If the old wheel is cached (same filename), bump the version in `version.txt` (e.g. `0.0.2` → `0.0.3`) before rebuilding to force a fresh environment install.

### `SystemExit: The connection string is an unresolved Databricks secret reference`

The Databricks secret scope or key doesn't exist, or the job principal lacks READ permission.

```bash
# Verify the secret exists
databricks secrets list-secrets adm --profile prod

# Grant READ to the job's group
databricks secrets put-acl adm datamodeling_hackathon READ --profile prod
```

### `OperationalError: Connection timed out` to Azure PostgreSQL

The Databricks serverless compute cannot reach the database. This is a network/firewall issue:

- **Azure PostgreSQL**: go to Azure Portal → PostgreSQL server → Networking → add a firewall rule for the Databricks workspace egress IPs, or enable **Allow public access from Azure services**.
- **Private endpoint only**: the server is not publicly reachable; use Azure Private Link or VNet peering between Databricks and the PostgreSQL VNet.

To diagnose from a Databricks notebook:

```python
import subprocess
r = subprocess.run(["nc", "-zv", "-w", "3", "<host>", "5432"], capture_output=True, text=True)
print(r.stderr)
# "Connection refused" → reachable but blocked at PostgreSQL level
# "timed out"         → not reachable at all (firewall / private endpoint)
```

### `ModuleNotFoundError: No module named 'openai'`

The `discover` entry point is running from the system Python, not your virtual environment.

```bash
# Option 1: install into the system Python
/path/to/python -m pip install openai

# Option 2: run via the venv Python directly
source .venv/bin/activate
discover --source unity_catalog ...
```

### `INVALID_PARAMETER_VALUE: Content in ChatMessage must have type String or List[ContentItem]`

The serving endpoint received a malformed message. This was a bug in an earlier version — update to the latest code and re-install:

```bash
git pull
pip install -e ".[dev]"
```

### `error: the following arguments are required: --source`

`--source` is a required argument. Always pass it:

```bash
discover --source unity_catalog --catalog ... --schema ...
```

Valid values: `unity_catalog`, `postgresql`, `sqlserver`, `azuresql`.

### Serving endpoint returns 404 or 403

```bash
# Verify the endpoint exists and is in a READY state
databricks serving-endpoints get --name databricks-claude-opus-4-8 --profile prod

# Verify your token has CAN_QUERY permission on the endpoint
databricks serving-endpoints get-permission-levels \
  --serving-endpoint-name databricks-claude-opus-4-8 --profile prod
```

### `ai_analysis` is null in the JSON

The AI agent requires both `DATABRICKS_TOKEN` and `DATABRICKS_HOST` to be set. If either is missing, the AI analysis step is skipped silently. Structural output (tables, relationships, profiles, DDL, ER diagram) is still written correctly.

### Warehouse ID

To list available SQL Warehouses:

```bash
databricks warehouses list --profile prod
```

Pass the ID with `--warehouse-id <id>` if the auto-selected warehouse is wrong.

---

## Project Structure

```
agentic-datamodeling/
├── README.md                              ← this file
├── CLAUDE.md                              ← developer context for Claude Code
├── databricks.yml                         ← DAB config — targets dev / prod
├── pyproject.toml                         ← package config, deps, entry points
├── Makefile                               ← dev shortcuts (make help for full list)
├── run.sh                                 ← build / test / release automation
├── version.txt                            ← semver (read by pyproject.toml)
├── test_endpoint.py                       ← smoke test for the serving endpoint
├── sources.yml.example                    ← multi-source config template
├── sources.yml                            ← your sources (git-ignored)
│
├── scripts/
│   └── generate_source_jobs.py            ← generates resources/source_jobs.yml from sources.yml
│
├── resources/
│   ├── agentic_datamodeling.yml           ← main pipeline job (handcrafted — uses `main` entry point)
│   └── source_jobs.yml                    ← AUTO-GENERATED from sources.yml — do not edit manually
│
└── src/
    └── adm/                               ← Python package
        ├── main.py                        ← CLI entry points: discover, ddl
        ├── catalog/
        │   ├── crawler.py                 ← CatalogCrawler — schema crawl orchestrator
        │   ├── profiler.py                ← 10-row samples, column stats, AI descriptions
        │   ├── relationships.py           ← RelationshipDetector (explicit FK + inferred)
        │   ├── registry.py                ← SourceRegistry — multi-source config
        │   └── sources/
        │       ├── base.py                ← SourceConnector abstract base class
        │       ├── unity_catalog.py       ← Databricks Unity Catalog connector
        │       └── jdbc.py                ← PostgreSQL / SQL Server / Azure SQL
        ├── agents/
        │   └── catalog_agent.py           ← 5-phase AI agent (OpenAI tool-use loop)
        └── ddl/
            └── generator.py               ← DDL + ERwin notes + Mermaid ER diagram
```

---

## Make Commands

Run `make help` to see all commands with descriptions.

### Bundle

| Command | Description |
|---------|-------------|
| `make generate-jobs` | Generate `resources/source_jobs.yml` from `sources.yml` (preview without deploying) |
| `make bundle-validate dev` | Validate DAB config for `dev` target |
| `make bundle-validate prod` | Validate DAB config for `prod` target |
| `make bundle-deploy dev` | Generate jobs + build wheel + deploy to `dev` |
| `make bundle-deploy prod` | Generate jobs + build wheel + deploy to `prod` |
| `make bundle-destroy prod` | Remove bundle-managed jobs (**Model Serving endpoint is NOT affected**) |

### Source Connectivity

| Command | Description |
|---------|-------------|
| `make check-sources dev` | Ping all sources in `sources.yml` against the `dev` profile |
| `make check-sources prod` | Ping all sources in `sources.yml` against the `prod` profile |

### Development

| Command | Description |
|---------|-------------|
| `make install` | Install dev dependencies into the active Python environment |
| `make lint` | Run pre-commit hooks (black, isort, flake8, mypy) |
| `make test` | Run all unit tests |
| `make test-quick` | Run tests excluding slow markers |
| `make build` | Build the Python wheel |
| `make clean` | Remove build artifacts |
