"""Nominate source-bound complementary pairs in the same document and section."""
import re
from collections import Counter, defaultdict
from itertools import zip_longest

from . import structure

SEED_LIMIT = 32


_RESUMPTION = re.compile(
    r"^\s*(?:(?:it|its|they|their|this|these|that|those|such|there|here|the former|the latter)\b|"
    r"finally,|subsequently,|next,|in turn,|"
    r"the (?:first|second|third|last|final|next|preceding|following|same|remaining)\b|"
    r"the [\w-]+ (?:stage|step|phase|layer|process|component|operation)\b|"
    r"in (?:this|that|the final|the next) (?:step|stage|phase)|其中|该|其|这一步|在此)", re.I)


_COMPARISON = re.compile(r"\b(?:compar\w*|differ\w*|between|versus|higher|lower|change|increase|decrease)\b|比较|相比", re.I)


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


_STAGE = re.compile(r"\b(?:phase|stage)\s+(\d+|[ivx]+)\b", re.I)


_NAMED = re.compile(r"\b(?:[A-Z][A-Z\d-]+|[A-Za-z]*[a-z][A-Z][A-Za-z\d-]*|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")


_UNIT = re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(%|bps\b|USD\b|EUR\b|GBP\b|km\b|kg\b|mg\b|ms\b|mL\b)")


_PROMPT_WORDS = structure.DEFINITION_STOP | {w.casefold() for w in structure.QUERY_WORDS}


def _terms(text):
    return structure._terms(text) - _PROMPT_WORDS


def _seeds(view):
    # Two banks prevent an already-full core from crowding every new source out:
    # retained anchors can nominate neighbours, while new lexical hits can point
    # back to their antecedent.
    order = view['built']['joint_hits']
    default = view['default_indices']
    banks = (view['built']['ranked'], [i for i in order if i not in default], order)
    seeds = dict.fromkeys(i for row in zip_longest(*banks) for i in row if i is not None)
    return list(seeds)[:SEED_LIMIT]


def _connection(left, right, *, task, words, texts, bridges, near, comparison, requested_scopes):
    """Name an observable local join. A lexical join is not semantic proof."""
    ltext, rtext = texts[left], texts[right]
    lw, rw = words[left], words[right]
    lh, rh = lw & task, rw & task
    if not (lh or rh):
        return None
    shared = lw & rw & bridges
    names = {s.casefold() for s in _NAMED.findall(ltext)} & {s.casefold() for s in _NAMED.findall(rtext)}
    link = sorted(shared | names)
    continuation = near and bool(_RESUMPTION.search(rtext))
    # Repeated topic with no second information, or bare adjacency, cannot
    # nominate a semantic candidate. Explicit continuations can supply an
    # unmentioned value even when their words do not echo the question.
    if not (lw - rw and rw - lw) or not (shared or names or continuation):
        return None
    if not continuation and not (lh and rh and (lh != rh or names and _YEAR.search(rtext))):
        return None
    scope_changes = []
    for label, pattern, requested in requested_scopes:
        before, after = set(pattern.findall(ltext)), set(pattern.findall(rtext))
        if not comparison and requested and (before | after) and requested.isdisjoint(before | after):
            return None
        if before and after and before.isdisjoint(after):
            scope_changes.append(label)
    if scope_changes and not comparison:
        return None
    if not near and not names:
        # Mere topical co-occurrence across scattered sentences is not a local
        # binding. A non-adjacent bridge needs an explicit common name.
        return None
    if continuation and not names:
        reason, unresolved = 'local_antecedent', ['implicit_subject']
    elif names:
        reason, unresolved = 'explicit_shared_name', []
    else:
        reason, unresolved = 'shared_bridge_term', ['term_referent']
    # A topic match cannot silently transfer a predicate between unrelated
    # named speakers/subjects. If there is a real bridging term, retain the
    # possibility as ambiguous.
    before, after = set(_NAMED.findall(ltext)), set(_NAMED.findall(rtext))
    if before and after and before.isdisjoint(after):
        if not link or (not comparison and not continuation and not (shared - task)):
            return None
        unresolved.append('competing_entities')
    unresolved.extend('compare_' + key for key in scope_changes)
    return reason, unresolved, link


def _proposals(view, seeds, texts, words, task):
    built = view['built']
    docs, scopes = built['node_documents'], built['anchors']
    default = view['default_indices']
    if len(default) == len(texts):
        return []

    def within(seed, other):
        return 0 <= other < len(texts) and docs[other] == docs[seed] and scopes[other] == scopes[seed]

    adjacent = {seed: [j for j in (seed - 1, seed + 1) if within(seed, j)] for seed in seeds}
    proposals = {}
    freq = Counter(w for row in words for w in row)
    # Rare shared content can connect nonadjacent original sentences within
    # the same outline scope. Suppress high-frequency terms and numbers.
    bridges = {w for w, n in freq.items() if 2 <= n <= 8 and len(w) >= 3}
    postings = defaultdict(list)
    for i, row in enumerate(words):
        for w in sorted(row & bridges):
            postings[w].append(i)
    headings = built['headings']
    compared = bool(_COMPARISON.search(view['question']))
    requested_scopes = [(name, pattern, set(pattern.findall(view['question'])))
        for name, pattern in (('period', _YEAR), ('stage', _STAGE), ('unit', _UNIT))]
    for rank, seed in enumerate(seeds):
        if seed in headings:
            continue
        partners = set(adjacent[seed])
        for term in words[seed] & bridges:
            partners.update(postings[term])
        for other in sorted(partners):
            if other == seed or not within(seed, other) or other in headings:
                continue
            group = tuple(sorted((seed, other)))
            if group in proposals or set(group) <= default:
                continue
            a, b = group
            linked = _connection(a, b, task=task, words=words, texts=texts, bridges=bridges,
                                 near=b - a == 1, comparison=compared, requested_scopes=requested_scopes)
            if linked:
                reason, unresolved, terms = linked
                support = (words[a] | words[b]) & task
                distinct = (words[a] & task) ^ (words[b] & task)
                order = (-len(distinct), -len(support), rank, b - a, group)
                proposals[group] = (order, reason, unresolved, terms, seed)
    ordered = sorted(proposals.items(), key=lambda item: item[1][0])
    # A long option can produce many superficially high-coverage matches from
    # one seed. Give each source anchor a turn within each connection family,
    # and interleave families before spending the common packing budget.
    routes = defaultdict(dict)
    for group, item in ordered:
        _, reason, _, _, seed = item
        routes[reason].setdefault(seed, []).append((group, item))
    banks = []
    for reason in ('local_antecedent', 'explicit_shared_name', 'shared_bridge_term'):
        per_seed = routes[reason].values()
        banks.append([value for row in zip_longest(*per_seed) for value in row if value])
    return [value for row in zip_longest(*banks) for value in row if value]
