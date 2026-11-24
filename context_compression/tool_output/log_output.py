"""Extract task-relevant, line-addressable evidence from pytest and text logs."""
import re
from collections import Counter

from ..sources import physical_lines

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COUNT = r"\d+ (?:failed|passed|errors?|skipped|warnings?|xfailed|xpassed|deselected|reruns?)"
_PYTEST_SUMMARY = re.compile(
    rf"^(?:(?:{_COUNT})(?:,\s*{_COUNT})*|no tests ran)(?:\s+in\s+\d[\d:.]*s?(?:\s+\([\d:]+\))?)?$"
    r"|^interrupted:\s*\S.*$", re.I,
)
_EXIT_STATUS = re.compile(
    r"^(?:(?:process|command)\s+)?(?:(?:finished|completed|terminated|exited)\s+(?:with\s+)?)?"
    r"(?:exit(?:ed)?(?:\s+with)?\s+)?(?:non-zero\s+)?(?:code|status)\s*[:=]?\s*(-?\d+)\.?$", re.I,
)
_ERROR = re.compile(r"\b(?:error|critical|fatal|panic|exception|failed|failure|traceback)\b|^\s*[\w.]+(?:Error|Exception):|错误|失败", re.I)
_WARNING = re.compile(r"\bwarn(?:ing)?\b|警告", re.I)
_SKIPPED = re.compile(r"\b(?:skip(?:ped)?|xfail(?:ed)?|deselected)\b|跳过", re.I)
_ASSERT = re.compile(r"^\s*(?:E\s+|>\s*assert\b|assert(?:ionerror)?\b|expected\b|actual\b|[+-]\s+\S)|预期|期望|实际", re.I)
_LOCATION = re.compile(r'\b[^\s:]+\.py:\d+\b|\bFile "[^"]+", line \d+')
_SECTION = re.compile(r"^_{2,}\s+\S.*?\s+_{2,}$")
_STOP = set("what which were the why did with from and please list show all test tests failing failures "
            "failed failure output log logs error errors".split())


def _status_kind(line):
    text = line.strip().strip("=!").strip()
    if _PYTEST_SUMMARY.fullmatch(text):
        return "pytest"
    if _EXIT_STATUS.fullmatch(text) and re.search(r"exit|process|command", text, re.I):
        return "process"
    return None


def _failure(line):
    """Extract literal pytest node IDs, including parameters containing spaces or dashes."""
    text = line.strip()
    match = re.match(r"^(?:FAILED|ERROR)\s+(.+)$", text)
    if match:
        rest = match[1]
        depth = 0
        for index, char in enumerate(rest):
            if char == "[":
                depth += 1
            elif char == "]" and depth:
                depth -= 1
            elif not depth and rest.startswith(" - ", index):
                rest = rest[:index]
                break
        rest = rest.removeprefix("collecting ")
        # pytest collection failures report the module path without a test node.
        if "::" in rest or rest.endswith(".py"):
            return rest
        return None
    match = re.match(r"^(.+?::.+?)\s+(?:FAILED|ERROR)(?:\s+\[\s*\d+%\])?$", text)
    return match[1] if match else None


def _keywords(question):
    words = set(re.findall(r"[a-z\d_]{2,}", question.casefold())) - _STOP
    for part in re.findall(r"[\u3400-\u9fff]+", question):
        words.update(part[i:i + 2] for i in range(len(part) - 1))
    return words


def compress_log(raw: str, question: str, budget: int, counter) -> str:
    """Return original lines with one-based source locations, under the exact token budget."""
    if type(budget) is not int or budget < 1:
        raise ValueError("log budget must be a positive integer")
    if counter.text(raw) <= budget:
        return raw
    return log_excerpt(raw, question, budget, counter)


def log_excerpt(raw, question, budget, counter):
    """Pack an oversized log, keeping one-based source line locations."""
    lines = [_ANSI.sub("", line.rstrip("\r\n")) for line in physical_lines(raw)]
    total = len(lines)
    failures = {}
    ids = {}
    sections = []
    current = None
    warning_rows = {}
    warnings = Counter()
    statuses = {}
    latest = {}
    for i, line in enumerate(lines):
        kind = _status_kind(line)
        if kind:
            statuses[i] = kind
            latest[kind] = i
        node = _failure(line)
        if node:
            failures[node] = i  # Prefer the final short-test-summary occurrence.
            ids[i] = node
        if _SECTION.match(line.strip()):
            current = i
        elif line.lstrip().startswith("==="):
            current = None
        sections.append(current)
        if not kind and _WARNING.search(line) and not (_ERROR.search(line) or node):
            warnings[line] += 1
            warning_rows.setdefault(line, i)

    kept = {}
    covered_ids = set()

    def render():
        header = f"[log excerpt; {len(kept)}/{total} lines"
        if failures:
            header += f"; {len(covered_ids)}/{len(failures)} observed failure IDs shown"
        body = [header + "]"]
        for i in sorted(kept):
            note = f" [exact warning x{warnings[lines[i]]}]" if warnings[lines[i]] > 1 else ""
            body.append(f"L{i + 1} {kept[i]}{note}")
        return "\n".join(body)

    if counter.text(render()) > budget:
        raise ValueError("log budget cannot hold the omission notice")

    def add(i, compact=None):
        if not 0 <= i < total or not lines[i].strip():
            return
        # An earlier outcome is meaningful only alongside the latest one of its kind.
        kind = statuses.get(i)
        if kind and i != latest[kind] and latest[kind] not in kept:
            return
        if lines[i] in warning_rows:
            i = warning_rows[lines[i]]
        value = compact if compact is not None else lines[i]
        previous = kept.get(i)
        if value == previous or (previous is not None and compact is not None):
            return
        node = ids.get(i)
        new_id = node and node not in covered_ids
        kept[i] = value
        if new_id:
            covered_ids.add(node)
        if counter.text(render()) > budget:
            if previous is None:
                del kept[i]
            else:
                kept[i] = previous
            if new_id:
                covered_ids.remove(node)

    terms = _keywords(question)
    relevance = [sum(term in text for term in terms) for text in map(str.casefold, lines)]
    for i in sorted(latest.values(), reverse=True):
        add(i)
    if _SKIPPED.search(question):
        for i, line in enumerate(lines):
            if _SKIPPED.search(line) and i not in statuses:
                add(i)
    for node, i in sorted(failures.items(), key=lambda item: -relevance[item[1]]):
        add(i, "failure ID: " + node)

    detail = [i for i, line in enumerate(lines) if _ASSERT.search(line) or _LOCATION.search(line)]
    for i in sorted(detail, key=lambda i: -relevance[i]):
        if sections[i] is not None:
            add(sections[i])
        add(i)
    for i in sorted((i for i, score in enumerate(relevance) if score), key=lambda i: -relevance[i]):
        add(i)
    errors = [i for i, line in enumerate(lines) if _ERROR.search(line) and i not in ids]
    for i in errors:
        add(i)
    for i in reversed(statuses):
        add(i)
    for i in errors:
        add(i - 1)
        add(i + 1)
    for i in warning_rows.values():
        add(i)
    for i in failures.values():
        add(i)  # Use the full short summary when it fits.
    for i in (total - 1, 0):
        add(i)
    return render()
