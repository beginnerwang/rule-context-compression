"""Exact JSON values and RFC 6901 paths shared by selection and recovery."""
import json
import re


class JsonNumber(str):
    """Preserve the original JSON number lexeme, including decimal precision."""


def _invalid_constant(value):
    raise ValueError(f"non-standard JSON number: {value}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key!r}")
        result[key] = value
    return result


def parse_json(raw):
    """Parse standard JSON without losing numeric precision or duplicate keys silently."""
    return json.loads(raw, object_pairs_hook=unique_object, parse_int=JsonNumber,
                      parse_float=JsonNumber, parse_constant=_invalid_constant)


def compact_json(value):
    """Serialize a native Python value as a model-visible compact JSON payload."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode_json(value):
    if isinstance(value, JsonNumber):
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(encode_json(k) + ':' + encode_json(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(encode_json(v) for v in value) + ']'
    # A legal escaped unpaired surrogate must not break the tokenizer's UTF-8 input.
    return json.dumps(value, ensure_ascii=False).encode('utf-8', 'backslashreplace').decode('utf-8')


def make_pointer(parts):
    return ''.join('/' + str(key).replace('~', '~0').replace('/', '~1') for key in parts)


def lookup_pointer(document, pointer: str):
    if not pointer:
        return document
    if not pointer.startswith("/"):
        raise ValueError("a JSON Pointer starts with / or is empty")
    value = document
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("invalid JSON Pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch("0|[1-9][0-9]*", key) or int(key) >= len(value):
                raise ValueError(f"invalid array index in pointer: {key}")
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ValueError(f"JSON Pointer does not exist: {pointer}")
    return value
