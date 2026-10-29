#!/usr/bin/env python3
"""Build or verify an isolated research candidate; never promote or update P2."""
import argparse
import json
from pathlib import Path
import sys

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src.incremental_corpus import build_incremental, verify_candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--base-snapshot', required=True, type=Path)
    build.add_argument('--source-root', required=True, type=Path)
    build.add_argument('--output', required=True, type=Path)
    build.add_argument('--expected-fingerprint', required=True)
    build.add_argument('--expected-snapshot-id')
    build.add_argument('--research-only', required=True, action='store_true')
    verify = sub.add_parser('verify')
    verify.add_argument('--candidate', required=True, type=Path)
    verify.add_argument('--expected-fingerprint', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            result = build_incremental(P, args.base_snapshot, args.source_root, args.output,
                                       expected_fingerprint=args.expected_fingerprint,
                                       expected_snapshot_id=args.expected_snapshot_id,
                                       research_only=args.research_only)
        else:
            result = verify_candidate(P, args.candidate, args.expected_fingerprint)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({'status': 'INVALID', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
