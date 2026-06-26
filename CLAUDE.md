# Agentic Data Modeling — CLAUDE.md

This file is the primary context document for Claude Code when working in this project.
Read it fully before making any changes.

---

## Project Purpose

Enterprise AI data modeling platform built on Databricks. The system uses AI agents to:
1. Crawl Unity Catalog schemas and discover table relationships
2. Sample 10 rows per table and compute column-level statistics
3. Generate AI descriptions for every table and column
4. Draft logical data models in 3NF using business intake requirements
5. Generate physical data models with denormalization recommendations
6. Produce Databricks DDL with PK/FK constraints for deployment
7. Generate ERwin-importable SQL for ERD reverse engineering

**Target users:** Engineering teams (data ingestion), Product/BI stakeholders (curated data)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Platform | Databricks (Azure) |
| Deployment | Databricks Asset Bundles (DAB) |
| Language | Python 3.12+ |
| Package manager | `uv` + `pip` |
| AI agent | Claude via Databricks Model Serving (`openai` SDK, OpenAI-compatible endpoint) |
| Catalog access | `databricks-sdk` (`WorkspaceClient`) |
| SQL execution | Databricks Statement Execution API |
| Task runner | `make` (wraps `run.sh`) |

---

## Repository Layout

```
agentic-datamodeling/
├── CLAUDE.md                                  ← this file
├── README.md                                  ← user-facing setup guide
├── databricks.yml                             ← DAB root config (targets: dev/prod)
├── pyproject.toml                             ← package config, deps, entry points
├── Makefile                                   ← dev shortcuts
├── run.sh                                     ← build/test/release automation
├── version.txt                                ← semver, read by pyproject.toml
├── test_endpoint.py                           ← smoke test for Databricks serving endpoint
├── sources.yml.example                        ← multi-source config template
├── sources.yml                                ← your sources (git-ignored)
│
├── scripts/
│   └── generate_source_jobs.py                ← generates resources/source_jobs.yml from sources.yml
│
├── resources/
│   ├── agentic_datamodeling.yml               ← main pipeline job (handcrafted)
│   └── source_jobs.yml                        ← AUTO-GENERATED — one job per sources.yml entry
│
└── src/
    └── adm/                                   ← main Python package (adm)
        ├── __init__.py
        ├── main.py                            ← CLI entry points: discover, ddl; _parse_ai_analysis()
        ├── catalog/
        │   ├── __init__.py
        │   ├── crawler.py                     ← CatalogCrawler — schema crawl orchestrator
        │   ├── profiler.py                    ← MetadataProfiler — 10-row samples + stats + AI descriptions
        │   ├── relationships.py               ← RelationshipDetector + Relationship dataclass
        │   ├── registry.py                    ← SourceRegistry — multi-source config via sources.yml
        │   └── sources/
        │       ├── __init__.py               ← create_connector() factory
        │       ├── base.py                   ← SourceConnector abstract base class
        │       ├── unity_catalog.py          ← Databricks Unity Catalog (SDK + Statement Execution)
        │       └── jdbc.py                   ← PostgreSQL / SQL Server / Azure SQL (SQLAlchemy)
        ├── agents/
        │   ├── __init__.py
        │   └── catalog_agent.py              ← CatalogAgent (5-phase OpenAI-compatible tool-use loop)
        ├── mcp_server.py                     ← FastMCP server — 6 tools, Claude Desktop integration
        ├── ddl/
        │   ├── __init__.py
        │   └── generator.py                  ← Databricks DDL + ERwin notes generator
        └── (future modules here)

src/notebooks/
└── catalog_discovery.py                       ← Databricks notebook (interactive)

tests/
├── conftest.py
├── consts.py
├── fixtures/
└── unit_tests/
```

---

## Databricks Bundle (DAB) Configuration

### Targets

| Target | Profile (`~/.databrickscfg`) | Mode | Default |
|--------|------------------------------|------|---------|
| `dev` | `dev-azara` | development | yes |
| `uat` | `uat` | development | no |
| `prod` | `prod` | production | no |

