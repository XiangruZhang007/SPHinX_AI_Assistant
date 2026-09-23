"""Deterministic, dependency-free lexical retrieval for the Prototype V1 corpus."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from math import log
from pathlib import Path
import re
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
ROLE_HINTS = {
    "simulation": {"simulation_case", "simulation_example"},
    "case": {"simulation_case", "simulation_example"},
    "example": {"simulation_case", "simulation_example"},
    "dambreak": {"simulation_case", "simulation_example"},
    "heat": {"simulation_case", "simulation_example"},
    "transfer": {"simulation_case", "simulation_example"},
    "class": {"core_library", "simulator_library", "binding"},
    "function": {"core_library", "simulator_library", "binding"},
    "method": {"core_library", "simulator_library", "binding"},
    "api": {"core_library", "simulator_library", "binding"},
    "source": {"core_library", "simulator_library", "binding"},
    "code": {"core_library", "simulator_library", "binding"},
    "builder": {"simulator_library", "binding"},
    "config": {"config_or_schema"},
    "configuration": {"config_or_schema"},
    "configured": {"config_or_schema"},
    "schema": {"config_or_schema"},
    "json": {"config_or_schema"},
    "test": {"unit_test", "test"},
}
REPOSITORY_HINTS = {"sphinxsys": "SPHinXsys", "sphinxsim": "SPHinXsim"}
GENERIC_SYMBOL_TERMS = {"what", "when", "where", "which", "who", "with", "from", "this", "that"}
GENERIC_PATH_INTENT_TERMS = {
    "simulation", "example", "case", "source", "code", "function", "method",
    "config", "configuration", "configured", "schema", "test", "field", "fields",
    "dynamic", "dynamics", "what", "when", "where", "which", "who", "how",
    "does", "used", "use", "defined", "define", "information", "sphinxsys", "sphinxsim",
}
SCHEMA_CONFIG_INTENT_TERMS = {"config", "configuration", "configured", "schema", "json"}


def tokenize(text: str) -> list[str]:
    """Lowercase words plus identifier components, including CamelCase pieces."""
    tokens: list[str] = []
    for value in TOKEN_RE.findall(text):
        lowered = value.lower()
        tokens.append(lowered)
        if lowered in {"configuration", "configured"}:
            tokens.append("config")
        if len(lowered) > 4 and lowered.endswith("s") and not lowered.endswith("ss"):
            tokens.append(lowered[:-1])
        for part in re.split(r"_+", value):
            tokens.extend(piece.lower() for piece in CAMEL_RE.findall(part) if piece.lower() != lowered)
    return tokens


def _canonical(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _context_text(record: dict[str, Any]) -> str:
    return json.dumps(record.get("parent_context", {}), ensure_ascii=False, sort_keys=True)


def _searchable_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(key, ""))
        for key in ("content", "symbol", "source_path", "source_role", "repository", "section_title")
    ) + "\n" + _context_text(record)


@dataclass(frozen=True)
class SearchResult:
    record: dict[str, Any]
    lexical_score: float
    symbol_boost: float
    path_boost: float
    role_boost: float
    repository_boost: float

    @property
    def score(self) -> float:
        return self.lexical_score + self.symbol_boost + self.path_boost + self.role_boost + self.repository_boost

    def as_dict(self, include_debug: bool = False) -> dict[str, Any]:
        output = {
            "score": round(self.score, 6),
            "repository": self.record["repository"],
            "source_role": self.record["source_role"],
            "source_path": self.record["source_path"],
            "chunk_type": self.record.get("chunk_type"),
            "symbol_or_section_title": self.record.get("symbol") or self.record.get("section_title"),
            "start_line": self.record.get("start_line"),
            "end_line": self.record.get("end_line"),
        }
        if include_debug:
            output["score_components"] = {
                "lexical": round(self.lexical_score, 6),
                "symbol_boost": self.symbol_boost,
                "path_boost": self.path_boost,
                "role_boost": self.role_boost,
                "repository_boost": self.repository_boost,
                "final": round(self.score, 6),
            }
        return output


class RetrievalV1:
    """In-memory BM25 baseline over the existing V1 JSONL corpus."""

    def __init__(self, corpus_path: Path, k1: float = 1.5, b: float = 0.75):
        self.corpus_path = corpus_path
        self.k1 = k1
        self.b = b
        self.records = self._load_records(corpus_path)
        self.term_frequencies = [Counter(tokenize(_searchable_text(record))) for record in self.records]
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

    @staticmethod
    def _load_records(path: Path) -> list[dict[str, Any]]:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if not records:
            raise ValueError(f"Corpus is empty: {path}")
        return records

    def _bm25(self, query_terms: list[str], index: int) -> float:
        counts = self.term_frequencies[index]
        length = self.document_lengths[index]
        denominator_scale = self.k1 * (1.0 - self.b + self.b * length / self.average_document_length)
        return sum(
            self.idf.get(term, 0.0) * counts[term] * (self.k1 + 1.0) / (counts[term] + denominator_scale)
            for term in set(query_terms)
            if term in counts
        )

    @staticmethod
    def _symbol_boost(query: str, record: dict[str, Any]) -> float:
        symbol = str(record.get("symbol") or record.get("section_title") or "")
        canonical_symbol = _canonical(symbol)
        query_terms = set(tokenize(query))
        explicit_identifiers = {
            _canonical(value)
            for value in TOKEN_RE.findall(query)
            if "_" in value or re.search(r"[a-z][A-Z]", value)
        }
        symbol_terms = {
            term for term in tokenize(symbol) if len(term) >= 3 and term != canonical_symbol
        }
        if len(canonical_symbol) >= 4 and canonical_symbol not in GENERIC_SYMBOL_TERMS and canonical_symbol in query_terms:
            return 24.0 if canonical_symbol in explicit_identifiers else 12.0
        if len(symbol_terms) >= 2 and symbol_terms <= query_terms:
            return 10.0
        return 0.0

    @staticmethod
    def _path_boost(query_terms: list[str], record: dict[str, Any]) -> float:
        path_terms = set(tokenize(record["source_path"]))
        meaningful = {term for term in query_terms if len(term) >= 4}
        generic_boost = min(8.25, 2.75 * len(path_terms & meaningful))
        identifiers = meaningful - GENERIC_PATH_INTENT_TERMS
        identifier_boost = min(6.0, 6.0 * len(path_terms & identifiers))
        return generic_boost + identifier_boost

    @staticmethod
    def _role_boost(query_terms: list[str], record: dict[str, Any]) -> float:
        if set(query_terms) & SCHEMA_CONFIG_INTENT_TERMS:
            if record["source_role"] == "config_or_schema":
                return 14.0
            if record["source_role"] == "test":
                return -1.0
        hinted_roles = set().union(*(ROLE_HINTS.get(term, set()) for term in set(query_terms)))
        return 2.5 if record["source_role"] in hinted_roles else 0.0

    @staticmethod
    def _repository_boost(query_terms: list[str], record: dict[str, Any]) -> float:
        target = next((REPOSITORY_HINTS[term] for term in query_terms if term in REPOSITORY_HINTS), None)
        return 1.5 if target == record["repository"] else 0.0

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        query_terms = tokenize(query)
        if not query_terms:
            return []
        results = []
        for index, record in enumerate(self.records):
            lexical = self._bm25(query_terms, index)
            symbol = self._symbol_boost(query, record)
            path = self._path_boost(query_terms, record)
            role = self._role_boost(query_terms, record)
            repository = self._repository_boost(query_terms, record)
            if lexical + symbol + path + role + repository > 0:
                results.append(SearchResult(record, lexical, symbol, path, role, repository))
        ranked = sorted(results, key=lambda result: (-result.score, result.record["chunk_id"]))
        unique_results = []
        seen_evidence = set()
        for result in ranked:
            record = result.record
            evidence_key = (
                record["repository"],
                record["source_path"],
                record.get("symbol") or record.get("section_title"),
            )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
            unique_results.append(result)
            if len(unique_results) == top_k:
                break
        return unique_results


def content_preview(content: str, limit: int = 220) -> str:
    text = " ".join(content.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
