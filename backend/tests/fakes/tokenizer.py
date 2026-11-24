"""Deterministic tokenizer for HybridChunker tests: one character is one token. Avoids
downloading the BGE-M3 tokenizer in the gate."""

from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer


class CharTokenizer(BaseTokenizer):
    max_tokens: int = 60

    def count_tokens(self, text: str) -> int:
        return len(text)

    def get_max_tokens(self) -> int:
        return self.max_tokens

    def get_tokenizer(self):
        # semchunk requires a callable token counter here.
        return len
