"""Unity Catalog source connector — uses Databricks SDK + Statement Execution API."""

from __future__ import annotations

import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from adm.catalog.sources.base import SourceConnector


class UnityCatalogConnector(SourceConnector):
    """Connects to a Databricks Unity Catalog catalog/schema."""

    def __init__(self, catalog: str, schema: str | None = None, warehouse_id: str | None = None):
        self._catalog = catalog
        self._schema = schema
        self.w = WorkspaceClient()
        self.warehouse_id = warehouse_id or self._resolve_warehouse()

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def source_type(self) -> str:
        return "unity_catalog"

    @property
    def catalog(self) -> str:
        return self._catalog

    @property
    def schema(self) -> str | None:
        return self._schema

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_warehouse(self) -> str:
        warehouses = [w for w in self.w.warehouses.list() if w.state and w.state.value in ("RUNNING", "STARTING")]
        if not warehouses:
            warehouses = list(self.w.warehouses.list())
        if not warehouses:
            raise RuntimeError("No SQL warehouses found. Create one or pass warehouse_id.")
        return warehouses[0].id

    # ------------------------------------------------------------------
    # SQL execution
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str, timeout: str = "50s") -> list[dict[str, Any]]:
        """Execute SQL via Statement Execution API and return rows as dicts."""
        response = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=sql,
            wait_timeout=timeout,
        )
        statement_id = response.statement_id
        while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            response = self.w.statement_execution.get_statement(statement_id)

        if response.status.state != StatementState.SUCCEEDED:
            err = response.status.error
            raise RuntimeError(f"SQL failed [{response.status.state}]: {err.message if err else 'unknown'}")

        if not response.result or not response.result.data_array:
            return []

        columns = [col.name for col in response.manifest.schema.columns]
        return [dict(zip(columns, row)) for row in response.result.data_array]

    # ------------------------------------------------------------------
    # Schema discovery
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict]:
        schemas = [self._schema] if self._schema else self._list_schemas()
        tables = []
        for sc in schemas:
            for t in self.w.tables.list(catalog_name=self._catalog, schema_name=sc):
                tables.append({
                    "name": t.name,
                    "schema": sc,
                    "full_name": f"{self._catalog}.{sc}.{t.name}",
                    "table_type": t.table_type.value if t.table_type else None,
                    "comment": t.comment,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type_text,
                            "nullable": c.nullable,
                            "comment": c.comment,
                            "position": c.position,
                        }
                        for c in sorted(t.columns or [], key=lambda c: c.position or 0)
                    ],
                    "primary_keys": [],
                })
        return tables

    def _list_schemas(self) -> list[str]:
        return [s.name for s in self.w.schemas.list(catalog_name=self._catalog) if s.name != "information_schema"]

    def get_primary_keys(self) -> list[dict]:
        sc = self._schema or "%"
        return self.execute_sql(f"""
        SELECT kcu.table_schema, kcu.table_name, kcu.column_name, kcu.ordinal_position
        FROM `{self._catalog}`.information_schema.key_column_usage kcu
        JOIN `{self._catalog}`.information_schema.table_constraints tc
            ON  kcu.constraint_name = tc.constraint_name
            AND kcu.table_catalog   = tc.table_catalog
            AND kcu.table_schema    = tc.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND kcu.table_schema LIKE '{sc}'
        ORDER BY kcu.table_schema, kcu.table_name, kcu.ordinal_position
        """)

    def get_foreign_keys(self) -> list[dict]:
        sc = self._schema or "%"
        return self.execute_sql(f"""
        SELECT
            kcu.table_schema  AS child_schema,
            kcu.table_name    AS child_table,
            kcu.column_name   AS child_column,
            ccu.table_schema  AS parent_schema,
            ccu.table_name    AS parent_table,
            ccu.column_name   AS parent_column,
            tc.constraint_name
        FROM `{self._catalog}`.information_schema.referential_constraints rc
        JOIN `{self._catalog}`.information_schema.key_column_usage kcu
            ON  rc.constraint_name    = kcu.constraint_name
            AND rc.constraint_catalog = kcu.constraint_catalog
            AND rc.constraint_schema  = kcu.constraint_schema
        JOIN `{self._catalog}`.information_schema.constraint_column_usage ccu
            ON  rc.unique_constraint_name    = ccu.constraint_name
            AND rc.unique_constraint_catalog = ccu.constraint_catalog
            AND rc.unique_constraint_schema  = ccu.constraint_schema
        JOIN `{self._catalog}`.information_schema.table_constraints tc
            ON  rc.constraint_name    = tc.constraint_name
            AND rc.constraint_catalog = tc.table_catalog
            AND rc.constraint_schema  = tc.table_schema
        WHERE kcu.table_schema LIKE '{sc}'
        ORDER BY kcu.table_schema, kcu.table_name, kcu.ordinal_position
        """)

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def get_table_stats(self, table_ref: str) -> dict:
        rows = self.execute_sql(f"SELECT COUNT(*) AS row_count FROM {table_ref}")
        row_count = int(rows[0]["row_count"]) if rows else 0

        col_rows = self.execute_sql(f"DESCRIBE TABLE {table_ref}")
        cols = [r["col_name"] for r in col_rows if r["col_name"] and not r["col_name"].startswith("#")]

        if not cols or row_count == 0:
            return {"row_count": row_count, "null_rates": {}}

        null_exprs = ", ".join(f"SUM(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END) AS `{c}`" for c in cols)
        null_rows = self.execute_sql(f"SELECT {null_exprs} FROM {table_ref}")
        null_rates = {c: round(int(null_rows[0][c] or 0) / row_count, 4) for c in cols} if null_rows else {}

        return {"row_count": row_count, "null_rates": null_rates}

    def sample_data(self, table_ref: str, n: int = 5) -> list[dict]:
        return self.execute_sql(f"SELECT * FROM {table_ref} LIMIT {n}")

    def ping(self) -> dict:
        """Test connectivity by listing schemas in the catalog."""
        try:
            schemas = self._list_schemas()
            return {
                "ok": True,
                "source_type": self.source_type,
                "catalog": self.catalog,
                "schema": self.schema,
                "detail": f"Connected. Found {len(schemas)} schemas.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "source_type": self.source_type,
                "catalog": self.catalog,
                "schema": self.schema,
                "detail": str(exc),
            }

    def check_duplicates(self, table_ref: str, key_columns: list[str]) -> dict:
        keys = ", ".join(f"`{c}`" for c in key_columns)
        rows = self.execute_sql(f"""
        SELECT COUNT(*) AS duplicate_groups, SUM(cnt - 1) AS duplicate_rows
        FROM (
            SELECT {keys}, COUNT(*) AS cnt
            FROM {table_ref}
            GROUP BY {keys}
            HAVING COUNT(*) > 1
        )
        """)
        if rows:
            return {
                "duplicate_groups": int(rows[0]["duplicate_groups"] or 0),
                "duplicate_rows": int(rows[0]["duplicate_rows"] or 0),
            }
        return {"duplicate_groups": 0, "duplicate_rows": 0}
