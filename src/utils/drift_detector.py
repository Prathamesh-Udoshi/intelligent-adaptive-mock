"""
Contract Drift Detection Engine
================================
Compares real API responses against learned schemas to detect breaking changes.

Features:
  - Structural drift detection (missing fields, new fields, type changes)
  - Severity scoring (0-100 scale)
  - AI Narrator: Converts technical drift details into plain-English, actionable summaries
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime


def detect_schema_drift(learned_schema: Optional[Dict], actual_response: Any) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Detects if the actual response has drifted from the learned schema.
    
    Returns:
        (has_drift, drift_details): Tuple of boolean and list of drift issues
    """
    if not learned_schema:
        return False, []
    
    if not isinstance(actual_response, dict):
        if not isinstance(learned_schema, dict):
            return False, []
        return True, [{"type": "type_mismatch", "path": "$", "expected": "object", "actual": type(actual_response).__name__}]
    
    drift_issues = []
    _compare_schemas(learned_schema, actual_response, "$", drift_issues)
    
    return len(drift_issues) > 0, drift_issues


def _compare_schemas(learned: Dict, actual: Dict, path: str, issues: List[Dict]) -> None:
    """
    Recursively compares learned schema against actual response.
    """
    # Check for missing fields (fields that were in learned schema but not in actual)
    learned_keys = set(learned.keys())
    actual_keys = set(actual.keys())
    
    missing_fields = learned_keys - actual_keys
    for field in missing_fields:
        issues.append({
            "type": "missing_field",
            "path": f"{path}.{field}",
            "severity": "high",
            "message": f"Field '{field}' was expected but is missing from response"
        })
    
    # Check for new fields (fields in actual but not in learned schema)
    new_fields = actual_keys - learned_keys
    for field in new_fields:
        issues.append({
            "type": "new_field",
            "path": f"{path}.{field}",
            "severity": "low",
            "message": f"New field '{field}' detected in response"
        })
    
    # Check for type changes in common fields
    common_fields = learned_keys & actual_keys
    for field in common_fields:
        learned_value = learned[field]
        actual_value = actual[field]
        field_path = f"{path}.{field}"
        
        # Handle nested objects
        if isinstance(learned_value, dict) and isinstance(actual_value, dict):
            _compare_schemas(learned_value, actual_value, field_path, issues)
        elif isinstance(learned_value, dict) and not isinstance(actual_value, dict):
            issues.append({
                "type": "type_change",
                "path": field_path,
                "severity": "high",
                "expected": "object",
                "actual": type(actual_value).__name__,
                "message": f"Field '{field}' changed from object to {type(actual_value).__name__}"
            })
        elif isinstance(learned_value, list) and isinstance(actual_value, list):
            # Check array item structure if both have items
            if learned_value and actual_value and isinstance(learned_value[0], dict) and isinstance(actual_value[0], dict):
                _compare_schemas(learned_value[0], actual_value[0], f"{field_path}[0]", issues)
        elif isinstance(learned_value, list) and not isinstance(actual_value, list):
            issues.append({
                "type": "type_change",
                "path": field_path,
                "severity": "high",
                "expected": "array",
                "actual": type(actual_value).__name__,
                "message": f"Field '{field}' changed from array to {type(actual_value).__name__}"
            })
        elif type(learned_value) != type(actual_value):
            # Type mismatch for primitive values
            issues.append({
                "type": "type_change",
                "path": field_path,
                "severity": "medium",
                "expected": type(learned_value).__name__,
                "actual": type(actual_value).__name__,
                "message": f"Field '{field}' type changed from {type(learned_value).__name__} to {type(actual_value).__name__}"
            })


def calculate_drift_score(drift_issues: List[Dict[str, Any]]) -> float:
    """
    Calculates a drift severity score (0-100) based on detected issues.
    Higher score = more severe drift.
    """
    if not drift_issues:
        return 0.0
    
    severity_weights = {
        "high": 10.0,
        "medium": 5.0,
        "low": 1.0
    }
    
    total_score = sum(severity_weights.get(issue.get("severity", "low"), 1.0) for issue in drift_issues)
    
    # Normalize to 0-100 scale (cap at 100)
    return min(100.0, total_score)


def format_drift_summary(drift_issues: List[Dict[str, Any]]) -> str:
    """
    Creates a human-readable summary of drift issues.
    """
    if not drift_issues:
        return "No drift detected"
    
    high_severity = [i for i in drift_issues if i.get("severity") == "high"]
    medium_severity = [i for i in drift_issues if i.get("severity") == "medium"]
    low_severity = [i for i in drift_issues if i.get("severity") == "low"]
    
    parts = []
    if high_severity:
        parts.append(f"{len(high_severity)} critical issue(s)")
    if medium_severity:
        parts.append(f"{len(medium_severity)} warning(s)")
    if low_severity:
        parts.append(f"{len(low_severity)} minor change(s)")
    
    return ", ".join(parts)


# ──────────────────────────────────────────────────────
# AI CONTRACT CHANGE NARRATOR
# Converts raw drift details into actionable plain-English explanations.
# ──────────────────────────────────────────────────────

# Maps field name patterns to human-friendly domain context
_FIELD_CONTEXT = {
    "avatar": "user profile images",
    "email": "email addresses",
    "name": "display names",
    "id": "unique identifiers",
    "uuid": "unique identifiers",
    "token": "authentication tokens",
    "password": "credentials",
    "price": "pricing information",
    "amount": "monetary values",
    "total": "totals/aggregates",
    "status": "status tracking",
    "created": "creation timestamps",
    "updated": "update timestamps",
    "url": "links/URLs",
    "image": "image assets",
    "phone": "phone numbers",
    "address": "addresses",
    "role": "user permissions",
    "type": "entity categorization",
    "count": "counts/quantities",
    "data": "response payloads",
    "items": "list items",
    "results": "query results",
    "error": "error handling",
    "message": "messaging",
    "description": "descriptions",
    "title": "titles/headings",
}

