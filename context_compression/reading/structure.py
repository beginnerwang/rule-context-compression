"""Source-scoped headings, dependencies and query-route retrieval."""
import re
from bisect import bisect_left, bisect_right

from .passages import Passage, sentences
from .lexical import LexicalIndex


CONDITION = re.compile(r"^(?:(?:if|when|only\s+if|unless|except|provided|otherwise|however|"
                       r"although|because|despite|in\s+contrast)\b|如果|除非|但是|然而|仅当|但|否则)", re.I)


QUERY_WORDS = {"What", "Which", "Where", "When", "Why", "How", "Who", "The", "This", "That", "According"}


INSTRUCTION = re.compile(r"^(?:you|we|i|they|it|please|do|don't|generate|issue|return|click|select|use|follow|"
                         r"ensure|press|type|open|enter|choose|provide|read|write|stop|navigate|wait|check|"
                         r"observe|submit|answer|set|add|remove|delete|start|let|make|find|calculate|tell|"
                         r"determine|evaluate|explain|remember|perform)\b|"
                         r"\b(?:you|your|should|must|shall|will|cannot|need to|have to)\b", re.I)


STOP_TERMS = frozenset("a an the of and or to in on for with from by as is are be at that this it its article articles paper papers method methods approach algorithm design relationship between correct description".split())


DEFINITION_CUE = re.compile(r"\b(?:denot(?:e[sd]?|ing)|defined\s+as|refers?\s+to|consists?\s+of)\b|"
                            r"[⊆⊂]|定义为|表示为", re.I)


DEFINITION_MAX_SENTENCES = 6


DEFINITION_MAX_CHARACTERS = 1600


DEFINITION_STOP = STOP_TERMS | frozenset(
    "we our their these those this only all each also then using used use where which respectively "
    "following figure depicts explained section consider assume propose framework model models".split())


def _source_lines(source):
    lines = source.splitlines(keepends=True)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    return lines, offsets


def _quoted_lines(lines, reset_lines=()):
    """Do not interpret prompt/code examples as document structure."""
    protected, fence, quote = [], None, None
    for i, line in enumerate(lines):
        if i in reset_lines:
            fence, quote = None, None
        marker = re.fullmatch(r"\s*(`{3,}|~{3,})(?:[\w+-]+)?\s*", line)
        protected.append(bool(fence or quote or marker))
        if marker and quote is None:
            token = marker[1][0]
            fence = None if fence == token else token if fence is None else fence
        elif fence is None:
            for match in re.finditer(r'"""|\x27\x27\x27', line):
                quote = None if quote == match[0] else match[0] if quote is None else quote
            if quote is not None:
                protected[-1] = True
    return protected


def _document_boundaries(source, lines, offsets):
    """Conservative explicit wrappers or academic title/author/affiliation/Abstract blocks.

    Plain mentions of another paper and an isolated Abstract are not boundaries.
    Unmarked concatenations without this evidence remain one unresolved scope.
    """
    protected = _quoted_lines(lines)
    found = {0}
    explicit = re.compile(r"^(?:#{1,6}\s*)?(?:document|paper|article|文档|论文|文章)\s*"
                          r"(?:\d+|[A-Z])(?:\s*[:：]\s*\S.*)?\s*$", re.I)
    for i, line in enumerate(lines):
        title = line.strip()
        if not protected[i] and (explicit.fullmatch(title) or re.fullmatch(r'<document(?:\s+[^<>]+)?>', title, re.I)):
            found.add(offsets[i])
        if title.casefold() not in ("abstract", "摘要"):
            continue
        lo = max(0, offsets[i] - 1600)
        separators = list(re.finditer(r"\n[ \t\r]*\n[ \t\r]*\n+", source[lo:offsets[i]]))
        start = lo + separators[-1].end() if separators else 0
        # PDF extraction can leave example quotes unbalanced. A new academic
        # frontmatter block after an isolated page number may close that stale
        # quote state; an Abstract embedded in an ordinary quoted example may not.
        previous = source[:start].rstrip().rsplit("\n", 1)[-1].strip()
        if protected[i] and not re.fullmatch(r"\d{1,4}", previous):
            continue
        front = source[start:offsets[i]]
        front_lines = [s.strip() for s in front.splitlines() if s.strip()]
        # At least a title and author line, followed by numbered affiliations.
        affiliations = sum(bool(re.match(r"^[1-9][0-9,*†‡ ]*[A-Z][A-Za-z]", s)) for s in front_lines)
        author_line = any(len(re.findall(r"\b[A-Z][a-z]+", s)) >= 3 and re.search(r"[1-9†‡∗]", s)
                          for s in front_lines[1:])
        title_like = (front_lines and 12 <= len(front_lines[0]) <= 240
                      and not re.search(r'[{}\[\]"`=]', front_lines[0])
                      and not INSTRUCTION.search(front_lines[0]))
        if start > 0 and title_like and author_line and affiliations >= 1 and len(front_lines) <= 18:
            found.add(start)
    return sorted(found)


