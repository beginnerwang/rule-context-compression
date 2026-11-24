"""Exact text counts with the same pinned Qwen tokenizer used by the reading baseline."""
from pathlib import Path

from tokenizers import Tokenizer


class TokenCounter:
    def __init__(self, path=None):
        asset = Path(path) if path is not None else Path(__file__).with_name("assets") / "tokenizer.json"
        self.tokenizer = Tokenizer.from_file(str(asset))

    def text(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)
