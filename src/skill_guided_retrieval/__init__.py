"""Independent, read-only source exploration; no production Retriever integration."""
from .explorer import Session
from .scope import build_scopes, AccessPolicy

__all__ = ['Session', 'build_scopes', 'AccessPolicy']
