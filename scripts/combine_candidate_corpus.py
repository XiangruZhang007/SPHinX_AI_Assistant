#!/usr/bin/env python3
"""Concatenate freshly generated components without overwriting a destination."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


REQUIRED = {"chunk_id", "repository", "source_role", "source_path", "chunk_type",
            "node_type", "symbol", "start_line", "end_line", "byte_start",
            "byte_end", "parent_context", "content"}


def read_records(path: Path) -> list[dict]:
    records, identifiers = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        if not isinstance(record, dict) or not REQUIRED <= record.keys():
            raise ValueError(f"Missing record fields: {path}:{number}")
        if any(not isinstance(record[key], str) or not record[key]
               for key in ("chunk_id", "repository", "source_role", "source_path", "chunk_type", "node_type")):
            raise ValueError(f"Invalid record identity: {path}:{number}")
        if record["chunk_id"] in identifiers:
            raise ValueError(f"Duplicate chunk ID: {record['chunk_id']}")
        identifiers.add(record["chunk_id"])
        if record["repository"] not in {"SPHinXsys", "SPHinXsim"}:
            raise ValueError(f"Unknown repository: {path}:{number}")
        source = Path(record["source_path"])
        if source.is_absolute() or ".." in source.parts or source.parts[:2] != ("repos", record["repository"]):
            raise ValueError(f"Invalid source path: {path}:{number}")
        if (any(type(record[key]) is not int for key in ("start_line", "end_line", "byte_start", "byte_end"))
                or not 1 <= record["start_line"] <= record["end_line"]
                or not 0 <= record["byte_start"] < record["byte_end"]
                or not isinstance(record["content"], str)
                or len(record["content"].encode("utf-8")) != record["byte_end"] - record["byte_start"]):
            raise ValueError(f"Invalid content/range: {path}:{number}")
        records.append(record)
    if not records:
        raise ValueError(f"Empty component: {path}")
    return records


def combine(inputs: list[Path], output: Path) -> dict:
    """Preserve record order/values; validate a staged file before no-clobber link."""
    if not inputs:
        raise ValueError("At least one component is required")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    records = [record for path in inputs for record in read_records(path)]
    identifiers = [r["chunk_id"] for r in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate chunk IDs across components")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=".candidate-stage-", delete=False) as handle:
            stage = Path(handle.name)
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if read_records(stage) != records:
            raise ValueError("Staged records differ from inputs")
        os.link(stage, output)  # Atomic no-clobber publication, including races.
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
    return {"total_chunk_count": len(records), "component_count": len(inputs)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(combine(args.input, args.output), sort_keys=True))
