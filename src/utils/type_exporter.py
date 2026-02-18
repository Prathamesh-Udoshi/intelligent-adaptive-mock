"""
Type Exporter — Auto-Generate Client Types from Learned Schemas
================================================================
Converts learned JSON schemas (from schema_learner.py) into:
  - TypeScript interfaces
  - Python Pydantic models
  - JSON Schema documents

The exporter infers types from sample values and uses field-name heuristics
for smarter annotations (e.g., email fields, datetime strings, UUIDs).
"""

import re
import json
from datetime import datetime


# ──────────────────────────────────────────────────────
# NAMING HELPERS
# ──────────────────────────────────────────────────────

def _path_to_interface_name(path: str, method: str) -> str:
    """
    Converts an API path like '/users/{id}/orders' and method 'GET'
    into a clean interface name like 'GetUsersOrders'.
    Parameter placeholders like {id} are stripped.
    """
    # Remove parameter placeholders entirely
    clean = re.sub(r'\{[^}]+\}', '', path)
    # Split by separators
    parts = re.split(r'[/_\-]+', clean)
    # Filter empty and PascalCase each
    parts = [p.capitalize() for p in parts if p]
    # Prepend method
    name = method.capitalize() + ''.join(parts)
    return name


def _to_pascal_case(field_name: str) -> str:
    """Converts snake_case or kebab-case to PascalCase."""
    parts = re.split(r'[_\-]+', field_name)
    return ''.join(p.capitalize() for p in parts if p)


def _to_camel_case(field_name: str) -> str:
    """Converts snake_case to camelCase."""
    parts = re.split(r'[_\-]+', field_name)
    if not parts:
        return field_name
    return parts[0].lower() + ''.join(p.capitalize() for p in parts[1:])


# ──────────────────────────────────────────────────────
# TYPE INFERENCE FROM SAMPLE VALUES
# ──────────────────────────────────────────────────────

def _infer_ts_type(value, field_name: str = "") -> str:
    """Infers a TypeScript type from a sample value."""
    if value is None:
        return "any"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        # Check for datetime patterns
        if re.match(r'^\d{4}-\d{2}-\d{2}', value):
            return "string  // ISO 8601 datetime"
        # Check for UUID
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', value, re.IGNORECASE):
            return "string  // UUID"
        # Check for email
        if '@' in value and '.' in value:
            return "string  // email"
        # Check for URL
        if value.startswith(('http://', 'https://')):
            return "string  // URL"
        return "string"
    if isinstance(value, list):
        return "any[]"
    if isinstance(value, dict):
        return "Record<string, any>"
    return "any"


def _infer_python_type(value, field_name: str = "") -> str:
    """Infers a Python type annotation from a sample value."""
    if value is None:
        return "Any"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "List[Any]"
    if isinstance(value, dict):
        return "Dict[str, Any]"
    return "Any"


def _infer_json_schema_type(value, field_name: str = "") -> dict:
    """Infers a JSON Schema type descriptor from a sample value."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        schema = {"type": "string"}
        # Add format hints based on content
        if re.match(r'^\d{4}-\d{2}-\d{2}', value):
            schema["format"] = "date-time"
        elif re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', value, re.IGNORECASE):
            schema["format"] = "uuid"
        elif '@' in value and '.' in value:
            schema["format"] = "email"
        elif value.startswith(('http://', 'https://')):
            schema["format"] = "uri"
        return schema
    if isinstance(value, list):
        return {"type": "array", "items": {}}
    if isinstance(value, dict):
        return {"type": "object"}
    return {}


# ──────────────────────────────────────────────────────
# SHARED NAME REGISTRY (avoids conflicts with short names)
# ──────────────────────────────────────────────────────

def _unique_name(name: str, registry: set) -> str:
    """Returns a unique name, appending a number if there's a conflict."""
    if name not in registry:
        registry.add(name)
        return name
    i = 2
    while f"{name}{i}" in registry:
        i += 1
    unique = f"{name}{i}"
    registry.add(unique)
    return unique


# ──────────────────────────────────────────────────────
# TYPESCRIPT EXPORTER
# ──────────────────────────────────────────────────────

