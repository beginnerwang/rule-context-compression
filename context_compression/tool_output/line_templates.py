"""Lossless line factoring inspired by TokenSnip G; no external compression engine."""
import json
from collections import Counter, defaultdict
from os.path import commonprefix

from ..json_codec import compact_json, unique_object
from ..sources import physical_lines, source_id


_RULE = "Run [n,values]: consecutive lines from n are prefix+value+suffix. Merge plain by line number; append eol unless ends overrides it."
_EOL = ("\r\n", "\n", "\r", "")
_FIELDS = {"view", "rule", "lines", "eol", "ends", "templates", "plain", "sha256"}


def _groups(bodies):
    # Try shared suffixes first (TokenSnip G), then prefix-only runs in remaining rows.
    used = set()
    templates = []
    for suffix_first in (True, False):
        buckets = defaultdict(list)
        for number, text in enumerate(bodies, 1):
            if number not in used and len(text) >= 96:
                key = text[-64:] if suffix_first else text[:64]
                buckets[key].append(number)
        for numbers in buckets.values():
            if len(numbers) < 8:
                continue
            texts = [bodies[number - 1] for number in numbers]
            suffix = commonprefix([text[-512:][::-1] for text in texts])[::-1]
            rests = [text[:len(text) - len(suffix)] for text in texts]
            prefix = commonprefix([text[:512] for text in rests])
            runs = []
            previous = 0
            for number, rest in zip(numbers, rests):
                if not runs or number != previous + 1:
                    runs.append([number, []])
                runs[-1][1].append(rest[len(prefix):])
                previous = number
            templates.append({"prefix": prefix, "suffix": suffix, "runs": runs})
            used.update(numbers)
    return templates, [[n, text] for n, text in enumerate(bodies, 1) if n not in used]


def template_view(raw: str, budget: int, counter, *, input_tokens=None) -> str | None:
    """Return a profitable, budget-fitting and independently decodable view, when available."""
    if type(budget) is not int or budget < 1:
        raise ValueError("template budget must be a positive integer")
    lines = physical_lines(raw)
    if len(lines) < 8:
        return None
    bodies = [line.rstrip("\r\n") for line in lines]
    templates, plain = _groups(bodies)
    if not templates:
        return None
    endings = [line[len(body):] for line, body in zip(lines, bodies)]
    eol = Counter(endings).most_common(1)[0][0]
    overrides = [[n, ending] for n, ending in enumerate(endings, 1) if ending != eol]
    view = compact_json({"view": "line_templates", "rule": _RULE, "lines": len(lines),
                     "eol": eol, "ends": overrides, "templates": templates,
                     "plain": plain, "sha256": source_id(raw)})
    size = counter.text(view)
    if size > budget or size >= (input_tokens if input_tokens is not None else counter.text(raw)):
        return None
    if decode_template(view) != raw:
        raise ValueError("generated template did not reconstruct its source")
    return view


def _pairs(rows, count, runs=False):
    if not isinstance(rows, list):
        raise ValueError("expected a list of numbered rows")
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("expected a line number and string")
        number, text = row
        if type(number) is not int or not 1 <= number <= count:
            raise ValueError("invalid line number")
        values = text if runs else [text]
        if not isinstance(values, list) or not values or number + len(values) - 1 > count:
            raise ValueError("invalid line run")
        for offset, value in enumerate(values):
            if not isinstance(value, str):
                raise ValueError("invalid row value")
            yield number + offset, value


def decode_template(view: str) -> str:
    """Restore a self-contained serialized view and verify its source checksum."""
    doc = json.loads(view, object_pairs_hook=unique_object)
    if (not isinstance(doc, dict) or doc.keys() != _FIELDS
            or doc["view"] != "line_templates" or doc["rule"] != _RULE):
        raise ValueError("invalid line template format")
    count, default = doc["lines"], doc["eol"]
    if type(count) is not int or count < 1 or default not in _EOL:
        raise ValueError("invalid line count or default ending")
    endings = {}
    for number, ending in _pairs(doc["ends"], count):
        if number in endings or ending not in _EOL:
            raise ValueError("invalid or repeated line ending")
        endings[number] = ending

    ordered = {}

    def collect(rows, prefix, suffix, *, runs=False):
        for number, middle in _pairs(rows, count, runs):
            body = prefix + middle + suffix
            end = endings.get(number, default)
            if number in ordered or "\r" in body or "\n" in body or (not end and number != count):
                raise ValueError("invalid or repeated template line")
            ordered[number] = body + end

    if not isinstance(doc["templates"], list) or not doc["templates"]:
        raise ValueError("expected at least one template")
    for group in doc["templates"]:
        if (not isinstance(group, dict) or group.keys() != {"prefix", "suffix", "runs"}
                or not isinstance(group["prefix"], str) or not isinstance(group["suffix"], str)
                or not group["runs"]):
            raise ValueError("invalid template group")
        collect(group["runs"], group["prefix"], group["suffix"], runs=True)
    collect(doc["plain"], "", "")
    if len(ordered) != count:
        raise ValueError("missing template line")
    lines = [ordered[n] for n in range(1, count + 1)]
    result = "".join(lines)
    if physical_lines(result) != lines:
        raise ValueError("template line numbers do not match source boundaries")
    if source_id(result) != doc["sha256"]:
        raise ValueError("restored template failed its source checksum")
    return result
