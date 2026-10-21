"""Readable final-record contracts shared by model roles."""
from typing import Any, Dict

from pydantic import BaseModel


def field_contract(schema: type[BaseModel]) -> str:
    """Describe the final record in prose so the model does not echo a schema document."""
    document = schema.model_json_schema()
    definitions = document.get("$defs", {})
    lines = []

    def resolve(node: Dict[str, Any]) -> Dict[str, Any]:
        return definitions[node["$ref"].split("/")[-1]] if "$ref" in node else node

    def kind(node: Dict[str, Any]) -> str:
        node = resolve(node)
        if "anyOf" in node:
            return " or ".join(kind(option) for option in node["anyOf"])
        if "enum" in node:
            return "one of " + ", ".join(repr(value) for value in node["enum"])
        if node.get("type") == "array":
            return "array of " + kind(node["items"])
        return node.get("type", "value")

    def fields(node: Dict[str, Any], prefix: str = "") -> None:
        node = resolve(node)
        for name, spec in node.get("properties", {}).items():
            path = prefix + name
            required = "required" if name in node.get("required", []) else "optional"
            limits = [f"{key}={spec[key]}" for key in (
                "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum") if key in spec]
            lines.append(f"- {path}: {kind(spec)} ({', '.join([required, *limits])})")
            child = resolve(spec)
            if child.get("type") == "array":
                child = resolve(child["items"])
                fields(child, path + "[].")
            elif child.get("type") == "object":
                fields(child, path + ".")
    fields(document)
    return "\n".join(lines)
