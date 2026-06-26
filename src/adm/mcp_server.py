"""MCP server — exposes Agentic Data Modeling tools to Claude.ai / Claude desktop.

Caching strategy:
  - Every fresh crawl is saved to ~/.adm_cache/{key}__crawl.json
    AND writes all 4 output files (JSON, SQL, ERwin notes, Mermaid ER diagram)
    to ~/adm-outputs/YYYY-MM-DD/HH-MM-SS/
  - All tools (discover, ER diagram, relationships, table info) read from the
    crawl cache by default — no repeat trips to Databricks/PostgreSQL.
  - run_ai_analysis has its own cache (~/.adm_cache/{key}.json).
  - Pass force_refresh=True to any tool to bypass the cache and re-crawl.

Supports:
  - Databricks Unity Catalog  (catalog + schema)
  - PostgreSQL / SQL Server   (connection_string + schema, or PG_CONNECTION_STRING env var)
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Data Modeling",
    instructions=(
        "You can query Databricks Unity Catalog and PostgreSQL/SQL Server databases. "
        "For Unity Catalog: provide catalog + schema. "
        "For PostgreSQL: provide connection_string + schema (or rely on PG_CONNECTION_STRING env var). "
        "All tools cache results — set force_refresh=True only when the user explicitly asks for "
        "a new or fresh crawl/analysis."
    ),
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LOCAL_OUTPUT_ROOT = os.path.expanduser(os.environ.get("ADM_OUTPUT_ROOT", "~/adm-outputs"))
_WORKSPACE_OUTPUT_ROOT = "/Workspace/Shared/hackathon/agentic-datamodeling/outputs"


def _cache_dir() -> Path:
    d = Path(os.environ.get("ADM_CACHE_DIR", os.path.expanduser("~/.adm_cache")))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def _resolve_backend(
    source: str,
    catalog: str | None,
) -> tuple[str | None, str | None]:
    """
    Resolve (catalog, connection_string) based on the requested source.

    source values:
      "auto"        — catalog arg or DATABRICKS_CATALOG env var present → Databricks;
                      otherwise → PG_CONNECTION_STRING
      "databricks"  — always use DATABRICKS_CATALOG + token (ignore PG)
      "postgresql"  — always use PG_CONNECTION_STRING (ignore DATABRICKS_CATALOG)
    """
    if source == "postgresql":
        pg = os.environ.get("PG_CONNECTION_STRING")
        if not pg:
            raise ValueError(
                "PG_CONNECTION_STRING is not set in the MCP server environment. "
                "Add it to the startup script."
            )
        return None, pg

    if source == "databricks":
        cat = catalog or os.environ.get("DATABRICKS_CATALOG")
        if not cat:
            raise ValueError(
                "No Databricks catalog provided and DATABRICKS_CATALOG env var is not set."
            )
        return cat, None

    # "auto": explicit catalog wins; else PG if available; else DATABRICKS_CATALOG
    explicit_catalog = catalog or os.environ.get("DATABRICKS_CATALOG")
    pg = os.environ.get("PG_CONNECTION_STRING")
    if explicit_catalog:
        return explicit_catalog, None
    if pg:
        return None, pg
    raise ValueError(
        "No data source configured. Set DATABRICKS_CATALOG or PG_CONNECTION_STRING "
        "in the MCP server startup script."
    )


def _resolve_connection_string(cs: str | None, catalog: str | None = None) -> str | None:
    """Only falls back to PG_CONNECTION_STRING when no catalog is given."""
    if cs:
        return cs
    if catalog:
        return None  # catalog present → Unity Catalog, never fall back to PG
    return os.environ.get("PG_CONNECTION_STRING") or None


def _resolve_catalog(catalog: str | None) -> str | None:
    """Fall back to DATABRICKS_CATALOG env var when not passed explicitly."""
    return catalog or os.environ.get("DATABRICKS_CATALOG") or None


def _resolve_warehouse_id(warehouse_id: str | None) -> str | None:
    """Fall back to WAREHOUSE_ID env var when not passed explicitly."""
    return warehouse_id or os.environ.get("WAREHOUSE_ID") or None


def _cache_key(schema: str, catalog: str | None, connection_string: str | None) -> str:
    """Single stable string key for this source+schema combination."""
    if connection_string:
        h = hashlib.md5(connection_string.encode()).hexdigest()[:8]
        m = re.search(r"/([^/?]+)(?:\?|$)", connection_string)
        db = m.group(1) if m else "jdbc"
        return f"pg_{db}_{h}__{schema}"
    return f"{catalog or 'unknown'}__{schema}"


def _crawl_cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}__crawl.json"


def _analysis_cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


# ---------------------------------------------------------------------------
# Saved-file lookup — ~/adm-outputs is the primary cache
# ---------------------------------------------------------------------------


def _output_file_prefix(source_type: str, catalog: str, schema: str) -> str:
    """Consistent filename prefix used both when saving and when searching."""
    if source_type in ("postgresql", "sqlserver", "azuresql"):
        return f"{source_type}_{catalog}_{schema}"
    return f"databricks_{catalog}_{schema}"


def _find_latest_saved_file(
    schema: str,
    catalog: str | None,
    connection_string: str | None,
) -> tuple[Path | None, datetime | None]:
    """
    Scan ~/adm-outputs for the most recent JSON saved for this source+schema.

    Checks both naming conventions:
      databricks_{catalog}_{schema}.json  (Unity Catalog)
      postgresql_*_{schema}.json          (PostgreSQL — any db name)
      catalog_discovery_{catalog}_{schema}.json  (legacy CLI naming)
    """
    roots = [Path(_LOCAL_OUTPUT_ROOT)]
    if Path(_WORKSPACE_OUTPUT_ROOT).is_dir():
        roots.append(Path(_WORKSPACE_OUTPUT_ROOT))

    is_pg = bool(connection_string)
    patterns = []

    if is_pg:
        # Match any postgresql/sqlserver file for this schema
        patterns += [f"postgresql_*_{schema}.json", f"sqlserver_*_{schema}.json"]
    else:
        cat = catalog or "*"
        patterns += [
            f"databricks_{cat}_{schema}.json",
            f"catalog_discovery_{cat}_{schema}.json",  # legacy
        ]

    best_path: Path | None = None
    best_mtime: datetime | None = None

    for root in roots:
        for pattern in patterns:
            for match in root.rglob(pattern):
                mtime = datetime.fromtimestamp(match.stat().st_mtime)
                if best_mtime is None or mtime > best_mtime:
                    best_path, best_mtime = match, mtime

    return best_path, best_mtime


def _load_saved_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _rels_from_dicts(rel_dicts: list) -> list:
    """Reconstruct Relationship objects from saved dict list."""
    from adm.catalog.relationships import Relationship
    return [
        Relationship(
            child_table=r["child_table"],
            child_column=r["child_column"],
            parent_table=r["parent_table"],
            parent_column=r["parent_column"],
            relationship_type=r.get("type", r.get("relationship_type", "inferred_name")),
            confidence=r.get("confidence", 0.7),
            child_schema=r.get("child_schema", ""),
            parent_schema=r.get("parent_schema", ""),
            constraint_name=r.get("constraint_name"),
        )
        for r in rel_dicts
    ]


def _save_output_files(metadata: dict, relationships: list) -> str:
    """Write all 4 output files to ~/adm-outputs/ and return the folder path."""
    from adm.ddl.generator import generate_from_file

    ts = datetime.now()
    folder = Path(_LOCAL_OUTPUT_ROOT) / ts.strftime("%Y-%m-%d") / ts.strftime("%H-%M-%S")
    folder.mkdir(parents=True, exist_ok=True)

    source_type = metadata.get("source_type", "unity_catalog")
    catalog = metadata.get("catalog") or "unknown"
    schema = metadata.get("schema") or "unknown"
    prefix = _output_file_prefix(source_type, catalog, schema)
    json_path = folder / f"{prefix}.json"

    report = {
        "source_type": source_type,
        "catalog": catalog,
        "schema": schema,
        "tables": metadata.get("tables", []),
        "foreign_keys": metadata.get("foreign_keys", []),
        "relationships": [r.to_dict() for r in relationships],
        "profiles": {},
        "ai_analysis": None,
    }
    json_path.write_text(json.dumps(report, indent=2, default=str))

    try:
        generate_from_file(report_path=str(json_path))
    except Exception:
        pass

    return str(folder)


# ---------------------------------------------------------------------------
# Core crawl — saved files first, live crawl only when needed
# ---------------------------------------------------------------------------


def _get_or_crawl(
    schema: str,
    catalog: str | None,
    connection_string: str | None,
    warehouse_id: str | None,
    force_refresh: bool,
) -> tuple[dict, list, str, bool]:
    """
    Return (metadata, relationships, cached_at_str, from_cache).

    Cache priority (when force_refresh=False):
      1. Most recent file in ~/adm-outputs/ (databricks_* or postgresql_*)
      2. ~/.adm_cache/{key}__crawl.json  (fast in-session fallback)

    On a fresh crawl: saves all 4 files to ~/adm-outputs/ + updates crawl cache.
    """
    catalog = _resolve_catalog(catalog)
    warehouse_id = _resolve_warehouse_id(warehouse_id)
    connection_string = _resolve_connection_string(connection_string, catalog)
    key = _cache_key(schema, catalog, connection_string)

    if not force_refresh:
        # ── 1. Check ~/adm-outputs/ ──────────────────────────────────────
        saved_path, saved_mtime = _find_latest_saved_file(schema, catalog, connection_string)
        if saved_path and saved_mtime:
            data = _load_saved_file(saved_path)
            if data and data.get("tables"):
                rels = _rels_from_dicts(data.get("relationships", []))
                cached_at = saved_mtime.strftime("%Y-%m-%d %H:%M:%S")
                return data, rels, cached_at, True

        # ── 2. Check ~/.adm_cache/ crawl cache ───────────────────────────
        cp = _crawl_cache_path(key)
        if cp.exists():
            try:
                data = json.loads(cp.read_text())
                if data.get("tables"):
                    rels = _rels_from_dicts(data.get("relationships", []))
                    return data, rels, data.get("cached_at", ""), True
            except Exception:
                pass

    # ── 3. Fresh crawl from Databricks / PostgreSQL ───────────────────────
    from adm.catalog.crawler import CatalogCrawler
    from adm.catalog.relationships import RelationshipDetector

    if connection_string:
        cs_lower = connection_string.lower()
        src = "postgresql" if ("postgresql" in cs_lower or "postgres" in cs_lower) else "sqlserver"
        crawler = CatalogCrawler.from_jdbc(src, connection_string, schema)
    else:
        if not catalog:
            raise ValueError("No catalog specified and DATABRICKS_CATALOG env var not set.")
        crawler = CatalogCrawler.from_unity_catalog(
            catalog=catalog, schema=schema, warehouse_id=warehouse_id or None
        )

    metadata = crawler.crawl()
    relationships = RelationshipDetector().detect_all(metadata)

    # Save all 4 output files
    _save_output_files(metadata, relationships)

    # Also update the fast crawl cache
    cp = _crawl_cache_path(key)
    cp.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "source_type": metadata.get("source_type"),
        "catalog": metadata.get("catalog"),
        "schema": metadata.get("schema"),
        "tables": metadata.get("tables", []),
        "foreign_keys": metadata.get("foreign_keys", []),
        "relationships": [r.to_dict() for r in relationships],
    }, indent=2, default=str))

    cached_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return metadata, relationships, cached_at, False


# ---------------------------------------------------------------------------
# AI analysis cache
# ---------------------------------------------------------------------------


def _load_ai_cache(key: str, catalog: str | None) -> dict | None:
    """Check MCP cache and CLI outputs for a stored AI analysis."""
    candidates: list[tuple[datetime, str, dict]] = []

    cp = _analysis_cache_path(key)
    if cp.exists():
        try:
            data = json.loads(cp.read_text())
            ts = datetime.fromisoformat(data.get("cached_at", "1970-01-01"))
            candidates.append((ts, "mcp_cache", data))
        except Exception:
            pass

    # Also scan CLI outputs for Unity Catalog sources
    if catalog:
        schema = key.split("__")[-1]
        roots = [_LOCAL_OUTPUT_ROOT]
        if os.path.isdir(_WORKSPACE_OUTPUT_ROOT):
            roots.append(_WORKSPACE_OUTPUT_ROOT)
        for root in roots:
            pattern = os.path.join(root, "**", f"catalog_discovery_{catalog}_{schema}.json")
            for match in glob.glob(pattern, recursive=True):
                p = Path(match)
                try:
                    data = json.loads(p.read_text())
                    if data.get("ai_analysis"):
                        mtime = datetime.fromtimestamp(p.stat().st_mtime)
                        candidates.append((mtime, f"cli:{p}", data))
                except Exception:
                    pass

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    ts, src, data = candidates[0]
    return {"cached_at": ts.strftime("%Y-%m-%d %H:%M:%S"), "source": src, "data": data}


def _save_ai_cache(key: str, schema: str, catalog: str | None, metadata: dict, relationships: list, analysis) -> None:
    cp = _analysis_cache_path(key)
    cp.write_text(json.dumps({
        "catalog": catalog,
        "schema": schema,
        "cached_at": datetime.now().isoformat(),
        "source_type": metadata.get("source_type"),
        "tables": metadata.get("tables", []),
        "relationships": [r.to_dict() for r in relationships],
        "ai_analysis": analysis,
    }, indent=2, default=str))


# ---------------------------------------------------------------------------
# Tool 1 — list_schemas
# ---------------------------------------------------------------------------


@mcp.tool()
def list_schemas(
    source: str = "auto",
    catalog: Optional[str] = None,
) -> str:
    """List all schemas available in the configured data source.

    Credentials are read from server environment variables — never ask the user for them.

    Args:
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default)
                to let the server decide based on environment variables.
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
    """
    try:
        catalog, connection_string = _resolve_backend(source, catalog)
        if connection_string:
            from adm.catalog.crawler import CatalogCrawler
            cs_lower = connection_string.lower()
            src = "postgresql" if ("postgresql" in cs_lower or "postgres" in cs_lower) else "sqlserver"
            crawler = CatalogCrawler.from_jdbc(src, connection_string, "public")
            rows = crawler.execute_sql(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "ORDER BY schema_name"
            )
            names = [r.get("schema_name", r.get("SCHEMA_NAME", "")) for r in rows]
            return json.dumps({"source": src, "schemas": names, "count": len(names)}, indent=2)

        if not catalog:
            return json.dumps({"error": "No catalog configured. Set DATABRICKS_CATALOG env var or pass catalog name."})

        from databricks.sdk import WorkspaceClient
        schemas = list(WorkspaceClient().schemas.list(catalog_name=catalog))
        names = [s.name for s in schemas]
        return json.dumps({"catalog": catalog, "schemas": names, "count": len(names)}, indent=2)

    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 2 — discover_schema
# ---------------------------------------------------------------------------


@mcp.tool()
def discover_schema(
    schema: str,
    source: str = "auto",
    catalog: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Crawl a database schema and return all tables, columns, PKs, FKs, and relationships.

    Results are cached — subsequent calls return instantly from saved files in ~/adm-outputs/.
    A fresh crawl also saves JSON + DDL + ERwin notes + Mermaid ER diagram.

    Credentials are read from server environment variables — never ask the user for them.

    Args:
        schema: Schema name (e.g. 'public', 'dbo', 'sales').
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default).
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
        force_refresh: Set True only when the user explicitly asks for a new / fresh crawl.
    """
    try:
        catalog, connection_string = _resolve_backend(source, catalog)
        metadata, relationships, cached_at, from_cache = _get_or_crawl(
            schema, catalog, connection_string, None, force_refresh
        )
        tables_summary = [
            {
                "table": t["name"],
                "columns": [c["name"] for c in t["columns"]],
                "primary_keys": t.get("primary_keys", []),
                "column_types": {c["name"]: c.get("type", "unknown") for c in t["columns"]},
            }
            for t in metadata["tables"]
        ]
        rels_summary = [
            {
                "child": f"{r.child_table}.{r.child_column}" if hasattr(r, "child_table") else f"{r['child_table']}.{r['child_column']}",
                "parent": f"{r.parent_table}.{r.parent_column}" if hasattr(r, "parent_table") else f"{r['parent_table']}.{r['parent_column']}",
                "type": r.relationship_type if hasattr(r, "relationship_type") else r["type"],
                "confidence": round(r.confidence if hasattr(r, "confidence") else r["confidence"], 2),
            }
            for r in relationships
        ]
        return json.dumps({
            "cached": from_cache,
            "cached_at": cached_at,
            "source_type": metadata.get("source_type"),
            "catalog": catalog,
            "schema": schema,
            "table_count": len(tables_summary),
            "relationship_count": len(rels_summary),
            "tables": tables_summary,
            "relationships": rels_summary,
        }, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 3 — get_er_diagram
# ---------------------------------------------------------------------------


@mcp.tool()
def get_er_diagram(
    schema: str,
    source: str = "auto",
    catalog: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Generate a Mermaid ER diagram for a database schema.

    Results are cached — returns instantly on repeat calls.
    Paste the output into mermaid.live to visualise.

    Credentials are read from server environment variables — never ask the user for them.

    Args:
        schema: Schema name.
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default).
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
        force_refresh: Set True only when the user explicitly asks for a new / fresh diagram.
    """
    try:
        from adm.ddl.generator import generate_mermaid_er_diagram

        catalog, connection_string = _resolve_backend(source, catalog)
        metadata, relationships, cached_at, from_cache = _get_or_crawl(
            schema, catalog, connection_string, None, force_refresh
        )
        rels_dicts = [r.to_dict() if hasattr(r, "to_dict") else r for r in relationships]
        report = {
            "catalog": catalog or schema,
            "schema": schema,
            "tables": metadata["tables"],
            "relationships": rels_dicts,
        }
        diagram = generate_mermaid_er_diagram(report)
        note = f"\n\n> *{'Cached' if from_cache else 'Fresh'} as of {cached_at}*"
        return diagram + note
    except Exception as exc:
        return f"Error generating ER diagram: {exc}"


# ---------------------------------------------------------------------------
# Tool 4 — get_relationships
# ---------------------------------------------------------------------------


@mcp.tool()
def get_relationships(
    schema: str,
    source: str = "auto",
    catalog: Optional[str] = None,
    table_name: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Get all table relationships (explicit FK + inferred from column naming).

    Results are cached — returns instantly on repeat calls.

    Credentials are read from server environment variables — never ask the user for them.

    Args:
        schema: Schema name.
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default).
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
        table_name: Filter to relationships involving this table only.
        force_refresh: Set True only when the user explicitly asks for fresh data.
    """
    try:
        catalog, connection_string = _resolve_backend(source, catalog)
        metadata, relationships, cached_at, from_cache = _get_or_crawl(
            schema, catalog, connection_string, None, force_refresh
        )
        rels = [r.to_dict() if hasattr(r, "to_dict") else r for r in relationships]
        if table_name:
            tl = table_name.lower()
            rels = [r for r in rels if r["child_table"].lower() == tl or r["parent_table"].lower() == tl]

        return json.dumps({
            "cached": from_cache,
            "cached_at": cached_at,
            "source_type": metadata.get("source_type"),
            "schema": schema,
            "filter_table": table_name,
            "relationship_count": len(rels),
            "relationships": rels,
        }, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 5 — get_table_info
# ---------------------------------------------------------------------------


@mcp.tool()
def get_table_info(
    schema: str,
    table_name: str,
    source: str = "auto",
    catalog: Optional[str] = None,
    sample_rows: int = 5,
    force_refresh: bool = False,
) -> str:
    """Get detailed metadata for a specific table: columns, PKs, relationships, and sample rows.

    Schema metadata is cached — only sample rows are fetched live.

    Credentials are read from server environment variables — never ask the user for them.

    Args:
        schema: Schema name.
        table_name: Table to inspect.
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default).
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
        sample_rows: Number of sample rows to return (default 5, max 20).
        force_refresh: Set True only when the user explicitly asks for fresh data.
    """
    try:
        catalog, connection_string = _resolve_backend(source, catalog)
        metadata, relationships, cached_at, from_cache = _get_or_crawl(
            schema, catalog, connection_string, None, force_refresh
        )

        table = next((t for t in metadata["tables"] if t["name"].lower() == table_name.lower()), None)
        if table is None:
            return json.dumps({
                "error": f"Table '{table_name}' not found in '{schema}'",
                "available_tables": [t["name"] for t in metadata["tables"]],
            })

        rels = [r.to_dict() if hasattr(r, "to_dict") else r for r in relationships]
        tl = table_name.lower()
        related = [r for r in rels if r["child_table"].lower() == tl or r["parent_table"].lower() == tl]

        # Sample rows are always live (small, fast)
        n = min(max(1, sample_rows), 20)
        ref = f"{schema}.{table_name}" if connection_string else f"{catalog}.{schema}.{table_name}"
        try:
            from adm.catalog.crawler import CatalogCrawler
            if connection_string:
                cs_lower = connection_string.lower()
                src = "postgresql" if ("postgresql" in cs_lower or "postgres" in cs_lower) else "sqlserver"
                crawler = CatalogCrawler.from_jdbc(src, connection_string, schema)
            else:
                crawler = CatalogCrawler.from_unity_catalog(catalog=catalog, schema=schema, warehouse_id=_resolve_warehouse_id(None))
            samples = crawler.sample_data(ref, n)
        except Exception:
            samples = []

        return json.dumps({
            "cached": from_cache,
            "cached_at": cached_at,
            "source_type": metadata.get("source_type"),
            "schema": schema,
            "table": table["name"],
            "columns": table["columns"],
            "primary_keys": table.get("primary_keys", []),
            "relationships": related,
            "sample_data": samples,
        }, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 6 — run_ai_analysis
# ---------------------------------------------------------------------------


@mcp.tool()
def run_ai_analysis(
    schema: str,
    source: str = "auto",
    catalog: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Run the full AI-powered data modeling pipeline (logical model, physical model,
    data quality, recommendations).

    Both the crawl and AI analysis are cached — returns instantly by default.
    Set force_refresh=True only when the user explicitly asks for a new / fresh analysis.

    All credentials are read from server environment variables — never ask the user for them.

    Args:
        schema: Schema name.
        source: Which backend to query. Use "postgresql" for PostgreSQL,
                "databricks" for Databricks Unity Catalog, or "auto" (default).
        catalog: Databricks catalog name (databricks source only). Leave blank to use default.
        force_refresh: Re-run AI analysis even if cached results exist.
    """
    from adm.agents.catalog_agent import CatalogAgent

    catalog, connection_string = _resolve_backend(source, catalog)
    key = _cache_key(schema, catalog, connection_string)

    # --- Check AI analysis cache ---
    if not force_refresh:
        cached = _load_ai_cache(key, catalog)
        if cached and cached["data"].get("ai_analysis"):
            ai = cached["data"]["ai_analysis"]
            return json.dumps({
                "cached": True,
                "cached_at": cached["cached_at"],
                "note": f"Stored analysis from {cached['cached_at']}. Ask for 'new analysis' to regenerate.",
                "ai_analysis": ai,
                "table_count": len(cached["data"].get("tables", [])),
                "relationship_count": len(cached["data"].get("relationships", [])),
            }, indent=2, default=str)

    if not os.environ.get("DATABRICKS_TOKEN"):
        return json.dumps({"error": "DATABRICKS_TOKEN not set in MCP server environment."})

    try:
        # Crawl (uses crawl cache too)
        metadata, relationships, _, _ = _get_or_crawl(
            schema, catalog, connection_string, None, force_refresh
        )

        # Run AI agent
        warehouse_id = _resolve_warehouse_id(None)
        from adm.catalog.crawler import CatalogCrawler
        if connection_string:
            cs_lower = connection_string.lower()
            src = "postgresql" if ("postgresql" in cs_lower or "postgres" in cs_lower) else "sqlserver"
            crawler = CatalogCrawler.from_jdbc(src, connection_string, schema)
        else:
            crawler = CatalogCrawler.from_unity_catalog(catalog=catalog, schema=schema, warehouse_id=warehouse_id)

        raw = CatalogAgent(crawler=crawler).run()

        analysis = None
        m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            try:
                analysis = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        if analysis is None:
            try:
                analysis = json.loads(raw.strip())
            except json.JSONDecodeError:
                analysis = raw

        _save_ai_cache(key, schema, catalog, metadata, relationships, analysis)

        return json.dumps({
            "cached": False,
            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "Fresh analysis completed and cached.",
            "ai_analysis": analysis,
            "table_count": len(metadata["tables"]),
            "relationship_count": len(relationships),
        }, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agentic Data Modeling MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"Starting MCP SSE server on http://{args.host}:{args.port}", file=sys.stderr)
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
