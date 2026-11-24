"""All offsets refer to Python Unicode characters in the untouched source."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    id: str
    start: int
    end: int


def paragraphs(source):
    """Partition the whole string, retaining separators and leading whitespace."""
    ranges, start = [], 0
    for sep in re.finditer(r"\n[ \t\r]*\n+", source):
        ranges.append((start, sep.end()))
        start = sep.end()
    if start < len(source):
        ranges.append((start, len(source)))
    return [Passage(f"p{i:06d}", a, b) for i, (a, b) in enumerate(ranges)]


# Conservative rules: never normalize text, and keep whitespace/quotes attached.
_ENDS = re.compile(r'''[.!?。！？]+["'”’»）)\]]*''')
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "fig", "eq", "no", "al", "inc"}
_ELLIPSES = re.compile(r"\.(?:\s*\.){2,}|…+")
_ENUMERATION_LEFT = frozenset(",;，；{[(（+−-*/×⋅∪∩")
_ENUMERATION_RIGHT = frozenset(",;，；}])）+−-*/×⋅∪∩")


def _technical_ellipsis_ranges(source, start, end):
    """Recognize enumeration continuations without rewriting mathematical text.

    Requiring a delimiter/operator on both sides keeps ordinary sentence-final
    ellipses, such as ``Wait... Next sentence.``, under the existing rules.
    Whitespace may wrap an enumeration, but an ellipsis cannot span paragraphs.
    """
    for match in _ELLIPSES.finditer(source, start, end):
        left, right = match.start() - 1, match.end()
        while left >= start and source[left].isspace():
            left -= 1
        while right < end and source[right].isspace():
            right += 1
        if (left >= start and right < end
                and source[left] in _ENUMERATION_LEFT
                and source[right] in _ENUMERATION_RIGHT
                and not re.search(r"\n[ \t\r]*\n", source[left + 1:right])):
            yield match.start(), match.end()


def sentence_ranges(source, start=0, end=None):
    """Partition a paragraph into lossless sentence spans, not rewritten strings.

    English periods need following whitespace; abbreviations, initials and list
    numbers remain attached. Chinese sentence marks can end without whitespace.
    These rules are intentionally conservative for technical texts and tables.
    Mathematical enumeration ellipses remain inside the original sentence.
    """
    end = len(source) if end is None else end
    ranges, cursor = [], start
    ellipses = iter(_technical_ellipsis_ranges(source, start, end))
    ellipsis = next(ellipses, None)
    for match in _ENDS.finditer(source, start, end):
        while ellipsis is not None and ellipsis[1] <= match.start():
            ellipsis = next(ellipses, None)
        if ellipsis is not None and ellipsis[0] <= match.start() < ellipsis[1]:
            continue
        boundary = match.end()
        marks = match.group().rstrip('\"\'”’»）)]')
        if boundary < end and not source[boundary].isspace() and not any(c in marks for c in "。！？"):
            continue
        if marks == ".":
            word = re.search(r"([\w.]+)$", source[max(start, match.start() - 40):match.start()])
            token = word.group(1) if word else ""
            if token.lower() in _ABBREVIATIONS or token.isdigit() or (len(token) == 1 and token.isalpha()):
                continue
            if "." in token and all(len(x) <= 1 for x in token.split(".")):
                continue
        while boundary < end and source[boundary].isspace():
            boundary += 1
        if boundary > cursor:
            ranges.append((cursor, boundary))
            cursor = boundary
    if cursor < end:
        ranges.append((cursor, end))
    return ranges


def sentences(source):
    """One selectable sentence per lossless span, grouped by original paragraph ID.

    This is a conservative boundary parser, not a semantic dependency detector.
    A sentence exceeding the evidence budget remains indivisible.
    """
    return [Passage(f"{paragraph.id}.s{index:04d}", a, b)
            for paragraph in paragraphs(source)
            for index, (a, b) in enumerate(sentence_ranges(
                source, paragraph.start, paragraph.end))]


def merge_ranges(ranges):
    merged = []
    for a, b in sorted(set(tuple(x) for x in ranges)):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return merged
