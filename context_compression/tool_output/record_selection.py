"""Task-aware budget packing shared by JSON documents and JSON Lines streams."""
import re
from collections import Counter

from ..json_codec import JsonNumber, encode_json, make_pointer


_WORDS = re.compile(r'[a-z0-9]+|[\u3400-\u9fff]+')
_STOP = frozenset('a an and are as at be by for from in is of on or the to what which with'.split())
_HINTS = (
    (('失败', '错误', '异常', 'failure', 'failed', 'errors'), ('fail', 'failed', 'failure', 'error')),
    (('重试', '重跑', 'retry', 'retries'), ('retry', 'attempt')),
    (('用量', '消耗', 'token', 'tokens'), ('usage', 'tokens', 'input', 'output')),
    (('耗时', '延迟', 'latency', 'duration'), ('duration', 'elapsed', 'latency')),
    (('警告', 'warnings'), ('warn', 'warning')),
    (('跳过', 'skipped'), ('skip', 'skipped')),
)


def _terms(text):
    words = _WORDS.findall(re.sub(r'([a-z])([A-Z])', r'\1 \2', text).casefold())
    tokens = set()
    for word in words:
        if word[0] >= '\u3400' and len(word) > 1:
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
        elif word not in _STOP:
            tokens.add(word)
    return tokens


def query_terms(question):
    terms = _terms(question)
    for triggers, words in _HINTS:
        if terms.intersection(triggers):
            terms.update(words)
    return terms


_BAD = frozenset(('error', 'failed', 'fail', 'failure', 'fatal', 'critical', 'exception', '失败', '错误'))
_ERROR_FIELDS = frozenset(('error', 'errors', 'exception', 'failure', 'failures', '错误', '异常'))


def _error(key, value):
    field = key.casefold()
    if field in _ERROR_FIELDS:
        if isinstance(value, JsonNumber):
            # Compare the significand exactly, including signed/scientific zero.
            return re.fullmatch(r'-?0(?:\.0+)?(?:[eE][+-]?[0-9]+)?', value) is None
        return bool(value)
    if isinstance(value, dict):
        return any(_error(k, v) for k, v in value.items())
    if isinstance(value, list):
        return any(_error(key, child) for child in value)
    if field in ('ok', 'success', 'passed'):
        return value is False
    if field in ('status', 'state', 'level', 'severity', '结果', '状态') and isinstance(value, str):
        if value.casefold() in _BAD:
            return True
    if field in ('status_code', 'statuscode', 'http_status', 'status'):
        return isinstance(value, str) and re.fullmatch(r'[45][0-9][0-9]', value) is not None
    if field in ('exit_code', 'exitcode', 'returncode') and isinstance(value, JsonNumber):
        return re.fullmatch(r'-?[0-9]+', value) is not None and value.lstrip('-0') != ''
    return False


def select_records(units, question, limit, counter, *, view="selection", source_key="path",
               preferred=frozenset(), required_fields=()):
    """Select source-bound units; a JSONL location contains its physical line number."""
    highlights = query_terms(question)
    addresses = [parts[0] if source_key == "line" else make_pointer(parts) for parts, _ in units]
    matches = [_terms(str(address) + ' ' + encode_json(value)) & highlights
               for address, (_, value) in zip(addresses, units)]
    frequency = Counter(term for matched in matches for term in matched)
    def priority(i):
        parts, value = units[i]
        relevance = sum(1 / frequency[term] for term in sorted(matches[i]))
        error = _error(str(parts[-1]) if parts else '', value)
        return -relevance, not error, i not in preferred, i not in (0, len(units) - 1), i

    # Encode accepted entries once. Most candidates near the budget boundary
    # can also be screened by counting just their small entry, rather than
    # re-tokenizing the entire accumulated packet on every rejection.
    kept = {}
    def render():
        entries = ','.join(kept[i] for i in sorted(kept))
        return ('{"view":' + encode_json(view) + ',"units":' + str(len(units)) +
                ',"omitted":' + str(len(units) - len(kept)) + ',"items":[' + entries + ']}')

    result = render()
    room = limit - counter.text(result)
    if room < 0:
        notice = '{"view":"omitted","expand":true}'
        if counter.text(notice) <= limit:
            return notice
        raise ValueError("JSON budget too small to report omitted content")

    # The count in the header and punctuation between entries can re-tokenize.
    # A local estimate filters candidates; only the full measured view is accepted.
    boundary_slack = counter.text(',[]{}:' + str(len(units)))
    def admit(index, item):
        nonlocal result, room
        entry = encode_json(item)
        previous = kept.get(index)
        if previous is None and counter.text(entry) > room + boundary_slack:
            return False
        kept[index] = entry
        trial = render()
        size = counter.text(trial)
        if size <= limit:
            result, room = trial, limit - size
            return True
        if previous is None:
            del kept[index]
        else:
            kept[index] = previous
        return False

    for i in sorted(range(len(units)), key=priority):
        _, value = units[i]
        address = addresses[i]
        if admit(i, {source_key: address, 'value': value}):
            continue
        if not isinstance(value, dict) or len(value) < 2:
            continue
        # Display a projection rather than a partial value masquerading as a full row.
        def field_priority(key):
            match = _terms(key + ' ' + encode_json(value[key])) & highlights
            identifier = key.casefold() in ('id', 'name', 'key', 'sample_id', 'test_id')
            return (-len(match), not _error(key, value[key]), not identifier)
        fields = {key: value[key] for key in required_fields if key in value}
        for key in sorted((key for key in value if key not in fields), key=field_priority):
            fields[key] = value[key]
            if not admit(i, {source_key: address, 'fields': fields, 'fields_omitted': len(value) - len(fields)}):
                del fields[key]
    return result
