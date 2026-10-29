#!/usr/bin/env python3
"""P5A candidate-only entry point. No approval, publication or pointer writes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src import ci_v2_research as c


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--key', required=True)
    plan.add_argument('--profile', required=True)
    plan.add_argument('--sys-revision', required=True)
    plan.add_argument('--sim-revision', required=True)
    plan.add_argument('--output', required=True, type=Path)
    execute = commands.add_parser('execute-plan')
    execute.add_argument('--plan', required=True, type=Path)
    execute.add_argument('--work', required=True, type=Path)
    execute.add_argument('--output', required=True, type=Path)
    local = commands.add_parser('local')
    for name in ('base', 'source-root', 'reference', 'policy', 'historical', 'output'):
        local.add_argument('--' + name, required=True, type=Path)
    local.add_argument('--expected-reference-sha256', required=True)
    local.add_argument('--profile', choices=('pinned-research', 'formal', 'diagnostic'), default='pinned-research')
    local.add_argument('--git-checkout', action='store_true')
    restore = commands.add_parser('restore')
    for name in ('archive', 'destination', 'reference'):
        restore.add_argument('--' + name, required=True, type=Path)
    restore.add_argument('--expected-reference-sha256', required=True)
    materialize = commands.add_parser('materialize')
    for name in ('base', 'destination', 'reference'):
        materialize.add_argument('--' + name, required=True, type=Path)
    materialize.add_argument('--expected-reference-sha256', required=True)
    a = parser.parse_args(argv)
    report_dir = a.output if a.command in ('local', 'plan', 'execute-plan') else None
    try:
        lock = c.read(c.LOCK)
        if a.command == 'plan':
            a.output.mkdir(parents=True, exist_ok=False)
            revs = {'SPHinXsys': a.sys_revision, 'SPHinXsim': a.sim_revision}
            c.validate_request(a.profile, a.key, revs)
            ref = c.reference(c.read(c.REGISTRY), a.key)
            c.provenance_gate(a.profile, revs, ref, lock)
            from src.corpus_snapshot import checked_file
            policy_path = checked_file(P, ref['policy_path'])
            c.authorize(ref, policy_path, c.canonical_sha(ref), a.profile, lock)
            c.need(ref['baseline_kind'] == 'p2', 'BOOTSTRAP_REQUIRED', 'Research-parent runner bootstrap not qualified')
            result = {'schema_version': 1, 'key': a.key, 'profile': a.profile,
                      'upstreams': revs, 'reference_sha256': c.canonical_sha(ref),
                      'status': 'PINNED_RESEARCH_PLAN_ONLY', **c.RESTRICTIONS}
            c.write(a.output / 'plan.json', result)
        elif a.command == 'execute-plan':
            plan = c.read(a.plan)
            c.validate_request(plan['profile'], plan['key'], plan['upstreams'])
            ref = c.reference(c.read(c.REGISTRY), plan['key'])
            c.need(c.canonical_sha(ref) == plan['reference_sha256'], 'INVALID_REFERENCE', 'Reviewed registry changed after planning')
            c.provenance_gate(plan['profile'], plan['upstreams'], ref, lock)
            c.frozen_runtime(P, lock)
            c.need(shutil.disk_usage(a.work.parent).free >= 8 * 1024**3, 'RESOURCE_LIMIT',
                   'Pinned research run requires at least 8 GiB free workspace before restore/checkouts')
            a.work.mkdir(parents=True, exist_ok=False)
            c.download(ref['storage_locator'], a.work / 'baseline.tar.gz', ref['transport_sha256'])
            c.download(ref['historical_locator'], a.work / 'historical.json', ref['historical_sha256'])
            from src.corpus_snapshot import checked_file
            policy_path = checked_file(P, ref['policy_path'])
            policy = c.authorize(ref, policy_path, plan['reference_sha256'], plan['profile'], lock)
            c.need(policy['historical_rows_sha256'] == ref['historical_sha256'], 'AUTHORIZATION_FAILED', 'Historical storage/authorization mismatch')
            c.restore(P, a.work / 'baseline.tar.gz', a.work / 'baseline', ref, lock)
            for repository, url in c.SOURCES.items():
                dest = a.work / 'source/repos' / repository
                dest.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['git', '-c', 'credential.helper=', 'clone', '--no-checkout', '--filter=blob:none', url, str(dest)], check=True)
                subprocess.run(['git', '-C', str(dest), '-c', 'credential.helper=', 'fetch', '--depth=1', 'origin', plan['upstreams'][repository]], check=True)
                subprocess.run(['git', '-C', str(dest), 'checkout', '--detach', plan['upstreams'][repository]], check=True)
            result = c.run_local(P, a.work / 'baseline', a.work / 'source', a.output, ref, policy_path,
                     plan['reference_sha256'], profile=plan['profile'], historical_path=a.work / 'historical.json', git_checkout=True)
            c.package_candidate(a.output, a.output.parent / 'candidate.tar.gz')
        elif a.command in ('restore', 'materialize'):
            ref = c.read(a.reference)
            c.need(c.canonical_sha(ref) == a.expected_reference_sha256, 'INVALID_REFERENCE', 'Reference pin mismatch')
            result = (c.restore(P, a.archive, a.destination, ref, lock) if a.command == 'restore' else
                      c.materialize_anchor(P, a.base, a.destination, ref, lock))
        else:
            result = c.run_local(P, a.base, a.source_root, a.output, c.read(a.reference), a.policy,
                     a.expected_reference_sha256, profile=a.profile, historical_path=a.historical, git_checkout=a.git_checkout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.CalledProcessError) as error:
        result = {'status': getattr(error, 'status', 'INTEGRATION_FAILED'), 'error': str(error),
                  'formal_acceptance': 'NOT_ASSESSED', 'hosted_validation': 'HOSTED_NOT_RUN' if os.environ.get('GITHUB_ACTIONS') != 'true' else 'HOSTED_FAILED',
                  **c.RESTRICTIONS}
        if report_dir is not None:
            report_dir.mkdir(parents=True, exist_ok=True)
            failure = report_dir / 'failure.json'
            if not failure.exists():c.write(failure, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
