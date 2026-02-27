"""
Smart Schema Learner & Mock Data Generator
==========================================
Learns JSON structure from real API traffic and generates varied, realistic mock data
using field-name heuristics — no external LLM required.

The learner tracks:
  - Field structure (nested objects, arrays)
  - Value types and sample values
  - Value statistics (min/max for numbers, lengths for strings)
  - Observed enum values (for fields with limited distinct values)

The generator uses field-name pattern matching to produce realistic data:
  - *email* → random email addresses
  - *name*  → random human names
  - *id*    → UUIDs or auto-incrementing integers
  - *date*  → recent ISO datetimes
  - *price* → random floats in observed range
  - *url*   → placeholder URLs
  - etc.
"""

import random
import string
import uuid
import time
from datetime import datetime, timedelta


# ──────────────────────────────────────────────────────
# FIELD-NAME HEURISTIC PATTERNS
# Maps field name patterns to semantic types for smart generation.
# Order matters — first match wins.
# ──────────────────────────────────────────────────────

_FIELD_PATTERNS = [
    # Identifiers
    (["uuid"],                                          "uuid"),
    (["_id", "id"],                                     "id"),
    
    # Contact / Personal
    (["email", "e_mail", "mail"],                       "email"),
    (["phone", "mobile", "tel", "fax"],                 "phone"),
    (["first_name", "firstname", "fname"],              "first_name"),
    (["last_name", "lastname", "lname", "surname"],     "last_name"),
    (["full_name", "fullname", "display_name",
     "displayname", "username", "user_name",
     "author", "owner", "name"],                        "full_name"),
    
    # URLs and images
    (["avatar", "photo", "image", "img",
     "thumbnail", "thumb", "picture", "pic",
     "logo", "icon", "banner", "cover"],                "image_url"),
    (["url", "link", "href", "website",
     "homepage", "uri", "endpoint", "callback"],        "url"),
    
    # Date/Time
    (["created_at", "createdat", "created",
     "date_created", "creation_date",
     "registered", "signup_date", "joined"],            "datetime_past"),
    (["updated_at", "updatedat", "modified",
     "modified_at", "last_modified", "edited_at",
     "last_seen", "last_login", "last_active"],         "datetime_recent"),
    (["expires", "expiry", "expires_at",
     "expiration", "valid_until", "due_date",
     "deadline", "scheduled_at"],                       "datetime_future"),
    (["date", "time", "timestamp", "datetime",
     "at", "when"],                                     "datetime_past"),
    
    # Monetary / Numeric
    (["price", "cost", "amount", "total",
     "subtotal", "tax", "fee", "charge",
     "balance", "salary", "wage", "revenue",
     "discount", "tip"],                                "money"),
    (["currency", "currency_code"],                     "currency"),
    (["count", "total", "quantity", "qty",
     "num", "number", "size", "length",
     "followers", "following", "friends",
     "likes", "views", "downloads",
     "rating", "score", "rank", "level",
     "age", "year"],                                    "positive_int"),
    (["lat", "latitude"],                               "latitude"),
    (["lng", "lon", "longitude"],                       "longitude"),
    (["percent", "percentage", "ratio", "rate"],        "percentage"),
    
    # Text content
    (["title", "subject", "headline", "heading"],       "title"),
    (["description", "desc", "summary",
     "abstract", "excerpt", "overview",
     "bio", "about", "blurb"],                          "description"),
    (["body", "content", "text", "message",
     "comment", "note", "details",
     "instructions", "remarks"],                        "paragraph"),
    (["tag", "label", "category", "type",
     "kind", "group", "role"],                          "tag"),
    
    # Status / State
    (["status", "state", "phase"],                      "status"),
    (["active", "enabled", "visible",
     "published", "verified", "confirmed",
     "approved", "available", "online",
     "is_active", "is_enabled"],                        "boolean_true"),
    (["deleted", "archived", "disabled",
     "blocked", "banned", "suspended",
     "is_deleted", "is_archived"],                      "boolean_false"),
    
    # Address
    (["city"],                                          "city"),
    (["state", "province", "region"],                   "state"),
    (["country", "country_code", "nation"],             "country"),
    (["zip", "zipcode", "zip_code",
     "postal", "postal_code", "postcode"],              "zip_code"),
    (["address", "street", "address_line"],             "address"),
    
    # Tokens / Hashes
    (["token", "access_token", "refresh_token",
     "api_key", "apikey", "secret",
     "session", "session_id", "jwt"],                   "token"),
    (["hash", "checksum", "md5", "sha",
     "sha256", "sha1", "digest", "fingerprint"],        "hash"),
    
    # Color
    (["color", "colour", "hex_color",
     "background", "bg_color"],                         "color"),
    
    # IP / Network
    (["ip", "ip_address", "ipv4",
     "remote_addr", "client_ip"],                       "ipv4"),
]


