import re

def normalize_path(path: str) -> str:
    """
    Normalizes a path by replacing potential IDs (numbers, UUIDs) with placeholders.
    Example: /users/123/profile -> /users/{id}/profile
    """
    # Replace UUIDs
    uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    path = re.sub(uuid_pattern, '{id}', path, flags=re.IGNORECASE)
    
    # Replace numeric IDs
    # We look for path segments that are entirely numeric
    segments = path.split('/')
    normalized_segments = []
    for seg in segments:
        if seg.isdigit():
            normalized_segments.append('{id}')
        else:
            normalized_segments.append(seg)
            
    return '/'.join(normalized_segments)
