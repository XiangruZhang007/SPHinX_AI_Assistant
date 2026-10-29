#!/usr/bin/env python3
"""Schema-2 local provenance entry point; no network, publication or auto approval."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src import upstream_provenance as u
from src.corpus_snapshot import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('build', 'verify', 'full', 'compare', 'approve'):
        sub = commands.add_parser(name)
        sub.add_argument('--expected-fingerprint', required=True)
        if name in ('build', 'approve'):
            sub.add_argument('--research-only', action='store_true', required=True)
        flags = {'build': ('parent', 'base-source', 'target-source', 'output', 'policy'),
                 'verify': ('candidate',), 'full': ('source', 'output', 'revisions'),
                 'compare': ('candidate', 'full'),
                 'approve': ('candidate', 'parent', 'full', 'output', 'quality', 'authorization', 'approval-policy')}[name]
        for flag in flags: sub.add_argument('--' + flag, type=Path, required=True)
        if name in ('build', 'approve'): sub.add_argument('--expected-policy-sha256', required=True)
        if name == 'approve': sub.add_argument('--evidence', type=Path)
        if name == 'verify': sub.add_argument('--expected-id')
    a = parser.parse_args(argv)
    try:
        if a.command == 'build':
            r = u.build(P, a.parent, a.base_source, a.target_source, a.output, policy_path=a.policy,
                        expected_policy_sha256=a.expected_policy_sha256, expected_fingerprint=a.expected_fingerprint)
        elif a.command == 'verify':
            r = u.verify(P, a.candidate, a.expected_fingerprint, expected_id=a.expected_id)
        elif a.command == 'compare':
            r = u.compare_full(P, a.candidate, a.full, a.expected_fingerprint)
        elif a.command == 'full':
            r = u.full_reference(P, a.source, a.output, read_json(a.revisions), a.expected_fingerprint)
        else:
            r = u.approve(P, a.candidate, a.parent, a.full, a.output, expected_fingerprint=a.expected_fingerprint,
                quality=read_json(a.quality), authorization=read_json(a.authorization), approval_policy=a.approval_policy,
                expected_policy_sha256=a.expected_policy_sha256, evidence=a.evidence)
        print(json.dumps({k: v for k, v in r.items() if k not in ('receipt', 'lineage')}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.CalledProcessError) as error:
        print(json.dumps({'status': 'REJECTED', 'error': str(error), **u.LIMITS}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