# ──────────────────────────────────────────────────────
# DATA POOLS (for varied but realistic output)
# ──────────────────────────────────────────────────────

_FIRST_NAMES = [
    "Aarav", "Sophia", "Liam", "Aisha", "Mateo", "Yuki", "Oliver", "Mei",
    "Noah", "Zara", "Ethan", "Priya", "Lucas", "Sara", "Arjun", "Elena",
    "Kai", "Amara", "Leo", "Ananya", "James", "Luna", "Raj", "Isla",
    "Omar", "Chloe", "Ravi", "Hana", "Daniel", "Fatima"
]

_LAST_NAMES = [
    "Patel", "Kim", "Garcia", "Chen", "Smith", "Müller", "Tanaka", "Singh",
    "Johnson", "Ali", "Williams", "Nakamura", "Brown", "Lee", "Wilson",
    "Kumar", "Silva", "Andersen", "Martinez", "Wang", "Taylor", "Gupta",
    "Hernandez", "Park", "Thompson", "Shah", "Rodriguez", "Sato", "Moore", "Das"
]

_DOMAINS = [
    "gmail.com", "outlook.com", "company.io", "example.org", "mail.dev",
    "proton.me", "fastmail.com", "hey.com", "icloud.com", "pm.me"
]

_CITIES = [
    "San Francisco", "London", "Tokyo", "Mumbai", "Berlin", "Toronto",
    "Sydney", "Singapore", "Amsterdam", "Seoul", "Dubai", "São Paulo",
    "Stockholm", "Austin", "Barcelona", "Bangalore", "Paris", "New York"
]

_COUNTRIES = [
    "US", "GB", "JP", "IN", "DE", "CA", "AU", "SG", "NL", "KR",
    "AE", "BR", "SE", "ES", "FR", "IT", "CH", "NO", "DK", "FI"
]

_STATUSES = ["active", "pending", "inactive", "completed", "processing", "draft"]

_TITLES = [
    "Getting Started with the API", "Quarterly Performance Report",
    "Project Update: Phase 2", "New Feature Announcement",
    "Infrastructure Migration Plan", "Team Standup Notes",
    "Customer Feedback Summary", "Product Roadmap Q3",
    "Security Audit Results", "Release Notes v2.4"
]

_TAGS = [
    "featured", "important", "beta", "stable", "experimental",
    "premium", "free", "popular", "trending", "new",
    "admin", "user", "moderator", "editor", "viewer"
]

_DESCRIPTIONS = [
    "A comprehensive overview of the latest updates and improvements.",
    "This resource provides detailed information about the service.",
    "Automatically generated content based on observed API patterns.",
    "Key insights derived from production traffic analysis.",
    "A curated collection of data points for this entity."
]

_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
    "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9"
]

_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF"]

_STATES_US = [
    "CA", "NY", "TX", "FL", "WA", "IL", "MA", "CO", "GA", "PA"
]

# Auto-incrementing counter for sequential IDs
_id_counter = random.randint(1000, 9999)


# ──────────────────────────────────────────────────────
# SEMANTIC TYPE DETECTION
# ──────────────────────────────────────────────────────

def _detect_semantic_type(field_name: str) -> str:
    """
    Detects the semantic type of a field based on its name.
    Returns the semantic type string, or 'unknown' if no match.
    """
    lower = field_name.lower().strip()
    
    for patterns, semantic_type in _FIELD_PATTERNS:
        for pattern in patterns:
            # Exact match or suffix/contains match
            if lower == pattern:
                return semantic_type
            if lower.endswith(pattern) or lower.startswith(pattern):
                return semantic_type
            # Check for pattern within the name (e.g., "user_email_address" contains "email")
            if f"_{pattern}" in lower or f"{pattern}_" in lower:
                return semantic_type
    
    return "unknown"


# ──────────────────────────────────────────────────────
# SMART VALUE GENERATORS
# ──────────────────────────────────────────────────────