`prod` target has `workspace.root_path` explicitly set (required for `mode: production`).

### Variables (defined in `databricks.yml`)

| Variable | Description | Override per target |
|----------|-------------|---------------------|
| `catalog` | Unity Catalog catalog name | yes |
| `schema` | Unity Catalog schema name | yes |
| `warehouse_id` | SQL Warehouse ID for SQL tasks | optional |
| `serving_endpoint` | Databricks Model Serving endpoint name for the LLM | yes |

**Before first deploy:** set correct `catalog`, `schema`, and `serving_endpoint` per target in `databricks.yml`. Confirm the endpoint exists: `databricks serving-endpoints list --profile <profile>`.

### Bundle commands

```bash
make bundle-validate dev     # validate config for target
make bundle-validate prod
make bundle-deploy dev       # build wheel + deploy resources
make bundle-deploy prod
```

### Jobs

`sources.yml` is the single source of truth for discovery jobs. `make bundle-deploy` runs `scripts/generate_source_jobs.py` first, which writes `resources/source_jobs.yml` with one job per source entry — no manual job YAML needed.

| Resource file | Managed by | Entry point | Purpose |
|--------------|-----------|-------------|---------|
| `resources/source_jobs.yml` | **Auto-generated** from `sources.yml` | `discover` | One job per source — crawl, profile, AI analysis, DDL |
| `resources/agentic_datamodeling.yml` | Handcrafted | `main` | Main pipeline (different entry point) |

Job names follow the pattern `[{target}] Discover — {source_name}`.

All jobs use **serverless compute** (`environment_key: adm_env`, `spec.client: "1"`). The wheel is listed in `environments.spec.dependencies`, NOT in task-level `libraries` (which is for classic job clusters only).

```bash
# Run a generated discovery job
databricks bundle run discover_unity_prod -t prod
databricks bundle run discover_erp_postgres -t prod

# Run the main pipeline job
databricks bundle run agentic_datamodeling -t prod

# Regenerate source_jobs.yml without deploying
python scripts/generate_source_jobs.py
```

> `resources/source_jobs.yml` is auto-generated — never edit it manually. Edit `sources.yml` instead and re-run `make bundle-deploy`.

---

## Python Package (`adm`)

### Entry points (`pyproject.toml [project.scripts]`)

| Script | Function | Purpose |
|--------|----------|---------|
| `main` | `adm.main:main` | Top-level CLI dispatcher (`main discover ...`, `main ddl ...`) |
| `discover` | `adm.main:discover` | Shortcut to `main discover` — used by DAB jobs |

### CLI — `discover` subcommand

```bash
discover \
  --source {unity_catalog,postgresql,sqlserver,azuresql} \   # REQUIRED
  --catalog <catalog>          \   # unity_catalog only
  --schema  <schema>           \
  --warehouse-id <id>          \   # optional, unity_catalog only
  --connection-string <url>    \   # JDBC sources; or set ADM_CONNECTION_STRING env var
  --output-path <path>             # optional — defaults to timestamped workspace folder
```

Default output root (when `--output-path` is omitted):
```
/Workspace/Shared/hackathon/agentic-datamodeling/outputs/YYYY-MM-DD/HH-MM-SS/
```

### CLI — `ddl` subcommand

Generate Databricks DDL and ERwin notes from an existing discovery JSON:

```bash
main ddl <report.json> \
  --target-catalog <catalog>   \   # optional override
  --output-sql <path.sql>          # optional — defaults to <report>.sql
```

### Dependencies (`pyproject.toml`)

| Package | Group | Purpose |
|---------|-------|---------|
| `databricks-sdk>=0.20.0` | core | Unity Catalog, SQL execution, workspace APIs |
| `openai>=1.0.0` | core | OpenAI-compatible client for Databricks Model Serving |
| `sqlalchemy>=2.0.0` | core | JDBC connector — dialect-agnostic DB access |
| `pyyaml>=6.0` | core | YAML parsing for registry/config |
| `psycopg2-binary>=2.9` | `[postgresql]` | PostgreSQL driver |
| `pyodbc>=4.0` | `[sqlserver]` | SQL Server / Azure SQL driver |

