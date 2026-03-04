"""
Schema Intelligence Engine
===========================
Production-grade adaptive schema learning and contract change detection for
FastAPI backends. Learns JSON schemas dynamically from live API traffic,
per endpoint, with no hardcoded types.

Architecture:
  SchemaRegistry      — Stores schema per endpoint, supports load/save to JSON
  SchemaLearner       — Recursively traverses JSON and builds rich field metadata
  SchemaComparator    — Classifies differences as BREAKING / WARNING / INFO
  ContractChangeReporter — Generates human-readable per-field change reports

Key design decisions:
  - null is NEVER treated as a fixed type. Seeing null marks a field as
    nullable=True. A later non-null value is an INFO change (field became
    non-nullable), NOT a breaking change.
  - Type sets accumulate over time. string+number on the same field =
    a union type — this is a WARNING (numeric ↔ string can silently break
    comparisons), not BREAKING (no data is lost).
  - Field removal IS breaking. object→string IS breaking. array→object IS breaking.
  - New optional fields are INFO. New nullable fields are INFO.
  - All paths use JSONPath-like notation: $.field.nested[0].leaf

Usage (middleware example):
    registry = SchemaRegistry(persist_path="data/schemas.json")
    learner  = SchemaLearner()
    comparator = SchemaComparator()
    reporter   = ContractChangeReporter()

    # On each response:
    previous = registry.get("/analyze")
    new_schema = learner.learn(previous, response_body)
    registry.set("/analyze", new_schema)

    if previous:
        changes = comparator.compare(previous, new_schema, response_body)
        if changes:
            report = reporter.generate(changes, endpoint="/analyze")
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("mock_platform")


# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

class Severity:
    BREAKING = "BREAKING"   # Client will crash or lose data
    WARNING  = "WARNING"    # Client may behave incorrectly
    INFO     = "INFO"       # Safe change, good to know


class ChangeType:
    # BREAKING
    FIELD_REMOVED        = "field_removed"
    OBJECT_TO_PRIMITIVE  = "object_to_primitive"
    ARRAY_TO_NON_ARRAY   = "array_to_non_array"
    NON_ARRAY_TO_ARRAY   = "non_array_to_array"   # Usually breaking for consumers

    # WARNING
    TYPE_CHANGED         = "type_changed"          # e.g. string ↔ number

    # INFO
    NULL_TO_TYPED        = "null_to_typed"         # null → string: field became non-null
    NEW_FIELD            = "new_field"             # API added a field
    FIELD_BECAME_NULLABLE = "field_became_nullable" # field now sometimes returns null
    FIELD_BECAME_REQUIRED = "field_became_required" # field occurrence increased to 100%


# Human-readable severity icons
_SEVERITY_ICONS = {
    Severity.BREAKING: "🔴 BREAKING",
    Severity.WARNING:  "🟡 WARNING",
    Severity.INFO:     "🟢 INFO",
}

# Python type name → JSON type name
_PYTHON_TO_JSON_TYPE = {
    "str":      "string",
    "int":      "integer",
    "float":    "number",
    "bool":     "boolean",
    "dict":     "object",
    "list":     "array",
    "NoneType": "null",
}


def _json_type(value: Any) -> str:
    """Return the JSON Schema type name for a Python value."""
    return _PYTHON_TO_JSON_TYPE.get(type(value).__name__, type(value).__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# FIELD DESCRIPTOR
# Stored for every leaf field learned from API traffic.
# ──────────────────────────────────────────────────────────────────────────────

class FieldDescriptor:
    """
    Rich metadata for a single JSON field across all observed responses.

    Attributes:
        types_seen:  Set of JSON type names ever observed ("string", "integer", …)
        nullable:    True if null was ever observed for this field
        occurrences: How many times this field was observed (for required-field detection)
        last_seen:   ISO timestamp of last observation
        example:     Last non-null sample value (for documentation/mock generation)
    """
    __slots__ = ("types_seen", "nullable", "occurrences", "last_seen", "example")

    def __init__(self):
        self.types_seen: Set[str] = set()
        self.nullable: bool = False
        self.occurrences: int = 0
        self.last_seen: str = _now_iso()
        self.example: Any = None

    def observe(self, value: Any) -> None:
        """Record one observation of this field."""
        self.occurrences += 1
        self.last_seen = _now_iso()

        if value is None:
            self.nullable = True
            # Do NOT add "null" to types_seen — null is orthogonal to type.
        else:
            self.types_seen.add(_json_type(value))
            self.example = value

    def to_dict(self) -> Dict:
        return {
            "types_seen":  sorted(self.types_seen),
            "nullable":    self.nullable,
            "occurrences": self.occurrences,
            "last_seen":   self.last_seen,
            "example":     self.example,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "FieldDescriptor":
        fd = cls()
        fd.types_seen  = set(d.get("types_seen", []))
        fd.nullable    = d.get("nullable", False)
        fd.occurrences = d.get("occurrences", 0)
        fd.last_seen   = d.get("last_seen", _now_iso())
        fd.example     = d.get("example")
        return fd

    def primary_type(self) -> Optional[str]:
        """Return the most recently / commonly observed non-null type, or None."""
        if not self.types_seen:
            return None
        # Prefer object > array > string > number > boolean
        for preferred in ("object", "array", "string", "integer", "number", "boolean"):
            if preferred in self.types_seen:
                return preferred
        return next(iter(self.types_seen))


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINT SCHEMA
# A tree of FieldDescriptors plus nested sub-schemas.
# ──────────────────────────────────────────────────────────────────────────────

# Internal schema node:
# {
#   "__meta__": FieldDescriptor.to_dict(),   ← for THIS node's type info
#   "field_name": { ... nested node ... },   ← object children
#   "__items__": { ... node ... }            ← array item schema
# }
# Leaf nodes have only "__meta__".

_META = "__meta__"
_ITEMS = "__items__"


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA LEARNER
# ──────────────────────────────────────────────────────────────────────────────

class SchemaLearner:
    """
    Recursively traverses JSON API responses and builds/updates a rich schema.

    Handles:
      - Nested objects (arbitrary depth)
      - Arrays (learns item schema from all array elements, not just first)
      - Nullable fields (null → nullable=True, not a type change)
      - Union types (string and integer both seen → types_seen = {"string", "integer"})
      - Top-level arrays (e.g. responses that are [] not {})
    """

    def learn(
        self,
        current_schema: Optional[Dict],
        response_body: Any,
        _path: str = "$"
    ) -> Dict:
        """
        Update `current_schema` with the new `response_body` observation.

        Args:
            current_schema: Existing schema dict (may be None for first observation).
            response_body:  The parsed JSON response (dict, list, or primitive).

        Returns:
            Updated schema dict.
        """
        if current_schema is None:
            current_schema = {}

        # Update the root-level meta descriptor
        root_meta = FieldDescriptor.from_dict(
            current_schema.get(_META, {})
        )
        root_meta.observe(response_body)
        current_schema[_META] = root_meta.to_dict()

        if isinstance(response_body, dict):
            self._learn_object(current_schema, response_body, _path)
        elif isinstance(response_body, list):
            self._learn_array(current_schema, response_body, _path)
        # Primitives are fully captured by the meta descriptor above

        return current_schema

    def _learn_object(self, schema_node: Dict, obj: Dict, path: str) -> None:
        for key, value in obj.items():
            child_path = f"{path}.{key}"
            child_node = schema_node.get(key, {})

            child_meta = FieldDescriptor.from_dict(child_node.get(_META, {}))
            child_meta.observe(value)
            child_node[_META] = child_meta.to_dict()

            if isinstance(value, dict):
                self._learn_object(child_node, value, child_path)
            elif isinstance(value, list):
                self._learn_array(child_node, value, child_path)

            schema_node[key] = child_node

    def _learn_array(self, schema_node: Dict, arr: List, path: str) -> None:
        """Learn the item schema from ALL elements in the array (not just first)."""
        if not arr:
            return
        items_node = schema_node.get(_ITEMS, {})
        for i, item in enumerate(arr):
            item_path = f"{path}[{i}]"
            items_meta = FieldDescriptor.from_dict(items_node.get(_META, {}))
            items_meta.observe(item)
            items_node[_META] = items_meta.to_dict()

            if isinstance(item, dict):
                self._learn_object(items_node, item, item_path)
            elif isinstance(item, list):
                self._learn_array(items_node, item, item_path)

        schema_node[_ITEMS] = items_node


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA COMPARATOR
# ──────────────────────────────────────────────────────────────────────────────

class ContractChange:
    """Represents a single detected schema change."""

    def __init__(
        self,
        change_type: str,
        severity: str,
        path: str,
        old_types: Set[str],
        new_types: Set[str],
        old_nullable: bool,
        new_nullable: bool,
        explanation: str,
    ):
        self.change_type   = change_type
        self.severity      = severity
        self.path          = path
        self.old_types     = old_types
        self.new_types     = new_types
        self.old_nullable  = old_nullable
        self.new_nullable  = new_nullable
        self.explanation   = explanation

    def to_dict(self) -> Dict:
        return {
            "change_type":  self.change_type,
            "severity":     self.severity,
            "path":         self.path,
            "old_types":    sorted(self.old_types),
            "new_types":    sorted(self.new_types),
            "old_nullable": self.old_nullable,
            "new_nullable": self.new_nullable,
            "explanation":  self.explanation,
        }


class SchemaComparator:
    """
    Compares two schema snapshots (before / after a request) and classifies
    every detected difference by severity.

    Severity rules:
      BREAKING:
        - A field present in old schema is absent in new response
        - object → string/number/bool/array  (consumers will crash)
        - array  → object/string/number/bool (consumers will crash)

      WARNING:
        - string ↔ number (silent type coercion may hide bugs)
        - string ↔ boolean, number ↔ boolean

      INFO:
        - null → any type  (field was nullable, now has a real value)
        - New field appeared (additive — safe for most consumers)
        - Field became nullable (consumers should add null-checks)
    """

    def compare(
        self,
        old_schema: Dict,
        new_schema: Dict,
        _path: str = "$",
    ) -> List[ContractChange]:
        """
        Returns a list of ContractChange objects for every difference found.
        Call with the schema BEFORE and AFTER learning the latest response.
        """
        changes: List[ContractChange] = []
        self._compare_nodes(old_schema, new_schema, _path, changes)
        return changes

    def _get_meta(self, node: Dict) -> FieldDescriptor:
        return FieldDescriptor.from_dict(node.get(_META, {}))

    def _compare_nodes(
        self,
        old_node: Dict,
        new_node: Dict,
        path: str,
        changes: List[ContractChange],
    ) -> None:
        old_meta = self._get_meta(old_node)
        new_meta = self._get_meta(new_node)

        # ── Structural type change at this node ──────────────────────────────
        old_primary = old_meta.primary_type()
        new_primary = new_meta.primary_type()

        if old_primary and new_primary and old_primary != new_primary:
            self._classify_type_change(
                old_primary, new_primary,
                old_meta, new_meta, path, changes
            )
        elif old_primary and not new_primary and new_meta.nullable and not old_meta.nullable:
            # Was a typed field, now only null seen → became nullable
            changes.append(ContractChange(
                change_type   = ChangeType.FIELD_BECAME_NULLABLE,
                severity      = Severity.INFO,
                path          = path,
                old_types     = old_meta.types_seen,
                new_types     = new_meta.types_seen,
                old_nullable  = old_meta.nullable,
                new_nullable  = True,
                explanation   = (
                    f"Field at `{path}` was always `{old_primary}` but now "
                    f"returned null. Consumers should add null-checks."
                )
            ))

        # ── Nullability gained ───────────────────────────────────────────────
        if not old_meta.nullable and new_meta.nullable and new_primary:
            changes.append(ContractChange(
                change_type   = ChangeType.FIELD_BECAME_NULLABLE,
                severity      = Severity.INFO,
                path          = path,
                old_types     = old_meta.types_seen,
                new_types     = new_meta.types_seen,
                old_nullable  = False,
                new_nullable  = True,
                explanation   = (
                    f"Field `{path}` was never null before but now returns null. "
                    f"Add null-checks or optional chaining."
                )
            ))

        # ── Null → typed (field was only null before, now has a real type) ───
        if old_primary is None and old_meta.nullable and new_primary:
            changes.append(ContractChange(
                change_type   = ChangeType.NULL_TO_TYPED,
                severity      = Severity.INFO,
                path          = path,
                old_types     = old_meta.types_seen,
                new_types     = new_meta.types_seen,
                old_nullable  = True,
                new_nullable  = new_meta.nullable,
                explanation   = (
                    f"Field `{path}` previously only returned null. "
                    f"It now returns `{new_primary}`. This is safe — update "
                    f"your TypeScript types to reflect the actual type."
                )
            ))

        # ── Recurse into object children ─────────────────────────────────────
        old_keys = {k for k in old_node if k not in (_META, _ITEMS)}
        new_keys = {k for k in new_node if k not in (_META, _ITEMS)}

        # Fields removed (BREAKING)
        for key in old_keys - new_keys:
            old_child_meta = self._get_meta(old_node[key])
            changes.append(ContractChange(
                change_type   = ChangeType.FIELD_REMOVED,
                severity      = Severity.BREAKING,
                path          = f"{path}.{key}",
                old_types     = old_child_meta.types_seen,
                new_types     = set(),
                old_nullable  = old_child_meta.nullable,
                new_nullable  = False,
                explanation   = (
                    f"Field `{path}.{key}` (was `{'|'.join(sorted(old_child_meta.types_seen)) or 'null'}`) "
                    f"has been removed from the response. Any client code "
                    f"reading this field will receive `undefined`."
                )
            ))

        # New fields (INFO)
        for key in new_keys - old_keys:
            new_child_meta = self._get_meta(new_node[key])
            changes.append(ContractChange(
                change_type   = ChangeType.NEW_FIELD,
                severity      = Severity.INFO,
                path          = f"{path}.{key}",
                old_types     = set(),
                new_types     = new_child_meta.types_seen,
                old_nullable  = False,
                new_nullable  = new_child_meta.nullable,
                explanation   = (
                    f"New field `{path}.{key}` appeared "
                    f"(type: `{'|'.join(sorted(new_child_meta.types_seen)) or 'null'}`). "
                    f"This is additive — update your TypeScript types to include it."
                )
            ))

        # Recurse into common children
        for key in old_keys & new_keys:
            self._compare_nodes(
                old_node[key],
                new_node[key],
                f"{path}.{key}",
                changes,
            )

        # ── Recurse into array items ──────────────────────────────────────────
        if _ITEMS in old_node and _ITEMS in new_node:
            self._compare_nodes(
                old_node[_ITEMS],
                new_node[_ITEMS],
                f"{path}[*]",
                changes,
            )
        elif _ITEMS in old_node and _ITEMS not in new_node:
            _old_items_meta = self._get_meta(old_node[_ITEMS])
            changes.append(ContractChange(
                change_type   = ChangeType.ARRAY_TO_NON_ARRAY,
                severity      = Severity.BREAKING,
                path          = f"{path}[*]",
                old_types     = _old_items_meta.types_seen,
                new_types     = new_meta.types_seen,
                old_nullable  = _old_items_meta.nullable,
                new_nullable  = new_meta.nullable,
                explanation   = (
                    f"Field `{path}` was an array but is now "
                    f"`{new_primary or 'unknown'}`. All array iteration code will break."
                )
            ))

    def _classify_type_change(
        self,
        old_type: str,
        new_type: str,
        old_meta: FieldDescriptor,
        new_meta: FieldDescriptor,
        path: str,
        changes: List[ContractChange],
    ) -> None:
        """
        Classify a type change by severity based on the specific transition.
        """
        # object → anything else: BREAKING
        if old_type == "object" and new_type != "object":
            changes.append(ContractChange(
                change_type  = ChangeType.OBJECT_TO_PRIMITIVE,
                severity     = Severity.BREAKING,
                path         = path,
                old_types    = old_meta.types_seen,
                new_types    = new_meta.types_seen,
                old_nullable = old_meta.nullable,
                new_nullable = new_meta.nullable,
                explanation  = (
                    f"`{path}` changed from `object` to `{new_type}`. "
                    f"Any code doing `field.subKey` will throw TypeError."
                )
            ))
            return

        # array → non-array: BREAKING
        if old_type == "array" and new_type != "array":
            changes.append(ContractChange(
                change_type  = ChangeType.ARRAY_TO_NON_ARRAY,
                severity     = Severity.BREAKING,
                path         = path,
                old_types    = old_meta.types_seen,
                new_types    = new_meta.types_seen,
                old_nullable = old_meta.nullable,
                new_nullable = new_meta.nullable,
                explanation  = (
                    f"`{path}` changed from `array` to `{new_type}`. "
                    f"Any `.map()`, `.forEach()`, or array iteration will throw."
                )
            ))
            return

        # non-array → array: BREAKING
        if new_type == "array" and old_type != "array":
            changes.append(ContractChange(
                change_type  = ChangeType.NON_ARRAY_TO_ARRAY,
                severity     = Severity.BREAKING,
                path         = path,
                old_types    = old_meta.types_seen,
                new_types    = new_meta.types_seen,
                old_nullable = old_meta.nullable,
                new_nullable = new_meta.nullable,
                explanation  = (
                    f"`{path}` changed from `{old_type}` to `array`. "
                    f"Consumers expecting a scalar value will break."
                )
            ))
            return

        # string ↔ number, string ↔ boolean, number ↔ boolean: WARNING
        # These are "soft" type changes — data is present but type is wrong.
        _soft_change_pairs = {
            frozenset({"string", "integer"}),
            frozenset({"string", "number"}),
            frozenset({"string", "boolean"}),
            frozenset({"integer", "boolean"}),
            frozenset({"number", "boolean"}),
            frozenset({"integer", "number"}),
        }
        if frozenset({old_type, new_type}) in _soft_change_pairs:
            changes.append(ContractChange(
                change_type  = ChangeType.TYPE_CHANGED,
                severity     = Severity.WARNING,
                path         = path,
                old_types    = old_meta.types_seen,
                new_types    = new_meta.types_seen,
                old_nullable = old_meta.nullable,
                new_nullable = new_meta.nullable,
                explanation  = (
                    f"`{path}` changed type from `{old_type}` to `{new_type}`. "
                    f"Strict equality checks (===) and numeric operations "
                    f"may behave incorrectly. Check all consumers."
                )
            ))
            return

        # Any other type change
        changes.append(ContractChange(
            change_type  = ChangeType.TYPE_CHANGED,
            severity     = Severity.WARNING,
            path         = path,
            old_types    = old_meta.types_seen,
            new_types    = new_meta.types_seen,
            old_nullable = old_meta.nullable,
            new_nullable = new_meta.nullable,
            explanation  = (
                f"`{path}` changed type from `{old_type}` to `{new_type}`."
            )
        ))


# ──────────────────────────────────────────────────────────────────────────────
# CONTRACT CHANGE REPORTER
# ──────────────────────────────────────────────────────────────────────────────

class ContractChangeReporter:
    """
    Generates structured and human-readable contract change reports.
    """

    # Action recommendations per change type
    _ACTIONS = {
        ChangeType.FIELD_REMOVED:
            "Search your codebase for references to this field. Add fallback "
            "defaults (e.g. `field ?? defaultValue`) or remove the dependency.",

        ChangeType.OBJECT_TO_PRIMITIVE:
            "Update your data model. Any code doing `field.subKey` will "
            "throw TypeError. Check all object destructuring for this field.",

        ChangeType.ARRAY_TO_NON_ARRAY:
            "Any `.map()`, `.forEach()`, or array spread on this field will "
            "throw. Update consumers to handle a scalar value.",

        ChangeType.NON_ARRAY_TO_ARRAY:
            "Wrap references in array guards. Update TypeScript types to `[]`.",

        ChangeType.TYPE_CHANGED:
            "Check all comparison operators and arithmetic using this field. "
            "Update TypeScript types and add runtime type guards.",

        ChangeType.NULL_TO_TYPED:
            "This is safe — update your TypeScript types to reflect the "
            "actual type (remove `| null` if the field is no longer nullable).",

        ChangeType.NEW_FIELD:
            "No immediate action required. Update TypeScript types to "
            "include this field so it can be used by consumers.",

        ChangeType.FIELD_BECAME_NULLABLE:
            "Add null-checks or optional chaining (?.) everywhere this "
            "field is accessed to prevent runtime errors.",
    }

    def generate(
        self,
        changes: List[ContractChange],
        endpoint: str = "",
    ) -> Dict:
        """
        Generate a structured report including a plain-English narrative.

        Returns:
            {
                "endpoint":  str,
                "summary":   str,
                "breaking":  int,
                "warnings":  int,
                "info":      int,
                "changes":   [{ change_type, severity, path, old_types,
                                new_types, explanation, action }],
                "narrative": str    ← human-readable multi-line report
            }
        """
        breaking = [c for c in changes if c.severity == Severity.BREAKING]
        warnings = [c for c in changes if c.severity == Severity.WARNING]
        info     = [c for c in changes if c.severity == Severity.INFO]

        change_dicts = []
        for c in changes:
            d = c.to_dict()
            d["action"] = self._ACTIONS.get(c.change_type, "Review the impact manually.")
            change_dicts.append(d)

        # Sort: BREAKING first, then WARNING, then INFO
        _order = {Severity.BREAKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
        change_dicts.sort(key=lambda x: _order.get(x["severity"], 3))

        narrative = self._build_narrative(changes, endpoint, breaking, warnings, info)
        summary = self._build_summary(endpoint, breaking, warnings, info)

        return {
            "endpoint":  endpoint,
            "summary":   summary,
            "breaking":  len(breaking),
            "warnings":  len(warnings),
            "info":      len(info),
            "changes":   change_dicts,
            "narrative": narrative,
        }

    def _build_summary(
        self,
        endpoint: str,
        breaking: List,
        warnings: List,
        info: List,
    ) -> str:
        parts = []
        if breaking:
            parts.append(f"{len(breaking)} breaking")
        if warnings:
            parts.append(f"{len(warnings)} warning(s)")
        if info:
            parts.append(f"{len(info)} informational")
        label = endpoint or "unknown endpoint"
        return f"{label}: {', '.join(parts)}" if parts else f"{label}: no changes"

    def _build_narrative(
        self,
        changes: List[ContractChange],
        endpoint: str,
        breaking: List,
        warnings: List,
        info: List,
    ) -> str:
        lines = []
        ep_label = f" for `{endpoint}`" if endpoint else ""
        total = len(changes)

        if not changes:
            return f"✅ No contract changes detected{ep_label}."

        lines.append(f"⚠️  Contract Change Report{ep_label}")
        lines.append(
            f"   {total} change(s): "
            f"{len(breaking)} breaking · {len(warnings)} warning(s) · {len(info)} informational"
        )
        lines.append("")

        for idx, change in enumerate(
            sorted(changes, key=lambda c: {Severity.BREAKING: 0, Severity.WARNING: 1, Severity.INFO: 2}[c.severity]),
            start=1
        ):
            icon = _SEVERITY_ICONS[change.severity]
            old_t = " | ".join(sorted(change.old_types)) or "null"
            new_t = " | ".join(sorted(change.new_types)) or "null"

            lines.append(f"  {idx}. {icon}")
            lines.append(f"     Path:      {change.path}")
            lines.append(f"     Old types: {old_t}{'  (nullable)' if change.old_nullable else ''}")
            lines.append(f"     New types: {new_t}{'  (nullable)' if change.new_nullable else ''}")
            lines.append(f"     Why:       {change.explanation}")
            action = self._ACTIONS.get(change.change_type, "Review manually.")
            lines.append(f"     Action:    {action}")
            lines.append("")

        if breaking:
            lines.append("━" * 56)
            lines.append(f"🚨 {len(breaking)} BREAKING change(s) require immediate attention.")
            affected = [c.path for c in breaking]
            lines.append(f"   Affected paths: {', '.join(affected)}")

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA REGISTRY
# ──────────────────────────────────────────────────────────────────────────────

class SchemaRegistry:
    """
    Stores and retrieves per-endpoint learned schemas.
    Supports optional JSON persistence so schemas survive server restarts.

    Schema format: the internal tree format produced by SchemaLearner
    (not OpenAPI — it is richer, tracking nullable/types_seen per field).
    """

    def __init__(self, persist_path: Optional[str] = None):
        """
        Args:
            persist_path: Path to a JSON file for persistence.
                          If None, schemas are only kept in memory.
        """
        self._schemas: Dict[str, Dict] = {}
        self._persist_path = persist_path

        if persist_path and os.path.exists(persist_path):
            self._load(persist_path)
            logger.info(
                f"📂 SchemaRegistry: loaded schemas for "
                f"{len(self._schemas)} endpoint(s) from {persist_path}"
            )

    # ── Read / Write ─────────────────────────────────────────────────────────

    def get(self, endpoint: str) -> Optional[Dict]:
        """Return the current schema for an endpoint, or None if unseen."""
        return self._schemas.get(endpoint)

    def set(self, endpoint: str, schema: Dict) -> None:
        """Store a fresh schema for an endpoint and optionally persist it."""
        self._schemas[endpoint] = schema
        if self._persist_path:
            self._save(self._persist_path)

    def has(self, endpoint: str) -> bool:
        return endpoint in self._schemas

    def all_endpoints(self) -> List[str]:
        return list(self._schemas.keys())

    def to_openapi_components(self) -> Dict:
        """
        Export all learned schemas as OpenAPI 3.0 component schemas.
        Useful for generating API documentation automatically.
        """
        components = {}
        for endpoint, schema in self._schemas.items():
            name = endpoint.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root"
            components[name] = self._node_to_openapi(schema)
        return {"components": {"schemas": components}}

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._schemas, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"⚠️ SchemaRegistry: could not save to {path}: {e}")

    def _load(self, path: str) -> None:
        try:
            with open(path, "r") as f:
                self._schemas = json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ SchemaRegistry: could not load from {path}: {e}")

    def flush(self) -> None:
        """Force an immediate save (call on server shutdown)."""
        if self._persist_path:
            self._save(self._persist_path)
            logger.info(
                f"💾 SchemaRegistry: flushed {len(self._schemas)} "
                f"endpoint schemas to {self._persist_path}"
            )

    # ── OpenAPI export ────────────────────────────────────────────────────────

    def _node_to_openapi(self, node: Dict) -> Dict:
        """Recursively convert an internal schema node to an OpenAPI schema object."""
        meta_raw = node.get(_META, {})
        meta = FieldDescriptor.from_dict(meta_raw)
        primary = meta.primary_type()

        if primary == "object":
            props = {}
            for key, child in node.items():
                if key in (_META, _ITEMS):
                    continue
                props[key] = self._node_to_openapi(child)
            result: Dict[str, Any] = {"type": "object", "properties": props}

        elif primary == "array":
            items_node = node.get(_ITEMS, {})
            result = {
                "type": "array",
                "items": self._node_to_openapi(items_node) if items_node else {}
            }

        elif primary in ("integer", "number", "string", "boolean"):
            result = {"type": primary}
            if meta.example is not None:
                result["example"] = meta.example

        else:
            result = {}

        if meta.nullable:
            result["nullable"] = True

        # Union types → oneOf
        if len(meta.types_seen) > 1:
            one_of = [{"type": t} for t in sorted(meta.types_seen)]
            if meta.nullable:
                one_of.append({"type": "null"})
            result = {"oneOf": one_of}

        return result


# ──────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETONS  (import these in proxy.py / learning.py)
# ──────────────────────────────────────────────────────────────────────────────

_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR   = os.path.join(_BASE_DIR, "..", "data")
_SCHEMA_PATH = os.path.join(_DATA_DIR, "schemas.json")

# Single shared instances used across the application
schema_registry = SchemaRegistry(persist_path=_SCHEMA_PATH)
schema_learner   = SchemaLearner()
schema_comparator = SchemaComparator()
contract_reporter = ContractChangeReporter()


# ──────────────────────────────────────────────────────────────────────────────
# RESPONSE-vs-SCHEMA DRIFT DETECTOR
# Compares the EXPECTED schema (learned over time) against the RAW response
# body (what we ACTUALLY got). This is the correct approach because:
#   - The SchemaLearner only ACCUMULATES types and fields; it never removes them.
#   - Comparing two accumulated schemas will therefore never detect field removal
#     or type narrowing because both snapshots look "same or richer."
#   - Comparing schema-vs-raw-response catches REAL drift: missing fields, new
#     fields, type changes relative to what we've learned to expect.
# ──────────────────────────────────────────────────────────────────────────────

def _detect_response_drift(
    schema_node: Dict,
    actual: Any,
    path: str = "$",
) -> List[ContractChange]:
    """
    Compare a learned schema tree against a raw API response and return
    a list of ContractChange objects for every difference found.

    This is fundamentally different from SchemaComparator.compare() which
    compares two schema trees. This compares a schema tree against raw data.
    """
    changes: List[ContractChange] = []

    if not isinstance(schema_node, dict) or _META not in schema_node:
        return changes

    meta = FieldDescriptor.from_dict(schema_node.get(_META, {}))

    # Need at least 2 observations to have a baseline worth comparing against
    if meta.occurrences < 2:
        return changes

    expected_primary = meta.primary_type()

    # ── If the schema expects an object, compare object keys ──────────────
    if expected_primary == "object" and isinstance(actual, dict):
        schema_keys = {k for k in schema_node if k not in (_META, _ITEMS)}
        actual_keys = set(actual.keys())

        # Fields we expected but are MISSING from the response
        for key in schema_keys - actual_keys:
            child_meta = FieldDescriptor.from_dict(
                schema_node[key].get(_META, {}) if isinstance(schema_node[key], dict) else {}
            )
            # Only flag as BREAKING if the field has been seen multiple times
            # (i.e., it's an established field, not a one-off)
            if child_meta.occurrences >= 2:
                changes.append(ContractChange(
                    change_type  = ChangeType.FIELD_REMOVED,
                    severity     = Severity.BREAKING,
                    path         = f"{path}.{key}",
                    old_types    = child_meta.types_seen,
                    new_types    = set(),
                    old_nullable = child_meta.nullable,
                    new_nullable = False,
                    explanation  = (
                        f"Field `{path}.{key}` (was `{'|'.join(sorted(child_meta.types_seen)) or 'null'}`, "
                        f"seen {child_meta.occurrences} times) is missing from this response. "
                        f"Client code reading this field will get `undefined`."
                    ),
                ))

        # Fields in the response that are NOT in our learned schema
        for key in actual_keys - schema_keys:
            actual_type = _json_type(actual[key])
            changes.append(ContractChange(
                change_type  = ChangeType.NEW_FIELD,
                severity     = Severity.INFO,
                path         = f"{path}.{key}",
                old_types    = set(),
                new_types    = {actual_type},
                old_nullable = False,
                new_nullable = actual[key] is None,
                explanation  = (
                    f"New field `{path}.{key}` (type: `{actual_type}`) appeared "
                    f"in the response. Update TypeScript types to include it."
                ),
            ))

        # Check TYPE CHANGES in common fields
        for key in schema_keys & actual_keys:
            child_schema = schema_node.get(key, {})
            if not isinstance(child_schema, dict) or _META not in child_schema:
                continue
            child_meta = FieldDescriptor.from_dict(child_schema.get(_META, {}))
            actual_value = actual[key]
            actual_type = _json_type(actual_value)

            # Skip null values — null is orthogonal to type
            if actual_value is None:
                continue

            # Check if the actual type was EVER seen before
            if child_meta.types_seen and actual_type not in child_meta.types_seen and child_meta.occurrences >= 2:
                child_primary = child_meta.primary_type()
                # Classify severity
                structural_break = (
                    (child_primary == "object" and actual_type != "object") or
                    (child_primary == "array" and actual_type != "array") or
                    (actual_type == "array" and child_primary != "array")
                )
                severity = Severity.BREAKING if structural_break else Severity.WARNING
                change_type = (
                    ChangeType.OBJECT_TO_PRIMITIVE if child_primary == "object" else
                    ChangeType.ARRAY_TO_NON_ARRAY if child_primary == "array" else
                    ChangeType.NON_ARRAY_TO_ARRAY if actual_type == "array" else
                    ChangeType.TYPE_CHANGED
                )
                changes.append(ContractChange(
                    change_type  = change_type,
                    severity     = severity,
                    path         = f"{path}.{key}",
                    old_types    = child_meta.types_seen,
                    new_types    = {actual_type},
                    old_nullable = child_meta.nullable,
                    new_nullable = False,
                    explanation  = (
                        f"Field `{path}.{key}` changed type from "
                        f"`{'|'.join(sorted(child_meta.types_seen))}` to `{actual_type}`. "
                        f"Seen {child_meta.occurrences} times before with the old type(s)."
                    ),
                ))

            # Recurse into nested objects
            if isinstance(actual_value, dict):
                changes.extend(_detect_response_drift(child_schema, actual_value, f"{path}.{key}"))
            elif isinstance(actual_value, list) and actual_value and _ITEMS in child_schema:
                # Check array items against learned item schema
                for i, item in enumerate(actual_value[:1]):  # Check first item
                    changes.extend(_detect_response_drift(child_schema[_ITEMS], item, f"{path}.{key}[{i}]"))

    # ── If schema expects an object but got a primitive ────────────────────
    elif expected_primary == "object" and not isinstance(actual, dict):
        actual_type = _json_type(actual)
        if actual is not None:  # null is OK for nullable fields
            changes.append(ContractChange(
                change_type  = ChangeType.OBJECT_TO_PRIMITIVE,
                severity     = Severity.BREAKING,
                path         = path,
                old_types    = meta.types_seen,
                new_types    = {actual_type},
                old_nullable = meta.nullable,
                new_nullable = False,
                explanation  = (
                    f"`{path}` was always an object but is now `{actual_type}`. "
                    f"Any code doing `field.subKey` will throw TypeError."
                ),
            ))

    # ── If schema expects an array but got non-array ──────────────────────
    elif expected_primary == "array" and not isinstance(actual, list):
        actual_type = _json_type(actual)
        if actual is not None:
            changes.append(ContractChange(
                change_type  = ChangeType.ARRAY_TO_NON_ARRAY,
                severity     = Severity.BREAKING,
                path         = path,
                old_types    = meta.types_seen,
                new_types    = {actual_type},
                old_nullable = meta.nullable,
                new_nullable = False,
                explanation  = (
                    f"`{path}` was always an array but is now `{actual_type}`. "
                    f"Any array iteration (.map, .forEach) will break."
                ),
            ))

    return changes


# ──────────────────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTION  (drop-in replacement for learning.py's learn_schema())
# ──────────────────────────────────────────────────────────────────────────────

def learn_and_compare(
    endpoint: str,
    response_body: Any,
    request_body: Any = None,
) -> Tuple[Dict, List[Dict]]:
    """
    High-level function that:
      1. Retrieves the current schema for the endpoint (what we EXPECT)
      2. Compares the EXPECTED schema against the RAW response (what we GOT)
      3. Learns from this response (accumulates into the schema)
      4. Persists the updated schema

    Drift detection: compares learned schema against raw response body.
    This correctly detects missing fields, new fields, and type changes
    because it checks the ACTUAL data — not two accumulated schemas.

    Returns:
        (updated_schema, list_of_change_dicts)
        list_of_change_dicts is [] if no changes or no previous schema.
    """
    import copy

    previous = schema_registry.get(endpoint)

    # ── Step 1: Detect drift BEFORE learning ──────────────────────────────
    # Compare the existing schema (our expectations) against the raw response.
    # Must happen BEFORE learn() updates the schema with this response's data.
    change_dicts: List[Dict] = []
    if previous is not None and isinstance(response_body, (dict, list)):
        raw_changes = _detect_response_drift(previous, response_body)
        change_dicts = [c.to_dict() for c in raw_changes]

        if raw_changes:
            report = contract_reporter.generate(raw_changes, endpoint=endpoint)
            if report["breaking"] > 0:
                logger.warning(f"🚨 CONTRACT DRIFT [{endpoint}]: {report['summary']}")
            elif report["warnings"] > 0:
                logger.warning(f"🟡 CONTRACT CHANGE [{endpoint}]: {report['summary']}")
            else:
                logger.info(f"🟢 SCHEMA INFO [{endpoint}]: {report['summary']}")
            logger.debug(f"\n{report['narrative']}")

    # ── Step 2: Learn from this response (accumulate into schema) ─────────
    updated = schema_learner.learn(
        copy.deepcopy(previous) if previous else None,
        response_body,
    )
    schema_registry.set(endpoint, updated)

    return updated, change_dicts