def schema_to_typescript(schema: dict, interface_name: str = "ApiResponse", indent: int = 0, _parent_field: str = "", _registry: set = None) -> str:
    """
    Converts a learned schema dict into a TypeScript interface definition.
    Uses short nested names: ParentField + CurrentField for disambiguation.
    """
    if _registry is None:
        _registry = set()

    if not schema or not isinstance(schema, dict):
        return f"export interface {interface_name} {{\n  [key: string]: any;\n}}\n"

    lines = []
    sub_interfaces = []
    prefix = "  " * (indent + 1)

    for key, value in schema.items():
        if key.startswith("_"):
            continue

        if isinstance(value, dict):
            # Short name: just ParentField + CurrentField (not the whole chain)
            if _parent_field:
                raw_name = _to_pascal_case(_parent_field) + _to_pascal_case(key)
            else:
                raw_name = interface_name + _to_pascal_case(key)
            sub_name = _unique_name(raw_name, _registry)
            sub_interfaces.append(schema_to_typescript(value, sub_name, indent, _parent_field=key, _registry=_registry))
            lines.append(f"{prefix}{key}: {sub_name};")

        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                if _parent_field:
                    raw_name = _to_pascal_case(_parent_field) + _to_pascal_case(key) + "Item"
                else:
                    raw_name = _to_pascal_case(key) + "Item"
                sub_name = _unique_name(raw_name, _registry)
                sub_interfaces.append(schema_to_typescript(value[0], sub_name, indent, _parent_field=key, _registry=_registry))
                lines.append(f"{prefix}{key}: {sub_name}[];")
            elif value:
                item_type = _infer_ts_type(value[0], key)
                lines.append(f"{prefix}{key}: {item_type.split('//')[0].strip()}[];")
            else:
                lines.append(f"{prefix}{key}: any[];")

        else:
            ts_type = _infer_ts_type(value, key)
            lines.append(f"{prefix}{key}: {ts_type};")

    body = "\n".join(lines)
    main = f"export interface {interface_name} {{\n{body}\n}}\n"

    parts = sub_interfaces + [main]
    return "\n".join(parts)


def export_all_typescript(endpoints: list) -> str:
    """
    Generates TypeScript interfaces for all endpoints.
    
    Each endpoint dict should have:
      - method: str
      - path_pattern: str
      - request_schema: dict (optional)
      - response_schema: dict (optional)
    """
    output_lines = [
        "// ════════════════════════════════════════════════════════════",
        "// Auto-Generated TypeScript Interfaces",
        f"// Generated from learned API traffic on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "// Intelligent Adaptive Mock Platform",
        "// ════════════════════════════════════════════════════════════",
        "",
    ]

    for ep in endpoints:
        method = ep.get("method", "GET")
        path = ep.get("path_pattern", "/unknown")
        base_name = _path_to_interface_name(path, method)

        output_lines.append(f"// ── {method} {path}")
        output_lines.append("")

        req_schema = ep.get("request_schema")
        if req_schema and isinstance(req_schema, dict) and len(req_schema) > 0:
            req_ts = schema_to_typescript(req_schema, f"{base_name}Request")
            output_lines.append(req_ts)

        resp_schema = ep.get("response_schema")
        if resp_schema and isinstance(resp_schema, dict) and len(resp_schema) > 0:
            resp_ts = schema_to_typescript(resp_schema, f"{base_name}Response")
            output_lines.append(resp_ts)

        output_lines.append("")

    return "\n".join(output_lines)


# ──────────────────────────────────────────────────────
# PYDANTIC EXPORTER
# ──────────────────────────────────────────────────────

def schema_to_pydantic(schema: dict, class_name: str = "ApiResponse", _parent_field: str = "", _registry: set = None) -> str:
    """
    Converts a learned schema dict into a Pydantic model definition.
    Uses short nested names with conflict resolution.
    """
    if _registry is None:
        _registry = set()

    if not schema or not isinstance(schema, dict):
        return f"class {class_name}(BaseModel):\n    pass\n"

    lines = []
    sub_models = []

    for key, value in schema.items():
        if key.startswith("_"):
            continue

        if isinstance(value, dict):
            if _parent_field:
                raw_name = _to_pascal_case(_parent_field) + _to_pascal_case(key)
            else:
                raw_name = class_name + _to_pascal_case(key)
            sub_name = _unique_name(raw_name, _registry)
            sub_models.append(schema_to_pydantic(value, sub_name, _parent_field=key, _registry=_registry))
            lines.append(f"    {key}: {sub_name}")

        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                if _parent_field:
                    raw_name = _to_pascal_case(_parent_field) + _to_pascal_case(key) + "Item"
                else:
                    raw_name = _to_pascal_case(key) + "Item"
                sub_name = _unique_name(raw_name, _registry)
                sub_models.append(schema_to_pydantic(value[0], sub_name, _parent_field=key, _registry=_registry))
                lines.append(f"    {key}: List[{sub_name}]")
            elif value:
                item_type = _infer_python_type(value[0], key)
                lines.append(f"    {key}: List[{item_type}]")
            else:
                lines.append(f"    {key}: List[Any]")

        else:
            py_type = _infer_python_type(value, key)
            lines.append(f"    {key}: {py_type}")

    body = "\n".join(lines) if lines else "    pass"
    main = f"class {class_name}(BaseModel):\n{body}\n"

    parts = sub_models + [main]
    return "\n".join(parts)


