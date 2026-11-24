"""Source-addressed JSON Lines and compact ripgrep event streams."""
from __future__ import annotations

import re

from ..json_codec import JsonNumber, encode_json, parse_json
from ..sources import physical_lines
from .record_selection import query_terms, select_records

_HITS = {"match", "context"}
_META = {"begin", "end", "summary"}
_COLUMNS = ("line", "type", "path", "line_number", "lines", "absolute_offset", "submatches")
_DATA_KEYS = set(_COLUMNS[2:])
_META_QUERY = {"begin", "end", "summary", "stats", "statistics", "elapsed", "duration",
               "searched", "total", "totals", "汇总", "总计", "统计", "耗时", "总数"}


def parse_jsonl(raw: str) -> list[tuple[int, object]]:
    """Parse each nonblank physical line as an independent, strict JSON value."""
    records = []
    for number, line in enumerate(physical_lines(raw), 1):
        if not line.strip(" \t\r\n"):
            continue
        try:
            value = parse_json(line)
        except ValueError as exc:
            raise ValueError(f"invalid JSONL at source line {number}: {exc}") from exc
        records.append((number, value))
    if not records:
        raise ValueError("JSONL input has no records")
    return records


def _data_string(value):
    if isinstance(value, dict) and len(value) == 1:
        if type(value.get("text")) is str:
            return value["text"]
        if type(value.get("bytes")) is str:
            return value  # Keep ripgrep's base64 tag, including for empty byte strings.
    raise ValueError("unrecognized ripgrep data string")


def _uint(value):
    return isinstance(value, JsonNumber) and re.fullmatch(r"0|[1-9][0-9]*", value) is not None


def _rg_units(records):
    """Normalize only an unambiguous stream; preserve unknown event schemas as generic JSONL."""
    units = []
    hits = []
    meta = []
    for line, event in records:
        if not isinstance(event, dict) or set(event) != {"type", "data"}:
            return None
        kind, data = event["type"], event["data"]
        if type(kind) is not str or not isinstance(data, dict):
            return None
        if kind in _META:
            meta.append({"line": line, "value": event})
            units.append(((line,), event))
            continue
        if kind not in _HITS or set(data) != _DATA_KEYS:
            return None
        if data["line_number"] is not None and not _uint(data["line_number"]):
            return None
        if not _uint(data["absolute_offset"]) or not isinstance(data["submatches"], list):
            return None
        try:
            path, text = _data_string(data["path"]), _data_string(data["lines"])
        except ValueError:
            return None
        row = [line, kind, path, data["line_number"], text, data["absolute_offset"], data["submatches"]]
        hits.append(row)
        units.append(((line,), dict(zip(_COLUMNS[1:], row[1:]))))
    return (units, hits, meta) if hits else None


def jsonl_view(records, question: str, budget: int, counter) -> str:
    """Build a budgeted model view. Source line numbers refer to the unmodified stream."""
    if type(budget) is not int or budget < 1:
        raise ValueError("JSONL budget must be a positive integer")
    rg = _rg_units(records)
    if rg is None:
        units = [((line,), value) for line, value in records]
        return select_records(units, question, budget, counter, view="jsonl-selection", source_key="line")

    units, hits, meta = rg
    table = {"view": "rg", "columns": list(_COLUMNS), "rows": hits, "omitted": 0}
    if meta:
        table["events"] = meta
    candidate = encode_json(table)
    if counter.text(candidate) <= budget:
        return candidate

    query = query_terms(question)
    summaries = [entry for entry in meta if entry["value"]["type"] == "summary"]
    if 0 < len(summaries) < len(meta) and query.isdisjoint({"begin", "end"}):
        table["events"] = summaries
        table["omitted"] = len(meta) - len(summaries)
        candidate = encode_json(table)
        if counter.text(candidate) <= budget:
            return candidate
    if meta and query.isdisjoint(_META_QUERY):
        del table["events"]
        table["omitted"] = len(meta)
        candidate = encode_json(table)
        if counter.text(candidate) <= budget:
            return candidate

    preferred = {i for i, (_, record) in enumerate(units) if record["type"] == "match"}
    return select_records(units, question, budget, counter, view="rg-selection", source_key="line",
                      preferred=preferred, required_fields=("type",))