---

## Pipeline Flow

```
discover (CLI / DAB job)
    │
    ├─ CatalogCrawler.crawl()            → tables, columns, FK constraints
    ├─ RelationshipDetector.detect_all() → explicit + inferred relationships
    ├─ profiler.enrich_metadata()        → 10 sample rows + column stats + AI descriptions
    ├─ CatalogAgent.run()                → 5-phase AI analysis (logical/physical model)
    ├─ _parse_ai_analysis()              → extract structured JSON from agent response string
    │
    ├─ Write catalog_discovery.json          → full enriched report (ai_analysis is a dict)
    ├─ Write catalog_discovery.sql           → Databricks DDL (via ddl.generator)
    ├─ Write catalog_discovery.erwin_notes.txt
    └─ Write catalog_discovery.er_diagram.md → Mermaid erDiagram (via ddl.generator)
```

---

## Module Reference

### `adm/catalog/crawler.py` — `CatalogCrawler`

Thin orchestrator — delegates all source operations to the active connector.

```python
crawler = CatalogCrawler.from_unity_catalog(catalog="my_cat", schema="my_schema")
crawler = CatalogCrawler.from_jdbc("postgresql", connection_string="...", schema="public")

metadata = crawler.crawl()
```

`crawl()` returns:
```python
{
  "source_type": str,         # unity_catalog | postgresql | sqlserver | azuresql
  "catalog": str,
  "schema": str | None,
  "tables": [{ "name", "schema", "full_name", "table_type", "comment", "columns", "primary_keys" }],
  "foreign_keys": [{ "child_schema", "child_table", "child_column",
                     "parent_schema", "parent_table", "parent_column", "constraint_name" }]
}
```

### `adm/catalog/profiler.py` — `enrich_metadata()`

Enriches the crawl output with per-table samples, column statistics, and AI-generated descriptions.

```python
from adm.catalog.profiler import enrich_metadata

metadata = enrich_metadata(
    crawler=crawler,
    metadata=metadata,
    sample_n=10,
    generate_ai_descriptions=True,   # requires DATABRICKS_TOKEN + DATABRICKS_HOST
)
```

Adds a `profiles` key to metadata:
```python
{
  "profiles": {
    "property": {
      "sample_rows": [ {...}, ... ],
      "column_profiles": {
        "PropertyID": {
          "null_rate": 0.0, "n_distinct": 50,
          "min": 1.0, "max": 50.0, "mean": 25.5,
          "sample_values": ["1", "2", "3", "4", "5"]
        },
        ...
      },
      "table_description": "Stores physical real estate property records.",
      "column_descriptions": {
        "PropertyID": "Unique identifier for each property.",
        ...
      }
    }
  }
}
```

AI descriptions are generated by calling the configured serving endpoint with a structured prompt per table. Falls back gracefully if `DATABRICKS_TOKEN`/`DATABRICKS_HOST` are not set.

### `adm/catalog/relationships.py` — `RelationshipDetector`

| Type | Confidence | How |
|------|-----------|-----|
| `explicit_fk` | 1.00 | Unity Catalog FK constraints via `information_schema` |
| `inferred_name` | 0.70–0.90 | Column naming: `orders.customer_id → customers.id` |

Naming heuristics (in order): `{col}` == `{other_table}_id`, `{other_table}id`, singular form, stripped suffix.

### `adm/agents/catalog_agent.py` — `CatalogAgent`

5-phase Claude agent using OpenAI-compatible tool-use via Databricks Model Serving.

**Phases:** Discovery → Data Profiling → Logical Model (3NF) → Physical Model → JSON Output

