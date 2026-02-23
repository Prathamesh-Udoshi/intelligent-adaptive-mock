import re

def normalize_path(path: str) -> str:
    """
    Normalizes a path by replacing dynamic segments with semantic placeholders.
    
    Detects and replaces:
    - UUIDs:        /users/550e8400-e29b-41d4-a716-446655440000  → /users/{id}
    - Numeric IDs:  /users/123/profile                           → /users/{id}/profile
    - Hex hashes:   /files/a1b2c3d4e5f6a7b8c9d0                 → /files/{hash}
    - Slugs:        /posts/my-first-blog-post                    → /posts/{slug}
    - Base64:       /confirm/eyJhbGciOiJIUz...                   → /confirm/{token}
    """
    # Step 1: Replace UUIDs first (most specific pattern)
    uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    path = re.sub(uuid_pattern, '{id}', path, flags=re.IGNORECASE)
    
    # Step 2: Normalize remaining segments
    segments = path.split('/')
    normalized_segments = []
    for seg in segments:
        if not seg:
            normalized_segments.append(seg)
            continue
            
        # Pure numeric IDs: 123, 42, 99999
        if seg.isdigit():
            normalized_segments.append('{id}')
        
        # Hex hashes: a1b2c3d4e5f6 (16+ hex chars, no hyphens)
        elif re.match(r'^[0-9a-f]{16,}$', seg, re.IGNORECASE) and not seg.isdigit():
            normalized_segments.append('{hash}')
        
        # Base64 tokens: eyJhbGciOi... (20+ Base64 chars, often contain + / =)
        elif re.match(r'^[A-Za-z0-9+/]{20,}={0,2}$', seg) and not seg.replace('-', '').replace('_', '').isalpha():
            normalized_segments.append('{token}')
        
        # URL-safe slugs: my-first-blog-post (lowercase, 2+ hyphens, 8+ chars)
        elif re.match(r'^[a-z0-9]+(-[a-z0-9]+){2,}$', seg) and len(seg) > 8:
            normalized_segments.append('{slug}')
        
        # Short numeric-alpha IDs: abc123, x9y (3-12 chars mixing letters and digits)
        elif re.match(r'^(?=.*[a-zA-Z])(?=.*\d)[a-zA-Z0-9]{3,12}$', seg):
            # Only normalize if it looks like a generated ID, not a word like "v2" or "api"
            if len(seg) >= 6:
                normalized_segments.append('{id}')
            else:
                normalized_segments.append(seg)
        
        else:
            normalized_segments.append(seg)
            
    res = '/'.join(normalized_segments)
    if not res.startswith('/'):
        res = '/' + res
    return res
