"""CLI entry points for the Agentic Data Modeling pipeline."""

from __future__ import annotations

import argparse
import json
import re
import sys

_WORKSPACE_OUTPUT_ROOT = "/Workspace/Shared/hackathon/agentic-datamodeling/outputs"


def _parse_ai_analysis(raw: str | None) -> dict | str | None:
    """Extract and parse the JSON block from the AI agent's raw response string.

    The agent returns a markdown string that may embed a ```json ... ``` block.
    If found, parse it and return the dict so the output JSON is properly structured.
    Falls back to the raw string when no JSON block is present.
    """
    if not raw:
        return raw
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # No embedded JSON block — try parsing the whole string as JSON
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    return raw


def _build_output_path(catalog: str, schema: str, base_path: str | None) -> str:
    """Return a timestamped output path under the workspace outputs folder."""
    from datetime import datetime
    import os

    if base_path:
        os.makedirs(os.path.dirname(base_path) or ".", exist_ok=True)
        return base_path

    ts = datetime.now()
    folder = os.path.join(
        _WORKSPACE_OUTPUT_ROOT,
        ts.strftime("%Y-%m-%d"),
        ts.strftime("%H-%M-%S"),
    )
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"catalog_discovery_{catalog}_{schema}.json")


def _discover(args: argparse.Namespace) -> None:
    """Crawl a source database, detect relationships, and run AI data model analysis."""
    import os
    from adm.catalog.crawler import CatalogCrawler
    from adm.catalog.relationships import RelationshipDetector
    from adm.agents.catalog_agent import CatalogAgent

    # ------------------------------------------------------------------
    # Build the crawler from source type + connection args
    # ------------------------------------------------------------------
    source_type = args.source.lower()

    if source_type == "unity_catalog":
        if not args.catalog:
            raise SystemExit("--catalog is required for unity_catalog source.")
        crawler = CatalogCrawler.from_unity_catalog(
            catalog=args.catalog,
            schema=args.schema or None,
            warehouse_id=args.warehouse_id or None,
        )
    elif source_type in ("postgresql", "sqlserver", "azuresql"):
        conn_str = args.connection_string or os.environ.get("ADM_CONNECTION_STRING")
        if not conn_str:
            raise SystemExit(
                "--connection-string (or ADM_CONNECTION_STRING env var) is required "
                f"for source '{source_type}'."
            )
        if not args.schema:
            raise SystemExit(f"--schema is required for source '{source_type}'.")
        crawler = CatalogCrawler.from_jdbc(source_type, conn_str, args.schema)
    else:
        raise SystemExit(
            f"Unknown --source '{args.source}'. "
            "Supported: unity_catalog, postgresql, sqlserver, azuresql"
        )

    # ------------------------------------------------------------------
    # Crawl + detect relationships
    # ------------------------------------------------------------------
    metadata = crawler.crawl()
    detector = RelationshipDetector()
    relationships = detector.detect_all(metadata)

    print(f"\nRelationships found: {len(relationships)}")
    for r in relationships:
        print(
            f"  [{r.relationship_type}] "
            f"{r.child_table}.{r.child_column} → {r.parent_table}.{r.parent_column} "
            f"(conf={r.confidence:.0%})"
        )

    # ------------------------------------------------------------------
    # Sample + profile every table
    # ------------------------------------------------------------------
    from adm.catalog.profiler import enrich_metadata

    has_llm = bool(os.environ.get("DATABRICKS_TOKEN") and os.environ.get("DATABRICKS_HOST"))
    print("\nProfiling tables (10 rows each) ...")
    metadata = enrich_metadata(
        crawler=crawler,
        metadata=metadata,
        sample_n=10,
        generate_ai_descriptions=has_llm,
    )

    # ------------------------------------------------------------------
    # AI agent analysis + data model (requires DATABRICKS_TOKEN)
    # ------------------------------------------------------------------
    ai_report = None
    if has_llm:
        print("\nRunning AI agent — discovery + data model building ...")
        agent = CatalogAgent(crawler=crawler)
        ai_report = agent.run()
        print(ai_report)
    else:
        print("\nSkipping AI analysis — set DATABRICKS_TOKEN and DATABRICKS_HOST to enable.")

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------
    output = {
        "source_type": metadata.get("source_type"),
        "catalog": metadata.get("catalog"),
        "schema": metadata.get("schema"),
        "tables": metadata["tables"],
        "profiles": metadata.get("profiles", {}),
        "relationships": [r.to_dict() for r in relationships],
        "ai_analysis": _parse_ai_analysis(ai_report),
    }

    output_path = _build_output_path(
        catalog=metadata.get("catalog", "unknown"),
        schema=metadata.get("schema", "unknown"),
        base_path=args.output_path,
    )

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults written to   : {output_path}")

    # Auto-generate DDL + Mermaid ER diagram alongside the JSON
    from adm.ddl.generator import generate_from_file
    sql_path, notes_path, mermaid_path = generate_from_file(report_path=output_path)
    print(f"DDL written to       : {sql_path}")
    print(f"ERwin notes written  : {notes_path}")
    print(f"ER diagram written   : {mermaid_path}")


