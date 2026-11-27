"""Access to the JSON contracts in ``contracts/schemas``.

The schema files are the single source of truth. This module loads them and
builds model-facing tool definitions by inlining ``$ref`` so that a model API
receives self-contained JSON Schema.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"
if not SCHEMAS_DIR.is_dir():
    SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "contracts" / "schemas"

# Keywords removed from model-facing tool parameters. Some OpenAI-compatible
# endpoints reject them; the host validates tool arguments against the full
# schema regardless.
_MODEL_STRIP_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "title",
        "propertyNames",
        "unevaluatedProperties",
        "pattern",
        "minProperties",
        "uniqueItems",
    }
)


@lru_cache(maxsize=None)
def _load(name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_schema(name: str) -> dict[str, Any]:
    """Return a deep copy of ``contracts/schemas/<name>``."""
    return copy.deepcopy(_load(name))


def schema_names() -> list[str]:
    return sorted(p.name for p in SCHEMAS_DIR.glob("*.schema.json"))


@lru_cache(maxsize=1)
def _registry() -> Registry:
    schemas = (_load(name) for name in schema_names())
    return Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)


def validate(name: str, instance: Any) -> None:
    """Validate against the full contract, resolving references locally."""
    filename, _, fragment = name.partition("#")
    schema = _load(filename)
    if fragment:
        schema = {"$ref": f"{schema['$id']}#{fragment}"}
    Draft202012Validator(schema, registry=_registry()).validate(instance)


def _resolve_pointer(doc: dict[str, Any], fragment: str) -> Any:
    node: Any = doc
    if not fragment:
        return node
    for raw in fragment.lstrip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        node = node[key]
    return node


def _inline(node: Any, current: str, depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError("schema reference depth exceeded")
    if isinstance(node, list):
        return [_inline(item, current, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref = node["$ref"]
        file_part, _, fragment = ref.partition("#")
        target_file = file_part or current
        target = _resolve_pointer(_load(target_file), fragment)
        resolved = _inline(target, target_file, depth + 1)
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings and isinstance(resolved, dict):
            resolved = {**resolved, **_inline(siblings, current, depth + 1)}
        return resolved

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _MODEL_STRIP_KEYS or key.startswith("x-"):
            continue
        if key == "oneOf":
            key = "anyOf"
        out[key] = _inline(value, current, depth + 1)
    return out


def inline_schema(name: str, fragment: str = "") -> dict[str, Any]:
    """Return a self-contained copy of a schema (or one of its fragments)."""
    return _inline(_resolve_pointer(_load(name), fragment), name)


ToolSet = Literal["agent", "review"]


def tool_names(tool_set: ToolSet) -> list[str]:
    return list(_load("tools.schema.json")["x-tool-sets"][tool_set])


def tool_definitions(tool_set: ToolSet) -> list[dict[str, Any]]:
    """Function definitions in OpenAI Chat Completions ``tools`` format."""
    defs = _load("tools.schema.json")["$defs"]
    result = []
    for name in tool_names(tool_set):
        params = inline_schema("tools.schema.json", f"/$defs/{name}_args")
        description = params.pop("description", "")
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or defs[f"{name}_args"].get("description", ""),
                    "parameters": params,
                },
            }
        )
    return result


def review_output_schema() -> dict[str, Any]:
    """Schema for the review model's final JSON (strict structured output compatible)."""
    schema = load_schema("ai-review-model-output.schema.json")
    schema.pop("$schema", None)
    schema.pop("$id", None)
    return schema
