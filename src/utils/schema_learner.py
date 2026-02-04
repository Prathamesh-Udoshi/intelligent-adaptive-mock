import json

def learn_schema(current_schema, new_body):
    """
    Very basic schema learning. It merges the structure of new_body into current_schema.
    Returns a representative dictionary.
    """
    if not isinstance(new_body, dict):
        return current_schema or new_body

    if current_schema is None:
        return new_body

    # Simple merge: existing keys stay, new keys are added.
    # In a real production system, you'd use something like genson for full JSON Schema.
    merged = current_schema.copy()
    for k, v in new_body.items():
        if k not in merged:
            merged[k] = v
        elif isinstance(v, dict) and isinstance(merged[k], dict):
            merged[k] = learn_schema(merged[k], v)
            
    return merged

def generate_mock_response(schema):
    """
    Generates a mock response from a learned schema.
    For simplicity, just returns the schema itself as it contains representative data.
    """
    return schema or {"status": "success"}
