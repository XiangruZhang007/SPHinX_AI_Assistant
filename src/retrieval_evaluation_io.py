"""Portable input validation and exact comparison for the frozen Step 1.6 run."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath

BENCHMARK_ID = "step_1_6_frozen_evaluation_v2"
GROUP_COUNTS = {"A": 40, "B": 14, "C": 30, "D": 4}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def read_json(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Required input does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def check_checksum(path: Path, expected: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required input does not exist: {path}")
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {actual}")


def validate_structure(spec: dict) -> None:
    if not isinstance(spec, dict) or spec.get("benchmark_id") != BENCHMARK_ID:
        raise ValueError(f"Expected benchmark identity {BENCHMARK_ID}")
    if type(spec.get("schema_version")) is not int or spec["schema_version"] != 1:
        raise ValueError("Expected schema_version 1 (distinct from benchmark v2)")
    questions = spec.get("questions")
    if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
        raise ValueError("questions must be a list of objects")
    identifiers = [q.get("id") for q in questions]
    if not all(isinstance(i, str) and i for i in identifiers):
        raise ValueError("Every question needs a nonempty string ID")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate question IDs")
    if Counter(q.get("group") for q in questions) != GROUP_COUNTS:
        raise ValueError("Expected exactly 88 questions with A40/B14/C30/D4")
    if spec.get("counts") != GROUP_COUNTS:
        raise ValueError("Frozen group count metadata differs")
    for q in questions:
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            raise ValueError(f"Missing question text: {q['id']}")
        if q.get("evidence_mode") != "ALL_OF":
            raise ValueError(f"Unsupported evidence operator: {q['id']}")
        requirements = q.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"Missing evidence requirements: {q['id']}")
        for c in requirements:
            path = c.get("path", "")
            parts = PurePosixPath(path).parts
            if (not path or "\\" in path or ".." in parts or len(parts) < 3
                    or parts[0] != "repos" or parts[1] not in ("SPHinXsys", "SPHinXsim")):
                raise ValueError(f"Invalid repository-qualified evidence path: {q['id']}")
            if "range" in c:
                interval = c["range"]
                if (not isinstance(interval, list) or len(interval) != 2
                        or any(type(n) is not int for n in interval)
                        or not 1 <= interval[0] <= interval[1]):
                    raise ValueError(f"Invalid evidence range: {q['id']}")
        if type(q.get("exact_evidence_available")) is not bool or not isinstance(q.get("availability"), list):
            raise ValueError(f"Missing frozen availability classification: {q['id']}")
        if q["group"] == "D" and (q.get("primary_score_eligible") is not False
                or q.get("pre_ranking_coverage") not in ("COVERAGE_GAP", "PARTIAL_EVIDENCE")):
            raise ValueError(f"P1 diagnostic exclusion/classification changed: {q['id']}")
    by_id = {q["id"]: q for q in questions}
    if [c.get("value") for c in by_id["DOC-Q01"]["requirements"]] != ["Generate", "Validate"]:
        raise ValueError("DOC-Q01 must retain Generate AND Validate")
    for identifier, interval in [("DOC-Q30", [51, 55]), ("DOC-Q38", [76, 82]),
                                  ("DOC-Q39", [75, 82]), ("DOC-Q40", [36, 49])]:
        if [c.get("range") for c in by_id[identifier]["requirements"]] != [interval]:
            raise ValueError(f"Historical interval changed: {identifier}")
    c_questions = [q for q in questions if q["group"] == "C"]
    if sum(len(q["requirements"]) == 1 for q in c_questions) != 18:
        raise ValueError("C must retain 18 single and 12 multi-evidence questions")
    config = spec.get("config", {})
    if (config.get("k1"), config.get("b"), config.get("primary_top_k"),
            config.get("diagnostic_top_k"), config.get("python_hash_seed")) != (1.5, .75, 5, 10, "0"):
        raise ValueError("Frozen retrieval configuration changed")


def expected_inventory(spec: dict) -> set[tuple[str, str]]:
    return ({("documentation_only", q["id"]) for q in spec["questions"] if q["group"] == "A"}
            | {("combined", q["id"]) for q in spec["questions"]})


def validate_inputs(benchmark: Path, corpus: Path, documentation_corpus: Path,
                    output_dir: Path, manifest_path: Path, project_root: Path) -> dict:
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    manifest = read_json(manifest_path)
    check_checksum(benchmark, manifest["benchmark_sha256"])
    spec = read_json(benchmark)
    validate_structure(spec)
    for role, path in (("combined", corpus), ("documentation_only", documentation_corpus)):
        check_checksum(path, manifest["corpora"][role]["sha256"])
    for name, item in manifest["support_files"].items():
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Nonportable support path: {name}")
        check_checksum(benchmark.parent / item["path"], item["sha256"])
    for relative, expected in manifest["implementation_files"].items():
        check_checksum(project_root / relative, expected)
    # The original lock and hash links are preserved, not replaced by a new lock.
    support = lambda name: benchmark.parent / manifest["support_files"][name]["path"]
    lock = read_json(support("original_lock"))
    if lock["specification_sha256"] != file_sha256(benchmark):
        raise ValueError("Original evaluation lock does not match benchmark")
    check_checksum(support("source_review"), lock["source_review_sha256"])
    check_checksum(support("semantic_supplement"), lock["semantic_sha256"])
    for relative, checksum in lock["new_project_files"].items():
        check_checksum(project_root / relative, checksum)
    combined = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line]
    docs = [json.loads(line) for line in documentation_corpus.read_text(encoding="utf-8").splitlines() if line]
    if len(combined) != 11338 or len(docs) != 354:
        raise ValueError("Unexpected corpus record counts")
    if [r for r in combined if r["source_role"] == "documentation"] != docs:
        raise ValueError("Documentation component differs in identity, content, or ordering")
    if len({r["chunk_id"] for r in combined}) != len(combined):
        raise ValueError("Duplicate corpus chunk IDs")
    reference = read_json(support("reference_results"))
    if {(r["mode"], r["id"]) for r in reference} != expected_inventory(spec) or len(reference) != 128:
        raise ValueError("Archived execution inventory differs from frozen specification")
    return {"specification": spec, "manifest": manifest, "combined_records": combined,
            "reference_results": reference,
            "historical_documentation_results": read_json(support("historical_documentation_results")),
            "reference_artifact_checksums": read_json(support("reference_artifact_checksums"))}


def compare_results(rows: list[dict], reference: list[dict], spec: dict) -> list[dict]:
    actual = {(r["mode"], r["id"]): r for r in rows}
    if set(actual) != expected_inventory(spec) or len(rows) != 128:
        raise ValueError("Reproduction must contain all 128 unique question/corpus combinations")
    comparisons = []
    for old in reference:
        row = actual[old["mode"], old["id"]]
        score_differences = []
        for rank, (a, b) in enumerate(zip(old["top_10"], row["top_10"]), 1):
            if json.dumps(a["score"]) != json.dumps(b["score"]) or a["score_hex"] != b["score_hex"]:
                score_differences.append({"rank": rank, "historical": a["score"], "reproduced": b["score"],
                                          "historical_hex": a["score_hex"], "reproduced_hex": b["score_hex"]})
        if len(old["top_10"]) != len(row["top_10"]):
            score_differences.append({"result_count_changed": True})
        tests = {
            "top5_ids_equal": old["top_5_ids"] == row["top_5_ids"],
            "top10_ids_and_order_equal": old["top_10_ids"] == row["top_10_ids"],
            "scores_exact": not score_differences,
            "strict_outcome_and_coverage_equal": old["strict"] == row["strict"],
            "semantic_outcome_equal": old["semantic_anchor"] == row["semantic_anchor"],
            "diagnostic_equal": all(old.get(k) == row.get(k) for k in (
                "diagnostic_outcome", "failure_type", "union_coverage", "retrieved_partial_evidence_ids")),
            "entire_row_equal": old["full_row_sha256"] == canonical_sha256(row),
        }
        comparisons.append({"mode": row["mode"], "id": row["id"],
                            "corpus_sha256": spec["documentation_sha256"] if row["mode"] == "documentation_only" else spec["candidate_sha256"],
                            "historical": old, "reproduced": {k: row[k] for k in (
                                "id", "mode", "group", "strict", "semantic_anchor", "top_5_ids", "top_10_ids")},
                            "checks": tests, "exact_match": all(tests.values()),
                            "score_differences": score_differences,
                            "tolerance_used": False,
                            "cause": None if all(tests.values()) else "Difference observed; inspect checks and full result. No tolerance or oracle adjustment applied."})
    return comparisons
