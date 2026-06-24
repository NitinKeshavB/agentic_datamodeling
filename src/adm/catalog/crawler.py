"""CatalogCrawler — orchestrates crawl + profile using any SourceConnector."""

from __future__ import annotations

from adm.catalog.sources import SourceConnector, create_connector


class CatalogCrawler:
    """
    Crawls a source database and builds a unified metadata snapshot.

    Usage
    -----
    # Unity Catalog
    crawler = CatalogCrawler.from_unity_catalog(catalog="my_cat", schema="my_schema")

    # PostgreSQL
    crawler = CatalogCrawler.from_jdbc("postgresql", connection_string="postgresql+psycopg2://...", schema="public")

    # SQL Server / Azure SQL
    crawler = CatalogCrawler.from_jdbc("sqlserver", connection_string="mssql+pyodbc://...", schema="dbo")

    # Or pass a connector directly
    crawler = CatalogCrawler(connector)
    """

    def __init__(self, connector: SourceConnector):
        self.connector = connector

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_unity_catalog(cls, catalog: str, schema: str | None = None, warehouse_id: str | None = None) -> "CatalogCrawler":
        """Create a crawler for a Databricks Unity Catalog schema."""
        return cls(create_connector("unity_catalog", catalog=catalog, schema=schema, warehouse_id=warehouse_id))

    @classmethod
    def from_jdbc(cls, source_type: str, connection_string: str, schema: str) -> "CatalogCrawler":
        """Create a crawler for a JDBC source (postgresql, sqlserver, azuresql)."""
        return cls(create_connector(source_type, connection_string=connection_string, schema=schema))

    # ------------------------------------------------------------------
    # Convenience pass-throughs (keep API stable for callers)
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict]:
        return self.connector.list_tables()

    def get_primary_keys(self) -> list[dict]:
        return self.connector.get_primary_keys()

    def get_foreign_keys(self) -> list[dict]:
        return self.connector.get_foreign_keys()

    def execute_sql(self, sql: str) -> list[dict]:
        return self.connector.execute_sql(sql)

    def get_table_stats(self, table_ref: str) -> dict:
        return self.connector.get_table_stats(table_ref)

    def sample_data(self, table_ref: str, n: int = 5) -> list[dict]:
        return self.connector.sample_data(table_ref, n)

    def check_duplicates(self, table_ref: str, key_columns: list[str]) -> dict:
        return self.connector.check_duplicates(table_ref, key_columns)

    # ------------------------------------------------------------------
    # Full crawl — builds the unified metadata snapshot
    # ------------------------------------------------------------------

    def crawl(self) -> dict:
        """Crawl the source and return a unified metadata dict."""
        source = self.connector
        print(f"Crawling [{source.source_type}] {source.catalog}.{source.schema or '*'} ...")

        tables = self.list_tables()

        # Attach primary keys to each table entry
        try:
            pk_rows = self.get_primary_keys()
            pk_index: dict[str, list[str]] = {}
            for pk in pk_rows:
                pk_index.setdefault(pk["table_name"], []).append(pk["column_name"])
            for t in tables:
                if not t["primary_keys"]:
                    t["primary_keys"] = pk_index.get(t["name"], [])
        except Exception as e:
            print(f"Warning: could not fetch primary keys: {e}")

        # Fetch explicit FK constraints
        try:
            foreign_keys = self.get_foreign_keys()
        except Exception as e:
            print(f"Warning: could not fetch foreign keys: {e}")
            foreign_keys = []

        print(f"Found {len(tables)} tables, {len(foreign_keys)} explicit FK constraints.")

        return {
            "source_type": source.source_type,
            "catalog": source.catalog,
            "schema": source.schema,
            "tables": tables,
            "foreign_keys": foreign_keys,
        }
