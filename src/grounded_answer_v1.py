"""Provider-agnostic grounded answer synthesis for Prototype V1.

Configuration is read only from ``LLM_API_KEY``, ``LLM_MODEL``, and the
optional ``LLM_BASE_URL``.  When no base URL is supplied, the OpenAI SDK
default endpoint is used.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI

from src.retrieval_v1 import RetrievalV1, SearchResult, content_preview


DEFAULT_TOP_K = 5
TOTAL_EVIDENCE_CHAR_BUDGET = 18_000
MAX_EVIDENCE_CONTENT_CHARS = 4_000
LLM_TIMEOUT_SECONDS = 180
SENSITIVE_ERROR_FIELD_FRAGMENTS = {"authorization", "api_key", "apikey", "token", "secret", "password"}

SYSTEM_PROMPT = """You are a repository-grounded technical assistant.
Answer only from the supplied repository evidence. Do not rely on unsupported
outside knowledge. If the evidence is insufficient, state that clearly.

Distinguish SPHinXsys from SPHinXsim. Do not invent functions, classes,
configuration fields, source paths, line numbers, or runtime call graphs.
Tests and examples are useful evidence, but do not treat them as authoritative
implementation evidence when stronger library or schema evidence is supplied.
For technical claims, cite relevant repository paths. Prefer concise technical
explanations and distinguish direct evidence from interpretation when useful.

Do not speculate about parameter meanings, design intent, physical
interpretation, implementation purpose, or domain conventions unless the
supplied evidence directly supports the statement. Do not use wording such as
"presumably", "likely", "typically", or "probably" for unsupported claims.
When evidence only shows a signature, declaration, value, or call, describe
only what is explicitly visible.

