"""
SourceRegistry — loads source definitions from sources.yml and resolves credentials.

sources.yml defines every database the agent can connect to.
Connection strings for JDBC sources are stored as Databricks secrets (never in config files).

Local development: set the connection string as an environment variable instead of a secret.
  export ADM_<SECRET_KEY>=<connection_string>
  e.g. export ADM_ERP_POSTGRES_CONNECTION_STRING=postgresql+psycopg2://...
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adm.catalog.sources import SourceConnector, create_connector


@dataclass
class SourceDefinition:
    """A single source entry from sources.yml."""

    name: str
    source_type: str
    schema: str | None = None
    # Unity Catalog only
    catalog: str | None = None
    warehouse_id: str | None = None
    # JDBC only — one of connection_string or secret_* must be provided
    connection_string: str | None = None
    secret_scope: str | None = None
    secret_key: str | None = None
    # Freeform metadata
    description: str = ""
    tags: list[str] = field(default_factory=list)


class SourceRegistry:
    """
    Loads source definitions from sources.yml and creates SourceConnectors on demand.

    Credential resolution order for JDBC sources:
      1. connection_string field in sources.yml   (dev only — avoid for prod)
      2. Databricks secret  (scope + key defined in sources.yml)
      3. Environment variable  ADM_<SECRET_KEY>   (local development)
    """

    DEFAULT_CONFIG_PATH = Path("sources.yml")

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path or self.DEFAULT_CONFIG_PATH)
        self._definitions: dict[str, SourceDefinition] = {}
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"sources.yml not found at '{self._config_path}'. "
                "Create one from the sources.yml.example template."
            )

        with open(self._config_path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        for entry in raw.get("sources", []):
            defn = SourceDefinition(
                name=entry["name"],
                source_type=entry["type"],
                schema=entry.get("schema"),
                catalog=entry.get("catalog"),
                warehouse_id=entry.get("warehouse_id"),
                connection_string=entry.get("connection_string"),
                secret_scope=entry.get("secret_scope"),
                secret_key=entry.get("secret_key"),
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
            )
            self._definitions[defn.name] = defn

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    def _resolve_connection_string(self, defn: SourceDefinition) -> str:
        # 1. Inline (dev only)
        if defn.connection_string:
            return defn.connection_string

        # 2. Databricks secret
        if defn.secret_scope and defn.secret_key:
            try:
                # Works when running on a Databricks cluster
                from pyspark.dbutils import DBUtils  # type: ignore[import]
                from pyspark.sql import SparkSession  # type: ignore[import]

                spark = SparkSession.getActiveSession()
                if spark:
                    dbutils = DBUtils(spark)
                    return dbutils.secrets.get(scope=defn.secret_scope, key=defn.secret_key)
            except Exception:  # noqa: BLE001
                pass  # Not on a cluster — fall through to env var

            # 3. Environment variable  ADM_<SECRET_KEY>
            env_var = f"ADM_{defn.secret_key}"
            value = os.environ.get(env_var)
            if value:
                return value

            raise RuntimeError(
                f"Could not resolve connection string for source '{defn.name}'. "
                f"Set Databricks secret '{defn.secret_scope}/{defn.secret_key}' "
                f"or env var '{env_var}'."
            )

        raise RuntimeError(
            f"Source '{defn.name}' has no connection_string, secret_scope, or secret_key defined."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_sources(self) -> list[SourceDefinition]:
        """Return all registered source definitions."""
        return list(self._definitions.values())

    def get(self, name: str) -> SourceDefinition:
        """Return a source definition by name."""
        if name not in self._definitions:
            available = ", ".join(self._definitions)
            raise KeyError(f"Source '{name}' not found. Available: {available}")
        return self._definitions[name]

    def create_connector(self, name: str) -> SourceConnector:
        """Instantiate and return a live SourceConnector for the named source."""
        defn = self.get(name)

        if defn.source_type == "unity_catalog":
            if not defn.catalog:
                raise ValueError(f"Source '{name}' (unity_catalog) requires 'catalog'.")
            return create_connector(
                "unity_catalog",
                catalog=defn.catalog,
                schema=defn.schema,
                warehouse_id=defn.warehouse_id,
            )

        # JDBC sources
        conn_str = self._resolve_connection_string(defn)
        return create_connector(
            defn.source_type,
            connection_string=conn_str,
            schema=defn.schema or "public",
        )

    def check_all(self) -> list[dict]:
        """
        Ping every registered source and return a status list.
        Each entry: { name, ok, source_type, catalog, schema, detail }
        """
        results = []
        for defn in self._definitions.values():
            print(f"  Checking '{defn.name}' ({defn.source_type}) ...", end=" ", flush=True)
            try:
                connector = self.create_connector(defn.name)
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
            status = "OK" if result["ok"] else "FAILED"
            print(status)
            results.append(result)
        return results
