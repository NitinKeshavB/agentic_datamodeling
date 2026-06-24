"""Catalog discovery + data modeling AI agent — OpenAI-compatible tool-use loop."""

from __future__ import annotations

import json
import os

from openai import OpenAI

from adm.catalog.crawler import CatalogCrawler
from adm.catalog.relationships import (
    Relationship,
    RelationshipDetector,
)
from adm.catalog.sources import SourceConnector

# OpenAI-compatible tool format (works with Databricks Model Serving external models)
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": (
                "List all tables and their columns (name, type, nullable, comment, primary keys) "
                "in the target source database."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_relationships",
            "description": (
                "Return all detected table relationships — explicit FK constraints "
                "and relationships inferred from column naming conventions "
                "(e.g. orders.customer_id → customers.id). Includes confidence score per relationship."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_table_stats",
            "description": "Get row count and per-column null rates for a table.",
            "parameters": {
                "type": "object",
                "properties": {"table_name": {"type": "string", "description": "Unqualified table name"}},
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_duplicates",
            "description": "Check for duplicate rows based on specified key columns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "key_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns that should form a unique key",
                    },
                },
                "required": ["table_name", "key_columns"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sample_data",
            "description": "Fetch sample rows from a table to validate inferred relationships and data patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "n": {"type": "integer", "description": "Number of rows (default 5)", "default": 5},
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute a custom SQL query for ad-hoc analysis.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a senior data architect specialising in enterprise data modelling.

You are given access to a source database (Unity Catalog, PostgreSQL, SQL Server, or Azure SQL).
Your job is to:

PHASE 1 — Discovery
  - Enumerate all tables and columns
  - Identify explicit FK relationships and infer implicit ones from column naming
  - Identify candidate primary keys where none are declared

PHASE 2 — Data Profiling
  - Check row counts and null rates for key tables
  - Detect duplicates on primary key candidates
  - Sample data to validate inferred relationships

PHASE 3 — Logical Data Model (3NF)
  - Design a normalised logical data model incorporating discovered tables and relationships
  - Apply Third Normal Form: eliminate transitive dependencies, ensure each non-key attribute
    depends only on the primary key
  - Group tables into subject areas (e.g. Party, Location, Asset, Financial, Reference)

PHASE 4 — Physical Data Model
  - Propose denormalisation where needed for BI/reporting performance
  - Suggest indexes on FK columns and high-cardinality filter columns

PHASE 5 — Output
  Produce a final structured JSON report with these top-level keys:
    source_info        : {source_type, catalog, schema}
    tables             : [{name, columns, primary_key, subject_area}]
    relationships      : [{child_table, child_column, parent_table, parent_column, type, confidence}]
    data_quality       : [{table, issue_type, detail}]
    logical_model      : {subject_areas: {name: [table_names]}, entities: [{name, attributes, pk, fks}]}
    physical_model     : {denormalizations: [...], recommended_indexes: [...]}
    ddl_hints          : [{table, suggested_ddl_fragment}]
    modeling_recommendations : [strings]
"""

_DEFAULT_SERVING_ENDPOINT = "databricks-claude-opus-4-8"


class CatalogAgent:
    """AI agent that crawls any source database and builds an enterprise data model.

    Supports: Unity Catalog, PostgreSQL, SQL Server, Azure SQL.
    Uses Databricks Model Serving (OpenAI-compatible) as the LLM backend.
    """

    def __init__(
        self,
        crawler: CatalogCrawler | None = None,
        connector: SourceConnector | None = None,
        model: str | None = None,
    ):
        if crawler is None and connector is None:
            raise ValueError("Provide either a CatalogCrawler or a SourceConnector.")
        self.crawler = crawler or CatalogCrawler(connector)
        self.detector = RelationshipDetector()

        host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
        token = os.environ.get("DATABRICKS_TOKEN", "")
        self.client = OpenAI(
            api_key=token,
            base_url=f"{host}/serving-endpoints",
        )
        self.model = model or os.environ.get("SERVING_ENDPOINT", _DEFAULT_SERVING_ENDPOINT)
        self._metadata: dict | None = None
        self._relationships: list[Relationship] | None = None

    # ------------------------------------------------------------------
    # Convenience factory methods
    # ------------------------------------------------------------------

    @classmethod
    def for_unity_catalog(
        cls,
        catalog: str,
        schema: str | None = None,
        warehouse_id: str | None = None,
        model: str | None = None,
    ) -> "CatalogAgent":
        """Create an agent targeting a Databricks Unity Catalog schema."""
        return cls(crawler=CatalogCrawler.from_unity_catalog(catalog, schema, warehouse_id), model=model)

    @classmethod
    def for_postgresql(
        cls,
        connection_string: str,
        schema: str = "public",
        model: str | None = None,
    ) -> "CatalogAgent":
        """Create an agent targeting a PostgreSQL database."""
        return cls(crawler=CatalogCrawler.from_jdbc("postgresql", connection_string, schema), model=model)

    @classmethod
    def for_sqlserver(
        cls,
        connection_string: str,
        schema: str = "dbo",
        model: str | None = None,
    ) -> "CatalogAgent":
        """Create an agent targeting SQL Server or Azure SQL."""
        return cls(crawler=CatalogCrawler.from_jdbc("sqlserver", connection_string, schema), model=model)

    # ------------------------------------------------------------------
    # Cached metadata
    # ------------------------------------------------------------------

    def _get_metadata(self) -> dict:
        if self._metadata is None:
            self._metadata = self.crawler.crawl()
        return self._metadata

    def _get_relationships(self) -> list[Relationship]:
        if self._relationships is None:
            self._relationships = self.detector.detect_all(self._get_metadata())
        return self._relationships

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _dispatch_tool(self, name: str, inputs: dict) -> str:
        """Execute a tool call and return the result as a JSON string."""
        try:
            meta = self._get_metadata()
            connector = self.crawler.connector
            schema = meta.get("schema") or ""
            catalog = meta.get("catalog") or ""

            def _full_ref(table_name: str) -> str:
                if connector.source_type == "unity_catalog":
                    return f"`{catalog}`.`{schema}`.`{table_name}`"
                return f"{schema}.{table_name}"

            if name == "list_tables":
                return json.dumps(meta["tables"], default=str)

            if name == "get_relationships":
                return json.dumps([r.to_dict() for r in self._get_relationships()], default=str)

            if name == "get_table_stats":
                return json.dumps(self.crawler.get_table_stats(_full_ref(inputs["table_name"])), default=str)

            if name == "check_duplicates":
                return json.dumps(
                    self.crawler.check_duplicates(_full_ref(inputs["table_name"]), inputs["key_columns"]),
                    default=str,
                )

            if name == "sample_data":
                return json.dumps(
                    self.crawler.sample_data(_full_ref(inputs["table_name"]), inputs.get("n", 5)),
                    default=str,
                )

            if name == "execute_sql":
                return json.dumps(self.crawler.execute_sql(inputs["sql"]), default=str)

            return json.dumps({"error": f"Unknown tool: {name}"})

        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})

    # ------------------------------------------------------------------
    # Agentic loop
    # ------------------------------------------------------------------

    def run(self, prompt: str | None = None) -> str:
        """Run the full discovery + data modelling agent loop.

        Returns a structured JSON report string.
        """
        meta = self._get_metadata()

        if prompt is None:
            prompt = (
                f"Analyse the {meta['source_type']} source: "
                f"catalog='{meta['catalog']}', schema='{meta['schema']}'. "
                f"Run all five phases: Discovery, Data Profiling, Logical Data Model (3NF), "
                f"Physical Data Model, and Output. "
                f"Return the final structured JSON report."
            )

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=8096,
                tools=TOOLS,
                messages=messages,
            )

            choice = response.choices[0]
            tool_calls = choice.message.tool_calls or []

            if not tool_calls:
                return choice.message.content or ""

            assistant_msg: dict = {"role": "assistant", "content": choice.message.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for tc in tool_calls:
                inputs = json.loads(tc.function.arguments or "{}")
                result = self._dispatch_tool(tc.function.name, inputs)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
