"""Protect a ranked core and spend unused capacity on complementary source pairs."""
from . import pairs, structure
from .packing import (FINAL_BUDGET, SLOT_BUDGET, EvidenceBudget, _complete_package,
                      _retained_structure_packages, core_and_fill, payload, refill, subtract)

POOL_LIMIT = 8
TRIAL_LIMIT = 4 * POOL_LIMIT


def _fixed_incumbent(view, proposals, terms, protected, budget):
    """Prefer the first unambiguous pair that preserves all covered question terms."""
    baseline = view['default_indices']
    seen = {frozenset(baseline)}
    for witnesses, (_, _, ambiguities, _, _) in proposals:
        required = protected | _complete_package(view, witnesses)
        if not budget.fits_fixed(required, view['core_ranges']):
            continue
        chosen = refill(view, required, budget)
        key = frozenset(chosen)
        if key in seen:
            continue
        seen.add(key)
        if not ambiguities:
            removed_terms = {term for i in baseline - chosen for term in terms[i]}
            retained_terms = {term for i in chosen for term in terms[i]}
            if removed_terms <= retained_terms:
                return chosen
        if len(seen) - 1 >= POOL_LIMIT:
            break
    return baseline


def _eligible_pairs(view, proposals, terms, protected, budget):
    units = []
    for witnesses, (_, reason, _, links, _) in proposals:
        required = _complete_package(view, witnesses)
        if budget.total(protected | required) > FINAL_BUDGET:
            continue
        anchors = set.union(*(terms[i] for i in witnesses))
        features = {('join', reason, word) for word in anchors}
        bridge_words = structure._terms(' '.join(links)) & anchors
        features.update(('bridge', word) for word in bridge_words)
        units.append((required, features))
        if len(units) == POOL_LIMIT:
            break
    return units


def _marginal_gain(covered, trial):
    """Exact weighted SetCover difference. Each newly covered join has weight two."""
    return sum(2 if concept[0] == 'join' else 1 for concept in trial - covered)


def _expand(incumbent, units, terms, budget):
    """Greedily maximize additional coverage per additional serialized token."""
    def covered(indices):
        features = {('term', word) for i in indices for word in terms[i]}
        for required, pair_features in units:
            if required <= indices:
                features.update(pair_features)
        return features

    selected = set(incumbent)
    total = budget.total(selected)
    remaining = list(range(len(units)))
    while remaining:
        base = covered(selected)
        best = None
        for i in remaining:
            trial = selected | units[i][0]
            if selected == trial:
                continue
            gain = _marginal_gain(base, covered(trial))
            if not gain:
                continue
            size = budget.total(trial)
            if size > FINAL_BUDGET:
                continue
            priority = (gain / max(1, size - total), gain, -size, -i)
            if best is None or priority > best[0]:
                best = priority, i, size, trial
        if best is None:
            break
        _, index, total, selected = best
        remaining.remove(index)
    return selected


def compress_reading(source, question, options, counter):
    """Return exact Unicode slices within 4096 tokenizer tokens of compact evidence JSON.

    options is a label-to-text mapping; use {} for open-ended questions.
    counter.text must count tokens without adding special tokens.
    """
    if not isinstance(source, str) or not isinstance(question, str) or not question.strip():
        raise ValueError('source must be text and question must contain text')
    if not isinstance(options, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                             for k, v in options.items()):
        raise ValueError('options must map string labels to string values')
    built = structure.build(source, question, options)
    nodes = built['nodes']
    budget = EvidenceBudget(source, nodes, counter)
    core, default = core_and_fill(built, budget)
    core_ranges = budget.ranges(core)
    view = {'built': built, 'question': question, 'default_indices': default,
            'core_ranges': core_ranges,
            'retained_structure_packages': _retained_structure_packages(source, built, default)}
    protected = _complete_package(view, core)
    texts = built['texts']
    task = pairs._terms(built['joint_query'])
    words = [pairs._terms(text) for text in texts]
    terms = [row & task for row in words]
    proposals = pairs._proposals(view, pairs._seeds(view), texts, words, task)[:TRIAL_LIMIT]
    incumbent = _fixed_incumbent(view, proposals, terms, protected, budget)
    units = _eligible_pairs(view, proposals, terms, protected, budget)
    selected = _expand(incumbent, units, terms, budget)
    ranges = budget.ranges(selected)
    total = budget.tokens(ranges)
    if not protected <= incumbent <= selected or total > FINAL_BUDGET:
        raise ValueError('invalid packet: protected evidence missing or final budget exceeded')
    extra = budget.tokens(subtract(ranges, core_ranges))
    return {'evidence': payload(source, ranges), 'evidence_tokens': total, 'budget': FINAL_BUDGET,
            'core_tokens': budget.tokens(core_ranges), 'fixed_tokens': budget.total(incumbent),
            'supplement_tokens': extra, 'borrowed_tokens': max(0, extra - SLOT_BUDGET)}
