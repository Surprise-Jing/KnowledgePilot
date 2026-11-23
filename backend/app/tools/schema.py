"""Minimal JSON Schema validator for tool arguments.

Supports exactly the subset the tool schemas use: type (object, string, integer, boolean,
array), properties, required, additionalProperties: false, enum, minimum/maximum,
minLength/maxLength, minItems/maxItems, items and default. A full validator library would
be a new dependency for a handful of flat schemas."""

from typing import Any

from app.errors import AppError

_TYPE_NAMES = {
    "object": "对象",
    "string": "字符串",
    "integer": "整数",
    "boolean": "布尔值",
    "array": "数组",
}


def _invalid(message: str) -> AppError:
    return AppError(422, "invalid_argument", message)


def _check_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        # bool is a subclass of int in Python but not an integer in JSON.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    raise ValueError(f"unsupported schema type {expected!r}")


def _validate_value(value: Any, schema: dict, path: str) -> Any:
    expected = schema.get("type")
    if expected and not _check_type(value, expected):
        raise _invalid(f"参数 {path} 必须是{_TYPE_NAMES.get(expected, expected)}")
    if "enum" in schema and value not in schema["enum"]:
        options = "、".join(str(option) for option in schema["enum"])
        raise _invalid(f"参数 {path} 只能取 {options}")
    if expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise _invalid(f"参数 {path} 长度不能少于 {schema['minLength']} 个字符")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise _invalid(f"参数 {path} 长度不能超过 {schema['maxLength']} 个字符")
    if expected == "integer":
        if "minimum" in schema and value < schema["minimum"]:
            raise _invalid(f"参数 {path} 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise _invalid(f"参数 {path} 不能大于 {schema['maximum']}")
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise _invalid(f"参数 {path} 至少包含 {schema['minItems']} 项")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise _invalid(f"参数 {path} 最多包含 {schema['maxItems']} 项")
        if "items" in schema:
            return [
                _validate_value(item, schema["items"], f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
    if expected == "object":
        return validate_object(value, schema, prefix=f"{path}.")
    return value


def validate_object(arguments: dict, schema: dict, prefix: str = "") -> dict:
    """Validate `arguments` against an object schema and return a copy with defaults applied.
    Raises AppError(invalid_argument) with a Chinese message naming the offending field."""
    properties: dict = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise _invalid(f"不支持的参数：{'、'.join(prefix + name for name in unknown)}")
    for name in schema.get("required", []):
        if name not in arguments:
            raise _invalid(f"缺少必填参数 {prefix}{name}")
    required = set(schema.get("required", []))
    result = {}
    for name, property_schema in properties.items():
        # Models often send `null` for optional parameters they mean to leave out.
        if arguments.get(name) is None and name in arguments and name not in required:
            if "default" in property_schema:
                result[name] = property_schema["default"]
            continue
        if name in arguments:
            result[name] = _validate_value(arguments[name], property_schema, prefix + name)
        elif "default" in property_schema:
            result[name] = property_schema["default"]
    return result
