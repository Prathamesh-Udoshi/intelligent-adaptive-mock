"""
Contract Drift Detection Engine
Compares real API responses against learned schemas to detect breaking changes.
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
