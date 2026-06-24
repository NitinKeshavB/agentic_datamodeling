"""Relationship detection — explicit FK constraints and heuristic inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Relationship:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    relationship_type: str   # explicit_fk | inferred_name | inferred_pk_match
    confidence: float        # 0.0 – 1.0
    child_schema: str = ""
    parent_schema: str = ""
    constraint_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "child_table": self.child_table,
            "child_column": self.child_column,
            "parent_table": self.parent_table,
            "parent_column": self.parent_column,
            "child_schema": self.child_schema,
            "parent_schema": self.parent_schema,
            "type": self.relationship_type,
            "confidence": self.confidence,
            "constraint_name": self.constraint_name,
        }


class RelationshipDetector:
    """Detects table relationships via explicit constraints and column naming heuristics."""

    # ------------------------------------------------------------------
    # Explicit FK constraints
    # ------------------------------------------------------------------

    def detect_explicit(self, foreign_keys: list[dict]) -> list[Relationship]:
        return [
            Relationship(
                child_table=fk["child_table"],
                child_column=fk["child_column"],
                parent_table=fk["parent_table"],
                parent_column=fk["parent_column"],
                child_schema=fk.get("child_schema", ""),
                parent_schema=fk.get("parent_schema", ""),
                relationship_type="explicit_fk",
                confidence=1.0,
                constraint_name=fk.get("constraint_name"),
            )
            for fk in foreign_keys
        ]

    # ------------------------------------------------------------------
    # Heuristic: column naming conventions
    # ------------------------------------------------------------------

    def detect_by_column_naming(self, tables: list[dict]) -> list[Relationship]:
        """
        Infer relationships from column name patterns:
          - orders.customer_id  →  customers.id   (suffix _id matches table name)
          - orders.customer_id  →  customer.id    (singular form)
          - orders.cust_id      →  customers.id   (abbreviated prefix)
        """
        # Index: table_name (lower) → {primary_keys, columns}
        table_index: dict[str, dict] = {
            t["name"].lower(): {
                "name": t["name"],
                "schema": t.get("schema", ""),
                "primary_keys": [pk.lower() for pk in t.get("primary_keys", [])],
                "columns": [c["name"].lower() for c in t["columns"]],
            }
            for t in tables
        }

        relationships: list[Relationship] = []
        existing: set[tuple] = set()

        def _add(child_table, child_col, parent_table_info, parent_col, confidence, rtype):
            key = (child_table, child_col, parent_table_info["name"], parent_col)
            if key not in existing:
                existing.add(key)
                relationships.append(Relationship(
                    child_table=child_table,
                    child_column=child_col,
                    parent_table=parent_table_info["name"],
                    parent_column=parent_col,
                    child_schema=next((t["schema"] for t in tables if t["name"].lower() == child_table.lower()), ""),
                    parent_schema=parent_table_info["schema"],
                    relationship_type=rtype,
                    confidence=confidence,
                ))

        for table in tables:
            tname = table["name"].lower()
            for col in table["columns"]:
                cname = col["name"].lower()

                # Skip if this column is the table's own PK
                if cname in table_index[tname]["primary_keys"]:
                    continue

                # Only analyse columns ending in _id / id / _key / _fk
                if not any(cname.endswith(suffix) for suffix in ("_id", "id", "_key", "_fk", "_code")):
                    continue

                for other_name, other_info in table_index.items():
                    if other_name == tname:
                        continue

                    parent_pk = other_info["primary_keys"][0] if other_info["primary_keys"] else "id"

                    # Pattern 1: column == "{other_table}_id"  e.g. customer_id → customers
                    if cname == f"{other_name}_id" or cname == f"{other_name}id":
                        _add(table["name"], col["name"], other_info, parent_pk, 0.90, "inferred_name")
                        continue

                    # Pattern 2: singular form — strip trailing "s"/"es"
                    singular = other_name.rstrip("s")
                    if singular and cname in (f"{singular}_id", f"{singular}id"):
                        _add(table["name"], col["name"], other_info, parent_pk, 0.80, "inferred_name")
                        continue

                    # Pattern 3: column name without suffix matches table name
                    # e.g. "customer" column in a table, and "customers" table exists
                    stripped = cname.rstrip("_id").rstrip("id").rstrip("_key")
                    if stripped and (stripped == other_name or stripped == other_name.rstrip("s")):
                        _add(table["name"], col["name"], other_info, parent_pk, 0.70, "inferred_name")

        return relationships

    # ------------------------------------------------------------------
    # Combined detection
    # ------------------------------------------------------------------

    def detect_all(self, catalog_metadata: dict) -> list[Relationship]:
        """Run all detectors; explicit FKs shadow heuristic duplicates."""
        tables = catalog_metadata.get("tables", [])
        foreign_keys = catalog_metadata.get("foreign_keys", [])

        explicit = self.detect_explicit(foreign_keys)
        inferred = self.detect_by_column_naming(tables)

        # Suppress inferred relationships that are already explicit
        explicit_keys = {
            (r.child_table, r.child_column, r.parent_table, r.parent_column)
            for r in explicit
        }
        unique_inferred = [
            r for r in inferred
            if (r.child_table, r.child_column, r.parent_table, r.parent_column) not in explicit_keys
        ]

        all_rels = explicit + unique_inferred
        all_rels.sort(key=lambda r: (-r.confidence, r.child_table, r.child_column))
        return all_rels
