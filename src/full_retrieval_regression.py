"""P2.2 orchestration/review; frozen retrieval and grading are imported intact."""
from __future__ import annotations

from functools import partial
import os
from pathlib import Path
import sys

from .corpus_snapshot import digest, file_digest, read_json, read_records, write_json
from .remediation_evaluation import current_availability, evaluate
from .retrieval_evaluation_io import expected_inventory, validate_structure
from .retrieval_evaluation_criteria import matches, outcome
from .retrieval_evaluation_reports import aggregate_metrics
from .retrieval_identity_diagnostics import retrieval_fingerprint
from .retrieval_v2 import create_retriever

CONDITIONS = {"A": ("r1", "legacy"), "B": ("r1", "portable"),
              "C": ("r2", "legacy"), "D": ("r2", "portable")}
PAIRS = (("A", "B", "R1_corpus_identity"), ("A", "C", "retriever_legacy"),
         ("B", "D", "retriever_portable"), ("A", "D", "whole_migration"),
         ("C", "D", "R2_residual_identity"))


def condition_factory(condition):
    if condition not in CONDITIONS:raise ValueError("Unknown evaluation condition: " + str(condition))
    return partial(create_retriever, version=CONDITIONS[condition][0])


def verify_experiment(project, manifest_path):
    if os.environ.get("PYTHONHASHSEED") != "0":raise ValueError("Start Python with PYTHONHASHSEED=0")
    project, manifest_path = Path(project), Path(manifest_path)
    manifest = read_json(manifest_path); base = manifest_path.parent
    if manifest.get("schema_version") != 1:raise ValueError("Unsupported experiment manifest")
    for rel, expected in manifest["implementation_files"].items():
        if file_digest(project / rel) != expected:raise ValueError("Implementation checksum differs: " + rel)
    for rel, expected in manifest["input_files"].items():
        if file_digest(base / rel) != expected:raise ValueError("Input checksum differs: " + rel)
    spec = read_json(base / manifest["benchmark"]); validate_structure(spec)
    if len(spec["questions"]) != 88 or len(expected_inventory(spec)) != 128:
        raise ValueError("Expected unchanged 88 questions / 128 combinations")
    if manifest["parameters"] != {"k1": spec["config"]["k1"], "b": spec["config"]["b"]}:
        raise ValueError("Frozen BM25 parameters differ")
    fp = retrieval_fingerprint(project, manifest["exclusion_contract"], **manifest["parameters"])
    if fp != manifest["R2_fingerprint"]:raise ValueError("Frozen R2 fingerprint differs")
    return manifest, spec


def indexed_results(rows, spec):
    """Reject duplicate/missing mode+question keys, retaining original row bytes."""
    result = {}
    for row in rows:
        key = row["mode"], row["id"]
        if key in result:raise ValueError("Duplicate evaluation key: " + str(key))
        result[key] = row
    if set(result) != expected_inventory(spec):raise ValueError("Missing/unexpected evaluation combinations")
    return result