Do not silently merge SPHinXsys and SPHinXsim evidence. They may describe
related concepts, but do not present them as the same implementation or
configuration unless the supplied evidence explicitly establishes that
relationship. When both repositories are used, clearly identify which claims
come from SPHinXsys and which come from SPHinXsim; use wording such as
"Separately, the SPHinXsim configuration evidence shows..." where appropriate.
Do not imply that an SPHinXsim JSON configuration is the exact configuration
used by an SPHinXsys example unless the evidence proves that mapping.
"""


@dataclass(frozen=True)
class LLMConfiguration:
    api_key: str
    model: str
    base_url: str | None

    @classmethod
    def from_environment(
        cls,
        model_override: str | None = None,
        base_url_override: str | None = None,
        use_environment_base_url: bool = True,
    ) -> "LLMConfiguration":
        api_key = os.environ.get("LLM_API_KEY")
        model = model_override or os.environ.get("LLM_MODEL")
        base_url = (
            os.environ.get("LLM_BASE_URL") or None
            if use_environment_base_url
            else base_url_override
        )
        if not api_key:
            raise ValueError("LLM_API_KEY is required for this OpenAI-compatible prototype.")
        if not model:
            raise ValueError("LLM_MODEL is required.")
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("LLM_BASE_URL must be a valid http(s) URL when provided.")
        return cls(api_key=api_key, model=model, base_url=base_url)

    def safe_metadata(self) -> dict[str, str | None]:
        return {
            "model": self.model,
            "base_url": self.base_url or "OpenAI SDK default",
        }


def source_metadata(result: SearchResult) -> dict[str, Any]:
    record = result.record
    return {
        "repository": record["repository"],
        "source_role": record["source_role"],
        "source_path": record["source_path"],
        "symbol_or_section_title": record.get("symbol") or record.get("section_title"),
        "start_line": record.get("start_line"),
        "end_line": record.get("end_line"),
    }


def _redact_error_value(value: Any) -> Any:
    """Remove credential-like values from a provider response body."""
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(fragment in key.lower() for fragment in SENSITIVE_ERROR_FIELD_FRAGMENTS)
            else _redact_error_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_error_value(item) for item in value]
    return value


def _safe_provider_error_body(error: APIStatusError) -> str | None:
    """Return a short sanitized provider error body without inspecting headers."""
    body = getattr(error, "body", None)
    if body is None:
        return None
    sanitized = _redact_error_value(body)
    try:
        message = json.dumps(sanitized, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        message = str(sanitized)
    message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", message)
    message = re.sub(
        r"(?i)((?:api[_-]?key|authorization|token|secret|password)\s*[:=]\s*)[^\s,;\"']+",
        r"\1[redacted]",
        message,
    )
    return message[:600] if message and message not in {"{}", "[]", "null"} else None


def format_evidence(results: list[SearchResult]) -> tuple[str, list[dict[str, Any]]]:
    """Format ranked evidence under a deterministic character budget."""
    remaining = TOTAL_EVIDENCE_CHAR_BUDGET
    blocks: list[str] = []
    metadata: list[dict[str, Any]] = []
    for rank, result in enumerate(results, start=1):
        record = result.record
        source = source_metadata(result)
        label = source["symbol_or_section_title"] or "(unnamed chunk)"
        header = (
            f"[Evidence {rank}]\n"
            f"Repository: {source['repository']}\n"
            f"Role: {source['source_role']}\n"
            f"Path: {source['source_path']}\n"
            f"Symbol/Section: {label}\n"
            f"Lines: {source['start_line']}-{source['end_line']}\n"
            "Content:\n"
        )
        if len(header) >= remaining:
            break
        content_limit = min(MAX_EVIDENCE_CONTENT_CHARS, remaining - len(header))
        content = record["content"][:content_limit]
        if len(record["content"]) > content_limit:
            content += "\n[Evidence truncated to fit the Prototype V1 budget.]"
        block = header + content
        blocks.append(block)
        metadata.append(source)
        remaining -= len(block) + 2
        if remaining <= 0:
            break
    return "\n\n".join(blocks), metadata


class GroundedAnswerV1:
    """Retrieval, evidence packing, and generic OpenAI-compatible synthesis."""

    def __init__(self, corpus_path: Path):
        self.retriever = RetrievalV1(corpus_path)

    def answer(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        model_override: str | None = None,
        base_url_override: str | None = None,
        use_environment_base_url: bool = True,
    ) -> dict[str, Any]:
        results = self.retriever.search(query, top_k)
        evidence, sources = format_evidence(results)
        grounded_prompt = f"Question:\n{query}\n\nRepository evidence:\n{evidence}"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": grounded_prompt},
        ]
        query_fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
        prompt_fingerprint = hashlib.sha256(grounded_prompt.encode("utf-8")).hexdigest()[:12]
        first_source = sources[0]["source_path"] if sources else "(none)"
        print(
            "Grounded answer request: "
            f"query_sha256={query_fingerprint}, query_len={len(query)}, "
            f"prompt_sha256={prompt_fingerprint}, prompt_len={len(grounded_prompt)}, "
            f"prompt_contains_current_query={query in grounded_prompt}, "
            f"first_source={first_source}",
            flush=True,
        )
        base_result: dict[str, Any] = {
            "query": query,
            "answer": None,
            "model": model_override or os.environ.get("LLM_MODEL"),
            "base_url": (
                os.environ.get("LLM_BASE_URL") or "OpenAI SDK default"
                if use_environment_base_url
                else base_url_override or "OpenAI SDK default"
            ),
            "sources": sources,
            "success": False,
            "error": None,
        }
        if not results:
            base_result["error"] = "No retrieval evidence was found for the query."
            return base_result

        try:
            configuration = LLMConfiguration.from_environment(
                model_override=model_override,
                base_url_override=base_url_override,
                use_environment_base_url=use_environment_base_url,
            )
        except ValueError as error:
            base_result["error"] = str(error)
            return base_result

        base_result.update(configuration.safe_metadata())
        try:
            client_kwargs: dict[str, Any] = {
                "api_key": configuration.api_key,
                "timeout": LLM_TIMEOUT_SECONDS,
            }
            if configuration.base_url:
                client_kwargs["base_url"] = configuration.base_url
            client = OpenAI(**client_kwargs)
            response = client.chat.completions.create(
                model=configuration.model,
                messages=messages,
            )
        except AuthenticationError:
            base_result["error"] = "LLM authentication failed. Check LLM_API_KEY."
            return base_result
        except APIConnectionError:
            base_result["error"] = "LLM API connection failed. Check LLM_BASE_URL and network access."
            return base_result
        except APIStatusError as error:
            provider_body = _safe_provider_error_body(error)
            base_result["error"] = (
                f"LLM API request failed with HTTP status {error.status_code}."
                + (f" Provider response: {provider_body}" if provider_body else "")
            )
            return base_result
        except Exception as error:
            base_result["error"] = f"LLM request failed: {type(error).__name__}."
            return base_result

        answer = response.choices[0].message.content if response.choices else None
        if not answer or not answer.strip():
            base_result["error"] = "LLM returned an empty response."
            return base_result
        base_result["answer"] = answer.strip()
        print(
            f"Grounded answer response: answer_len={len(base_result['answer'])}",
            flush=True,
        )
        base_result["success"] = True
        return base_result


def source_preview(result: SearchResult) -> str:
    """Compact, CLI-safe preview without exposing full corpus records."""
    return content_preview(result.record["content"], limit=300)
