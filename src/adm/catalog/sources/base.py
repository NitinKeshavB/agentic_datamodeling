"""Abstract base class for all source connectors."""

from __future__ import annotations

from abc import (
    ABC,
    abstractmethod,
)
from typing import Any


class SourceConnector(ABC):
    """
    Common interface for all source database connectors.

    Implementations: UnityCatalogConnector, JDBCConnector
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def source_type(self) -> str:
        """e.g. 'unity_catalog', 'postgresql', 'sqlserver', 'azuresql'"""

    @property
    @abstractmethod
    def catalog(self) -> str:
        """Logical catalog / database name."""

    @property
    @abstractmethod
    def schema(self) -> str | None:
        """Schema / namespace being crawled. None means all schemas."""

    # ------------------------------------------------------------------
    # Schema discovery
    # ------------------------------------------------------------------

    @abstractmethod
    def list_tables(self) -> list[dict]:
        """
        Return all tables in the target schema.

        Each dict:
          name, schema, full_name, table_type, comment,
          columns: [{name, type, nullable, comment, position}],
          primary_keys: [col_name, ...]
        """

    @abstractmethod
    def get_primary_keys(self) -> list[dict]:
        """
        Return primary key column rows.
        Each dict: table_schema, table_name, column_name, ordinal_position
        """

    @abstractmethod
    def get_foreign_keys(self) -> list[dict]:
        """
        Return foreign key relationships.
        Each dict: child_schema, child_table, child_column,
                   parent_schema, parent_table, parent_column, constraint_name
        """

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    @abstractmethod
    def execute_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL string and return rows as a list of dicts."""

    @abstractmethod
    def get_table_stats(self, table_ref: str) -> dict:
        """
        Return row count and per-column null rates.
        { row_count: int, null_rates: {col: float} }
        """

    @abstractmethod
    def sample_data(self, table_ref: str, n: int = 5) -> list[dict]:
        """Return n sample rows from the table."""

    @abstractmethod
    def check_duplicates(self, table_ref: str, key_columns: list[str]) -> dict:
        """
        Check for duplicate rows on key_columns.
        { duplicate_groups: int, duplicate_rows: int }
        """

    # ------------------------------------------------------------------
    # Connectivity check
    # ------------------------------------------------------------------

    @abstractmethod
    def ping(self) -> dict:
        """
        Test connectivity to the source.
        Returns { ok: bool, source_type, catalog, schema, detail: str }
        """