def _split_boundaries(nodes, boundaries):
    result = []
    for node in nodes:
        lo, hi = bisect_right(boundaries, node.start), bisect_left(boundaries, node.end)
        cuts = [node.start, *boundaries[lo:hi], node.end]
        result.extend(Passage(node.id if len(cuts) == 2 else node.id + f".d{part:04d}", a, b)
                      for part, (a, b) in enumerate(zip(cuts, cuts[1:])))
    return result


def _headings(lines, offsets, nodes, boundaries=()):
    starts = [node.start for node in nodes]
    boundary_offsets = set(boundaries)
    protected = _quoted_lines(lines, {i for i, offset in enumerate(offsets) if offset in boundary_offsets})
    found = []
    def title_like(value):
        return (1 < len(value) <= 160 and len(value.split()) <= 18
                and not re.search(r"[.!?。！？;；:]$", value)
                and bool(re.match(r"[A-Z\u3400-\u9fff]", value)) and not INSTRUCTION.search(value))
    def numbered_title(value):
        # Numeric tables and equation fragments are not section titles. A lone
        # capitalised acronym is not enough without an explicit section marker.
        return (title_like(value)
                and not re.search(r"[=≠≈≤≥∑∥]|\b\w+\s*[+*/^]\s*\w+", value)
                and not re.search(r"[.!?。！？;；]$", value))
    def quantity_label(markdown, lineno):
        if markdown[1] != "#" or not re.match(r"of\b", markdown[2], re.I):
            return False
        # Require nearby table values: '# Of Mice and Men' remains a title.
        nearby = lines[max(0, lineno - 4):lineno] + lines[lineno + 1:lineno + 9]
        return any(re.fullmatch(r"[ \t]*[+−-]?\d[\d\s.,±%+−–-]*", value.strip())
                   for value in nearby)
    def table_acronym(value, lineno):
        # PDF architecture tables contain cells such as '2 FC'. Their numeric
        # prefixes are counts, not section numbers. Require a nearby caption so
        # genuine acronym section titles outside tables remain supported.
        return bool(re.fullmatch(r"[A-Z][A-Z0-9-]{0,7}", value) and any(
            re.match(r"\s*(?:TABLE\s+(?:[IVX]+|\d+)\b|表\s*\d+)", previous, re.I)
            for previous in lines[max(0, lineno - 12):lineno]))
    for lineno, line in enumerate(lines):
        if protected[lineno] or (offsets[lineno] != 0 and offsets[lineno] in boundary_offsets):
            continue
        offset = offsets[lineno]
        title = line.strip()
        level, heading_end = None, offset + len(line.rstrip())
        if 0 < len(title) <= 160:
            markdown = re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*", title)
            numbered = re.fullmatch(r"(\d{1,2}(?:\.\d{1,2})*)\.?[ \t]+([^\n]+)", title)
            roman = re.fullmatch(r"([IVX]{1,6})[.)][ \t]+([A-Z][^\n]+)", title)
            letter = re.fullmatch(r"([A-Z])[.)][ \t]+(.+)", title)
            standalone_roman = re.fullmatch(r"(?:II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)", title)
            # In extracted tables '#' means 'number', not a Markdown marker.
            # Keep ordinary lowercase/punctuated Markdown titles and deeper '#'
            # headings; do not apply the numbered-title prose heuristic to them.
            if markdown and not quantity_label(markdown, lineno):
                level = len(markdown[1])
            elif numbered and numbered_title(numbered[2]) and not table_acronym(numbered[2], lineno) and not any(
                    0 <= n < len(lines) and re.match(r"\s*\d+[.)]\s+", lines[n])
                    for n in (lineno - 1, lineno + 1)):
                level = numbered[1].count(".") + 1
            elif roman and title_like(roman[2]):
                level = 1
            elif letter and title_like(letter[2]):
                # Letter subheadings are below numbered/roman top-level sections.
                level = 2
            elif standalone_roman and (lineno == 0 or not lines[lineno - 1].strip()):
                level = 1
                # A standalone section number may be separated from its title.
                # Keep both source spans; never reconstruct a synthetic heading.
                following = lineno + 1
                if following < len(lines) and not lines[following].strip():
                    following += 1
                if (following < len(lines) and offsets[following] not in boundary_offsets
                        and not protected[following] and title_like(lines[following].strip())):
                    heading_end = offsets[following] + len(lines[following].rstrip())
        if level is not None and nodes:
            position = offset + len(line) - len(line.lstrip())
            index = max(0, bisect_right(starts, position) - 1)
            last = max(index, bisect_right(starts, max(position, heading_end - 1)) - 1)
            found.append({"offset": position, "end": heading_end, "level": level,
                          "indices": set(range(index, last + 1))})
    return found


