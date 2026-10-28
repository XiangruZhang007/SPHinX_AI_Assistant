"""Versioned source identity, separate from filesystem and record provenance.

All existing entry points default to ``legacy`` (the exact input path).
Opt-in ``portable-v1`` uses ``portable-v1/<repository>/<relative POSIX path>``
as the existing chunker's identity_path and the supplemental ID seed path.
JSON/documentation retain their already-portable algorithms. Source content,
offsets, routes and hashing algorithms are otherwise unchanged. The scheme is
generation configuration/summary metadata, not a new Retriever record field.
"""
from pathlib import Path

from .indexing_scope import normalize_relative_path

IDENTITY_SCHEMES = ("legacy", "portable-v1")


def validate_identity_scheme(identity_scheme: str) -> str:
    if identity_scheme not in IDENTITY_SCHEMES:
        raise ValueError(f"Unknown identity scheme: {identity_scheme!r}")
    return identity_scheme


def canonical_source_key(repository: str, relative_path: str) -> str:
    if repository not in {"SPHinXsys", "SPHinXsim"}:
        raise ValueError(f"Unknown repository identity: {repository!r}")
    return repository + "/" + normalize_relative_path(relative_path)


def source_identity_path(source_path: Path, identity_scheme: str = "legacy", *,
                         repository: str | None = None, repo_root: Path | None = None) -> str:
    """Return an ID seed only; never use the result to read a physical source."""
    validate_identity_scheme(identity_scheme)
    if identity_scheme == "legacy":
        return str(source_path)
    if repo_root is None:
        raise ValueError("portable-v1 requires an explicit repository root")
    source, root = Path(source_path).absolute(), Path(repo_root).absolute()
    relative = source.relative_to(root).as_posix()
    key = canonical_source_key(repository, relative)
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source resolves outside its repository")
    return "portable-v1/" + key
