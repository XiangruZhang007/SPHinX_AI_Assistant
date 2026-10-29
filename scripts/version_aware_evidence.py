#!/usr/bin/env python3
"""Q1 local evidence diagnostics. No scoring replacement, approval or network."""
import argparse
import json
from pathlib import Path
import sys
P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src import version_aware_evidence as q


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('analyze', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('--inputs', type=Path, required=True)
        p.add_argument('--expected-inputs-sha256', required=True)
        p.add_argument('--output' if name == 'analyze' else '--report', type=Path, required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'analyze':
            report = q.analyze_to(P, a.inputs, a.expected_inputs_sha256, a.output)
            result = {'status': 'DIAGNOSTIC_COMPLETED', 'report_id': report['report_id'], 'summary': report['summary'], **q.RESTRICTIONS}
        else: result = q.verify_report(P, a.inputs, a.expected_inputs_sha256, a.report)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({'status': 'REJECTED', 'error': str(error), **q.RESTRICTIONS}, ensure_ascii=False)); return 2


if __name__ == '__main__': raise SystemExit(main())