def run_condition(project, manifest_path, condition, output):
    """Pass an explicit factory into the unchanged 128-combination evaluator."""
    factory = condition_factory(condition)
    output = Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError("Evaluation output already exists")
    manifest, spec = verify_experiment(project, manifest_path); base = Path(manifest_path).parent
    version, corpus_name = CONDITIONS[condition]
    combined = base / manifest["corpora"][corpus_name]
    documentation = base / manifest["corpora"]["documentation_only"]
    components = {"combined": read_records(combined), "documentation_only": read_records(documentation)}
    if {mode: len(rows) for mode, rows in components.items()} != {"combined": 11370, "documentation_only": 354}:
        raise ValueError("Frozen corpus counts differ")
    if [r for r in components["combined"] if r["source_role"] == "documentation"] != components["documentation_only"]:
        raise ValueError("Documentation component differs from combined corpus")
    availability = [*current_availability(spec, components["documentation_only"], "documentation_only"),
                    *current_availability(spec, components["combined"], "combined")]
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "execution.json", {"condition": condition, "retriever_version": version,
               "manifest_sha256": file_digest(manifest_path), "manifest": str(Path(manifest_path).resolve()),
               "python": sys.version, "executable": sys.executable, "cwd": str(Path.cwd()),
               "hash_seed": os.environ["PYTHONHASHSEED"], "parameters": manifest["parameters"],
               "R2_fingerprint": manifest["R2_fingerprint"]["fingerprint"]})
    write_json(output / "pre-ranking-availability.json", availability)
    rows = evaluate(spec, combined, documentation, factory,
                    {mode: len(records) for mode, records in components.items()},
                    {(row["mode"], row["id"]): row for row in availability})
    indexed_results(rows, spec)
    write_json(output / "raw-results.json", rows)
    # Labels belong to new P2.2 files; historical raw rows are never modified.
    labelled = [{**row, "condition": condition, "retriever_version": version} for row in rows]
    write_json(output / "results.json", labelled)
    metrics = aggregate_metrics(rows)
    if metrics["combined_primary_all"]["n"] != 84 or metrics["group"]["documentation_only:A"]["n"] != 40:
        raise ValueError("Primary denominator differs")
    write_json(output / "metrics.json", metrics)
    write_json(output / "summary.json", {"status": "EVALUATED", "condition": condition,
               "retriever_version": version, "combinations": len(rows),
               "raw_results_sha256": file_digest(output / "raw-results.json"),
               "quality_status": "NOT_YET_REVIEWED"})
    write_json(output / "checksums.json", {p.name: file_digest(p) for p in sorted(output.iterdir()) if p.is_file()})
    return {"condition": condition, "combinations": len(rows), "status": "EVALUATED"}


def requirement_ranks(records, requirements, scores, trace):
    """Per-criterion post-dedup ranks; source availability is a separate fact."""
    retained = {index: rank for rank, index in enumerate(trace["retained_indices"], 1)}
    candidates = {row["record_index"]: row for row in trace["trace"]}
    result = []
    for number, criterion in enumerate(requirements):
        exact = [i for i, record in enumerate(records) if matches(record, criterion)]
        surviving = [i for i in exact if i in retained]
        eligible = [i for i in exact if scores[i].score > 0]
        rank = min((retained[i] for i in surviving), default=None)
        if rank is not None:status = "RANKED"
        elif not exact:status = "EVIDENCE_UNAVAILABLE"
        elif not eligible:status = "NONPOSITIVE_SCORE_FILTERED"
        else:status = "DEDUP_SUPPRESSED"
        result.append({"requirement_index": number, "criterion": criterion,
                       "evidence_available": bool(exact), "matching_record_indices": exact,
                       "matching_chunk_ids": [records[i]["chunk_id"] for i in exact],
                       "full_target_rank": rank, "rank_verified": rank is not None,
                       "status_verified": True, "rank_diagnostic_status": status,
                       "per_matching_record": [{"index": i, "chunk_id": records[i]["chunk_id"],
                          "score_hex": scores[i].score.hex(), "retained_rank": retained.get(i),
                          "exclusion": None if i in retained else "NONPOSITIVE_SCORE_FILTERED" if scores[i].score <= 0 else "DEDUP_SUPPRESSED",
                          "dedup_winner_index": candidates.get(i, {}).get("skipped_by_record_index")}
                          for i in exact]})
    return result


def transition(before, after):
    return ("PASS" if before else "FAIL") + "→" + ("PASS" if after else "FAIL")


