"""JDBC source connector — PostgreSQL, SQL Server, Azure SQL via SQLAlchemy."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine

from adm.catalog.sources.base import SourceConnector


class JDBCConnector(SourceConnector):
    """
    Connects to external relational databases via SQLAlchemy.

    Supported source types:
        postgresql  — postgresql+psycopg2://user:pass@host:5432/dbname
        sqlserver   — mssql+pyodbc://user:pass@host:1433/dbname?driver=ODBC+Driver+17+for+SQL+Server
        azuresql    — mssql+pyodbc://user:pass@server.database.windows.net:1433/dbname?driver=ODBC+Driver+17+for+SQL+Server

    Connection strings should be sourced from Databricks secrets at runtime, not hardcoded:
        conn_str = dbutils.secrets.get(scope="adm", key="POSTGRESQL_CONNECTION_STRING")
    """

    def __init__(self, connection_string: str, schema: str, source_type: str = "jdbc"):
        self._schema = schema
        self._source_type = source_type.lower()
        self._connection_string = self._normalise(connection_string, source_type)
        try:
            self.engine: Engine = create_engine(self._connection_string, future=True)
        except Exception as exc:
            scheme = (
                self._connection_string.split("://")[0]
                if "://" in self._connection_string
                else repr(self._connection_string[:40])
            )
            raise ValueError(
                f"Could not create SQLAlchemy engine for source_type='{source_type}'. "
                f"URL scheme parsed as '{scheme}'. "
                "Expected formats: postgresql+psycopg2://user:pass@host:5432/dbname  "
                "or mssql+pyodbc://user:pass@host:1433/dbname?driver=ODBC+Driver+17+for+SQL+Server"
            ) from exc
        self._catalog_name = self._parse_database_name(self._connection_string)

    @staticmethod
    def _normalise(conn_str: str, source_type: str) -> str:
        """Pin the SQLAlchemy dialect driver so sslmode and other params work correctly.

        postgres://            → postgresql+psycopg2://  (short-form used by many providers)
        postgresql://          → postgresql+psycopg2://  (asyncpg rejects sslmode)
        postgresql+asyncpg://  → postgresql+psycopg2://  (same reason)
        jdbc:postgresql://     → postgresql+psycopg2://  (JDBC-style URLs)
        mssql://               → mssql+pyodbc://
        jdbc:sqlserver://      → mssql+pyodbc://
        """
        st = source_type.lower()
        if st == "postgresql":
            for prefix, replacement in (
                ("jdbc:postgresql://", "postgresql+psycopg2://"),
                ("postgresql+asyncpg://", "postgresql+psycopg2://"),
                ("postgresql://", "postgresql+psycopg2://"),
                ("postgres://", "postgresql+psycopg2://"),
            ):
                if conn_str.startswith(prefix):
                    return conn_str.replace(prefix, replacement, 1)
        if st in ("sqlserver", "azuresql"):
            for prefix, replacement in (
                ("jdbc:sqlserver://", "mssql+pyodbc://"),
                ("mssql://", "mssql+pyodbc://"),
            ):
                if conn_str.startswith(prefix):
                    return conn_str.replace(prefix, replacement, 1)
        return conn_str

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def source_type(self) -> str:
        return self._source_type

    @property
    def catalog(self) -> str:
        return self._catalog_name

    @property
    def schema(self) -> str | None:
        return self._schema

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_database_name(connection_string: str) -> str:
        """Extract database name from the connection string."""
        try:
            parsed = urlparse(connection_string)
            db = parsed.path.lstrip("/").split("?")[0]
            return db or "unknown"
        except Exception:
            return "unknown"

    def _quote(self, name: str) -> str:
        """Return a safely quoted identifier using the engine dialect."""
        return self.engine.dialect.identifier_preparer.quote(name)

    def _table_ref(self, table_name: str) -> str:
        """Return a quoted schema.table reference."""
        return f"{self._quote(self._schema)}.{self._quote(table_name)}"

    # ------------------------------------------------------------------
    # SQL execution
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute a raw SQL string and return rows as dicts."""
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result.fetchall()]

    # ------------------------------------------------------------------
    # Schema discovery (via SQLAlchemy Inspector — dialect-agnostic)
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict]:
        """List tables in the schema using SQLAlchemy introspection."""
        inspector = inspect(self.engine)
        tables = []

        for table_name in inspector.get_table_names(schema=self._schema):
            raw_cols = inspector.get_columns(table_name, schema=self._schema)
            pk_info = inspector.get_pk_constraint(table_name, schema=self._schema)
            primary_keys = pk_info.get("constrained_columns", [])

            columns = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                    "comment": col.get("comment"),
                    "position": i,
                }
                for i, col in enumerate(raw_cols)
            ]

            tables.append(
                {
                    "name": table_name,
                    "schema": self._schema,
                    "full_name": f"{self._catalog_name}.{self._schema}.{table_name}",
                    "table_type": "TABLE",
                    "comment": inspector.get_table_comment(table_name, schema=self._schema).get("text"),
                    "columns": columns,
                    "primary_keys": primary_keys,
                }
            )

        return tables

    def get_primary_keys(self) -> list[dict]:
        """Return primary keys for all tables in the schema."""
        inspector = inspect(self.engine)
        rows = []
        for table_name in inspector.get_table_names(schema=self._schema):
            pk = inspector.get_pk_constraint(table_name, schema=self._schema)
            for i, col in enumerate(pk.get("constrained_columns", [])):
                rows.append(
                    {
                        "table_schema": self._schema,
                        "table_name": table_name,
                        "column_name": col,
                        "ordinal_position": i + 1,
                    }
                )
        return rows

    def get_foreign_keys(self) -> list[dict]:
        """Return foreign key relationships for all tables in the schema."""
        inspector = inspect(self.engine)
        rows = []
        for table_name in inspector.get_table_names(schema=self._schema):
            for fk in inspector.get_foreign_keys(table_name, schema=self._schema):
                for child_col, parent_col in zip(
                    fk.get("constrained_columns", []),
                    fk.get("referred_columns", []),
                ):
                    rows.append(
                        {
                            "child_schema": self._schema,
                            "child_table": table_name,
                            "child_column": child_col,
                            "parent_schema": fk.get("referred_schema") or self._schema,
                            "parent_table": fk.get("referred_table", ""),
                            "parent_column": parent_col,
                            "constraint_name": fk.get("name"),
                        }
                    )
        return rows

    # ------------------------------------------------------------------
    # Data operations (standard ANSI SQL — works across all dialects)
    # ------------------------------------------------------------------

    def get_table_stats(self, table_ref: str) -> dict:
        """Return row count and null rates for each column."""
        # table_ref may be passed as schema.table or full_name — use schema.table only
        table_name = table_ref.split(".")[-1].strip('`"[]')
        meta = MetaData()
        tbl = Table(table_name, meta, schema=self._schema, autoload_with=self.engine)

        with self.engine.connect() as conn:
            row_count = conn.execute(select(func.count()).select_from(tbl)).scalar() or 0

            if row_count == 0:
                return {"row_count": 0, "null_rates": {}}

            null_rates: dict[str, float] = {}
            for col in tbl.columns:
                null_count = conn.execute(select(func.count()).select_from(tbl).where(col.is_(None))).scalar() or 0
                null_rates[col.name] = round(null_count / row_count, 4)

        return {"row_count": row_count, "null_rates": null_rates}

    def sample_data(self, table_ref: str, n: int = 5) -> list[dict]:
        """Return n sample rows from the table."""
        table_name = table_ref.split(".")[-1].strip('`"[]')
        meta = MetaData()
        tbl = Table(table_name, meta, schema=self._schema, autoload_with=self.engine)

        with self.engine.connect() as conn:
            result = conn.execute(select(tbl).limit(n))
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result.fetchall()]

    def ping(self) -> dict:
        """Test connectivity by executing SELECT 1."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {
                "ok": True,
                "source_type": self.source_type,
                "catalog": self.catalog,
                "schema": self.schema,
                "detail": "Connected.",
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
        """Check for duplicate rows on the given key columns."""
        table_name = table_ref.split(".")[-1].strip('`"[]')
        meta = MetaData()
        tbl = Table(table_name, meta, schema=self._schema, autoload_with=self.engine)

        key_cols = [tbl.c[col] for col in key_columns if col in tbl.c]
        if not key_cols:
            return {"duplicate_groups": 0, "duplicate_rows": 0}

        subq = (
            select(*key_cols, func.count().label("cnt"))
            .select_from(tbl)
            .group_by(*key_cols)
            .having(func.count() > 1)
            .subquery()
        )

        with self.engine.connect() as conn:
            result = conn.execute(
                select(
                    func.count().label("duplicate_groups"),
                    func.sum(subq.c.cnt - 1).label("duplicate_rows"),
                ).select_from(subq)
            ).fetchone()

        return {
            "duplicate_groups": int(result.duplicate_groups or 0) if result else 0,
            "duplicate_rows": int(result.duplicate_rows or 0) if result else 0,
        }
