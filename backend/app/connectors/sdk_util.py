"""Helpers for adapting official SDK responses to plain API-shaped dicts.

The official Binance SDKs return pydantic models whose oneof fields dump as
wrapper objects ({"actual_instance": ..., "one_of_schemas": ...}). Connector
normalization works on the documented REST JSON shape instead, so tests can
use fixtures copied straight from the exchange API docs. This module unwraps
the SDK models back to that shape.
"""
from typing import Any

_NOISE_KEYS = {"one_of_schemas", "discriminator_value_class_map", "additional_properties"}


def unwrap(node: Any) -> Any:
    """Recursively unwrap oneof wrappers and drop SDK bookkeeping keys."""
    if isinstance(node, dict):
        if "actual_instance" in node:
            return unwrap(node["actual_instance"])
        return {
            k: unwrap(v)
            for k, v in node.items()
            if k not in _NOISE_KEYS and not k.startswith("oneof_schema_")
        }
    if isinstance(node, list):
        return [unwrap(item) for item in node]
    return node


def dump_sdk_model(model: Any) -> Any:
    """Dump a Binance SDK pydantic model to a plain dict/list in API shape."""
    if isinstance(model, list):
        return [dump_sdk_model(m) for m in model]
    return unwrap(model.model_dump(by_alias=True, exclude_none=True))