def _generate_smart_value(field_name: str, sample_value=None):
    """
    Generates a realistic value based on the field name and optionally the sample value type.
    Falls back to the sample value if no semantic type is detected.
    """
    global _id_counter
    sem_type = _detect_semantic_type(field_name)
    
    if sem_type == "uuid":
        return str(uuid.uuid4())
    
    elif sem_type == "id":
        # If the sample was a string UUID, generate UUID; if int, generate sequential int
        if isinstance(sample_value, str) and len(sample_value) > 10:
            return str(uuid.uuid4())
        _id_counter += 1
        return _id_counter
    
    elif sem_type == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last = random.choice(_LAST_NAMES).lower()
        domain = random.choice(_DOMAINS)
        return f"{first}.{last}@{domain}"
    
    elif sem_type == "phone":
        return f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"
    
    elif sem_type == "first_name":
        return random.choice(_FIRST_NAMES)
    
    elif sem_type == "last_name":
        return random.choice(_LAST_NAMES)
    
    elif sem_type == "full_name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    
    elif sem_type == "image_url":
        img_id = random.randint(1, 1000)
        return f"https://picsum.photos/seed/{img_id}/200/200"
    
    elif sem_type == "url":
        slug = ''.join(random.choices(string.ascii_lowercase, k=8))
        return f"https://example.com/{slug}"
    
    elif sem_type == "datetime_past":
        days_ago = random.randint(1, 365)
        dt = datetime.utcnow() - timedelta(days=days_ago, seconds=random.randint(0, 86400))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    elif sem_type == "datetime_recent":
        hours_ago = random.randint(1, 72)
        dt = datetime.utcnow() - timedelta(hours=hours_ago, seconds=random.randint(0, 3600))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    elif sem_type == "datetime_future":
        days_ahead = random.randint(1, 90)
        dt = datetime.utcnow() + timedelta(days=days_ahead, seconds=random.randint(0, 86400))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    elif sem_type == "money":
        if isinstance(sample_value, (int, float)):
            # Generate around the same order of magnitude
            magnitude = max(1.0, abs(sample_value))
            return round(random.uniform(magnitude * 0.5, magnitude * 1.5), 2)
        return round(random.uniform(9.99, 499.99), 2)
    
    elif sem_type == "currency":
        return random.choice(_CURRENCIES)
    
    elif sem_type == "positive_int":
        if isinstance(sample_value, (int, float)) and sample_value > 0:
            magnitude = max(1, int(abs(sample_value)))
            return random.randint(max(0, magnitude // 2), magnitude * 2)
        return random.randint(0, 100)
    
    elif sem_type == "latitude":
        return round(random.uniform(-90.0, 90.0), 6)
    
    elif sem_type == "longitude":
        return round(random.uniform(-180.0, 180.0), 6)
    
    elif sem_type == "percentage":
        return round(random.uniform(0, 100), 1)
    
    elif sem_type == "title":
        return random.choice(_TITLES)
    
    elif sem_type == "description":
        return random.choice(_DESCRIPTIONS)
    
    elif sem_type == "paragraph":
        sentences = random.sample(_DESCRIPTIONS, min(3, len(_DESCRIPTIONS)))
        return " ".join(sentences)
    
    elif sem_type == "tag":
        return random.choice(_TAGS)
    
    elif sem_type == "status":
        return random.choice(_STATUSES)
    
    elif sem_type == "boolean_true":
        return random.random() > 0.15  # 85% true
    
    elif sem_type == "boolean_false":
        return random.random() > 0.85  # 85% false
    
    elif sem_type == "city":
        return random.choice(_CITIES)
    
    elif sem_type == "state":
        return random.choice(_STATES_US)
    
    elif sem_type == "country":
        return random.choice(_COUNTRIES)
    
    elif sem_type == "zip_code":
        return f"{random.randint(10000, 99999)}"
    
    elif sem_type == "address":
        num = random.randint(1, 9999)
        street = f"{random.choice(_LAST_NAMES)} {'St' if random.random() > 0.5 else 'Ave'}"
        return f"{num} {street}"
    
    elif sem_type == "token":
        return ''.join(random.choices(string.ascii_letters + string.digits, k=64))
    
    elif sem_type == "hash":
        return ''.join(random.choices('0123456789abcdef', k=64))
    
    elif sem_type == "color":
        return random.choice(_COLORS)
    
    elif sem_type == "ipv4":
        return f"{random.randint(10,192)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    
    # FALLBACK: Return the sample value as-is (original behavior)
    return sample_value


# ──────────────────────────────────────────────────────
# SCHEMA LEARNING (enhanced, backward-compatible)
# ──────────────────────────────────────────────────────

def learn_schema(current_schema, new_body):
    """
    Learns the JSON structure from observed API traffic.
    
    For each field, stores the last observed value as a sample.
    The schema structure is backward-compatible with the original format:
      - dict fields → recursively learned sub-schemas
      - list fields → [learned_item_schema] (template from first element)
      - primitive fields → last seen value (used as sample for smart generation)
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
            current_schema[k] = v  # Store the last seen value as the sample
            
    return current_schema


# ──────────────────────────────────────────────────────
# MOCK RESPONSE GENERATION (handles both legacy + rich schema formats)
# ──────────────────────────────────────────────────────

_META = "__meta__"
_ITEMS = "__items__"


def _is_rich_schema(schema) -> bool:
    """
    Detect whether a schema dict came from SchemaLearner (rich format)
    vs. the older learn_schema() simple format.

    The rich format always has a top-level "__meta__" key.
    """
    return isinstance(schema, dict) and _META in schema


def _primary_type_from_meta(meta: dict):
    """Return the dominant JSON type recorded in a __meta__ descriptor, or None."""
    types_seen = set(meta.get("types_seen", []))
    for preferred in ("object", "array", "string", "integer", "number", "boolean"):
        if preferred in types_seen:
            return preferred
    return None


def _generate_from_rich_schema(node: dict, request_data=None, field_name="") -> object:
    """
    Recursively generate a mock value from a SchemaLearner rich-format node.

    Node structure:
        {
            "__meta__": { types_seen, nullable, example, ... },
            "child_key": { <nested node> },   # for object children
            "__items__": { <nested node> }    # for array items
        }
    """
    meta = node.get(_META, {})
    primary = _primary_type_from_meta(meta)
    example = meta.get("example")  # last observed real value

    # ── Object ────────────────────────────────────────────────────────────────
    if primary == "object":
        result = {}
        for key, child in node.items():
            if key in (_META, _ITEMS):
                continue
            # Priority: echo matching request key (scalars only)
            if (
                request_data
                and isinstance(request_data, dict)
                and key in request_data
                and not isinstance(request_data[key], (dict, list))
            ):
                result[key] = request_data[key]
            else:
                nested_req = (
                    request_data.get(key)
                    if isinstance(request_data, dict)
                    else None
                )
                result[key] = _generate_from_rich_schema(child, nested_req, field_name=key)
        return result

    # ── Array ─────────────────────────────────────────────────────────────────
    elif primary == "array":
        items_node = node.get(_ITEMS)
        if not items_node:
            return []
        item_count = random.randint(1, 4)
        return [
            _generate_from_rich_schema(items_node, None, field_name=field_name)
            for _ in range(item_count)
        ]

    # ── Primitive ─────────────────────────────────────────────────────────────
    else:
        # Try smart heuristic generation from field name first
        if field_name:
            smart = _generate_smart_value(field_name, example)
            if smart is not None:
                return smart

        # Fall back to the recorded example / type defaults
        if example is not None:
            return example

        # Last-resort defaults per type
        if primary == "string":
            return "mock_value"
        elif primary in ("integer", "number"):
            return random.randint(0, 100)
        elif primary == "boolean":
            return True
        return None


def generate_mock_response(schema, request_data=None):
    """
    Generates a mock response with realistic, varied data.

    Supports two internal schema formats:
      - Rich format  (SchemaIntelligence / SchemaLearner): has ``__meta__`` keys.
      - Legacy format (old learn_schema()): plain ``{field: last_value}`` dict.

    Priority within each format:
      1. Echo values from request_data if matching scalar keys exist.
      2. Use field-name heuristics for smart generation.
      3. Fall back to the recorded example / type defaults.
    """
    if not schema:
        return {"status": "success"}

    if _is_rich_schema(schema):
        return _generate_from_rich_schema(schema, request_data or {})

    # Legacy simple-format path
    return _deep_copy_and_correlate(schema, request_data or {})


def _deep_copy_and_correlate(schema_node, source, parent_key=""):
    """Generate a mock from the legacy simple schema format."""
    if not isinstance(schema_node, dict):
        # For primitive values stored directly, apply smart generation
        if parent_key:
            smart = _generate_smart_value(parent_key, schema_node)
            if smart is not None:
                return smart
        return schema_node

    result = {}
    for k, v in schema_node.items():
        # Skip internal metadata keys (safety guard for mixed schemas)
        if k.startswith("_"):
            continue

        # Priority 1: Echo from request data if key matches
        if source and k in source and not isinstance(source[k], (dict, list)):
            result[k] = source[k]

        # Priority 2: Recurse into nested objects
        elif isinstance(v, dict):
            nested_source = source.get(k) if isinstance(source, dict) else None
            result[k] = _deep_copy_and_correlate(v, nested_source, parent_key=k)

        # Priority 3: Handle arrays — generate varied items
        elif isinstance(v, list) and v:
            item_count = random.randint(1, 4)
            result[k] = [
                _deep_copy_and_correlate(v[0], None, parent_key=k)
                for _ in range(item_count)
            ]

        # Priority 4: Smart value generation for primitives
        else:
            smart = _generate_smart_value(k, v)
            if smart is not None:
                result[k] = smart
            else:
                result[k] = v

    return result
