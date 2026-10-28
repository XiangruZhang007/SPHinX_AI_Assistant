#!/usr/bin/env python3
"""Build fresh source/document components and a separate no-clobber candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from src.chunk_generation_v1 import generate, generate_sphinxsim, generate_combined_corpus
from src.documentation_chunking import generate_documentation, DEFAULT_CONFIG
from scripts.combine_candidate_corpus import combine, read_records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    a=parser.parse_args()
    if a.output_dir.exists() or a.output_dir.is_symlink():
        parser.error('Output directory already exists')
    a.output_dir.mkdir(parents=True,exist_ok=False);d=a.output_dir
    generate(P,d/'sphinxsys.jsonl',d/'sphinxsys-summary.json')
    generate_sphinxsim(P,d/'sphinxsim.jsonl',d/'sphinxsim-summary.json')
    generate_combined_corpus(d/'sphinxsys.jsonl',d/'sphinxsys-summary.json',d/'sphinxsim.jsonl',d/'sphinxsim-summary.json',d/'source.jsonl',d/'source-summary.json')
    generate_documentation(P,d/'documentation.jsonl',d/'documentation-summary.json',DEFAULT_CONFIG)
    combine([d/'source.jsonl',d/'documentation.jsonl'],d/'expanded-candidate.jsonl')
    rows=read_records(d/'expanded-candidate.jsonl')
    # Existing source records follow chunk_file text/newline conventions; they
    # intentionally overlap. Do not apply documentation partition rules here.
    sources={}
    for r in rows:
        if r['source_path'] not in sources:
            path=P/r['source_path']
            sources[r['source_path']]=(path.read_bytes() if r['source_role']=='documentation'
                                       else path.read_text(encoding='utf-8').encode())
        data=sources[r['source_path']];start,end=r['byte_start'],r['byte_end']
        if data[start:end] != r['content'].encode():
            raise ValueError('Source slice mismatch: '+r['chunk_id'])
    report={'count':len(rows),'unique_ids':True,'all_source_slices_match':True,
            'sha256':hashlib.sha256((d/'expanded-candidate.jsonl').read_bytes()).hexdigest(),
            'component_counts':{name:len(read_records(d/(name+'.jsonl'))) for name in ['sphinxsys','sphinxsim','source','documentation']}}
    (d/'build-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
