#!/usr/bin/env python3
"""Explicit P4 research build/approval/verify/package/restore. No production."""
import argparse
import json
from pathlib import Path
import sys
P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src.corpus_snapshot import read_json
from src.research_baseline import (build_next, approve_baseline, verify_baseline,
                                    package_baseline, restore_baseline, revalidate_legacy)
from src.approval_evidence import verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('build', 'approve', 'verify', 'package', 'restore', 'revalidate', 'verify-evidence'):
        sub = commands.add_parser(name)
        sub.add_argument('--expected-fingerprint', required=True)
        if name in ('build', 'approve', 'revalidate'):
            sub.add_argument('--research-only', action='store_true', required=True)
        if name == 'build':
            for flag in ('base', 'source-root', 'output'):
                sub.add_argument('--' + flag, type=Path, required=True)
            sub.add_argument('--expected-id', required=True)
        elif name in ('approve', 'revalidate'):
            for flag in (('candidate' if name == 'approve' else 'baseline'), 'parent', 'full', 'output', 'quality', 'authorization'):
                sub.add_argument('--' + flag, type=Path, required=True)
            if name == 'revalidate':
                sub.add_argument('--expected-id', required=True)
            sub.add_argument('--parent-format', choices=('p2', 'research'), required=True)
            sub.add_argument('--approval-policy', type=Path, required=True)
            sub.add_argument('--expected-policy-sha256', required=True)
            sub.add_argument('--evidence', type=Path)
        elif name == 'verify-evidence':
            sub.add_argument('--evidence', type=Path, required=True)
            sub.add_argument('--expected-manifest-sha256', required=True)
            sub.add_argument('--fresh-replay', action='store_true')
        elif name in ('verify', 'package'):
            sub.add_argument('--baseline', type=Path, required=True)
            if name == 'verify':
                sub.add_argument('--expected-id')
            else:
                sub.add_argument('--archive', type=Path, required=True)
        else:
            sub.add_argument('--archive', type=Path, required=True)
            sub.add_argument('--output', type=Path, required=True)
            sub.add_argument('--expected-archive-sha256', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'build':
            result = build_next(P, a.base, a.source_root, a.output,
                expected_fingerprint=a.expected_fingerprint, expected_id=a.expected_id)
        elif a.command == 'approve':
            result = approve_baseline(P, a.candidate, a.parent, a.full, a.output,
                expected_fingerprint=a.expected_fingerprint, parent_format=a.parent_format,
                quality=read_json(a.quality), authorization=read_json(a.authorization),
                approval_policy=a.approval_policy, expected_policy_sha256=a.expected_policy_sha256,
                evidence=a.evidence)
        elif a.command == 'verify':
            result = verify_baseline(P, a.baseline, a.expected_fingerprint, a.expected_id)
        elif a.command == 'revalidate':
            result = revalidate_legacy(P, a.baseline, a.parent, a.full, a.output,
                expected_fingerprint=a.expected_fingerprint, expected_id=a.expected_id, parent_format=a.parent_format,
                quality=read_json(a.quality), authorization=read_json(a.authorization),
                approval_policy=a.approval_policy, expected_policy_sha256=a.expected_policy_sha256, evidence=a.evidence)
        elif a.command == 'verify-evidence':
            result = verify_bundle(P, a.evidence, expected_manifest_sha=a.expected_manifest_sha256, fresh_replay=a.fresh_replay)
        elif a.command == 'package':
            result = package_baseline(P, a.baseline, a.archive, a.expected_fingerprint)
        else:
            result = restore_baseline(P, a.archive, a.output, a.expected_fingerprint, a.expected_archive_sha256)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({'status': 'REJECTED', 'error': str(error),
                         'P4_1_verification': 'P4_1_VERIFICATION_FAILED'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
