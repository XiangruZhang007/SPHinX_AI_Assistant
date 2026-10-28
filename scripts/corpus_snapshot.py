#!/usr/bin/env python3
"""Build, verify, package or restore a local full portable candidate snapshot."""
import argparse
import json
from pathlib import Path
import sys

P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from src.corpus_snapshot import SnapshotError,read_json,verify_snapshot,package_snapshot,restore_snapshot


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--source-root',type=Path,required=True)
    build.add_argument('--output',type=Path,required=True);build.add_argument('--inputs',type=Path,required=True)
    build.add_argument('--identity-scheme',choices=['portable-v1'],required=True)
    for command in ['verify','package','restore']:
        p=sub.add_parser(command);p.add_argument('--expected-fingerprint')
        if command=='verify':p.add_argument('--snapshot',type=Path,required=True)
        elif command=='package':
            p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--archive',type=Path,required=True)
        else:
            p.add_argument('--archive',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='build':
            from src.corpus_snapshot_build import build_full_snapshot
            result=build_full_snapshot(P,args.source_root,args.output,read_json(args.inputs),identity_scheme=args.identity_scheme)
        elif args.command=='verify':result=verify_snapshot(args.snapshot,args.expected_fingerprint)
        elif args.command=='package':result=package_snapshot(args.snapshot,args.archive,args.expected_fingerprint)
        else:result=restore_snapshot(args.archive,args.destination,args.expected_fingerprint)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 1 if result.get('build_compatibility')=='INCOMPATIBLE' else 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps({'structural_status':'STRUCTURALLY_INVALID','error':str(error)},ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