**Tools:** `list_tables`, `get_relationships`, `get_table_stats`, `check_duplicates`, `sample_data`, `execute_sql`

```python
agent = CatalogAgent.for_unity_catalog(catalog="my_cat", schema="my_schema")
report = agent.run()   # returns structured JSON string
```

**Required env vars:**

| Variable | Description |
|---|---|
| `DATABRICKS_HOST` | Workspace URL, e.g. `https://adb-xxx.azuredatabricks.net` |
| `DATABRICKS_TOKEN` | PAT or service principal token |
| `SERVING_ENDPOINT` | Endpoint name (default: `databricks-claude-opus-4-8`) |

On Databricks jobs, `DATABRICKS_HOST` and `DATABRICKS_TOKEN` are injected automatically.

Output: the agent returns a markdown string that may embed a ` ```json ``` ` block. `_parse_ai_analysis()` in `main.py` extracts and parses that block so the final JSON stores a proper dict with keys: `source_info`, `tables`, `relationships`, `data_quality`, `logical_model`, `physical_model`, `ddl_hints`, `modeling_recommendations`.

### `adm/ddl/generator.py` — DDL + ERwin notes + Mermaid diagram

Generates Databricks DDL, ERwin notes, and a Mermaid ER diagram from a discovery report.

```python
from adm.ddl.generator import generate_from_file, generate_mermaid_er_diagram

# Generate all three output files at once
sql_path, notes_path, mermaid_path = generate_from_file(
    report_path="catalog_discovery.json",
    target_catalog="hackathon_demo",   # optional override
    output_sql="output.sql",           # optional — defaults to <report>.sql
    output_mermaid="output.er_diagram.md",  # optional — defaults to <report>.er_diagram.md
)

# Generate just the Mermaid diagram string
diagram_md = generate_mermaid_er_diagram(report_dict)
```

`generate_from_file()` returns a **3-tuple** `(sql_path, notes_path, mermaid_path)`.

DDL includes:
- `CREATE TABLE IF NOT EXISTS` with Databricks types and `NOT NULL` on PK/FK columns
- `CONSTRAINT pk_<table> PRIMARY KEY (...) NOT ENFORCED` inline
- `ALTER TABLE ... ADD CONSTRAINT fk_... FOREIGN KEY ... NOT ENFORCED`
- `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL`
- OPTIMIZE/ZORDER hints as comments

Mermaid output (`er_diagram.md`) contains:
- A fenced `erDiagram` block — renders on GitHub, VS Code (Mermaid extension), Databricks notebooks, mermaid.live
- A Tables summary table (name, column count, PK, FK counts)
- A Relationships table (child/parent table+column, type, confidence)

Type overrides: `MonthlyRent` (and similar currency columns) → `DECIMAL(18,2)`.
Column names with spaces are auto-escaped with backticks.

**ERwin import:** File → Reverse Engineer → From Script → select `.sql` → dialect: Databricks (2021+) or Generic ANSI SQL.

### `adm/mcp_server.py` — MCP Server (Claude Desktop)

FastMCP server exposing 6 tools so Claude Desktop can trigger the full pipeline conversationally.

**Entry point:** `python -m adm.mcp_server` (stdio transport, default) or `--transport sse --port 8000`.

**Tools:**

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `list_schemas` | `source`, `catalog` | List schemas in Databricks or PostgreSQL |
| `discover_schema` | `schema`, `source`, `catalog`, `force_refresh` | Crawl schema; save 4 output files |
| `get_er_diagram` | `schema`, `source`, `catalog`, `force_refresh` | Return Mermaid ER diagram |
| `get_relationships` | `schema`, `source`, `catalog`, `table_name`, `force_refresh` | List FK + inferred relationships |
| `get_table_info` | `schema`, `table_name`, `source`, `catalog`, `sample_rows`, `force_refresh` | Column metadata + sample rows |
| `run_ai_analysis` | `schema`, `source`, `catalog`, `force_refresh` | Full AI pipeline (logical/physical model) |

