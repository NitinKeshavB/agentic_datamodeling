"""Source connectors — pluggable backends for catalog/schema discovery."""

from adm.catalog.sources.base import SourceConnector
from adm.catalog.sources.jdbc import JDBCConnector
from adm.catalog.sources.unity_catalog import UnityCatalogConnector


def create_connector(source_type: str, **kwargs) -> SourceConnector:
    """
    Factory — returns the right SourceConnector for the given source_type.

    Unity Catalog
    -------------
    create_connector("unity_catalog", catalog="my_cat", schema="my_schema")
    create_connector("unity_catalog", catalog="my_cat", schema="my_schema", warehouse_id="abc123")

    PostgreSQL
    ----------
    create_connector("postgresql", connection_string="postgresql+psycopg2://user:pass@host:5432/db", schema="public")

    SQL Server / Azure SQL
    ----------------------
    create_connector("sqlserver",  connection_string="mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+17+for+SQL+Server", schema="dbo")
    create_connector("azuresql",   connection_string="mssql+pyodbc://user:pass@server.database.windows.net:1433/db?driver=ODBC+Driver+17+for+SQL+Server", schema="dbo")
    """
    source_type = source_type.lower().replace("-", "_")

    if source_type == "unity_catalog":
        return UnityCatalogConnector(
            catalog=kwargs["catalog"],
            schema=kwargs.get("schema"),
            warehouse_id=kwargs.get("warehouse_id"),
        )

    if source_type in ("postgresql", "sqlserver", "azuresql", "mssql"):
        return JDBCConnector(
            connection_string=kwargs["connection_string"],
            schema=kwargs["schema"],
            source_type=source_type,
        )

    raise ValueError(
        f"Unknown source_type '{source_type}'. "
        f"Supported: unity_catalog, postgresql, sqlserver, azuresql"
    )


__all__ = ["SourceConnector", "UnityCatalogConnector", "JDBCConnector", "create_connector"]