def _check(args: argparse.Namespace) -> None:
    """Ping all sources in sources.yml and report connectivity status."""
    from adm.catalog.registry import SourceRegistry

    registry = SourceRegistry(config_path=args.config)

    sources = registry.list_sources()
    if not sources:
        print("No sources defined in sources.yml.")
        return

    if args.source:
        sources = [s for s in sources if s.name == args.source]
        if not sources:
            raise SystemExit(f"Source '{args.source}' not found in sources.yml.")

    print(f"\nChecking {len(sources)} source(s) ...\n")
    results = registry.check_all() if not args.source else _check_subset(registry, sources)

    # Summary table
    print("\n" + "-" * 70)
    print(f"  {'NAME':<25} {'TYPE':<15} {'STATUS':<8}  DETAIL")
    print("-" * 70)
    all_ok = True
    for r in results:
        status = "OK" if r["ok"] else "FAILED"
        if not r["ok"]:
            all_ok = False
        print(f"  {r['name']:<25} {r['source_type']:<15} {status:<8}  {r['detail']}")
    print("-" * 70)

    if not all_ok:
        raise SystemExit("\nOne or more sources failed connectivity check.")
    print("\nAll sources connected successfully.")


def _check_subset(registry, sources) -> list[dict]:
    """Ping a filtered subset of sources."""
    results = []
    for defn in sources:
        print(f"  Checking '{defn.name}' ({defn.source_type}) ...", end=" ", flush=True)
        try:
            connector = registry.create_connector(defn.name)
            result = connector.ping()
            result["name"] = defn.name
        except Exception as exc:  # noqa: BLE001
            result = {
                "name": defn.name,
                "ok": False,
                "source_type": defn.source_type,
                "catalog": defn.catalog or "",
                "schema": defn.schema or "",
                "detail": str(exc),
            }
        print("OK" if result["ok"] else "FAILED")
        results.append(result)
    return results


def _ddl(args: argparse.Namespace) -> None:
    """Generate Databricks DDL and ERwin import notes from a discovery JSON report."""
    from adm.ddl.generator import generate_from_file

    sql_path, notes_path, mermaid_path = generate_from_file(
        report_path=args.report,
        target_catalog=args.target_catalog,
        output_sql=args.output_sql,
    )
    print(f"DDL written to       : {sql_path}")
    print(f"ERwin notes written  : {notes_path}")
    print(f"ER diagram written   : {mermaid_path}")


def _run(args: argparse.Namespace) -> None:
    """Run the data modeling pipeline."""
    print("Starting Agentic Data Modeling pipeline")
    print(f"Target: {args.catalog}.{args.schema}")
    # TODO: implement full pipeline logic


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adm",
        description="Agentic Data Modeling CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- discover -------------------------------------------------------
    discover_p = sub.add_parser(
        "discover",
        help="Crawl a source database, detect relationships, and build a data model",
    )
    discover_p.add_argument(
        "--source",
        required=True,
        choices=["unity_catalog", "postgresql", "sqlserver", "azuresql"],
        help="Source database type",
    )
    # Unity Catalog args
    discover_p.add_argument("--catalog", default=None, help="Catalog name (unity_catalog only)")
    discover_p.add_argument("--warehouse-id", default=None, dest="warehouse_id", help="SQL Warehouse ID (unity_catalog only)")
    # JDBC args
    discover_p.add_argument(
        "--connection-string",
        default=None,
        dest="connection_string",
        help="SQLAlchemy connection string for JDBC sources. "
             "Can also be set via ADM_CONNECTION_STRING env var.",
    )
    # Common
    discover_p.add_argument("--schema", default=None, help="Schema / namespace to crawl")
    discover_p.add_argument(
        "--output-path",
        default=None,
        dest="output_path",
        help=(
            "Path to write JSON output. Defaults to a timestamped folder under "
            f"{_WORKSPACE_OUTPUT_ROOT}/YYYY-MM-DD/HH-MM-SS/"
        ),
    )
    discover_p.set_defaults(func=_discover)

    # ---- check ----------------------------------------------------------
    check_p = sub.add_parser(
        "check",
        help="Ping all sources defined in sources.yml to verify connectivity",
    )
    check_p.add_argument(
        "--config",
        default="sources.yml",
        help="Path to sources.yml (default: ./sources.yml)",
    )
    check_p.add_argument(
        "--source",
        default=None,
        help="Check a single named source instead of all",
    )
    check_p.set_defaults(func=_check)

    # ---- ddl ------------------------------------------------------------
    ddl_p = sub.add_parser(
        "ddl",
        help="Generate Databricks DDL + ERwin import notes from a discovery JSON report",
    )
    ddl_p.add_argument("report", help="Path to catalog_discovery JSON file")
    ddl_p.add_argument(
        "--target-catalog",
        default=None,
        dest="target_catalog",
        help="Override the catalog name in the generated DDL",
    )
    ddl_p.add_argument(
        "--output-sql",
        default=None,
        dest="output_sql",
        help="Path for the output .sql file (default: <report>.sql)",
    )
    ddl_p.set_defaults(func=_ddl)

    # ---- run ------------------------------------------------------------
    run_p = sub.add_parser("run", help="Run the data modeling pipeline")
    run_p.add_argument("--catalog", required=True)
    run_p.add_argument("--schema", required=True)
    run_p.set_defaults(func=_run)

    return parser


def main() -> None:
    """Top-level CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


def discover() -> None:
    """Wheel entry point that delegates to the adm discover subcommand."""
    sys.argv = ["adm", "discover"] + sys.argv[1:]
    main()


if __name__ == "__main__":
    main()
