"""Lossless-first JSON output compression with RFC 6901 source pointers."""
from ..json_codec import encode_json, make_pointer, parse_json
from .record_selection import select_records


def _tables(value, parts=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _tables(child, parts + (key,))
    elif isinstance(value, list) and len(value) > 1 and isinstance(value[0], dict) and value[0]:
        columns = list(value[0])
        # Equal key sequences preserve both order and absent versus null fields.
        if all(isinstance(row, dict) and list(row) == columns for row in value):
            yield parts, columns, value


def _table_view(root, parts, columns, records):
    view = {'view': 'table', 'pointer': make_pointer(parts), 'columns': columns,
            'rows': [list(record.values()) for record in records]}
    if parts:
        # Only the designated location is replaced by a placeholder.
        skeleton = root.copy()
        cursor = skeleton
        for key in parts[:-1]:
            cursor[key] = cursor[key].copy()
            cursor = cursor[key]
        cursor[parts[-1]] = None
        view['document'] = skeleton
    return encode_json(view)


def _units(value, parts=()):
    # Keep array records whole; also expose sibling metadata in API envelopes.
    if isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield parts + (index,), item
    elif isinstance(value, dict) and value and (not parts or any(isinstance(v, (list, dict)) for v in value.values())):
        for key, child in value.items():
            yield from _units(child, parts + (key,))
    else:
        yield parts, value


def compress_json(raw: str, question: str, budget: int, counter) -> str:
    """Compact JSON within budget; paths in a lossy selection address the original.

    A table's rows zip with its columns to reconstruct an array, optionally at
    pointer in document. A selection item's value is exact and fields is a
    projection. Omitted counts completely absent units, not projected fields.
    """
    if type(budget) is not int or budget < 1:
        raise ValueError("JSON budget must be a positive integer")
    root = parse_json(raw)
    if counter.text(raw) <= budget:
        return raw
    return json_view(raw, root, question, budget, counter)


def json_view(raw, root, question, budget, counter):
    """Pack an already-parsed document whose raw text exceeds the budget."""
    compact = encode_json(root)
    if compact != raw and counter.text(compact) <= budget:
        return compact
    # Prefer the schema with the largest repeated representation.
    table = max(_tables(root), key=lambda t: (len(t[2]) - 1) * len(encode_json(t[1])), default=None)
    if table:
        result = _table_view(root, *table)
        if counter.text(result) <= budget:
            return result
    return select_records(list(_units(root)), question, budget, counter)
