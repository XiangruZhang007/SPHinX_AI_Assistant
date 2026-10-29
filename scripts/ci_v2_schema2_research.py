#!/usr/bin/env python3
"""Manual schema-2 research workflow. No approval, publication or pointer writes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src import ci_v2_research as c, schema2_workflow as w, trusted_snapshot as t


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    for name in ('key', 'sys-revision', 'sim-revision'):
        plan.add_argument('--' + name, required=True)
    plan.add_argument('--output', type=Path, required=True)
    execute = commands.add_parser('execute-plan')
    for name in ('plan', 'work', 'output'): execute.add_argument('--' + name, type=Path, required=True)
    local = commands.add_parser('local')
    for name in ('archive', 'base-source', 'target-source', 'output', 'reference', 'policy', 'parent-rows'):
        local.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-reference-sha256', 'expected-policy-sha256'): local.add_argument('--' + name, required=True)
    local.add_argument('--q1-inputs', type=Path)
    local.add_argument('--q1-inputs-sha256')
    verify = commands.add_parser('verify-run')
    verify.add_argument('--root', type=Path, required=True)
    restore = commands.add_parser('restore')
    for name in ('archive', 'destination', 'reference'): restore.add_argument('--' + name, type=Path, required=True)
    restore.add_argument('--expected-reference-sha256', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'plan':
            result = w.plan(P, c.read(t.REGISTRY), a.key, {'SPHinXsys': a.sys_revision, 'SPHinXsim': a.sim_revision})
            a.output.mkdir(exist_ok=False, parents=True)
            c.write(a.output / 'plan.json', result)
        elif a.command == 'execute-plan': result = w.execute_plan(P, a.plan, a.work, a.output)
        elif a.command == 'verify-run': result = w.verify_run(P, a.root)
        elif a.command == 'restore': result = t.restore(P, a.archive, a.destination, c.read(a.reference), a.expected_reference_sha256)
        else:
            result = w.run_local(P, a.archive, a.base_source, a.target_source, a.output, c.read(a.reference),
                a.expected_reference_sha256, a.policy, a.expected_policy_sha256, a.parent_rows,
                q1_inputs=a.q1_inputs, q1_sha=a.q1_inputs_sha256)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, tarfile.TarError, subprocess.CalledProcessError) as error:
        result = {'status': getattr(error, 'status', 'INTEGRATION_FAILED'), 'error': str(error), **c.RESTRICTIONS}
        output = getattr(a, 'output', None)
        if output is not None:
            output.mkdir(parents=True, exist_ok=True)
            if not (output / 'failure.json').exists(): c.write(output / 'failure.json', result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__': raise SystemExit(main())
