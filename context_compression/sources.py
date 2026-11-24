"""Keep immutable UTF-8 originals so a compressed view remains inspectable."""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from .json_codec import encode_json, lookup_pointer, parse_json


def physical_lines(raw: str) -> list[str]:
    """Split only on CRLF, LF or CR, preserving terminators and source line numbers."""
    return re.findall(r"[^\r\n]*(?:\r\n|[\r\n])|[^\r\n]+$", raw)


def source_id(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OriginalStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, raw: str) -> str:
        data = raw.encode("utf-8")
        key = hashlib.sha256(data).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / key
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("stored original failed its checksum")
            return key
        tmp = tempfile.NamedTemporaryFile(dir=self.directory, delete=False)
        name = Path(tmp.name)
        try:
            with tmp:
                tmp.write(data)
            os.replace(name, path)
        finally:
            name.unlink(missing_ok=True)
        return key

    def read(self, key: str) -> str:
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("source ID must be a lowercase SHA-256 digest")
        raw = (self.directory / key).read_bytes()
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError("stored original failed its checksum")
        return raw.decode("utf-8")


def restore(store, key, *, lines=None, pointer=None, chars=None):
    raw = store.read(key)
    if sum(x is not None for x in (lines, pointer, chars)) > 1:
        raise ValueError("select one slice of the original")
    if chars is not None:
        start, end = chars
        if not 0 <= start <= end <= len(raw):
            raise ValueError("character range is outside the original")
        return raw[start:end]
    if pointer is not None:
        return encode_json(lookup_pointer(parse_json(raw), pointer))
    if lines is not None:
        first, last = lines
        parts = physical_lines(raw)
        if not 1 <= first <= last <= len(parts):
            raise ValueError("line range is outside the original")
        return "".join(parts[first - 1:last])
    return raw