def compare_matrix(conditions, spec, correspondence):
    if set(conditions) != set(CONDITIONS):raise ValueError("Expected all four conditions")
    indices = {c: indexed_results(rows, spec) for c, rows in conditions.items()}
    id_maps = {"legacy": {r["legacy_id"]: r["index"] for r in correspondence},
               "portable": {r["portable_id"]: r["index"] for r in correspondence}}
    comparisons = []; flat = []; summaries = {}
    for mode, qid in sorted(expected_inventory(spec)):
        query = next(q for q in spec["questions"] if q["id"] == qid)
        cells = {}; mapped = {}
        for condition in CONDITIONS:
            row = indices[condition][mode, qid]; version, corpus = CONDITIONS[condition]
            if outcome([item["record"] for item in row["top_10"]], query["requirements"]) != row["strict"]:
                raise ValueError("Frozen grading does not reproduce stored row")
            mapped[condition] = [id_maps[corpus][identifier] for identifier in row["top_10_ids"]]
            cell = {"condition": condition, "retriever_version": version, "corpus_mode": mode,
                    "question_id": qid, "group": query["group"], "primary": query["group"] != "D",
                    "evidence_available": row["evidence_available"], "strict": row["strict"],
                    "top10_corresponding_record_indices": mapped[condition],
                    "top10_ids": row["top_10_ids"], "top10_scores_hex": [item["score_hex"] for item in row["top_10"]],
                    "failure_type": row.get("failure_type", row.get("diagnostic_outcome"))}
            cells[condition] = cell; flat.append(cell)
        pairs = []
        for left, right, mechanism in PAIRS:
            a, b = cells[left], cells[right]
            pair = {"from": left, "to": right, "mechanism": mechanism,
                    "hit5_transition": transition(a["strict"]["hit_at_5"], b["strict"]["hit_at_5"]),
                    "hit10_transition": transition(a["strict"]["hit_at_10"], b["strict"]["hit_at_10"]),
                    "mapped_top5_equal": mapped[left][:5] == mapped[right][:5],
                    "mapped_top10_equal": mapped[left] == mapped[right],
                    "availability_equal": a["evidence_available"] == b["evidence_available"]}
            pairs.append(pair)
            key = f"{left}→{right}:{mode}:" + ("primary" if query["group"] != "D" else "diagnostic")
            group = summaries.setdefault(key, {"n": 0, "hit5": {}, "hit10": {}}); group["n"] += 1
            for metric in ("hit5", "hit10"):
                group[metric].setdefault(pair[metric + "_transition"], []).append(qid)
        comparisons.append({"corpus_mode": mode, "question_id": qid, "question": query["question"],
                            "requirements": query["requirements"], "conditions": cells, "comparisons": pairs})
    keys = {(r["condition"], r["corpus_mode"], r["question_id"]) for r in flat}
    if len(keys) != 512 or len(flat) != 512:raise ValueError("Expected exactly 512 unique composite keys")
    return {"flat": flat, "per_question": comparisons, "transition_summaries": summaries}


def quality_review(matrix, invariants_valid=True, unresolved=False):
    losses = []; gains = []; availability = []
    for item in matrix["per_question"]:
        for pair in item["comparisons"]:
            if not pair["availability_equal"]:availability.append({"mode": item["corpus_mode"], "id": item["question_id"], "pair": [pair["from"], pair["to"]]})
            if pair["to"] != "D" or pair["from"] not in {"A", "B"} or not item["conditions"]["D"]["primary"]:continue
            for metric in ("hit5", "hit10"):
                detail = {"mode": item["corpus_mode"], "id": item["question_id"],
                          "baseline": pair["from"], "metric": metric}
                if pair[metric + "_transition"] == "PASS→FAIL":losses.append(detail)
                elif pair[metric + "_transition"] == "FAIL→PASS":gains.append(detail)
    status = ("QUALITY_REJECTED" if not invariants_valid or availability else
              "QUALITY_REVIEW_REQUIRED" if losses or unresolved else "QUALITY_ACCEPTED")
    return {"retrieval_quality": status, "new_or_unresolved_primary_losses": losses,
            "primary_gains": gains, "availability_changes": availability,
            "automatic_reuse": False, "production_promotion": "NOT_AUTHORIZED",
            "P2_historical_snapshot_quality_overwritten": False}