**`source` parameter routing:**

| Value | Behaviour |
|-------|-----------|
| `"auto"` | Uses `DATABRICKS_CATALOG` env var if set; else `PG_CONNECTION_STRING` |
| `"postgresql"` | Always uses `PG_CONNECTION_STRING`; ignores Databricks settings |
| `"databricks"` | Always uses `DATABRICKS_CATALOG` + `DATABRICKS_TOKEN` |

**Credential resolution (never ask the user):**

| Env var | Used for |
|---------|---------|
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_TOKEN` | Databricks PAT |
| `DATABRICKS_CATALOG` | Default Unity Catalog catalog name |
| `WAREHOUSE_ID` | Default SQL Warehouse (optional — auto-selected if unset) |
| `SERVING_ENDPOINT` | Model Serving endpoint name |
| `PG_CONNECTION_STRING` | PostgreSQL connection string (`postgresql+psycopg2://...`) |

**Cache priority (when `force_refresh=False`):**
1. Most recent `~/adm-outputs/**/{source}_{catalog}_{schema}.json` — file saved by a prior crawl
2. `~/.adm_cache/{key}__crawl.json` — fast in-session crawl cache
3. Live crawl from Databricks / PostgreSQL (saves files + updates cache)

**Output file naming:**
- Databricks: `~/adm-outputs/YYYY-MM-DD/HH-MM-SS/databricks_{catalog}_{schema}.*`
- PostgreSQL: `~/adm-outputs/YYYY-MM-DD/HH-MM-SS/postgresql_{dbname}_{schema}.*`

**Claude Desktop config (Windows + WSL2):**
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

The `-d Ubuntu-20.04` flag is required — without it, WSL defaults to its default distro (often Docker Desktop on machines with Docker installed).

---

## Secrets

No Databricks secrets are required. The Anthropic API key is embedded in the Model Serving endpoint configuration (`anthropic_api_key_plaintext`) and is managed entirely by Databricks. Application code only uses `DATABRICKS_TOKEN` (a Databricks PAT), which on jobs is injected automatically by the runtime.

---

## Authentication

- **Local:** set `DATABRICKS_CONFIG_PROFILE=<profile>` and `DATABRICKS_TOKEN=<pat>` env vars
- **On cluster/job:** Databricks runtime injects `DATABRICKS_HOST` and `DATABRICKS_TOKEN` automatically

---

## Development Workflow

```bash
make install           # install dev dependencies
make lint              # pre-commit hooks (black, isort, flake8, mypy)
make test              # unit tests
make build             # build wheel
make bundle-validate dev
make bundle-deploy dev
```

Code quality: `black` (line length 119), `isort`, `flake8`, `mypy`.

---

## Adding a New Source Connector

1. Create `src/adm/catalog/sources/<name>.py` implementing all `SourceConnector` abstract methods
2. Register it in `sources/__init__.py` `create_connector()` factory
3. No changes needed to `CatalogCrawler`, `RelationshipDetector`, `CatalogAgent`, or `profiler`

## Adding New Agents

1. Create `src/adm/agents/<name>_agent.py` following the OpenAI tool-use pattern in `catalog_agent.py`
2. Add a CLI subcommand in `main.py` (`sub.add_parser(...)` + `set_defaults(func=...)`)
3. Add entry point in `pyproject.toml [project.scripts]`
4. Add a job resource in `resources/<name>_job.yml`
5. Run `make bundle-validate dev` to confirm

---

## Known Constraints

