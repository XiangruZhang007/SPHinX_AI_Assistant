"""Configuration-only file selection; independent of chunking and source roles.

Directory rules recurse, with case-sensitive path components and case-insensitive
extensions. Explicit files bypass directory/extension inclusion, never exclusions.
Future documentation can use the same directory, extension and explicit-file rules;
no documentation or CLI scope is enabled by the bundled configuration.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "indexing_scope.json"
_RULE_KEYS = {"include_directories", "extensions", "include_files", "exclude_directories", "exclude_files"}


def normalize_relative_path(path: str) -> str:
    """Validate a POSIX relative path, normalizing harmless '.' and repeated '/' ."""
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise ValueError("Expected a nonempty repository-relative POSIX path")
    if "\\" in path or ":" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError(f"Malformed POSIX path: {path!r}")
    if ".." in path.split("/"):
        raise ValueError("Path traversal is forbidden")
    normalized = PurePosixPath(path).as_posix()
    if normalized == ".":
        raise ValueError("Path must identify a repository entry")
    return normalized


def _repository_name(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"Invalid repository name: {name!r}")


def validate_config(config: Any) -> None:
    """Raise ValueError for unsupported schema or malformed/unknown rules."""
    if not isinstance(config, dict) or set(config) != {"schema_version", "repositories"}:
        raise ValueError("Expected schema_version and repositories")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported schema_version")
    repositories = config["repositories"]
    if not isinstance(repositories, dict) or not repositories:
        raise ValueError("repositories must be a nonempty object")
    for name, rules in repositories.items():
        _repository_name(name)
        if not isinstance(rules, dict) or set(rules) != _RULE_KEYS:
            raise ValueError(f"Invalid rule keys for {name}")
        for key, values in rules.items():
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"{name}.{key} must be a list of strings")
            if key == "extensions":
                if any(not re.fullmatch(r"\.[a-z0-9]+", v) for v in values):
                    raise ValueError("Extensions must be lowercase dotted suffixes")
            else:
                for value in values:
                    if normalize_relative_path(value) != value:
                        raise ValueError("Configuration paths must be canonical POSIX paths")
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate entries in {name}.{key}")


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Read and validate a versioned indexing configuration."""
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def _rules(config: dict[str, Any], repository: str) -> dict[str, list[str]]:
    _repository_name(repository)
    if repository not in config["repositories"]:
        raise ValueError(f"Unknown repository: {repository!r}")
    return config["repositories"][repository]


def _matches(path: str, rules: dict[str, list[str]]) -> bool:
    if path in rules["exclude_files"] or any(
        path == root or path.startswith(root + "/") for root in rules["exclude_directories"]
    ):
        return False
    if path in rules["include_files"]:
        return True
    return PurePosixPath(path).suffix.lower() in rules["extensions"] and any(
        path.startswith(root + "/") for root in rules["include_directories"]
    )


def is_in_scope(config: dict[str, Any], repository: str, path: str) -> bool:
    """Match a repository-relative file path; invalid inputs raise ValueError."""
    validate_config(config)
    return _matches(normalize_relative_path(path), _rules(config, repository))


def discover_files(config: dict[str, Any], repository: str, repo_root: Path) -> list[Path]:
    """Select files in declared directory order, sorting within each directory.

    Preserve V1 duplicates when included directories overlap. Explicit files are
    appended in declared order only when not already selected. Like V1, pathlib
    does not recurse into directory symlinks; is_file() accepts file symlinks.
    Missing scope directories select no files.
    """
    validate_config(config)
    rules = _rules(config, repository)
    repo_root = Path(repo_root)
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Repository checkout unavailable: {repo_root}")
    selected = []
    for directory in rules["include_directories"]:
        selected.extend(
            path for path in sorted((repo_root / directory).rglob("*"))
            if path.is_file() and _matches(path.relative_to(repo_root).as_posix(), rules)
        )
    seen = set(selected)
    for relative in rules["include_files"]:
        path = repo_root / relative
        if path not in seen and path.is_file() and _matches(relative, rules):
            selected.append(path)
            seen.add(path)
    return selected
