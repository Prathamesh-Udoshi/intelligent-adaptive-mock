"""
AI Mock Generator
==================
Uses OpenAI (gpt-4o-mini) with few-shot prompting to generate realistic,
varied mock responses based on real req/res pairs learned during proxy mode.

Strategy:
  1. Walk the stored rich schema tree and extract real "example" values
     stored by SchemaLearner for every field → reconstruct a real-looking
     golden response.
  2. Feed that golden request + response pair as a few-shot example to the
     model, along with the current incoming request body.
  3. Ask the model to generate a fresh, realistic variation that preserves
     the exact structure (keys, types, nesting) but varies the values.

This approach is far more reliable than schema-tree reconstruction because:
  - It handles deeply nested structures without brittle type detection.
  - It handles fields that were only ever null (optional fields).
  - The model understands semantic context — it knows "readiness_score" is a
    percentage, "issues" is a list of problems, etc.
  - Responses feel authentic, not machine-generated.

Fallback chain (in proxy.py):
  1. AI generator (if OPENAI_API_KEY set and schema has real examples)
  2. Schema-tree reconstruction (generate_mock_response)
  3. Generic fallback: {"message": "AI fallback (No patterns learned yet)"}
"""

import json
import logging
import os

logger = logging.getLogger("mock_platform")

# ── Constants ──────────────────────────────────────────────────────────────────

_META  = "__meta__"
_ITEMS = "__items__"

_MODEL        = os.environ.get("OPENAI_MOCK_MODEL", "gpt-4o-mini")
_MAX_TOKENS   = int(os.environ.get("OPENAI_MOCK_MAX_TOKENS", "2000"))
_TEMPERATURE  = float(os.environ.get("OPENAI_MOCK_TEMPERATURE", "0.7"))

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an intelligent API mock server. Your task is to generate a realistic,
varied JSON response that matches the structure and semantics of a real API
response you have learned from production traffic.

Rules (follow ALL of them):
1. Return ONLY valid JSON — no explanation, no markdown, no code blocks.
2. Keep the EXACT same top-level keys as the golden response example.
3. Preserve data types: if a field is a number → keep it a number; string → string, etc.
4. VARY the values realistically:
   - Numeric scores / counters: change by ±10–30% from the example.
   - String messages / descriptions: write fresh but similarly styled text.
   - Arrays: include 1–3 items with varied but plausible content.
   - Booleans: keep realistic distribution (don't always flip them).
5. Consider the incoming request — make the response contextually relevant.
6. Do NOT copy example values verbatim. Generate fresh, plausible variations.
7. Null/optional fields: leave them null if they were null in the example.\
"""


# ── Example extractor ──────────────────────────────────────────────────────────

def extract_example_from_schema(node, max_array_items: int = 2):
    """
    Reconstruct a realistic example value from a rich SchemaLearner schema node.

    Walk the __meta__ / __items__ tree and use the stored ``example`` (last
    real non-null observed value) at every leaf.  For nodes that were only
    ever null, returns None (the field is optional).

    Works on both:
      - Rich format  (SchemaLearner): has ``__meta__`` keys.
      - Legacy format (learn_schema): plain {field: last_value} dict.
    """
    if not isinstance(node, dict):
        return node  # Primitive stored directly in legacy format

    # ── Rich format ──────────────────────────────────────────────────────────
    if _META in node:
        meta     = node[_META]
        types_seen = set(meta.get("types_seen", []))
        example  = meta.get("example")

        primary = None
        for t in ("object", "array", "string", "integer", "number", "boolean"):
            if t in types_seen:
                primary = t
                break

        if primary == "object":
            result = {}
            for key, child in node.items():
                if key in (_META, _ITEMS):
                    continue
                result[key] = extract_example_from_schema(child, max_array_items)
            return result

        elif primary == "array":
            items_node = node.get(_ITEMS)
            if not items_node:
                return []
            return [
                extract_example_from_schema(items_node, max_array_items)
                for _ in range(max_array_items)
            ]

        else:
            # Primitive or only-null field — return the example value (may be None)
            return example

    # ── Legacy format ────────────────────────────────────────────────────────
    result = {}
    for k, v in node.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and v:
            result[k] = [extract_example_from_schema(v[0], max_array_items)]
        elif isinstance(v, dict):
            result[k] = extract_example_from_schema(v, max_array_items)
        else:
            result[k] = v
    return result


# ── Main generator ─────────────────────────────────────────────────────────────

async def generate_ai_mock(
    endpoint_path:   str,
    method:          str,
    response_schema,
    request_schema,
    current_request_body: dict,
) -> dict | None:
    """
    Generate a mock response using OpenAI few-shot prompting.

    Args:
        endpoint_path:        Normalised path (e.g. ``/analyze``).
        method:               HTTP method (``POST``, ``GET``, …).
        response_schema:      Rich or legacy schema learned from real responses.
        request_schema:       Rich or legacy schema learned from real requests.
        current_request_body: The incoming request payload.

    Returns:
        A Python dict with the generated response, or ``None`` if generation
        failed or no API key / examples are available.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.debug("OPENAI_API_KEY not set — skipping AI mock generation.")
        return None

    if not response_schema:
        logger.debug(f"No response schema for {method} {endpoint_path} — skipping AI mock.")
        return None

    # ── Extract real examples from stored schema ───────────────────────────────
    try:
        example_response = extract_example_from_schema(response_schema, max_array_items=2)
        example_request  = extract_example_from_schema(request_schema,  max_array_items=1) \
                           if request_schema else {}
    except Exception as exc:
        logger.warning(f"⚠️ Failed to extract examples from schema: {exc}")
        return None

    # Bail out if the example response is empty or trivially small
    if not example_response or example_response == {"status": "success"}:
        logger.debug(f"Example response is too sparse for AI mock on {method} {endpoint_path}.")
        return None

    # ── Build the prompt ───────────────────────────────────────────────────────
    user_prompt = (
        f"Endpoint: {method} {endpoint_path}\n\n"
        f"Golden request example (learned from real traffic):\n"
        f"{json.dumps(example_request, indent=2, default=str)}\n\n"
        f"Golden response example (learned from real traffic):\n"
        f"{json.dumps(example_response, indent=2, default=str)}\n\n"
        f"Current incoming request:\n"
        f"{json.dumps(current_request_body, indent=2, default=str)}\n\n"
        f"Generate a realistic mock response for this request. "
        f"Return ONLY the JSON object."
    )

    # ── Call OpenAI ────────────────────────────────────────────────────────────
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        completion = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
            response_format={"type": "json_object"},  # Guarantees valid JSON back
        )

        content = completion.choices[0].message.content
        result  = json.loads(content)

        logger.info(f"✨ AI mock generated for {method} {endpoint_path} "
                    f"(tokens: {completion.usage.total_tokens})")
        return result

    except json.JSONDecodeError as exc:
        logger.warning(f"⚠️ AI mock returned invalid JSON for {method} {endpoint_path}: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"⚠️ AI mock generation failed for {method} {endpoint_path}: {exc}")
        return None
