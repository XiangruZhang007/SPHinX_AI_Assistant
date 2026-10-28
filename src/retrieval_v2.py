"""Opt-in R2 lexical index, retaining R1 scoring, ranking and original records.

Only parent_context.original_function_chunk_id is omitted from the search view.
Identity-like text in source content or any other field remains untouched. R2
does not promise identity-independent rankings: R1's chunk-ID tie-break and
evidence deduplication are inherited unchanged.
"""
from __future__ import annotations

from collections import Counter
from math import log
from pathlib import Path
from typing import Any

from . import retrieval_v1 as r1

VERSION = "r2-identity-neutral-v1"
EXCLUDED_CONTEXT_KEY = "original_function_chunk_id"
RETRIEVER_VERSIONS = ("r1", "r2")


def searchable_text(record: dict[str, Any]) -> str:
    """Use R1 serialization on a separate shallow view; never mutate a record."""
    context = record.get("parent_context")
    if not isinstance(context, dict) or EXCLUDED_CONTEXT_KEY not in context:
        return r1._searchable_text(record)
    view = {
        **record,
        "parent_context": {key: value for key, value in context.items()
                           if key != EXCLUDED_CONTEXT_KEY},
    }
    return r1._searchable_text(view)


class RetrievalV2(r1.RetrievalV1):
    """Explicit R2 index constructor; all score/search methods remain R1's.

    R1 calls a module-level text function in its constructor, so overriding a
    method would not work. This constructor builds the same index statistics
    from the filtered view. The retained texts are the actual input used for
    tokenization, making isolation directly inspectable.
    """
    version = VERSION

    def __init__(self, corpus_path: Path, k1: float = 1.5, b: float = 0.75):
        self.corpus_path = corpus_path
        self.k1 = k1
        self.b = b
        self.records = self._load_records(corpus_path)
        self.indexed_texts = tuple(searchable_text(record) for record in self.records)
        self.term_frequencies = [Counter(r1.tokenize(text)) for text in self.indexed_texts]
        self.document_lengths = [sum(counts.values()) for counts in self.term_frequencies]
        self.average_document_length = sum(self.document_lengths) / len(self.document_lengths)
        document_frequencies: Counter[str] = Counter()
        for counts in self.term_frequencies:
            document_frequencies.update(counts.keys())
        total = len(self.records)
        self.idf = {
            term: log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequencies.items()
        }


def create_retriever(corpus_path: Path, *, version: str = "r1",
                     k1: float = 1.5, b: float = 0.75) -> r1.RetrievalV1:
    """New standalone selector; historical entry points still import R1 directly."""
    if version not in RETRIEVER_VERSIONS:
        raise ValueError(f"Unknown Retriever version: {version!r}")
    factory = r1.RetrievalV1 if version == "r1" else RetrievalV2
    return factory(corpus_path, k1=k1, b=b)