# Impact explanations by drift type
_IMPACT_TEMPLATES = {
    "missing_field": {
        "high_impact": "This will break any UI component that renders or references this field.",
        "action": "Add a null-check or optional chaining (?.) for this field in your frontend code."
    },
    "new_field": {
        "high_impact": "This is typically safe, but may indicate an upcoming API version migration.",
        "action": "Consider updating your TypeScript types to include this new field."
    },
    "type_change": {
        "high_impact": "Any strict comparisons (===) or type-dependent logic will fail silently.",
        "action": "Update the field type in your data model and check all components using this field."
    },
    "type_mismatch": {
        "high_impact": "The entire response shape has changed, breaking all consumers.",
        "action": "This is a major breaking change — coordinate with the backend team immediately."
    }
}

# Severity labels
_SEVERITY_LABELS = {
    "high": "🔴 BREAKING",
    "medium": "🟡 WARNING",
    "low": "🟢 INFO"
}


def _extract_field_name(path: str) -> str:
    """Extracts the last field name from a JSON path like $.data.users[0].avatar_url"""
    # Remove array indices and split
    clean = path.replace("[0]", "").replace("[", ".").replace("]", "")
    parts = clean.split(".")
    return parts[-1] if parts else path


def _get_field_context(field_name: str) -> str:
    """Gets a human-friendly context hint for a field name."""
    lower = field_name.lower()
    for pattern, context in _FIELD_CONTEXT.items():
        if pattern in lower:
            return context
    return None


def _humanize_type(type_name: str) -> str:
    """Converts Python type names to friendly labels."""
    type_map = {
        "str": "text (string)",
        "int": "number (integer)",
        "float": "number (decimal)",
        "bool": "boolean (true/false)",
        "NoneType": "null/empty",
        "list": "array/list",
        "dict": "object",
        "object": "object",
        "array": "array/list",
    }
    return type_map.get(type_name, type_name)


def narrate_drift(drift_issues: List[Dict[str, Any]], endpoint_path: str = "") -> str:
    """
    AI Narrator: Converts technical drift details into a plain-English,
    actionable report for developers and non-technical stakeholders.
    
    Args:
        drift_issues: List of drift issue dicts from detect_schema_drift()
        endpoint_path: Optional endpoint path for context (e.g., "/users/{id}")
    
    Returns:
        A multi-line human-readable narrative string.
    """
    if not drift_issues:
        return "✅ No contract changes detected. The API response matches the learned schema."
    
    # Group issues by severity
    high = [i for i in drift_issues if i.get("severity") == "high"]
    medium = [i for i in drift_issues if i.get("severity") == "medium"]
    low = [i for i in drift_issues if i.get("severity") == "low"]
    
    lines = []
    
    # Header
    endpoint_label = f" for {endpoint_path}" if endpoint_path else ""
    total = len(drift_issues)
    lines.append(f"⚠️ Contract Change Detected{endpoint_label}")
    lines.append(f"   {total} change(s) found: {len(high)} breaking, {len(medium)} warnings, {len(low)} informational")
    lines.append("")
    
    # Detail each issue
    for idx, issue in enumerate(drift_issues, 1):
        issue_type = issue.get("type", "unknown")
        severity = issue.get("severity", "low")
        path = issue.get("path", "$")
        field_name = _extract_field_name(path)
        severity_label = _SEVERITY_LABELS.get(severity, "⚪ UNKNOWN")
        
        lines.append(f"  {idx}. {severity_label}: {_format_issue_headline(issue, field_name)}")
        
        # Add field context if available
        context = _get_field_context(field_name)
        if context:
            lines.append(f"     → This field is related to {context}.")
        
        # Add location
        lines.append(f"     📍 Location: {path}")
        
        # Add impact and action
        templates = _IMPACT_TEMPLATES.get(issue_type, {})
        if templates:
            lines.append(f"     💥 Impact: {templates.get('high_impact', '')}")
            lines.append(f"     🔧 Action: {templates.get('action', '')}")
        
        lines.append("")
    
    # Summary recommendation
    if high:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🚨 RECOMMENDED: {len(high)} breaking change(s) require immediate frontend updates.")
        
        # Collect affected components hint
        affected_fields = [_extract_field_name(i["path"]) for i in high]
        lines.append(f"   Affected fields: {', '.join(affected_fields)}")
        lines.append(f"   Search your codebase for these field names to find components that need updates.")
    
    return "\n".join(lines)


def _format_issue_headline(issue: Dict, field_name: str) -> str:
    """Creates a clear one-line headline for a drift issue."""
    issue_type = issue.get("type", "unknown")
    
    if issue_type == "missing_field":
        return f'The "{field_name}" field has been REMOVED from the response'
    
    elif issue_type == "new_field":
        return f'A new "{field_name}" field has APPEARED in the response'
    
    elif issue_type == "type_change":
        expected = _humanize_type(issue.get("expected", "?"))
        actual = _humanize_type(issue.get("actual", "?"))
        return f'The "{field_name}" field CHANGED TYPE from {expected} to {actual}'
    
    elif issue_type == "type_mismatch":
        expected = _humanize_type(issue.get("expected", "?"))
        actual = _humanize_type(issue.get("actual", "?"))
        return f'Response root type changed from {expected} to {actual}'
    
    return issue.get("message", f"Unknown change in {field_name}")
