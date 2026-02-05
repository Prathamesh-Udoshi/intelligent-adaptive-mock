def learn_schema(current_schema, new_body):
    """
    Improves schema learning by capturing structure and sample values.
    """
    if not isinstance(new_body, dict):
        return {"_type": type(new_body).__name__, "_sample": new_body}

    if current_schema is None or not isinstance(current_schema, dict):
        current_schema = {}

    for k, v in new_body.items():
        if isinstance(v, dict):
            current_schema[k] = learn_schema(current_schema.get(k), v)
        elif isinstance(v, list):
            if v:
                # Capture the structure of the first element as the item type
                current_schema[k] = [learn_schema(None, v[0])]
            else:
                current_schema[k] = []
        else:
            current_schema[k] = v # Store the last seen value as the sample
            
    return current_schema

def generate_mock_response(schema, request_data=None):
    """
    Generates a mock response and echoes values from request_data if possible.
    """
    if not schema:
        return {"status": "success"}
    
    response = _deep_copy_and_correlate(schema, request_data or {})
    return response

def _deep_copy_and_correlate(schema_node, source):
    if not isinstance(schema_node, dict):
        return schema_node
    
    result = {}
    for k, v in schema_node.items():
        # Echoing Logic: If a key in the response matches a key in the request, echo it!
        if source and k in source and not isinstance(source[k], (dict, list)):
            result[k] = source[k]
        elif isinstance(v, dict):
            result[k] = _deep_copy_and_correlate(v, source.get(k) if isinstance(source, dict) else None)
        elif isinstance(v, list) and v:
            # Generate a small list based on the template
            result[k] = [_deep_copy_and_correlate(v[0], None) for _ in range(2)]
        else:
            result[k] = v
            
    return result
