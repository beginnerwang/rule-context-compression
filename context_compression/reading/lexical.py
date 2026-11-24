"""Deterministic BM25 lexical scores; no graph or trained encoder."""
import math
import re
from collections import Counter, defaultdict

STOP = set("a an the of in on to and or is are was were be been for with by as at it this that these those from".split())


def terms(text):
    # Latin words/numbers plus CJK characters; do not normalize source text itself.
    return [x for x in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", text.casefold()) if x not in STOP]


class LexicalIndex:
    def __init__(self, texts):
        self.postings = defaultdict(dict)
        self.lengths = []
        for i, text in enumerate(texts):
            counts = Counter(terms(text))
            self.lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                self.postings[term][i] = frequency
        n = len(self.lengths)
        self.average = sum(self.lengths) / max(1, n) or 1
        self.idf = {term: math.log(1 + (n - len(hits) + .5) / (len(hits) + .5))
                    for term, hits in self.postings.items()}

    def ranked_hits(self, query):
        scores = [0.0] * len(self.lengths)
        # Stable accumulation order keeps exact scores and receipt identities equal
        # across interpreter hash seeds; sorting only after summation is too late.
        for t in sorted(set(terms(query))):
            for i, tf in self.postings.get(t, {}).items():
                scores[i] += self.idf[t] * tf * 2.2 / (tf + 1.2 * (.25 + .75 * self.lengths[i]/self.average))
        maximum = max(scores, default=0) or 1
        return sorted((i for i, score in enumerate(scores) if score > 0),
                      key=lambda i: (-scores[i] / maximum, i))