def _definition_blocks(texts, nodes, node_documents, headings, section_keys):
    """Bounded, disjoint local definitions and their uses; never a recursive search.

    One intervening explanatory sentence is allowed. Blocks cannot cross a
    paragraph, section, or document, and heading nodes cannot acquire definition
    dependencies that would then be inherited by the rest of a section.
    """
    heading_nodes = {i for h in headings for i in h["indices"]}
    def terms(text):
        return _terms(text) - DEFINITION_STOP
    def symbols(text):
        # Mixed-case variable names survive PDF line breaks in subscripts.
        values = set(re.findall(r"\b(?:[A-Z][a-z0-9]{1,2}|[a-zA-Z]_[a-zA-Z0-9]+)\b", text))
        return values - {"We", "The", "It", "In", "As", "An", "If", "To", "By", "Of", "On",
                         "At", "No", "So", "Is", "Or", "Do", "Be", "For", "But", "And", "Let"}
    blocks, occupied = [], set()
    for start, text in enumerate(texts):
        if start in occupied or start in heading_nodes or not DEFINITION_CUE.search(text):
            continue
        vocabulary, variables = terms(text), symbols(text)
        last, gaps = start, 0
        for end in range(start + 1, min(len(nodes), start + DEFINITION_MAX_SENTENCES)):
            if (end in heading_nodes or end in occupied
                    or node_documents[end] != node_documents[start]
                    or nodes[end].id.split(".")[0] != nodes[start].id.split(".")[0]
                    or section_keys[end] != section_keys[start]
                    or nodes[end].end - nodes[start].start > DEFINITION_MAX_CHARACTERS):
                break
            related = len(vocabulary & terms(texts[end])) >= 2 or bool(variables & symbols(texts[end]))
            if related:
                last = end
                if DEFINITION_CUE.search(texts[end]):
                    vocabulary.update(terms(texts[end]))
                    variables.update(symbols(texts[end]))
            else:
                gaps += 1
                if gaps > 1:
                    break
        if last > start:
            group = set(range(start, last + 1))
            occupied.update(group)
            blocks.append(group)
    return blocks


def _closure(seed, edges):
    chosen, pending = {seed}, [seed]
    while pending:
        for other in edges[pending.pop()]:
            if other not in chosen:
                chosen.add(other)
                pending.append(other)
    return chosen


def _terms(text):
    # PDF line-end hyphenation is normalised for matching only, never for output.
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text).casefold()
    return {term for term in re.findall(r"[a-z][a-z0-9]+|[\u3400-\u9fff]+", text)
            if len(term) >= 3 and term not in STOP_TERMS}


DEPENDENT = re.compile(r'^(?:this|these|those|it|they|such|however|therefore|otherwise|unless|except|provided|in contrast|more specifically)\b',re.I)


END = re.compile(r'[.!?。！？][\s\"\x27”’»）)\]]*$')


