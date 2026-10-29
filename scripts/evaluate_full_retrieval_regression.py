#!/usr/bin/env python3
"""Run one explicit frozen P2.2 condition with the existing evidence evaluator."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.full_retrieval_regression import run_condition


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--condition', choices=['A', 'B', 'C', 'D'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_condition(PROJECT, args.inputs, args.condition, args.output), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'PARTIAL_COMPLETION', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
