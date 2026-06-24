"""Table and column profiler — samples data, computes stats, generates AI descriptions."""

from __future__ import annotations

import json
import os
from typing import Any

from adm.catalog.crawler import CatalogCrawler

# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def _col_stats(col_name: str, rows: list[dict]) -> dict:
    """Compute basic column statistics from sample rows."""
    values = [r.get(col_name) for r in rows]
    non_null = [v for v in values if v is not None]
    total = len(values)

    stats: dict[str, Any] = {
        "null_count": total - len(non_null),
        "null_rate": round((total - len(non_null)) / total, 4) if total else 0,
        "n_distinct": len(set(str(v) for v in non_null)),
        "sample_values": list(dict.fromkeys(str(v) for v in non_null))[:5],
    }

    # Numeric range
    numeric = []
    for v in non_null:
        try:
            numeric.append(float(v))
        except (TypeError, ValueError):
            pass

    if numeric:
        stats["min"] = min(numeric)
        stats["max"] = max(numeric)
        stats["mean"] = round(sum(numeric) / len(numeric), 4)

    return stats


def _profile_table(crawler: CatalogCrawler, table: dict, n: int = 10) -> dict:
    """Sample n rows and compute per-column stats for one table."""
    catalog = table.get("full_name", "").split(".")[0] or ""
    schema = table["schema"]
    tname = table["name"]

    if crawler.connector.source_type == "unity_catalog":
        table_ref = f"`{catalog}`.`{schema}`.`{tname}`"
    else:
        table_ref = f"{schema}.{tname}"

    try:
        rows = crawler.sample_data(table_ref, n)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "sample_rows": [], "column_profiles": {}}

    col_profiles = {col["name"]: _col_stats(col["name"], rows) for col in table["columns"]}

    return {
        "sample_rows": rows,
        "column_profiles": col_profiles,
    }


# ---------------------------------------------------------------------------
# AI description generator
# ---------------------------------------------------------------------------


def _build_description_prompt(table: dict, profile: dict) -> str:
    """Build a prompt asking Claude to describe the table and each column."""
    col_info = []
    for col in table["columns"]:
        cname = col["name"]
        ctype = col["type"]
        stats = profile.get("column_profiles", {}).get(cname, {})
        samples = stats.get("sample_values", [])
        col_info.append(f"  - {cname} ({ctype}): sample values = {samples}")

    sample_rows = profile.get("sample_rows", [])[:3]

    return f"""You are a data catalog expert. Given the following table from a real estate database,
generate concise descriptions for the table itself and each column.

Table: {table['name']}
Columns:
{chr(10).join(col_info)}

Sample rows (up to 3):
{json.dumps(sample_rows, indent=2, default=str)}

Respond ONLY with a JSON object in this exact format:
{{
  "table_description": "<one sentence describing what this table stores>",
  "column_descriptions": {{
    "<column_name>": "<one sentence description>",
    ...
  }}
}}"""


def _generate_descriptions(table: dict, profile: dict) -> dict:
    """Call the LLM to generate table and column descriptions."""
    from openai import OpenAI

    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "")
    endpoint = os.environ.get("SERVING_ENDPOINT", "databricks-claude-opus-4-8")

    if not host or not token:
        return {"table_description": None, "column_descriptions": {}}

    client = OpenAI(api_key=token, base_url=f"{host}/serving-endpoints")

    try:
        response = client.chat.completions.create(
            model=endpoint,
            messages=[{"role": "user", "content": _build_description_prompt(table, profile)}],
            max_tokens=1024,
        )
        raw = response.choices[0].message.content or ""
        # Extract JSON block if wrapped in markdown
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        return json.loads(raw.strip())
    except Exception as exc:  # noqa: BLE001
        return {"table_description": f"(description generation failed: {exc})", "column_descriptions": {}}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enrich_metadata(
    crawler: CatalogCrawler,
    metadata: dict,
    sample_n: int = 10,
    generate_ai_descriptions: bool = True,
) -> dict:
    """
    Enrich a crawl metadata dict with samples, column stats, and AI descriptions.

    Adds a 'profiles' key to metadata:
    {
      "table_name": {
        "sample_rows": [...],
        "column_profiles": { "col": {null_rate, n_distinct, sample_values, min, max, mean} },
        "table_description": "...",
        "column_descriptions": { "col": "..." }
      }
    }
    """
    tables = metadata.get("tables", [])
    profiles: dict[str, dict] = {}

    for table in tables:
        tname = table["name"]
        print(f"  Profiling {tname} (sampling {sample_n} rows) ...", flush=True)

        profile = _profile_table(crawler, table, n=sample_n)

        if generate_ai_descriptions:
            descriptions = _generate_descriptions(table, profile)
            profile["table_description"] = descriptions.get("table_description")
            profile["column_descriptions"] = descriptions.get("column_descriptions", {})
        else:
            profile["table_description"] = None
            profile["column_descriptions"] = {}

        profiles[tname] = profile

    metadata["profiles"] = profiles
    return metadata