def context_bundles(source, nodes):
    """Keep broken sentences together and attach directly dependent neighbors."""
    groups = []
    for i, node in enumerate(nodes):
        continuation = False
        if i:
            previous = nodes[i - 1]
            left = source[previous.start:previous.end]
            # Ignore an isolated PDF page number while recognizing a line break.
            left = re.sub(r'\n\s*\d+\s*$', '', left).rstrip()
            right = source[node.start:node.end].lstrip()
            continuation = bool(left and right and not END.search(left)
                                and (right[0].islower() or left.endswith('-')))
        if continuation:
            groups[-1].append(i)
        else:
            groups.append([i])
    result = {}
    for j, group in enumerate(groups):
        required = set(group)
        head = nodes[group[0]]
        if j and DEPENDENT.match(source[head.start:head.end].lstrip()):
            required.update(groups[j - 1])
        if j + 1 < len(groups):
            following = nodes[groups[j + 1][0]]
            if DEPENDENT.match(source[following.start:following.end].lstrip()):
                required.update(groups[j + 1])
        for i in group:
            result[i] = required.copy()
    return result


def build(source, question, options):
    """Index sentences, close their structural dependencies, and fuse BM25 routes."""
    lines, offsets = _source_lines(source)
    boundaries = _document_boundaries(source, lines, offsets)
    nodes = _split_boundaries(sentences(source), boundaries)
    node_documents = [bisect_right(boundaries, n.start) - 1 for n in nodes]
    texts = [source[node.start:node.end] for node in nodes]
    if "".join(texts) != source:
        raise ValueError("sentence partition does not preserve the original document")
    index = LexicalIndex(texts)
    headings = _headings(lines, offsets, nodes, boundaries)
    edges = context_bundles(source, nodes)
    for heading in headings:
        for i in heading["indices"]:
            edges[i].update(heading["indices"])
    anchors = {i: set() for i in range(len(nodes))}
    stack, cursor, previous_document = [], 0, None
    section_keys = {}
    for i, node in enumerate(nodes):
        if node_documents[i] != previous_document:
            stack = []
            previous_document = node_documents[i]
        while cursor < len(headings) and headings[cursor]["offset"] < node.end:
            heading = headings[cursor]
            while stack and stack[-1]["level"] >= heading["level"]:
                stack.pop()
            stack.append(heading)
            cursor += 1
        anchors[i].update(j for h in stack for j in h["indices"] if node_documents[j] == node_documents[i])
        section_keys[i] = tuple(h["offset"] for h in stack)
        edges[i].update(anchors[i])
        if i and node_documents[i - 1] == node_documents[i] and CONDITION.match(texts[i].lstrip()):
            edges[i].add(i - 1)
        if i + 1 < len(nodes) and node_documents[i + 1] == node_documents[i] and CONDITION.match(texts[i + 1].lstrip()):
            edges[i].add(i + 1)
    definition_blocks = _definition_blocks(texts, nodes, node_documents, headings, section_keys)
    for group in definition_blocks:
        for i in group:
            edges[i].update(group)
    # Filter *all* edges, including inherited continuation bundles, before closure.
    edges = {i: {j for j in neighbours if node_documents[j] == node_documents[i]}
             for i, neighbours in edges.items()}
    closed = {i: _closure(i, edges) for i in range(len(nodes))}

    ordered = [options[key] for key in sorted(options)]
    joint_query = " ".join([question, *ordered])
    joint_hits = index.ranked_hits(joint_query)
    queries = [question]
    if ordered:
        queries.append(joint_query)
        queries.extend(question + " " + value for value in ordered)
    scores = {}
    for query in queries:
        hits = joint_hits if query == joint_query else index.ranked_hits(query)
        for rank, i in enumerate(hits[:16], 1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (60 + rank)
    ranked = sorted(scores, key=lambda i: (-scores[i], i))
    # Pair nomination also excludes standalone document marker headings.
    pair_headings = headings if len(boundaries) == 1 else _headings(lines, offsets, nodes)
    heading_nodes = {i for h in pair_headings for i in h['indices']
                     if max(nodes[i].start, h['offset']) < min(nodes[i].end, h['end'])}
    return dict(nodes=nodes, texts=texts, ranked=ranked, bundles=closed, headings=heading_nodes,
                anchors=anchors, node_documents=node_documents, joint_query=joint_query, joint_hits=joint_hits)