def export_all_pydantic(endpoints: list) -> str:
    """
    Generates Pydantic models for all endpoints.
    """
    output_lines = [
        "# ════════════════════════════════════════════════════════════",
        "# Auto-Generated Pydantic Models",
        f"# Generated from learned API traffic on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "# Intelligent Adaptive Mock Platform",
        "# ════════════════════════════════════════════════════════════",
        "",
        "from __future__ import annotations",
        "from typing import Any, Dict, List, Optional",
        "from pydantic import BaseModel",
        "",
    ]

    for ep in endpoints:
        method = ep.get("method", "GET")
        path = ep.get("path_pattern", "/unknown")
        base_name = _path_to_interface_name(path, method)

        output_lines.append(f"# ── {method} {path}")
        output_lines.append("")

        req_schema = ep.get("request_schema")
        if req_schema and isinstance(req_schema, dict) and len(req_schema) > 0:
            req_py = schema_to_pydantic(req_schema, f"{base_name}Request")
            output_lines.append(req_py)

        resp_schema = ep.get("response_schema")
        if resp_schema and isinstance(resp_schema, dict) and len(resp_schema) > 0:
            resp_py = schema_to_pydantic(resp_schema, f"{base_name}Response")
            output_lines.append(resp_py)

        output_lines.append("")

    return "\n".join(output_lines)


# ──────────────────────────────────────────────────────
# JSON SCHEMA EXPORTER
# ──────────────────────────────────────────────────────

def schema_to_json_schema(schema: dict, title: str = "ApiResponse") -> dict:
    """
    Converts a learned schema dict into a JSON Schema document.
    """
    if not schema or not isinstance(schema, dict):
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            "type": "object",
            "properties": {}
        }

    properties = {}
    required_fields = []

    for key, value in schema.items():
        if key.startswith("_"):
            continue

        required_fields.append(key)

        if isinstance(value, dict):
            properties[key] = schema_to_json_schema(value, title=_to_pascal_case(key))

        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                properties[key] = {
                    "type": "array",
                    "items": schema_to_json_schema(value[0], title=_to_pascal_case(key) + "Item")
                }
            elif value:
                properties[key] = {
                    "type": "array",
                    "items": _infer_json_schema_type(value[0], key)
                }
            else:
                properties[key] = {"type": "array", "items": {}}

        else:
            properties[key] = _infer_json_schema_type(value, key)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "type": "object",
        "properties": properties,
        "required": required_fields
    }


def export_all_json_schema(endpoints: list) -> dict:
    """
    Generates JSON Schema for all endpoints.
    Returns a dict with endpoint keys mapping to their request/response schemas.
    """
    result = {
        "_meta": {
            "generator": "Intelligent Adaptive Mock Platform",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "schema_version": "https://json-schema.org/draft/2020-12/schema"
        },
        "endpoints": {}
    }

    for ep in endpoints:
        method = ep.get("method", "GET")
        path = ep.get("path_pattern", "/unknown")
        base_name = _path_to_interface_name(path, method)
        key = f"{method} {path}"

        endpoint_schemas = {}

        req_schema = ep.get("request_schema")
        if req_schema and isinstance(req_schema, dict) and len(req_schema) > 0:
            endpoint_schemas["request"] = schema_to_json_schema(req_schema, f"{base_name}Request")

        resp_schema = ep.get("response_schema")
        if resp_schema and isinstance(resp_schema, dict) and len(resp_schema) > 0:
            endpoint_schemas["response"] = schema_to_json_schema(resp_schema, f"{base_name}Response")

        if endpoint_schemas:
            result["endpoints"][key] = endpoint_schemas

    return result
