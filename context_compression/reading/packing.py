"""Exact source ranges, protected rule core and atomic supplement units."""
import re

from ..json_codec import compact_json
from .passages import merge_ranges

CORE_BUDGET, SLOT_BUDGET, FINAL_BUDGET = 3072, 1024, 4096


def payload(source, ranges):
    return {'passages': [{'start': a, 'end': b, 'text': source[a:b]}
                         for a, b in merge_ranges(ranges)]}


def subtract(ranges, protected):
    result, protected = [], merge_ranges(protected)
    for a, b in merge_ranges(ranges):
        cursor = a
        for lo, hi in protected:
            if hi <= cursor or lo >= b:
                continue
            if cursor < lo:
                result.append((cursor, lo))
            cursor = max(cursor, hi)
            if cursor >= b:
                break
        if cursor < b:
            result.append((cursor, b))
    return result


def count(source, ranges, counter):
    return counter.text(compact_json(payload(source, ranges)))


def substantive(source, ranges):
    for a, b in ranges:
        text = source[a:b].strip()
        if not text or text.casefold() in {'rev.', 'lett.', 'phys.', 'vol.', 'pp.', 'ibid.'}:
            continue
        if '\n' not in text and re.match(r'^#{1,6}\s', text):
            continue
        if any(c.isalnum() for c in text):
            return True
    return False


_OUTLINE = re.compile(r'(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|'
    r'(?:\d{1,3}(?:\.\d{1,3})*|[IVXLCDM]{1,8}|[A-Z])[.)]\s+|'
    r'(?:[A-Za-z]+\s+)?[IVXLCDM]{1,8}:\s+|第[一二三四五六七八九十百\d]+[章节部篇])')


_SENTENCE_END = re.compile(r'[.!?。！？][\s\"\x27”’»）)\]]*$')


def _retained_structure_packages(source, built, default):
    """Atomic *retained* outline/table units, without changing the baseline.

    Group consecutive short outline rows in their original document scope;
    short non-sentence labels/date qualifiers may continue a row. Ordinary
    prose terminates a run, and is not frozen just because it is adjacent.
    Tabular paragraphs are atomic independently. Only already-retained nodes
    become replacement obligations: this is not new recall or a claim that the
    baseline retained every row of the source table/outline.
    """
    nodes = built['nodes']
    paragraphs = []
    for i, node in enumerate(nodes):
        key = (built['node_documents'][i], node.id.split('.', 1)[0])
        if not paragraphs or paragraphs[-1][0] != key:
            paragraphs.append((key, []))
        paragraphs[-1][1].append(i)
    groups, run, row_count, document = [], [], 0, None

    def flush():
        nonlocal run, row_count
        if row_count >= 2:
            groups.append(set(run))
        run, row_count = [], 0

    for (doc, _), indices in paragraphs:
        text = source[nodes[indices[0]].start:nodes[indices[-1]].end].strip()
        if document != doc:
            flush()
            document = doc
        if not text:
            continue
        lines = [line for line in text.splitlines() if line.strip()]
        table = sum('|' in line or '\t' in line for line in lines) >= 2
        if table:
            flush()
            groups.append(set(indices))
            continue
        markers = len(_OUTLINE.findall(text))
        # A marked list item can be a sentence; an unmarked prose sentence cannot
        # extend an outline. This prevents an entire following section freezing.
        row = bool(markers and len(text) <= 800)
        continuation = len(text) <= 160 and len(lines) <= 3 and not _SENTENCE_END.search(text)
        if row or (run and continuation):
            run.extend(indices)
            row_count += markers
        else:
            flush()
    flush()
    result = []
    for original in groups:
        retained = original & default
        if len(retained) < 2:
            continue
        required = set().union(*(built['bundles'][i] for i in retained))
        if not required <= default:
            raise ValueError('frozen default lacks a retained structure dependency')
        result.append({'indices': required, 'retained_indices': retained})
    return result


def _complete_package(view, indices):
    """Close source dependencies and retained structural units to a fixed point."""
    chosen = set(indices)
    while True:
        expanded = set(chosen)
        for i in chosen:
            expanded.update(view['built']['bundles'][i])
        for unit in view['retained_structure_packages']:
            if unit['retained_indices'] & expanded:
                expanded.update(unit['indices'])
        if expanded == chosen:
            return chosen
        chosen = expanded


class EvidenceBudget:
    """Cache exact compact evidence-JSON counts for one immutable source."""

    def __init__(self, source, nodes, counter):
        self.source = source
        self.nodes = nodes
        self.counter = counter
        self.counts = {}

    def ranges(self, indices):
        return merge_ranges((self.nodes[i].start, self.nodes[i].end) for i in indices)

    def tokens(self, ranges):
        key = tuple((a, b) for a, b in ranges)
        if key not in self.counts:
            self.counts[key] = count(self.source, key, self.counter)
        return self.counts[key]

    def total(self, indices):
        return self.tokens(self.ranges(indices))

    def fits_fixed(self, indices, core_ranges):
        ranges = self.ranges(indices)
        return (self.tokens(ranges) <= FINAL_BUDGET
                and self.tokens(subtract(ranges, core_ranges)) <= SLOT_BUDGET)


def core_and_fill(built, budget):
    """Freeze the BM25-ranked 3K core, then fill a separate 1K slot in source order."""
    core = set()
    for seed in built['ranked']:
        trial = core | built['bundles'][seed]
        if trial != core and budget.total(trial) <= CORE_BUDGET:
            core = trial
    core_ranges = budget.ranges(core)
    selected = core.copy()
    nodes = built['nodes']
    seen = set()
    for seed in dict.fromkeys([*built['ranked'], *range(len(nodes))]):
        group = frozenset(built['bundles'][seed])
        if group in seen or group <= selected:
            continue
        seen.add(group)
        extra = group - selected
        if substantive(budget.source, [(nodes[i].start, nodes[i].end) for i in extra]):
            trial = selected | group
            if budget.fits_fixed(trial, core_ranges):
                selected = trial
    return core, selected


def refill(view, required, budget):
    """Keep complete default units that fit beside the nominated source pair."""
    chosen = set(required)
    default = view['default_indices']
    built = view['built']
    seen = set()
    for seed in dict.fromkeys([*built['ranked'], *sorted(default)]):
        group = frozenset(_complete_package(view, built['bundles'][seed]))
        if group in seen or not group <= default or group <= chosen:
            continue
        seen.add(group)
        if budget.fits_fixed(chosen | group, view['core_ranges']):
            chosen.update(group)
    return chosen
