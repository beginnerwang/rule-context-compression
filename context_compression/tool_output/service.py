"""Format-aware, in-process compression of one UTF-8 tool response."""
import json
import re

from ..json_codec import parse_json
from ..sources import source_id
from .json_output import json_view
from .jsonl_output import jsonl_view, parse_jsonl
from .line_templates import template_view
from .log_output import log_excerpt


def _parse_input(raw, kind, mode):
    if kind == "jsonl":
        if mode == "baseline":
            raise ValueError("baseline accepts log or single JSON; use fused mode for JSONL")
        return "jsonl", parse_jsonl(raw)
    if kind == "log" or (kind == "auto" and not raw.lstrip().startswith(("{", "["))):
        return "log", None
    try:
        return "json", parse_json(raw)
    except json.JSONDecodeError as error:
        if kind == "auto":
            if (mode == "fused" and error.msg == "Extra data"
                    and raw[:error.pos].rstrip(" \t").endswith(("\r", "\n"))):
                return "jsonl", parse_jsonl(raw)
            # [INFO], [3/20] and [123] can prefix text log lines.
            if re.match(r'\s*\[[^\[\]{}",\n\r]+\]', raw):
                return "log", None
        raise


def compress_tool(raw, question, budget, counter, *, kind="auto", mode="fused", store=None):
    """Return the exact model view and a separate receipt; baseline reproduces the old rules."""
    if not isinstance(raw, str) or not isinstance(question, str):
        raise ValueError("tool result and question must be UTF-8 text")
    if type(budget) is not int or budget < 1:
        raise ValueError("tool budget must be a positive integer")
    if kind not in ("auto", "json", "jsonl", "log") or mode not in ("fused", "baseline"):
        raise ValueError("unknown tool format or compression mode")
    kind, document = _parse_input(raw, kind, mode)
    original = store.save(raw) if store is not None else source_id(raw)
    input_tokens = counter.text(raw)
    path, view = "passthrough", raw
    if input_tokens > budget:
        if kind == "json":
            path, view = "rules", json_view(raw, document, question, budget, counter)
        elif kind == "jsonl":
            path, view = "jsonl", jsonl_view(document, question, budget, counter)
        else:
            template = template_view(raw, budget, counter, input_tokens=input_tokens) if mode == "fused" else None
            if template is None:
                path, view = "rules", log_excerpt(raw, question, budget, counter)
            else:
                path, view = "line_templates", template
    output_tokens = input_tokens if path == "passthrough" else counter.text(view)
    if output_tokens > budget:
        raise ValueError(f"tool view exceeds budget ({output_tokens} > {budget})")
    return {"text": view, "source_id": original, "format": kind, "mode": mode, "engine": "rules",
            "path": path, "budget": budget, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "changed": view != raw}
