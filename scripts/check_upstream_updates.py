#!/usr/bin/env python3
"""Report whether configured upstream branches differ from validated commits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "upstream_baseline.json"
REQUIRED_FIELDS = ("remote", "branch", "baseline_commit")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpstreamCheckError(RuntimeError):
    """Raised when a configured upstream cannot be checked safely."""


def load_baseline(path: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise UpstreamCheckError(f"Config file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise UpstreamCheckError(f"Malformed JSON in config file {path}: {error.msg}") from error

    if not isinstance(raw, dict) or not raw:
        raise UpstreamCheckError("Config must be a non-empty JSON object.")

    baseline: dict[str, dict[str, str]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name or not isinstance(value, dict):
            raise UpstreamCheckError("Each upstream config entry must have a non-empty string name and object value.")
        missing = [field for field in REQUIRED_FIELDS if not isinstance(value.get(field), str) or not value[field]]
        if missing:
            raise UpstreamCheckError(f"Config entry {name!r} is missing required field(s): {', '.join(missing)}")
        baseline_commit = value["baseline_commit"]
        if not SHA_RE.fullmatch(baseline_commit):
            raise UpstreamCheckError(
                f"Config entry {name!r} has invalid baseline_commit: expected a 40-character hexadecimal SHA."
            )
        baseline[name] = {
            "remote": value["remote"],
            "branch": value["branch"],
            "baseline_commit": baseline_commit.lower(),
        }
    return baseline


def query_latest_commit(
    remote: str,
    branch: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    run = runner or subprocess.run
    reference = f"refs/heads/{branch}"
    try:
        result = run(
            ["git", "ls-remote", remote, reference],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise UpstreamCheckError(f"git ls-remote failed for {remote} {reference}: {error}") from error

    if result.returncode != 0:
        detail = result.stderr.strip() or "no error detail available"
        raise UpstreamCheckError(f"git ls-remote failed for {remote} {reference}: {detail}")

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise UpstreamCheckError(f"Configured branch was not found: {remote} {reference}")
    if len(lines) != 1:
        raise UpstreamCheckError(f"git ls-remote returned multiple results for {remote} {reference}")

    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != reference:
        raise UpstreamCheckError(f"Invalid git ls-remote result for {remote} {reference}")
    latest_commit = fields[0]
    if not SHA_RE.fullmatch(latest_commit):
        raise UpstreamCheckError(f"git ls-remote returned an invalid SHA for {remote} {reference}")
    return latest_commit.lower()


def check_updates(
    config_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, dict[str, str | bool]]:
    baseline = load_baseline(config_path)
    report: dict[str, dict[str, str | bool]] = {}
    for name in sorted(baseline):
        entry = baseline[name]
        latest_commit = query_latest_commit(entry["remote"], entry["branch"], runner)
        report[name] = {
            "branch": entry["branch"],
            "baseline_commit": entry["baseline_commit"],
            "latest_commit": latest_commit,
            "update_available": latest_commit != entry["baseline_commit"],
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    arguments = parser.parse_args(argv)
    try:
        report = check_updates(arguments.config)
    except UpstreamCheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
