"""Bounded P2.1 diagnostics, not the official frozen benchmark evaluation.

The six predeclared queries score every corresponding record. Diagnostic trace
reconstruction is checked against actual inherited R1.search output, and never
changes the production ranker, deduplication, source records or frozen oracles.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import platform
import sys

from . import retrieval_v1 as r1
from .retrieval_v2 import VERSION, searchable_text, create_retriever
from .retrieval_evaluation_criteria import matches, outcome
from .corpus_snapshot import canonical, digest, file_digest, read_json, write_json

DIAGNOSTIC_FILES = (
    "src/retrieval_identity_diagnostics.py", "scripts/diagnose_identity_neutral_retrieval.py",
    "src/retrieval_evaluation_criteria.py", "src/corpus_snapshot.py", "src/indexing_scope.py",
)


def retrieval_fingerprint(project, exclusion_contract, k1, b):
    """Exact R2 + reused R1 contract; independent of corpus, queries and paths."""
    project = Path(project)
    inputs = {
        "schema_version": 1, "version": VERSION,
        "implementation_files": {name: file_digest(project / name)
                                 for name in ("src/retrieval_v1.py", "src/retrieval_v2.py")},
        "exclusion_contract": exclusion_contract, "parameters": {"k1": k1, "b": b},
        "tokenizer": {"TOKEN_RE": r1.TOKEN_RE.pattern, "TOKEN_FLAGS": r1.TOKEN_RE.flags,
                      "CAMEL_RE": r1.CAMEL_RE.pattern, "CAMEL_FLAGS": r1.CAMEL_RE.flags,
                      "implementation": "unchanged src/retrieval_v1.py tokenize"},
        "scoring": "unchanged R1 _bm25, boosts and SearchResult.score",
        "ranking": "unchanged R1 search, chunk_id tie-break and evidence deduplication",
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                    "machine": platform.machine(), "hash_seed": os.environ.get("PYTHONHASHSEED"),
                    "dependencies": "stdlib only; no third-party retrieval packages"},
        "filters": "NONE; entire supplied combined corpus",
        "serialization": "SHA256 canonical UTF8 JSON, sorted keys, compact separators, no trailing newline",
    }
    return {"fingerprint": digest(inputs), "inputs": inputs}


def record_scores(retriever, query):
    """Invoke actual inherited scoring methods; preserve evaluation order."""
    terms = r1.tokenize(query)
    results = []
    for index, record in enumerate(retriever.records):
        result = r1.SearchResult(
            record, retriever._bm25(terms, index), retriever._symbol_boost(query, record),
            retriever._path_boost(terms, record), retriever._role_boost(terms, record),
            retriever._repository_boost(terms, record),
        )
        results.append(result)
    return results


def score_values(result):
    return [result.lexical_score, result.symbol_boost, result.path_boost,
            result.role_boost, result.repository_boost, result.score]


def score_evidence(result):
    values = score_values(result)
    return {"values": values, "hex": [float(value).hex() for value in values]}


def evidence_key(record):
    key = (record["repository"], record["source_path"],
           record.get("symbol") or record.get("section_title"))
    if record.get("node_type") in {"function_definition", "method_declaration"}:
        key += (record.get("start_line"), record.get("end_line"), record["content"])
    return key


def rank_trace(retriever, query, results):
    """Read-only trace mirroring R1, proven against its actual returned Top15."""
    ranked = sorted((i for i, result in enumerate(results) if result.score > 0),
                    key=lambda i: (-results[i].score, results[i].record["chunk_id"]))
    trace = []; winners = {}; retained = []
    for position, index in enumerate(ranked, 1):
        result = results[index]; key = evidence_key(result.record)
        winner = winners.get(key)
        if winner is None:
            winners[key] = index; retained.append(index)
        trace.append({"candidate_rank": position, "record_index": index,
                      "chunk_id": result.record["chunk_id"], "scores": score_evidence(result),
                      "evidence_key_digest": digest(list(key)),
                      "retained_rank": len(retained) if winner is None else None,
                      "skipped_by_record_index": winner})
    actual = retriever.search(query, top_k=15)
    expected = [results[i] for i in retained[:15]]
    if [(x.record, x.score.hex()) for x in actual] != [(x.record, x.score.hex()) for x in expected]:
        raise ValueError("Diagnostic trace does not reproduce actual inherited search")
    return {"trace": trace, "ranked_indices": ranked, "retained_indices": retained,
            "winners": winners, "top15": actual}


def classify_identity_order(left, right, left_scores, right_scores):
    """Exact-score invariance is mandatory; only same-score ID order may differ."""
    if len(left_scores) != len(right_scores) or any(
            score_evidence(a) != score_evidence(b) for a, b in zip(left_scores, right_scores)):
        raise ValueError("Cross-identity R2 score mismatch")
    groups = []
    for scores, trace in [(left_scores, left), (right_scores, right)]:
        by_score = defaultdict(list)
        for i in trace["ranked_indices"]:by_score[scores[i].score.hex()].append(i)
        groups.append(by_score)
    if groups[0].keys() != groups[1].keys():raise ValueError("Candidate score groups differ")
    reordered = []
    for score, indices in groups[0].items():
        other = groups[1][score]
        if set(indices) != set(other):raise ValueError("Unequal-score candidate movement")
        if indices != other:
            reordered.append({"score_hex": score, "legacy_indices": indices, "portable_indices": other})
    if left["winners"].keys() != right["winners"].keys():raise ValueError("Evidence keys differ")
    changes = []
    for key, old in left["winners"].items():
        new = right["winners"][key]
        if old != new:
            if left_scores[old].score != left_scores[new].score:
                raise ValueError("Dedup winner changed outside a score tie")
            changes.append({"evidence_key_digest": digest(list(key)),
                            "legacy_winner": old, "portable_winner": new,
                            "score_hex": left_scores[old].score.hex(),
                            "legacy_retained_rank": left["retained_indices"].index(old) + 1,
                            "portable_retained_rank": right["retained_indices"].index(new) + 1})
    # Same score distribution after dedup proves no non-tied downstream movement.
    if [left_scores[i].score for i in left["retained_indices"]] != [
            right_scores[i].score for i in right["retained_indices"]]:
        raise ValueError("Unexplained dedup-propagated score/rank difference")
    old_ranks = {i: n for n, i in enumerate(left["retained_indices"], 1)}
    new_ranks = {i: n for n, i in enumerate(right["retained_indices"], 1)}
    common = old_ranks.keys() & new_ranks.keys()
    movement = [{"record_index": i, "legacy_rank": old_ranks[i], "portable_rank": new_ranks[i],
                 "classification": "equal-score ordering; any changed dedup winners listed separately"}
                for i in sorted(common) if old_ranks[i] != new_ranks[i]]
    return {"score_mismatches": 0, "unexplained_differences": 0,
            "reordered_tie_groups": reordered, "dedup_winner_changes": changes,
            "retained_rank_changes": movement,
            "non_tied_downstream_score_distribution_changes": 0,
            "top15_correspondence_equal": left["retained_indices"][:15] == right["retained_indices"][:15]}


def _check_correspondence(old, new, correspondence, allowed):
    if len(old) != len(new) or len(old) != len(correspondence):raise ValueError("Corpus correspondence count differs")
    def strip(record):
        # A comparison-only deep copy; never used for search or citations.
        result = json.loads(json.dumps(record))
        for path in allowed:
            parts = path.split("."); parent = result
            for key in parts[:-1]:
                if not isinstance(parent, dict) or key not in parent:break
                parent = parent[key]
            else:
                if isinstance(parent, dict):parent.pop(parts[-1], None)
        return result
    for index, (a, b, item) in enumerate(zip(old, new, correspondence)):
        if (item["index"], item["legacy_id"], item["portable_id"]) != (index, a["chunk_id"], b["chunk_id"]):
            raise ValueError("Incorrect ordered record correspondence")
        if strip(a) != strip(b):raise ValueError("Unexpected nonidentity field difference")


def run_diagnostics(project, manifest_path, output):
    """New experiment only; no historical evaluator or full benchmark invocation."""
    if os.environ.get("PYTHONHASHSEED") != "0":raise ValueError("Start Python with PYTHONHASHSEED=0")
    project, manifest_path, output = Path(project), Path(manifest_path), Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError("Diagnostic output already exists")
    manifest = read_json(manifest_path); base = manifest_path.parent
    if manifest.get("schema_version") != 1:raise ValueError("Unsupported diagnostic manifest")
    for rel, expected in manifest["implementation_files"].items():
        if file_digest(project / rel) != expected:raise ValueError("Frozen implementation changed: " + rel)
    for rel, expected in manifest["input_files"].items():
        if file_digest(base / rel) != expected:raise ValueError("Frozen input changed: " + rel)
    question_set = read_json(base / manifest["questions"])
    questions = question_set["questions"]
    if not 1 <= len(questions) <= 8 or len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Only a small frozen diagnostic subset is allowed")
    k1, b = manifest["parameters"]["k1"], manifest["parameters"]["b"]
    actual_fp = retrieval_fingerprint(project, manifest["exclusion_contract"], k1, b)
    if actual_fp != manifest["retriever_fingerprint"]:raise ValueError("R2 fingerprint differs from frozen expectation")
    paths = {name: base / rel for name, rel in manifest["corpora"].items()}
    engines = {version + "_" + corpus: create_retriever(path, version=version, k1=k1, b=b)
               for version in ("r1", "r2") for corpus, path in paths.items()}
    old, new = engines["r2_legacy"], engines["r2_portable"]
    correspondence = read_json(base / manifest["correspondence"])
    _check_correspondence(old.records, new.records, correspondence, manifest["allowed_identity_paths"])
    if len(old.records) != manifest["records"]:raise ValueError("Frozen corpus record count differs")
    before = {name: digest(engine.records) for name, engine in engines.items()}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "execution.json", {"argv": sys.argv, "python": sys.version,
               "executable": sys.executable, "hash_seed": os.environ["PYTHONHASHSEED"],
               "cwd": str(Path.cwd()), "inputs_manifest": str(manifest_path.resolve()),
               "retriever_fingerprint": actual_fp["fingerprint"], "parameters": {"k1": k1, "b": b}})
    lexical_mismatch = []; examples = []; example_kinds = set(); changed = 0
    with (output / "lexical-records.jsonl").open("x", encoding="utf-8") as stream:
        for index, (a, z) in enumerate(zip(old.records, new.records)):
            left, right = old.indexed_texts[index], new.indexed_texts[index]
            if left != right or old.term_frequencies[index] != new.term_frequencies[index] or old.document_lengths[index] != new.document_lengths[index]:
                lexical_mismatch.append(index)
            r1a, r1b = r1._searchable_text(a), r1._searchable_text(z)
            changed += left != r1a
            evidence = {"index": index, "legacy_id": a["chunk_id"], "portable_id": z["chunk_id"],
                        "source_path": a["source_path"], "R2_actual_text": left,
                        "R2_token_counter": dict(sorted(old.term_frequencies[index].items())),
                        "R2_document_length": old.document_lengths[index],
                        "portable_text_and_counter_and_length_equal": index not in lexical_mismatch,
                        "R1_legacy_text_sha256": digest(r1a), "R1_portable_text_sha256": digest(r1b),
                        "R1_legacy_length": engines["r1_legacy"].document_lengths[index],
                        "R1_portable_length": engines["r1_portable"].document_lengths[index]}
            stream.write(canonical(evidence).decode() + "\n")
            if a["source_path"].endswith("/cli.py"):kind = "CLI"
            elif a["source_role"] == "documentation":kind = "documentation_" + Path(a["source_path"]).suffix
            elif a["source_role"] == "config_or_schema":kind = "JSON_config"
            elif "original_function_chunk_id" in a.get("parent_context", {}):kind = "semantic_source"
            elif a["source_path"].endswith(".py"):kind = "Python_source"
            else:kind = "ordinary_source"
            if kind not in example_kinds:
                example_kinds.add(kind)
                examples.append({**evidence, "kind": kind, "original_record": a, "R1_actual_text": r1a,
                                 "R1_token_counter": dict(sorted(engines["r1_legacy"].term_frequencies[index].items()))})
    stats_equal = old.average_document_length.hex() == new.average_document_length.hex() and old.idf == new.idf
    write_json(output / "text-examples.json", examples)
    write_json(output / "lexical-summary.json", {"records": len(old.records), "changed_from_R1_records": changed,
               "cross_identity_mismatching_indices": lexical_mismatch, "all_corpus_statistics_equal": stats_equal,
               "R2_average_document_length_hex": old.average_document_length.hex(),
               "R1_legacy_average_document_length_hex": engines["r1_legacy"].average_document_length.hex(),
               "R1_portable_average_document_length_hex": engines["r1_portable"].average_document_length.hex()})
    write_json(output / "R2-corpus-statistics.json", {"average_document_length": old.average_document_length,
               "average_document_length_hex": old.average_document_length.hex(),
               "document_lengths": old.document_lengths, "idf_hex": {t: v.hex() for t, v in sorted(old.idf.items())}})
    if lexical_mismatch or not stats_equal:raise ValueError("Exact R2 lexical/statistical isolation failed")
    summaries = []; all_focused = []
    for q in questions:
        scores = {name: record_scores(engine, q["question"]) for name, engine in engines.items()}
        mismatch = [i for i, (a, b) in enumerate(zip(scores["r2_legacy"], scores["r2_portable"]))
                    if score_evidence(a) != score_evidence(b)]
        with (output / (q["id"] + "-all-record-scores.jsonl")).open("x", encoding="utf-8") as stream:
            for index in range(len(old.records)):
                stream.write(canonical({"index": index, "conditions": {
                    name: score_evidence(values[index]) for name, values in scores.items()}}).decode() + "\n")
        if mismatch:raise ValueError("Exact R2 per-record diagnostic score isolation failed: " + q["id"])
        traces = {name: rank_trace(engines[name], q["question"], values) for name, values in scores.items()}
        for name, trace in traces.items():
            with (output / (q["id"] + "-" + name + "-ranking-trace.jsonl")).open("x", encoding="utf-8") as stream:
                for row in trace["trace"]:stream.write(canonical(row).decode() + "\n")
            engine = engines[name]; retained = trace["retained_indices"]
            availability = [[i for i, r in enumerate(engine.records) if matches(r, criterion)] for criterion in q["requirements"]]
            ranks = [next((n for n, i in enumerate(retained, 1) if i in set(indices)), None) for indices in availability]
            # Frozen outcome uses only Top10. Full ranks are separate diagnostics.
            strict = outcome([engine.records[i] for i in retained[:10]], q["requirements"])
            focused = {"id": q["id"], "question": q["question"], "condition": name,
                       "requirements": q["requirements"], "matching_record_indices": availability,
                       "evidence_available": all(availability), "full_evidence_ranks": ranks,
                       "strict_top10": strict,
                       "target_score_evidence": [{"index": i, "scores": score_evidence(scores[name][i]),
                          "document_length": engine.document_lengths[i],
                          "query_term_frequencies": {t: engine.term_frequencies[i][t] for t in sorted(set(r1.tokenize(q["question"])))}}
                          for i in sorted({i for matches_ in availability for i in matches_})],
                       "top15": [{"rank": n, "record_index": i, "scores": score_evidence(scores[name][i]),
                                  "record": engine.records[i]} for n, i in enumerate(retained[:15], 1)]}
            focused["top5_ids"] = [r["record"]["chunk_id"] for r in focused["top15"][:5]]
            focused["top10_ids"] = [r["record"]["chunk_id"] for r in focused["top15"][:10]]
            all_focused.append(focused)
        classification = classify_identity_order(traces["r2_legacy"], traces["r2_portable"],
                                                   scores["r2_legacy"], scores["r2_portable"])
        write_json(output / (q["id"] + "-tie-dedup-analysis.json"), classification)
        summaries.append({"id": q["id"], "records_compared": len(old.records), "score_mismatches": len(mismatch),
                          "reordered_tie_groups": len(classification["reordered_tie_groups"]),
                          "dedup_winner_changes": len(classification["dedup_winner_changes"]),
                          "retained_rank_changes": len(classification["retained_rank_changes"]),
                          "unexplained_differences": classification["unexplained_differences"],
                          "top15_correspondence_equal": classification["top15_correspondence_equal"]})
    for name, engine in engines.items():
        if digest(engine.records) != before[name]:raise ValueError("Original in-memory records mutated")
    for rel, expected in manifest["input_files"].items():
        if file_digest(base / rel) != expected:raise ValueError("Persisted input changed")
    write_json(output / "focused-results.json", all_focused)
    summary = {"status": "DIAGNOSTICS_COMPLETE", "records": len(old.records), "queries": len(questions),
               "query_ids": [q["id"] for q in questions], "conditions": list(engines),
               "R2_cross_identity_record_query_pairs": len(old.records) * len(questions),
               "lexical_mismatches": 0, "score_mismatches": 0, "record_mutations": 0,
               "retriever_fingerprint": actual_fp["fingerprint"], "per_query": summaries,
               "official_full_evaluation_run": False, "quality_acceptance": "NOT_ASSESSED_FOCUSED_ONLY",
               "P2_snapshot_quality_changed": False, "automatic_reuse": False}
    write_json(output / "summary.json", summary)
    write_json(output / "checksums.json", {p.name: file_digest(p) for p in sorted(output.iterdir()) if p.is_file()})
    return summary