- `information_schema` FK/PK tables are populated only if constraints were explicitly declared in Unity Catalog. Most ingested tables will not have them — rely on `inferred_name` relationships.
- Serverless jobs require Databricks Runtime serverless to be enabled on the workspace. Serverless runs **Python 3.10** — `requires-python` in `pyproject.toml` must be `>=3.10` or the wheel will be rejected at install time.
- `prod` DAB target requires `workspace.root_path` to be set (already configured).
- Cannot set permissions on the `admins` group for jobs — use named groups or users only.
- `wheel` path in environment dependencies uses `${workspace.root_path}/artifacts/.internal/*.whl`. Do NOT use `${workspace.file_path}` — that resolves to `root_path/files/`, not `root_path/artifacts/`.
- `{{secrets/scope/key}}` in `python_wheel_task.parameters` is **not resolved** by Databricks (only notebook/SQL tasks get secret substitution). `_resolve_secret_ref()` in `main.py` detects the unresolved pattern and reads the secret via `WorkspaceClient().secrets.get_secret()` at runtime.
- The `environment_variables` field is **not** in the DABs bundle schema for task objects — the CLI drops it silently. Use parameters + `_resolve_secret_ref()` for secrets instead.
- Secret scope `adm` must grant at least `READ` to the job's running principal (`datamodeling_hackathon` group has READ in prod).
- When bumping `version.txt`, the wheel filename changes (e.g. `0.0.1` → `0.0.2`), forcing the serverless environment to reinstall. If you deploy the same version twice, the cached environment may run old code.
- Outbound internet from the Databricks workspace to `api.anthropic.com` is not required — all LLM calls go through the internal Model Serving endpoint.
- `--source` is a required argument for the `discover` CLI command. Valid values: `unity_catalog`, `postgresql`, `sqlserver`, `azuresql`.
- AI descriptions and AI agent analysis are skipped silently if no Databricks credentials are available. Credential resolution order: `DATABRICKS_TOKEN`+`DATABRICKS_HOST` env vars → SDK auto-detection (`~/.databrickscfg` via `DATABRICKS_CONFIG_PROFILE`). The JSON, DDL, and ER diagram are still written; `ai_analysis` will be null and `table_description`/`column_descriptions` will be empty.
- Every run produces **4 output files**: `.json`, `.sql`, `.erwin_notes.txt`, `.er_diagram.md` — all in the same timestamped folder.
- `generate_from_file()` returns a **3-tuple** `(sql_path, notes_path, mermaid_path)` — update any callers if you extend it.
- `resources/source_jobs.yml` is auto-generated by `scripts/generate_source_jobs.py` — never edit manually; edit `sources.yml` and re-run `make bundle-deploy`.
- `make bundle-deploy` automatically runs `make generate-jobs` first — sources.yml is the single source of truth for all discovery jobs.
- `JDBCConnector._normalise()` rewrites these prefixes to the correct SQLAlchemy dialect: `postgres://`, `postgresql://`, `postgresql+asyncpg://`, `jdbc:postgresql://` → `postgresql+psycopg2://`; `mssql://`, `jdbc:sqlserver://` → `mssql+pyodbc://`. psycopg2 is required for `sslmode` support.
- On WSL2, `localhost` refers to the Linux VM, not the Windows host — use the nameserver IP from `/etc/resolv.conf` to reach Windows-hosted databases.
- MCP server tools have no `connection_string` or `warehouse_id` parameters — these are resolved exclusively from env vars in the startup script. Never add them back to tool signatures or Claude will ask users for credentials.
- MCP `source="auto"` defaults to Databricks when `DATABRICKS_CATALOG` is set, even if `PG_CONNECTION_STRING` is also set. Users must say "use PostgreSQL" or "query Postgres" to steer to `source="postgresql"`.
- MCP server uses stdio transport by default (required for Claude Desktop). Logs go to stderr — they are invisible during normal operation but visible when running the script directly in a terminal. Pass `--transport sse --port 8000` to run as an HTTP server for debugging.
- `_resolve_backend(source, catalog)` is the single routing function for all 6 tools — always call it first to get `(catalog, connection_string)`. Never call `_resolve_catalog` / `_resolve_connection_string` directly in tool bodies.
- Saved output files in `~/adm-outputs/` are the primary cache. `~/.adm_cache/` is a secondary crawl-only cache. AI analysis results are cached separately in `~/.adm_cache/{key}.json`.
